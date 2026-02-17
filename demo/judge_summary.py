#!/usr/bin/env python3
"""LLM judge summaries for demo runs.

Supports:
- Workshop comparison: deterministic baseline JSON + LLM verbose log
- Valentines single-run transcript summary: LLM verbose log only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import textwrap
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from pydantic import BaseModel, Field

from miniverse.llm_utils import call_llm_with_retries


class CompareJudgeOutput(BaseModel):
    executive_summary: str
    key_differences: list[str] = Field(default_factory=list)
    coordination_findings: list[str] = Field(default_factory=list)
    next_experiments: list[str] = Field(default_factory=list)


class SingleJudgeOutput(BaseModel):
    executive_summary: str
    key_events: list[str] = Field(default_factory=list)
    coordination_signals: list[str] = Field(default_factory=list)
    next_experiments: list[str] = Field(default_factory=list)


ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metric_by_label(metrics: Dict[str, Any]) -> Dict[str, Any]:
    by_label: Dict[str, Any] = {}
    for stat in metrics.values():
        if not isinstance(stat, dict):
            continue
        label = stat.get("label")
        if isinstance(label, str) and label:
            by_label[label] = stat.get("value")
    return by_label


def _parse_baseline_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    final_state = payload.get("final_state") or {}
    metrics = ((final_state.get("resources") or {}).get("metrics") or {})
    return {
        "run_id": payload.get("run_id"),
        "ticks_completed": payload.get("ticks_completed"),
        "metrics_by_label": _metric_by_label(metrics),
    }


def _extract_last_numeric_metric(log_text: str, label: str) -> Optional[float]:
    matches = re.findall(
        rf"^\s*{re.escape(label)}:\s*([-+]?\d+(?:\.\d+)?)\b",
        log_text,
        flags=re.MULTILINE,
    )
    if not matches:
        return None
    return float(matches[-1])


def _extract_action_counts(log_text: str) -> Dict[str, int]:
    counts: Counter[str] = Counter()
    for record in _extract_action_records(log_text):
        counts[record["action_type"]] += 1
    return dict(sorted(counts.items()))


def _extract_action_records(log_text: str) -> list[dict[str, Any]]:
    """Parse per-tick action bullets from log summaries.

    Expected format in each tick summary:
      Actions:
        - Agent Name: action_type ... comm.to=agent_id - reasoning...
    """
    clean = _strip_ansi(log_text)
    lines = clean.splitlines()
    records: list[dict[str, Any]] = []
    tick_num: Optional[int] = None
    in_actions = False
    tick_re = re.compile(r"^=== Tick (\d+)/")
    action_re = re.compile(r"^\s*(?:-\s+)?([^:]+):\s+([a-z_]+)\b(.*)$")
    comm_to_re = re.compile(r"\bcomm\.to=([a-z_]+)\b")

    for line in lines:
        stripped = line.strip()
        tick_match = tick_re.match(stripped)
        if tick_match:
            tick_num = int(tick_match.group(1))
            in_actions = False
            continue
        if stripped == "Actions:":
            in_actions = True
            continue
        if not in_actions:
            continue
        if not stripped:
            in_actions = False
            continue
        if not stripped.startswith("- "):
            continue
        m = action_re.match(stripped)
        if not m:
            continue
        agent_name = m.group(1).strip()
        action_type = m.group(2).strip()
        rest = m.group(3) or ""
        comm_to_match = comm_to_re.search(rest)
        comm_to = comm_to_match.group(1) if comm_to_match else None
        records.append(
            {
                "tick": tick_num,
                "agent": agent_name,
                "action_type": action_type,
                "comm_to": comm_to,
                "raw": stripped,
            }
        )
    if records:
        return records

    # Backward-compatible fallback for older verbose logs that did not emit
    # an explicit "Actions:" block header.
    tick_num = None
    fallback_re = re.compile(
        r"^\s{2,}([A-Za-z][A-Za-z .'-]+):\s+([a-z_]+)\b(.*\s-\s.*)$"
    )
    for line in lines:
        stripped = line.strip()
        tick_match = tick_re.match(stripped)
        if tick_match:
            tick_num = int(tick_match.group(1))
            continue
        m = fallback_re.match(line)
        if not m:
            continue
        agent_name = m.group(1).strip()
        action_type = m.group(2).strip()
        rest = m.group(3) or ""
        comm_to_match = comm_to_re.search(rest)
        comm_to = comm_to_match.group(1) if comm_to_match else None
        records.append(
            {
                "tick": tick_num,
                "agent": agent_name,
                "action_type": action_type,
                "comm_to": comm_to,
                "raw": stripped,
            }
        )

    return records


def _extract_message_snippets(log_text: str, *, max_items: int = 16) -> list[str]:
    clean = _strip_ansi(log_text)
    lines = clean.splitlines()
    snippets: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^\s*Message:\s*(.*)$", line)
        if not m:
            i += 1
            continue
        content = m.group(1).strip()
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            # Continuation lines in wrapped logs are indented and not new labels.
            if re.match(r"^\s{6,}\S", nxt) and not re.match(
                r"^\s{6,}(Reasoning:|Message:|\[LLM|\[Plan|=== Tick|Actions:)", nxt
            ):
                content = f"{content} {nxt.strip()}"
                j += 1
                continue
            break
        compact = " ".join(content.strip('" ').split())
        if compact:
            snippets.append(compact)
        i = j
    # De-duplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for item in snippets:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
        if len(unique) >= max_items:
            break
    return unique


def _extract_agent_id_name_map(log_text: str) -> dict[str, str]:
    clean = _strip_ansi(log_text)
    mapping: dict[str, str] = {}
    for line in clean.splitlines():
        m = re.match(r"^\s*-\s+(.+?)\s+\[([a-z0-9_]+)\]\s+\(", line)
        if not m:
            continue
        name = " ".join(m.group(1).split())
        agent_id = m.group(2).strip()
        if agent_id and name:
            mapping[agent_id] = name
    return mapping


def _extract_final_resources(log_text: str) -> dict[str, str]:
    clean = _strip_ansi(log_text)
    lines = clean.splitlines()
    final_resources: dict[str, str] = {}
    in_final = False
    for line in lines:
        stripped = line.rstrip()
        if stripped.strip() == "Final resources:":
            in_final = True
            continue
        if not in_final:
            continue
        if not stripped.strip():
            break
        m = re.match(r"^\s{2}([^:]+):\s*(.+?)\s*$", stripped)
        if not m:
            continue
        final_resources[m.group(1).strip()] = m.group(2).strip()
    return final_resources


def _build_single_facts(*, scenario: str, llm_log_text: str) -> dict[str, Any]:
    actions = _extract_action_records(llm_log_text)
    id_to_name = _extract_agent_id_name_map(llm_log_text)
    action_counts = Counter(a["action_type"] for a in actions)
    by_agent_counts: Dict[str, int] = defaultdict(int)
    by_agent_action_counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    directed_edges: Counter[tuple[str, str]] = Counter()
    for record in actions:
        agent = record["agent"]
        by_agent_counts[agent] += 1
        by_agent_action_counts[agent][record["action_type"]] += 1
        comm_to = record.get("comm_to")
        if isinstance(comm_to, str) and comm_to:
            directed_edges[(agent, comm_to)] += 1

    outgoing_messages_by_agent: Dict[str, int] = defaultdict(int)
    inbound_messages_by_agent: Dict[str, int] = defaultdict(int)
    for (src, dst_id), count in directed_edges.items():
        outgoing_messages_by_agent[src] += count
        dst_name = id_to_name.get(dst_id, dst_id)
        inbound_messages_by_agent[dst_name] += count

    coordinator = None
    if outgoing_messages_by_agent or inbound_messages_by_agent:
        traffic_by_agent: Dict[str, int] = defaultdict(int)
        for agent, count in outgoing_messages_by_agent.items():
            traffic_by_agent[agent] += count
        for agent, count in inbound_messages_by_agent.items():
            traffic_by_agent[agent] += count
        coordinator = max(
            traffic_by_agent.items(),
            key=lambda kv: kv[1],
        )[0]
    elif by_agent_counts:
        coordinator = max(by_agent_counts.items(), key=lambda kv: kv[1])[0]

    return {
        "scenario": scenario,
        "run_id": _extract_run_id(llm_log_text),
        "ticks_completed": _extract_ticks_completed(llm_log_text),
        "final_resources": _extract_final_resources(llm_log_text),
        "action_counts": dict(sorted(action_counts.items())),
        "actions_by_agent": dict(sorted(by_agent_counts.items(), key=lambda kv: kv[0].lower())),
        "actions_by_agent_type": {
            agent: dict(sorted(counts.items()))
            for agent, counts in sorted(by_agent_action_counts.items(), key=lambda kv: kv[0].lower())
        },
        "outgoing_messages_by_agent": dict(
            sorted(outgoing_messages_by_agent.items(), key=lambda kv: kv[0].lower())
        ),
        "inbound_messages_by_agent": dict(
            sorted(inbound_messages_by_agent.items(), key=lambda kv: kv[0].lower())
        ),
        "communication_edges_top": [
            {"from": src, "to": id_to_name.get(dst, dst), "count": count}
            for (src, dst), count in directed_edges.most_common(12)
        ],
        "likely_coordinator": coordinator,
        "message_snippets": _extract_message_snippets(llm_log_text, max_items=20),
        "schema_retry_count": llm_log_text.count("LLM schema validation failed"),
        "log_digest": _collect_digest_lines(llm_log_text),
    }


def _extract_run_id(log_text: str) -> Optional[str]:
    clean = _strip_ansi(log_text)
    matches = re.findall(r"^\s*Run ID:\s*([0-9a-fA-F-]+)\s*$", clean, flags=re.MULTILINE)
    return matches[-1] if matches else None


def _extract_ticks_completed(log_text: str) -> Optional[int]:
    clean = _strip_ansi(log_text)
    matches = re.findall(r"^\s*Completed\s+(\d+)\s+ticks\s*$", clean, flags=re.MULTILINE)
    if not matches:
        return None
    return int(matches[-1])


def _collect_digest_lines(log_text: str, *, max_lines: int = 180) -> list[str]:
    clean = _strip_ansi(log_text)
    keywords = (
        "=== Tick",
        "Clock:",
        "Task flow:",
        "Resources:",
        "Pending Tasks:",
        "Execution Capacity:",
        "Simulation complete",
        "Run ID:",
        "schema validation failed",
        ": communicate",
        ": work",
        ": analyze",
        ": monitor",
        ": rest",
    )
    lines = [line.strip() for line in clean.splitlines() if any(k in line for k in keywords)]
    if len(lines) <= max_lines:
        return lines
    head = max_lines // 2
    tail = max_lines - head
    return lines[:head] + ["... (omitted) ..."] + lines[-tail:]


def _print_section(title: str, lines: Iterable[str]) -> None:
    print(f"\n{title}")
    for line in lines:
        if not line:
            continue
        wrapped = textwrap.fill(
            line,
            width=100,
            initial_indent="  - ",
            subsequent_indent="    ",
        )
        print(wrapped)


def _fallback_workshop_summary(
    baseline: dict[str, Any],
    llm_log_text: str,
) -> CompareJudgeOutput:
    base = baseline["metrics_by_label"]
    pending_base = _as_float(base.get("Pending Tasks"))
    pending_llm = _extract_last_numeric_metric(llm_log_text, "Pending Tasks")
    arrived_base = _as_float(base.get("Tasks Arrived (Total)"))
    arrived_llm = _extract_last_numeric_metric(llm_log_text, "Tasks Arrived (Total)")
    completed_base = _as_float(base.get("Tasks Completed (Total)"))
    completed_llm = _extract_last_numeric_metric(llm_log_text, "Tasks Completed (Total)")
    exec_base = _as_float(base.get("Execution Capacity"))
    exec_llm = _extract_last_numeric_metric(llm_log_text, "Execution Capacity")
    actions = _extract_action_counts(llm_log_text)
    retries = llm_log_text.count("LLM schema validation failed")

    diffs: list[str] = []
    if pending_base is not None and pending_llm is not None:
        diffs.append(f"Pending Tasks: baseline={pending_base:.2f}, llm={pending_llm:.2f}.")
    if completed_base is not None and completed_llm is not None:
        diffs.append(
            f"Tasks Completed (Total): baseline={completed_base:.2f}, llm={completed_llm:.2f}."
        )
    if arrived_base is not None and arrived_llm is not None:
        diffs.append(
            f"Tasks Arrived (Total): baseline={arrived_base:.2f}, llm={arrived_llm:.2f}."
        )
    if exec_base is not None and exec_llm is not None:
        diffs.append(f"Execution Capacity: baseline={exec_base:.2f}, llm={exec_llm:.2f}.")

    return CompareJudgeOutput(
        executive_summary=(
            "Deterministic mode followed fixed role policy, while the LLM run showed "
            "agent-dependent coordination and pacing behavior."
        ),
        key_differences=diffs or ["Could not compute numeric deltas from artifacts."],
        coordination_findings=[
            f"LLM action mix: {actions or {'unknown': 0}}",
            f"Schema retries observed: {retries}",
        ],
        next_experiments=[
            "Increase run length and compare queue behavior under higher arrival pressure.",
            "Track push/rest usage by agent to measure rotation strategies.",
        ],
    )


def _fallback_single_summary(log_text: str, scenario: str) -> SingleJudgeOutput:
    facts = _build_single_facts(scenario=scenario, llm_log_text=log_text)
    action_mix = facts.get("action_counts") or {"unknown": 0}
    retries = int(facts.get("schema_retry_count") or 0)
    coordinator = facts.get("likely_coordinator") or "none"
    ticks = facts.get("ticks_completed")
    run_id = facts.get("run_id")
    edge_preview = facts.get("communication_edges_top") or []
    edge_line = "No directed communication parsed."
    if edge_preview:
        edge = edge_preview[0]
        edge_line = (
            f"Most frequent directed link: {edge.get('from')} -> "
            f"{edge.get('to')} ({edge.get('count')} messages)."
        )
    snippets = facts.get("message_snippets") or []
    snippet_line = snippets[0] if snippets else "No message snippet captured."

    return SingleJudgeOutput(
        executive_summary=(
            f"{scenario.capitalize()} run completed in {ticks if ticks is not None else '?'} ticks"
            f"{f' (Run ID: {run_id})' if run_id else ''}. "
            f"Likely coordination hub: {coordinator}. Action mix and communication traces were extracted "
            "from per-tick action summaries."
        ),
        key_events=[
            f"Action mix: {action_mix}",
            f"Likely coordination hub: {coordinator}",
            edge_line,
            f"Representative communication: {snippet_line}",
            f"Schema retries observed: {retries}",
        ],
        coordination_signals=[
            "Directed communication and follow-through parsed from each tick's Actions block.",
            "Coordinator inference is based on per-agent action and edge frequency.",
        ],
        next_experiments=[
            "Repeat with a different seed and compare whether coordination motifs persist.",
            "Track recurring message templates to detect looped coordination vs net progress.",
        ],
    )


def _single_output_is_thin(output: SingleJudgeOutput, facts: dict[str, Any]) -> bool:
    text = " ".join(
        [output.executive_summary] + output.key_events + output.coordination_signals
    ).lower()
    if len(output.key_events) < 4:
        return True
    run_id = str(facts.get("run_id") or "").strip().lower()
    if run_id and run_id not in text:
        return True
    ticks = facts.get("ticks_completed")
    if ticks is not None and str(ticks) not in text:
        return True
    coordinator = str(facts.get("likely_coordinator") or "").strip().lower()
    if coordinator and coordinator not in text:
        return True
    if facts.get("message_snippets") and "message" not in text and "communication" not in text:
        return True
    return False


async def _judge_workshop_compare(
    *,
    baseline: dict[str, Any],
    llm_log_text: str,
) -> CompareJudgeOutput:
    provider = os.getenv("LLM_PROVIDER")
    model = os.getenv("LLM_MODEL")
    if not provider or not model:
        return _fallback_workshop_summary(baseline, llm_log_text)

    labels = [
        "Pending Tasks",
        "Tasks Arrived (Total)",
        "Tasks Completed (Total)",
        "Execution Capacity",
    ]
    baseline_metrics = {
        label: baseline["metrics_by_label"].get(label)
        for label in labels
    }
    llm_metrics = {label: _extract_last_numeric_metric(llm_log_text, label) for label in labels}
    facts = {
        "baseline": {
            "run_id": baseline.get("run_id"),
            "ticks_completed": baseline.get("ticks_completed"),
            "metrics": baseline_metrics,
        },
        "llm": {
            "metrics": llm_metrics,
            "action_counts": _extract_action_counts(llm_log_text),
            "schema_retry_count": llm_log_text.count("LLM schema validation failed"),
        },
        "llm_digest": _collect_digest_lines(llm_log_text),
    }

    system_prompt = (
        "You are an executive reviewer for simulation experiments. "
        "Compare deterministic baseline vs LLM-driven run and produce a concise, "
        "evidence-grounded executive summary."
    )
    user_prompt = (
        "Analyze these workshop artifacts and return JSON only.\n\n"
        "Rules:\n"
        "- Be concrete and specific to the provided metrics/log evidence.\n"
        "- Call out whether differences were coordination-related or mostly policy/mechanics.\n"
        "- Keep each bullet short.\n\n"
        "Artifacts JSON:\n"
        f"{json.dumps(facts, indent=2)}"
    )

    try:
        return await call_llm_with_retries(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            llm_provider=provider,
            llm_model=model,
            response_model=CompareJudgeOutput,
        )
    except Exception:
        return _fallback_workshop_summary(baseline, llm_log_text)


async def _judge_single_run(*, scenario: str, llm_log_text: str) -> SingleJudgeOutput:
    provider = os.getenv("LLM_PROVIDER")
    model = os.getenv("LLM_MODEL")
    facts = _build_single_facts(scenario=scenario, llm_log_text=llm_log_text)
    if not provider or not model:
        return _fallback_single_summary(llm_log_text, scenario)

    system_prompt = (
        "You are an executive reviewer for simulation experiments. "
        "Summarize what happened in a single LLM-driven run."
    )
    user_prompt = (
        "Analyze this transcript digest and return JSON only.\n\n"
        "Rules:\n"
        "- Be specific and evidence-grounded.\n"
        "- Focus on coordination/social dynamics and outcome-relevant behavior.\n"
        "- Name the likely coordination hub explicitly if present in facts.\n"
        "- Reference at least one concrete message/logistics detail from message_snippets.\n"
        "- Mention run_id and ticks_completed in executive_summary when available.\n"
        "- Keep each bullet short.\n\n"
        "Artifacts JSON:\n"
        f"{json.dumps(facts, indent=2)}"
    )

    try:
        output = await call_llm_with_retries(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            llm_provider=provider,
            llm_model=model,
            response_model=SingleJudgeOutput,
        )
        if _single_output_is_thin(output, facts):
            return _fallback_single_summary(llm_log_text, scenario)
        return output
    except Exception:
        return _fallback_single_summary(llm_log_text, scenario)


def _print_compare_output(output: CompareJudgeOutput) -> None:
    print("")
    print("=" * 80)
    print("LLM Judge: Executive Summary")
    print("=" * 80)
    print(textwrap.fill(output.executive_summary, width=100))
    _print_section("Key differences", output.key_differences)
    _print_section("Coordination findings", output.coordination_findings)
    _print_section("Next experiments", output.next_experiments)


def _print_single_output(output: SingleJudgeOutput) -> None:
    print("")
    print("=" * 80)
    print("LLM Judge: Executive Summary")
    print("=" * 80)
    print(textwrap.fill(output.executive_summary, width=100))
    _print_section("Key events", output.key_events)
    _print_section("Coordination signals", output.coordination_signals)
    _print_section("Next experiments", output.next_experiments)


async def _main_async(args: argparse.Namespace) -> int:
    llm_log_path = Path(args.llm_log)
    llm_log_text = llm_log_path.read_text()

    if args.scenario == "workshop":
        if not args.baseline_json:
            raise ValueError("--baseline-json is required for --scenario workshop")
        baseline = _parse_baseline_json(Path(args.baseline_json))
        output = await _judge_workshop_compare(
            baseline=baseline,
            llm_log_text=llm_log_text,
        )
        _print_compare_output(output)
        return 0

    output = await _judge_single_run(
        scenario=args.scenario,
        llm_log_text=llm_log_text,
    )
    _print_single_output(output)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM judge summary for demo runs")
    parser.add_argument(
        "--scenario",
        required=True,
        choices=["workshop", "valentines"],
        help="Demo scenario type",
    )
    parser.add_argument(
        "--llm-log",
        required=True,
        help="Path to verbose LLM run log",
    )
    parser.add_argument(
        "--baseline-json",
        default=None,
        help="Baseline JSON artifact (required for workshop comparison)",
    )
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
