"""Miniverse CLI - Run agent-based simulations from the command line.

Usage:
    miniverse run <scenario> --ticks N [--llm] [--seed S] [--output json]
    miniverse list
    miniverse info <scenario>

Examples:
    miniverse run org-hierarchy --ticks 20
    miniverse run org-hierarchy --ticks 20 --llm
    miniverse run org-hierarchy --ticks 10 --seed 42 --output json
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(
    name="miniverse",
    help="Run LLM-driven agent-based simulations for computational social science.",
    add_completion=False,
    no_args_is_help=True,
)


@app.command()
def run(
    scenario: str = typer.Argument(
        ...,
        help="Scenario template name (e.g., 'org-hierarchy') or path to scenario.json",
    ),
    ticks: int = typer.Option(
        10,
        "--ticks",
        "-t",
        help="Number of simulation ticks to run",
    ),
    llm: bool = typer.Option(
        False,
        "--llm",
        help="Enable LLM-based cognition (requires API key)",
    ),
    seed: Optional[int] = typer.Option(
        None,
        "--seed",
        "-s",
        help="Random seed for reproducibility",
    ),
    output: str = typer.Option(
        "text",
        "--output",
        "-o",
        help="Output format: 'text' (human-readable) or 'json' (machine-readable)",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Suppress per-tick output, only show final result",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        "-d",
        help="Enable debug logging (sets DEBUG_LLM, DEBUG_MEMORY, MINIVERSE_VERBOSE)",
    ),
) -> None:
    """Run a simulation with the specified scenario."""
    asyncio.run(
        _run_simulation(
            scenario=scenario,
            ticks=ticks,
            use_llm=llm,
            seed=seed,
            output_format=output,
            quiet=quiet,
            debug=debug,
        )
    )


@app.command(name="list")
def list_scenarios() -> None:
    """List available scenario templates."""
    from miniverse.templates import list_templates, get_template_info

    templates = list_templates()

    if not templates:
        typer.echo("No scenario templates found.")
        typer.echo("Templates should be in miniverse/templates/")
        raise typer.Exit(1)

    typer.echo("Available scenarios:\n")
    for name in templates:
        try:
            info = get_template_info(name)
            agent_count = len(info.get("agents", []))
            description = info.get("description", "No description")
            # Truncate description
            if len(description) > 50:
                description = description[:47] + "..."
            typer.echo(f"  {name:<20} {agent_count} agents  - {description}")
        except Exception:
            typer.echo(f"  {name:<20} (error loading info)")


@app.command()
def info(
    scenario: str = typer.Argument(..., help="Scenario template name"),
) -> None:
    """Show detailed information about a scenario template."""
    from miniverse.templates import get_template_path, get_template_info

    try:
        template_path = get_template_path(scenario)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    try:
        info_data = get_template_info(scenario)
    except Exception as e:
        typer.echo(f"Error loading scenario: {e}", err=True)
        raise typer.Exit(1)

    typer.echo(f"\nScenario: {scenario}")
    typer.echo(f"Path: {template_path}")
    typer.echo("")

    if "description" in info_data:
        typer.echo(f"Description:\n  {info_data['description']}")
        typer.echo("")

    agents = info_data.get("agents", [])
    if agents:
        typer.echo(f"Agents: {len(agents)}")
        for agent in agents:
            profile = agent.get("profile", {})
            agent_id = profile.get("agent_id", "unknown")
            name = profile.get("name", "Unknown")
            role = profile.get("role", "")
            typer.echo(f"  - {agent_id} ({name}) - {role}")
        typer.echo("")

    # Environment type
    env_graph = info_data.get("environment_graph")
    env_grid = info_data.get("environment_grid")
    if env_grid:
        width = env_grid.get("width", 0)
        height = env_grid.get("height", 0)
        typer.echo(f"Environment: Grid (Tier 2) - {width}x{height}")
    elif env_graph:
        nodes = env_graph.get("nodes", {})
        typer.echo(f"Environment: Graph (Tier 1) - {len(nodes)} locations")
        for node_id, node in nodes.items():
            node_name = node.get("name", node_id)
            capacity = node.get("capacity", "unlimited")
            typer.echo(f"  - {node_id}: {node_name} (capacity: {capacity})")
    else:
        typer.echo("Environment: Abstract (Tier 0)")
    typer.echo("")

    # Resources
    resources = info_data.get("resources", {}).get("metrics", {})
    if resources:
        typer.echo("Resources:")
        for key, stat in resources.items():
            value = stat.get("value", "?")
            unit = stat.get("unit", "")
            label = stat.get("label", key)
            typer.echo(f"  - {label}: {value} {unit}")


async def _run_simulation(
    scenario: str,
    ticks: int,
    use_llm: bool,
    seed: Optional[int],
    output_format: str,
    quiet: bool,
    debug: bool,
) -> None:
    """Core simulation execution logic."""
    import contextlib
    import io
    import random

    from miniverse import Orchestrator, AgentCognition
    from miniverse.cognition import Scratchpad
    from miniverse.config import Config
    from miniverse.scenario import ScenarioLoader
    from miniverse.templates import get_template_path, load_template_rules, load_template_cognition

    # Set debug environment variables if requested
    if debug:
        os.environ["DEBUG_LLM"] = "true"
        os.environ["DEBUG_MEMORY"] = "true"
        os.environ["MINIVERSE_VERBOSE"] = "true"

    # Resolve scenario path
    scenario_path = Path(scenario)
    if scenario_path.exists() and scenario_path.suffix == ".json":
        # Direct path to scenario.json
        template_path = scenario_path.parent
        scenario_name = scenario_path.stem
    elif scenario_path.is_dir() and (scenario_path / "scenario.json").exists():
        # Directory containing scenario.json
        template_path = scenario_path
        scenario_name = "scenario"
    else:
        # Template name - look in templates directory
        try:
            template_path = get_template_path(scenario)
            scenario_name = "scenario"
        except ValueError:
            typer.echo(f"Error: Scenario '{scenario}' not found.", err=True)
            typer.echo("Use 'miniverse list' to see available scenarios.", err=True)
            raise typer.Exit(1)

    # Load scenario
    try:
        loader = ScenarioLoader(scenarios_dir=template_path)
        world_state, profiles = loader.load(scenario_name)
    except Exception as e:
        typer.echo(f"Error loading scenario: {e}", err=True)
        raise typer.Exit(1)

    profiles_map = {p.agent_id: p for p in profiles}

    # Load rules from template
    rules = load_template_rules(template_path, seed=seed)

    # Validate LLM config if needed
    if use_llm:
        try:
            Config.validate()
        except ValueError as e:
            typer.echo(f"Error: {e}", err=True)
            typer.echo("Set LLM_PROVIDER, LLM_MODEL, and API key environment variables.", err=True)
            raise typer.Exit(1)

    # Build cognition map
    cognition_map = load_template_cognition(template_path, profiles_map, use_llm=use_llm)

    # Get LLM config
    provider = Config.LLM_PROVIDER if use_llm else None
    model = Config.LLM_MODEL if use_llm else None

    # Create orchestrator
    orchestrator = Orchestrator(
        world_state=world_state,
        agents=profiles_map,
        world_prompt="You oversee simulation state transitions.",
        agent_prompts={
            agent_id: f"You are {profile.name}, the {profile.role}."
            for agent_id, profile in profiles_map.items()
        },
        llm_provider=provider,
        llm_model=model,
        simulation_rules=rules,
        agent_cognition=cognition_map,
    )

    # Run simulation
    if quiet or output_format == "json":
        # Suppress orchestrator output for quiet mode or JSON output
        with contextlib.redirect_stdout(io.StringIO()):
            result = await orchestrator.run(num_ticks=ticks)
    else:
        result = await orchestrator.run(num_ticks=ticks)

    # Output results
    if output_format == "json":
        output_data = {
            "run_id": str(result["run_id"]),
            "scenario": scenario,
            "ticks_completed": ticks,
            "seed": seed,
            "llm_enabled": use_llm,
            "final_state": result["final_state"].model_dump(mode="json"),
        }
        typer.echo(json.dumps(output_data, indent=2, default=str))
    else:
        # Text output - summary
        final_state = result["final_state"]
        typer.echo("")
        typer.echo(f"Run ID: {result['run_id']}")
        typer.echo(f"Completed {ticks} ticks")

        # Show key metrics
        if final_state.resources and final_state.resources.metrics:
            typer.echo("\nFinal resources:")
            for key, stat in final_state.resources.metrics.items():
                label = stat.label or key
                typer.echo(f"  {label}: {stat.value} {stat.unit or ''}")


def main() -> None:
    """CLI entry point."""
    app()


if __name__ == "__main__":
    main()
