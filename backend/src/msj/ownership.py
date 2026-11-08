"""Process identity helpers used to validate durable work reservations."""

import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except OSError:
        return "unknown"


def _process_start(pid: int) -> str | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        fields = stat[stat.rfind(")") + 2 :].split()
        return fields[19]
    except (OSError, IndexError):
        return None


@dataclass(frozen=True)
class OwnerIdentity:
    host: str
    pid: int
    boot_id: str
    started_at: str

    @classmethod
    def current(cls) -> OwnerIdentity:
        pid = os.getpid()
        return cls(socket.gethostname(), pid, _boot_id(), _process_start(pid) or "unknown")

    @staticmethod
    def alive(row: Any) -> bool:
        host, pid, boot = row["owner_host"], row["owner_pid"], row["owner_boot_id"]
        if not host or pid is None or not boot:
            return False
        if host != socket.gethostname():
            return True
        if boot != _boot_id():
            return False
        try:
            process_id = int(pid)
            os.kill(process_id, 0)
        except (ProcessLookupError, ValueError):
            return False
        except PermissionError:
            return True
        started_at = row["owner_started_at"]
        process_started = _process_start(process_id)
        return process_started is None or started_at == process_started
