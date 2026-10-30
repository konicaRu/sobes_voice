from app.resources import ProcInfo, summarize


def proc(pid, name, ram=0, cpu=0.0, vram=0):
    return ProcInfo(pid=pid, name=name, ram_mb=ram, cpu_percent=cpu, vram_mb=vram)


def report(procs, vram_used: int | None = 5000, vram_total: int | None = 12288, ram_used=8000, ram_total=32000, cpu=10.0):
    return summarize(procs, cpu_percent=cpu, ram_used_mb=ram_used, ram_total_mb=ram_total,
                     vram_used_mb=vram_used, vram_total_mb=vram_total)


def test_processes_of_one_program_are_summed():
    result = report([proc(1, "chrome", ram=300), proc(2, "chrome", ram=200), proc(3, "Telegram", ram=100)])
    top = result["ram"]["top"]
    assert top[0] == {"name": "chrome", "value": 500, "count": 2, "role": "closable"}
    assert top[1]["name"] == "Telegram"


def test_top_is_sorted_descending_and_limited_to_five():
    procs = [proc(i, f"app{i}", vram=i * 10) for i in range(1, 9)]
    top = report(procs)["vram"]["top"]
    assert [item["name"] for item in top] == ["app8", "app7", "app6", "app5", "app4"]


def test_zero_consumers_are_dropped():
    assert report([proc(1, "idle", ram=0)])["ram"]["top"] == []


def test_roles_protect_system_and_our_own_programs():
    procs = [proc(1, "dwm", vram=600), proc(2, "python", vram=100), proc(3, "LM Studio", vram=9000),
             proc(4, "chrome", vram=300)]
    roles = {item["name"]: item["role"] for item in report(procs)["vram"]["top"]}
    assert roles == {"LM Studio": "needed", "python": "needed", "dwm": "system", "chrome": "closable"}


def test_low_vram_advice_names_closable_programs_only():
    procs = [proc(1, "LM Studio", vram=9000), proc(2, "dwm", vram=600), proc(3, "chrome", vram=280),
             proc(4, "Telegram", vram=110)]
    advice = report(procs, vram_used=11800)["advice"]
    assert len(advice) == 1
    assert "chrome" in advice[0] and "Telegram" in advice[0]
    assert "LM Studio" not in advice[0] and "dwm" not in advice[0]


def test_enough_memory_gives_no_close_advice():
    advice = report([proc(1, "chrome", ram=500, vram=300)])["advice"]
    assert advice == ["Ресурсов достаточно: ничего закрывать не нужно."]


def test_high_cpu_and_low_ram_are_reported_too():
    procs = [proc(1, "chrome", ram=900, cpu=60.0), proc(2, "Telegram", ram=800, cpu=30.0)]
    advice = report(procs, cpu=95.0, ram_used=31000)["advice"]
    assert len(advice) == 2
    assert any("процессор" in line.lower() for line in advice)
    assert any("оперативн" in line.lower() for line in advice)


def test_unknown_vram_total_is_reported_as_none_without_vram_advice():
    result = report([proc(1, "chrome", vram=300)], vram_used=None, vram_total=None)
    assert result["vram"]["total_mb"] is None
    assert result["advice"] == ["Ресурсов достаточно: ничего закрывать не нужно."]


def test_idle_process_is_not_a_consumer_and_memory_compression_is_system():
    result = report([proc(1, "System Idle Process", cpu=94.0), proc(2, "MemCompression", ram=800)])
    assert result["cpu"]["top"] == []
    assert result["ram"]["top"][0]["role"] == "system"
