"""Проверка ресурсов компьютера: кто занимает процессор, оперативную и видеопамять и что можно закрыть."""
import json
import subprocess
import time
from dataclasses import dataclass

TOP_N = 5
MIN_VALUE_MB = 20  # мельче этого в списки не берём
LOW_VRAM_FREE_MB = 1500
LOW_RAM_FREE_MB = 2000
HIGH_CPU_PERCENT = 80
ADVICE_NAMES = 3

# нужны самому тренажёру: закрывать нельзя
NEEDED = {"python", "pythonw", "lm studio", "lms", "llama-server"}
# части Windows и драйверов: закрывать бессмысленно или опасно
SYSTEM = {
    "system", "system idle process", "registry", "dwm", "csrss", "winlogon", "wininit", "services", "lsass", "smss",
    "explorer", "svchost", "msmpeng", "searchhost", "searchapp", "startmenuexperiencehost", "textinputhost",
    "shellexperiencehost", "applicationframehost", "sihost", "taskhostw", "ctfmon", "fontdrvhost", "memory compression",
    "memcompression",
}
IDLE = "system idle process"  # это простой процессора, а не потребитель

NOTHING_TO_CLOSE = "Ресурсов достаточно: ничего закрывать не нужно."
POWERSHELL_GPU = (
    "Get-Counter '\\GPU Process Memory(*)\\Dedicated Usage' | ForEach-Object { $_.CounterSamples } | "
    "Where-Object { $_.CookedValue -gt 0 } | ForEach-Object { "
    "[pscustomobject]@{ pid = [int]($_.InstanceName -replace '^pid_(\\d+)_.*', '$1'); mb = [int]($_.CookedValue / 1MB) } } | "
    "ConvertTo-Json -Compress"
)


@dataclass
class ProcInfo:
    pid: int
    name: str
    ram_mb: int = 0
    cpu_percent: float = 0.0
    vram_mb: int = 0


def role_of(name: str) -> str:
    key = name.lower().removesuffix(".exe")
    if key in NEEDED:
        return "needed"
    if key in SYSTEM:
        return "system"
    return "closable"


def _top(procs: list[ProcInfo], field: str) -> list[dict]:
    groups: dict[str, dict] = {}
    for p in procs:
        value = getattr(p, field)
        if value <= 0 or p.name.lower() == IDLE:
            continue
        group = groups.setdefault(p.name.lower(), {"name": p.name, "value": 0, "count": 0, "role": role_of(p.name)})
        group["value"] += value
        group["count"] += 1
    items = sorted(groups.values(), key=lambda g: g["value"], reverse=True)
    return items[:TOP_N]


def _names_to_close(top: list[dict]) -> str:
    return ", ".join(item["name"] for item in top if item["role"] == "closable")


def summarize(
    procs: list[ProcInfo],
    *,
    cpu_percent: float,
    ram_used_mb: int,
    ram_total_mb: int,
    vram_used_mb: int | None,
    vram_total_mb: int | None,
) -> dict:
    cpu_top = _top(procs, "cpu_percent")
    cpu_top = [{**item, "value": round(item["value"], 1)} for item in cpu_top if item["value"] >= 0.5]
    ram_top = [item for item in _top(procs, "ram_mb") if item["value"] >= MIN_VALUE_MB]
    vram_top = [item for item in _top(procs, "vram_mb") if item["value"] >= MIN_VALUE_MB]

    advice: list[str] = []
    if vram_used_mb is not None and vram_total_mb is not None and vram_total_mb - vram_used_mb < LOW_VRAM_FREE_MB:
        names = _names_to_close(vram_top[:ADVICE_NAMES + 2])
        advice.append(
            f"Мало свободной видеопамяти: {vram_total_mb - vram_used_mb} МБ из {vram_total_mb}. "
            + (f"Можно закрыть: {names}." if names else "Закрывать из списка нечего, попробуйте перезагрузить модель.")
        )
    if ram_total_mb - ram_used_mb < LOW_RAM_FREE_MB:
        names = _names_to_close(ram_top[:ADVICE_NAMES + 2])
        advice.append(
            f"Мало свободной оперативной памяти: {ram_total_mb - ram_used_mb} МБ из {ram_total_mb}. "
            + (f"Можно закрыть: {names}." if names else "")
        )
    if cpu_percent > HIGH_CPU_PERCENT:
        names = _names_to_close(cpu_top[:ADVICE_NAMES + 2])
        advice.append(f"Процессор загружен на {round(cpu_percent)}%. " + (f"Можно закрыть: {names}." if names else ""))

    return {
        "cpu": {"percent": round(cpu_percent, 1), "top": cpu_top},
        "ram": {"used_mb": ram_used_mb, "total_mb": ram_total_mb, "top": ram_top},
        "vram": {"used_mb": vram_used_mb, "total_mb": vram_total_mb, "top": vram_top},
        "advice": [line.strip() for line in advice] or [NOTHING_TO_CLOSE],
    }


def _run(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def _gpu_by_pid() -> dict[int, int]:
    raw = _run(["powershell", "-NoProfile", "-Command", POWERSHELL_GPU]).strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    rows = data if isinstance(data, list) else [data]
    usage: dict[int, int] = {}
    for row in rows:
        usage[row["pid"]] = usage.get(row["pid"], 0) + row["mb"]
    return usage


def _gpu_totals() -> tuple[int | None, int | None]:
    raw = _run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"])
    try:
        used, total = (int(part) for part in raw.splitlines()[0].split(","))
    except (IndexError, ValueError):
        return None, None
    return used, total


def collect() -> dict:
    """Снимок системы: нужен psutil; видеопамять по процессам берётся из счётчиков Windows (nvidia-smi её не отдаёт)."""
    import psutil

    processes = list(psutil.process_iter(["pid", "name"]))
    for process in processes:
        try:
            process.cpu_percent(None)  # первый вызов только запоминает время, значения даёт второй
        except psutil.Error:
            pass
    psutil.cpu_percent(None)
    time.sleep(0.5)

    cores = psutil.cpu_count() or 1
    gpu = _gpu_by_pid()
    infos: list[ProcInfo] = []
    for process in processes:
        try:
            infos.append(ProcInfo(
                pid=process.pid,
                name=(process.info["name"] or "?").removesuffix(".exe"),
                ram_mb=int(process.memory_info().rss / 1048576),
                cpu_percent=process.cpu_percent(None) / cores,
                vram_mb=gpu.get(process.pid, 0),
            ))
        except psutil.Error:
            continue

    memory = psutil.virtual_memory()
    vram_used, vram_total = _gpu_totals()
    return summarize(
        infos,
        cpu_percent=psutil.cpu_percent(None),
        ram_used_mb=int((memory.total - memory.available) / 1048576),
        ram_total_mb=int(memory.total / 1048576),
        vram_used_mb=vram_used,
        vram_total_mb=vram_total,
    )
