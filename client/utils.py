"""Shared subprocess & timing utilities for client collectors."""
from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone

_HAS_NICE = shutil.which("nice") is not None
_HAS_IONICE = shutil.which("ionice") is not None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_cmd(cmd: list[str], timeout: int = 10, low_priority: bool = False) -> str:
    """Execute a command safely and return stripped stdout (empty string on failure)."""
    full_cmd = list(cmd)
    if low_priority:
        if _HAS_NICE:
            full_cmd = ["nice", "-n", "19"] + full_cmd
        if _HAS_IONICE:
            full_cmd = ["ionice", "-c", "3"] + full_cmd
    try:
        out = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""

