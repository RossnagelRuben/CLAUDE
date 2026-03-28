"""
Métricas del host para el panel /admin/server.
Historial en JSONL (data/server_metrics_history.jsonl), muestreo al consultar la API.
"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
HISTORY_FILE = DATA_DIR / "server_metrics_history.jsonl"

# Evitar escribir más de una muestra cada N segundos (varias pestañas / refrescos)
_MIN_SAMPLE_INTERVAL = 50.0
_last_append_lock = threading.Lock()
_last_append_ts = 0.0


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _try_psutil():
    try:
        import psutil  # type: ignore

        return psutil
    except ImportError:
        return None


def _cpu_mem_fallback() -> tuple[float, float]:
    """CPU aproximada vía loadavg y RAM vía /proc/meminfo (sin psutil)."""
    cpu_n = max(1, os.cpu_count() or 1)
    try:
        load1 = float(open("/proc/loadavg").read().split()[0])
        cpu_pct = min(100.0, (load1 / cpu_n) * 100.0)
    except OSError:
        cpu_pct = 0.0
    try:
        info: dict[str, int] = {}
        for line in open("/proc/meminfo", encoding="utf-8", errors="ignore"):
            m = re.match(r"^(\w+):\s+(\d+)\s+kB", line)
            if m:
                info[m.group(1)] = int(m.group(2))
        total = info.get("MemTotal", 0)
        avail = info.get("MemAvailable") or info.get("MemFree", 0)
        if total > 0:
            ram_pct = max(0.0, min(100.0, ((total - avail) / total) * 100.0))
        else:
            ram_pct = 0.0
    except OSError:
        ram_pct = 0.0
    return cpu_pct, ram_pct


def _disk_fallback(path: str = "/") -> tuple[str, float, float, float]:
    try:
        st = os.statvfs(path)
        total = st.f_frsize * st.f_blocks
        free = st.f_frsize * st.f_bavail
        used = total - free
        pct = (used / total * 100.0) if total > 0 else 0.0
        return "rootfs", used, total, pct
    except OSError:
        return "unknown", 0.0, 0.0, 0.0


def _uptime_seconds() -> float:
    try:
        return float(open("/proc/uptime").read().split()[0])
    except OSError:
        return 0.0


def _thermal_celsius() -> float | None:
    psutil = _try_psutil()
    if psutil:
        try:
            temps = psutil.sensors_temperatures()
            if not temps:
                return None
            vals: list[float] = []
            for _name, entries in temps.items():
                for e in entries:
                    if e.current is not None:
                        vals.append(float(e.current))
            if vals:
                return sum(vals) / len(vals)
        except Exception:
            pass
    # sysfs CPU temp común en Linux
    for pat in (
        "/sys/class/thermal/thermal_zone0/temp",
        "/sys/class/hwmon/hwmon0/temp1_input",
    ):
        try:
            raw = Path(pat).read_text().strip()
            v = float(raw)
            return v / 1000.0 if v > 1000 else v
        except OSError:
            continue
    return None


def _disk_device_for_mount(mount: str) -> str:
    psutil = _try_psutil()
    if psutil:
        try:
            for p in psutil.disk_partitions(all=False):
                if p.mountpoint == mount:
                    return p.device or "—"
        except Exception:
            pass
    try:
        out = subprocess.check_output(["findmnt", "-n", "-o", "SOURCE", mount], text=True, timeout=2)
        return out.strip() or "—"
    except Exception:
        return "—"


def _os_label() -> str:
    try:
        p = Path("/etc/os-release")
        if p.exists():
            data: dict[str, str] = {}
            for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    data[k.strip()] = v.strip().strip('"')
            name = data.get("PRETTY_NAME") or data.get("NAME", "")
            ver = data.get("VERSION_ID", "")
            if name:
                return f"{name}" + (f" ({ver})" if ver else "")
    except OSError:
        pass
    return platform.platform()[:80]


def collect_snapshot() -> dict:
    """Métricas instantáneas (bloquea ~150ms si hay psutil para CPU %)."""
    psutil = _try_psutil()
    if psutil:
        cpu_pct = float(psutil.cpu_percent(interval=0.15))
        mem = psutil.virtual_memory()
        ram_pct = float(mem.percent)
        ram_used = float(mem.used)
        ram_total = float(mem.total)
        disk = psutil.disk_usage("/")
        d_used = float(disk.used)
        d_total = float(disk.total)
        d_pct = float(disk.percent)
        dev = _disk_device_for_mount("/")
        cpu_cores = int(psutil.cpu_count(logical=True) or 1)
        try:
            freq = psutil.cpu_freq()
            mhz = float(freq.current) if freq and freq.current else None
        except Exception:
            mhz = None
    else:
        cpu_pct, ram_pct = _cpu_mem_fallback()
        dev, d_used, d_total, d_pct = _disk_fallback("/")
        ram_used = ram_total = 0.0
        try:
            with open("/proc/meminfo", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        ram_total = float(line.split()[1]) * 1024.0
                    elif line.startswith("MemAvailable:"):
                        av = float(line.split()[1]) * 1024.0
                        ram_used = ram_total - av
                        break
        except OSError:
            pass
        cpu_cores = max(1, os.cpu_count() or 1)
        mhz = None

    therm = _thermal_celsius()
    up = _uptime_seconds()

    return {
        "cpu_percent": round(cpu_pct, 1),
        "cpu_cores": cpu_cores,
        "cpu_mhz": round(mhz, 0) if mhz else None,
        "ram_percent": round(ram_pct, 1),
        "ram_used_bytes": int(ram_used),
        "ram_total_bytes": int(ram_total),
        "disk_device": dev,
        "disk_used_bytes": int(d_used),
        "disk_total_bytes": int(d_total),
        "disk_percent": round(d_pct, 1),
        "thermal_celsius": round(therm, 1) if therm is not None else None,
        "uptime_seconds": int(up),
        "os_label": _os_label(),
        "hostname": platform.node() or "—",
        "ts": time.time(),
    }


def _read_history_lines(max_bytes: int = 4_000_000) -> list[dict]:
    if not HISTORY_FILE.is_file():
        return []
    try:
        raw = HISTORY_FILE.read_bytes()
        if len(raw) > max_bytes:
            raw = raw[-max_bytes:]
        text = raw.decode("utf-8", errors="ignore")
        lines = [ln for ln in text.splitlines() if ln.strip()]
        out: list[dict] = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
        return out
    except OSError:
        return []


def _trim_history_file(keep_since_ts: float) -> None:
    rows = _read_history_lines()
    kept = [r for r in rows if isinstance(r.get("t"), (int, float)) and float(r["t"]) >= keep_since_ts]
    _ensure_data_dir()
    tmp = HISTORY_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in kept[-50000:]:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(HISTORY_FILE)


def append_sample_if_due() -> None:
    """Registra un punto de historial (throttle global)."""
    global _last_append_ts
    psutil = _try_psutil()
    now = time.time()
    with _last_append_lock:
        if now - _last_append_ts < _MIN_SAMPLE_INTERVAL:
            return
        _last_append_ts = now
        if psutil:
            cpu = float(psutil.cpu_percent(interval=0.12))
            mem = float(psutil.virtual_memory().percent)
        else:
            cpu, mem = _cpu_mem_fallback()
        row = {"t": now, "cpu": round(cpu, 2), "ram": round(mem, 2)}
        _ensure_data_dir()
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        # Mantener ~8 días como máximo
        if HISTORY_FILE.stat().st_size > 3 * 1024 * 1024:
            _trim_history_file(now - 8 * 24 * 3600)


def _bucket_series(
    points: list[dict],
    start_ts: float,
    bucket_seconds: float,
) -> tuple[list[str], list[float | None], list[float | None]]:
    """Etiquetas ISO + promedios por bucket."""
    buckets: dict[int, list[tuple[float, float, float]]] = {}
    for r in points:
        t = float(r.get("t", 0))
        if t < start_ts:
            continue
        cpu = r.get("cpu")
        ram = r.get("ram")
        if cpu is None or ram is None:
            continue
        b = int((t - start_ts) / bucket_seconds)
        buckets.setdefault(b, []).append((t, float(cpu), float(ram)))

    if not buckets:
        return [], [], []

    labels: list[str] = []
    cpus: list[float | None] = []
    rams: list[float | None] = []
    for b in sorted(buckets.keys()):
        chunk = buckets[b]
        avg_cpu = sum(x[1] for x in chunk) / len(chunk)
        avg_ram = sum(x[2] for x in chunk) / len(chunk)
        t_mid = start_ts + (b + 0.5) * bucket_seconds
        labels.append(datetime.fromtimestamp(t_mid, tz=timezone.utc).strftime("%m-%d %H:%M"))
        cpus.append(round(avg_cpu, 1))
        rams.append(round(avg_ram, 1))
    return labels, cpus, rams


def build_history_payload(range_key: str) -> dict:
    """range_key: 24h | 72h | 7d"""
    now = time.time()
    ranges = {"24h": 24, "72h": 72, "7d": 24 * 7}
    hours = ranges.get((range_key or "24h").lower(), 24)
    start = now - hours * 3600
    points = [p for p in _read_history_lines() if isinstance(p.get("t"), (int, float)) and float(p["t"]) >= start]

    if hours <= 24:
        bucket = 300.0  # 5 min
    elif hours <= 72:
        bucket = 900.0  # 15 min
    else:
        bucket = 3600.0  # 1 h

    labels, cpu_s, ram_s = _bucket_series(points, start, bucket)
    return {
        "range": f"{hours}h",
        "labels": labels,
        "cpu": cpu_s,
        "ram": ram_s,
        "points_raw": len(points),
    }


def get_dashboard_payload(range_key: str = "24h") -> dict:
    snap = collect_snapshot()
    hist = build_history_payload(range_key)
    return {
        "ok": True,
        "snapshot": snap,
        "history": hist,
        "server_time_utc": datetime.now(timezone.utc).isoformat(),
    }
