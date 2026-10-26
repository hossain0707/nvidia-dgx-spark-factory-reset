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
import subprocess
import sys
import threading
import time

from .config import RecoveryError, Session, atomic_json
from .locking import ServiceLock
from .network import HostNetwork
from .server import serve


def app_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "spark_recovery.desktop"]


def launch(root: Path, operation="serve", *, stop_ics=False):
    if operation not in ("serve", "restore"):
        raise RecoveryError("Unsupported elevated desktop operation")
    args = app_command() + ["--desktop-service", str(root.resolve()), "--operation", operation]
    if stop_ics:
        args.append("--stop-ics")
    if sys.platform == "win32":
        outcome = ctypes.windll.shell32.ShellExecuteW(None, "runas", args[0],
                                                     subprocess.list2cmdline(args[1:]), None, 0)
        if outcome <= 32:
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
            self.set("ready", "Ethernet recovery services are ready; STOP is the boot-menu default")


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
                serve(root, session, manage=True, stop_ics=stop_ics)
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
