"""Narrow elevated service entry point; the Qt UI stays unelevated.

    A local session owner is trusted (as in the CLI). No network-facing IPC,
    arbitrary shell commands or target-disk operations are accepted here.
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
from pathlib import Path
import shlex
import signal
import re
import subprocess
import sys
import threading
import time

from .config import RecoveryError, Session, atomic_json
from .locking import ServiceLock
from .network import HostNetwork, command, powershell
from .server import serve


def app_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "spark_recovery.desktop"]


def prepare_shared_state(root: Path):
    """Keep elevated helper output readable by the unelevated session owner.

    Windows mkdir(mode=0700) may grant OWNER RIGHTS rather than a named user.
    A helper elevated as another administrator then creates files the GUI cannot
    read. Add only this user's explicit, inheritable SID; never Everyone/Users.
    icacls changes the DACL alone, without requiring SACL/security privileges.
    """
    if sys.platform != "win32":
        return
    sid = powershell("[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value").strip()
    if not re.fullmatch(r"S-1-(?:[0-9]+-)*[0-9]+", sid):
        raise RecoveryError("Cannot identify the Windows session owner; helper was not launched")
    command(["icacls.exe", str(root.resolve()), "/grant", f"*{sid}:(OI)(CI)F"])


def read_service_status(root: Path) -> dict | None:
    try:
        value = json.loads((root / "desktop-service.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except PermissionError as error:
        raise RecoveryError("Windows denied access to the helper status. Recovery readiness is unknown; "
                            "do not reboot the target. Repair this session's owner permissions before retrying.") from error
    except (OSError, ValueError) as error:
        raise RecoveryError(f"Cannot read the recovery helper status: {error}. Do not reboot the target.") from error
    if not isinstance(value, dict):
        raise RecoveryError("Invalid recovery helper status; do not reboot the target")
    return value


def launch(root: Path, operation="serve", *, stop_ics=False):
    if operation not in ("serve", "restore"):
        raise RecoveryError("Unsupported elevated desktop operation")
    prepare_shared_state(root)
    for name in ("desktop-stop", "desktop-service.json"):
        (root / name).unlink(missing_ok=True)
    args = app_command() + ["--desktop-service", str(root.resolve()), "--operation", operation]
    if stop_ics:
        args.append("--stop-ics")
    if sys.platform == "win32":
        execute = ctypes.windll.shell32.ShellExecuteW
        execute.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                            ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]
        execute.restype = ctypes.c_void_p
        outcome = execute(None, "runas", args[0], subprocess.list2cmdline(args[1:]), None, 0)
        if not outcome or outcome <= 32:
            raise RecoveryError("Administrator authorization was canceled or could not start")
        return None
    if sys.platform == "darwin":
        command = shlex.join(args)
        # JSON's quoting is also a valid AppleScript string literal for these paths.
        script = "do shell script " + json.dumps(command, ensure_ascii=False) + " with administrator privileges"
        return subprocess.Popen(["/usr/bin/osascript", "-e", script], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    return subprocess.Popen(["pkexec", *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class ServiceStatus(logging.Handler):
    def __init__(self, root: Path, operation: str):
        super().__init__()
        self.root = root
        self.mutex = threading.Lock()
        self.data = {"phase": "starting", "operation": operation, "pid": os.getpid(), "heartbeat": time.time(),
                     "message": "Verifying image and preparing host services"}
        self.publish()

    def publish(self):
        with self.mutex:
            self.data["heartbeat"] = time.time()
            atomic_json(self.root / "desktop-service.json", self.data)

    def set(self, phase, message):
        with self.mutex:
            self.data.update(phase=phase, message=message)
            self.data["heartbeat"] = time.time()
            atomic_json(self.root / "desktop-service.json", self.data)

    def emit(self, record):
        message = record.getMessage()
        if message.startswith("Serving ONLY"):
            self.set("ready", "Ethernet recovery services are ready; installation never starts without erase approval")
        elif message.startswith("Recovery stage: "):
            self.set("starting", message.removeprefix("Recovery stage: "))


def run_service(root: Path, operation="serve", *, stop_ics=False) -> int:
    root = root.expanduser().resolve()
    session = Session.load(root)
    handler = ServiceStatus(root, operation)
    logger = logging.getLogger()
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    log = logging.FileHandler(root / "desktop-service.log", encoding="utf-8")
    log.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(log)
    ending = threading.Event()

    def watch():
        while not ending.wait(1):
            handler.publish()
            if (root / "desktop-stop").exists():
                # Trigger the existing graceful SIGINT path in the main thread.
                signal.raise_signal(signal.SIGINT)
                return

    watcher = threading.Thread(target=watch, daemon=True)
    try:
        with ServiceLock(root):
            if operation == "serve":
                watcher.start()
                http_handler = None
                if (root / "diagnostic-agent.json").exists():
                    from functools import partial
                    from .diagnostic_agent import DiagnosticHTTPHandler
                    http_handler = partial(DiagnosticHTTPHandler, root=root, session=session)
                serve(root, session, manage=True, stop_ics=stop_ics, http_handler=http_handler)
            elif operation == "restore":
                HostNetwork(root, session).restore()
            else:
                raise RecoveryError("Unsupported desktop service operation")
        handler.set("stopped", "Services stopped; managed host networking restored")
        return 0
    except KeyboardInterrupt:
        handler.set("stopped", "Services stopped; any managed networking was restored")
        return 0
    except Exception as error:
        logging.exception("Desktop helper stopped")
        handler.set("error", str(error))
        return 2
    finally:
        ending.set()
        logger.removeHandler(handler)
        logger.removeHandler(log)
        logger.setLevel(previous_level)
        log.close()
