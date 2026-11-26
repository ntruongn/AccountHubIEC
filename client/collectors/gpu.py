"""Fast GPU collector (nvidia-smi) & medium Xid error collector (dmesg/journalctl)."""
from __future__ import annotations

from ..utils import run_cmd


def _num(val: str) -> float | None:
    try:
        return float(val)
    except ValueError:
        return None


def collect_gpu() -> tuple[list[dict], dict[int, int]]:
    """Return (gpus_list, gpu_mem_mb_by_pid)."""
    gpus: list[dict] = []
    by_pid: dict[int, int] = {}

    q = run_cmd(
        [
            "nvidia-smi",
            "--query-gpu=index,name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw,power.limit",
            "--format=csv,noheader,nounits",
        ],
        timeout=8,
    )
    for row in q.splitlines():
        fields = [x.strip() for x in row.split(",")]
        if len(fields) == 8 and fields[0].isdigit():
            gpus.append(
                {
                    "index": int(fields[0]),
                    "name": fields[1],
                    "temp_c": _num(fields[2]),
                    "util_pct": _num(fields[3]),
                    "mem_used_mb": _num(fields[4]),
                    "mem_total_mb": _num(fields[5]),
                    "power_w": _num(fields[6]),
                    "power_limit_w": _num(fields[7]),
                }
            )

    p = run_cmd(
        ["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
        timeout=8,
    )
    for row in p.splitlines():
        fields = [x.strip() for x in row.split(",")]
        if len(fields) == 2 and fields[0].isdigit() and fields[1].isdigit():
            by_pid[int(fields[0])] = int(fields[1])

    return gpus, by_pid


def collect_gpu_xid_errors() -> list[str]:
    """Recent NVRM Xid errors from dmesg/journalctl (requires kernel log permissions)."""
    out = run_cmd(["dmesg", "--ctime"], timeout=8) or run_cmd(
        ["journalctl", "-k", "-n", "2000", "--no-pager"], timeout=10
    )
    lines = [line for line in out.splitlines() if "NVRM: Xid" in line or " Xid" in line]
    return lines[-5:]

