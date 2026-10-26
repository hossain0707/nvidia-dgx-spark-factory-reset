"""Local-only, evidence-labelled timing. Never infers disk success from a transfer."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import time
import uuid

from .config import RecoveryError, atomic_json


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def duration(seconds: float) -> str:
    value = max(0, int(seconds))
    hours, remainder = divmod(value, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


class RecoveryTiming:
    """One immutable sequence of observations with monotonic elapsed intervals.

    Restarting the GUI cannot reconstruct a lost monotonic clock: restored active
    runs are explicitly interrupted instead of inventing a benchmark duration.
    """

    def __init__(self, path: Path, clock=None):
        self.path = path
        self.clock = clock or time.monotonic
        self.origin = self.clock()
        self.data = {"schema": 1, "run_id": uuid.uuid4().hex, "started_at": utc_now(),
                     "outcome": "in_progress", "events": [], "measurement": "host_monotonic",
                     "notes": "Console start/completion are operator observations, not remote certification."}

    @classmethod
    def load(cls, path: Path):
        result = cls(path)
        result.data = json.loads(path.read_text(encoding="utf-8"))
        if result.data.get("schema") != 1:
            raise RecoveryError("Unsupported timing report")
        if result.data["outcome"] == "in_progress":
            result.data["outcome"] = "interrupted"
            result.data["notes"] += " GUI restarted; continuous timing is unavailable."
            atomic_json(path, result.data)
        return result

    def mark(self, name: str, *, evidence="host_observed"):
        if self.data["outcome"] != "in_progress":
            raise RecoveryError("Timing run is closed; start a new session for a new measurement")
        names = {item["name"] for item in self.data["events"]}
        prerequisites = {"preparation_complete": "preparation_started", "diagnostic_verified": "services_ready",
                         "install_started": "diagnostic_verified", "first_boot_confirmed": "install_started"}
        if name in names:
            raise RecoveryError("This timing event has already been recorded")
        if name in prerequisites and prerequisites[name] not in names:
            raise RecoveryError("Timing event is out of sequence: " + name)
        self.data["events"].append({"name": name, "at": utc_now(),
                                    "elapsed_seconds": round(self.clock() - self.origin, 3), "evidence": evidence})
        if name == "first_boot_confirmed":
            self.data["outcome"] = "operator_confirmed_success"
        atomic_json(self.path, self.data)

    def finish(self, outcome: str):
        if outcome not in ("canceled", "failed", "interrupted"):
            raise RecoveryError("Invalid incomplete outcome")
        if self.data["outcome"] == "in_progress":
            self.data["outcome"] = outcome
            self.data["ended_at"] = utc_now()
            self.data["elapsed_seconds_at_end"] = round(self.clock() - self.origin, 3)
            atomic_json(self.path, self.data)

    def report(self) -> dict:
        result = json.loads(json.dumps(self.data))
        events = {item["name"]: item["elapsed_seconds"] for item in result["events"]}
        pairs = {"preparation_seconds": ("preparation_started", "preparation_complete"),
                 "recovery_session_seconds": ("services_ready", "first_boot_confirmed"),
                 "operator_observed_install_seconds": ("install_started", "first_boot_confirmed")}
        result["durations"] = {name: round(events[end] - events[start], 3)
                               for name, (start, end) in pairs.items() if start in events and end in events}
        # Deliberately no MAC, IP, account, disk serial, password or local path.
        return result
