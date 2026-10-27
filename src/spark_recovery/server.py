"""Read-only allowlisted HTTP plus TFTP/DHCP, with graceful host restoration."""
from __future__ import annotations

from contextlib import ExitStack
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import logging
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
from urllib.parse import urlsplit

from .config import RecoveryError, Session
from .dhcp import DHCPServer
from .network import HostNetwork, assigned, interface_has_address, require_admin
from .safety import check_active_menu, image_manifest
from .tftp import TFTPServer


class HTTPHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, files: dict[str, Path], allowed: set[str], **kwargs):
        self.files, self.allowed = files, allowed
        super().__init__(*args, **kwargs)

    def respond(self, head: bool = False):
        if self.client_address[0] not in self.allowed:
            self.send_error(403)
            return
        path = self.files.get(urlsplit(self.path).path)
        if path is None or not path.is_file():
            self.send_error(404)
            return
        try:
            with path.open("rb") as source:
                self.send_response(200)
                self.send_header("Content-Length", str(path.stat().st_size))
                self.send_header("Content-Type", "application/octet-stream")
                self.end_headers()
                if not head:
                    while data := source.read(1024 * 1024):
                        self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            logging.warning("HTTP client disconnected: %s", self.client_address[0])

    def do_GET(self):
        self.respond()

    def do_HEAD(self):
        self.respond(head=True)

    def log_message(self, template, *args):
        logging.info("HTTP %s %s", self.client_address[0], template % args)


def serve(root: Path, session: Session, manage: bool, stop_ics: bool, *, http_handler=None):
    require_admin()
    image = image_manifest(root, session, full=True)
    check_active_menu(root, session, image)
    if (root / "network.json").exists():
        raise RecoveryError("Network journal exists. Run restore first; do not start competing servers")
    network = HostNetwork(root, session)
    stop = threading.Event()
    with ExitStack() as resources:
        if manage:
            network.apply(stop_ics=stop_ics)
            resources.callback(network.restore)
        if not interface_has_address(session):
            raise RecoveryError("Server IP not assigned; use --manage-network or configure the Ethernet IP first")
        # Binding these ports is also a fail-closed check for existing services.
        dhcp = DHCPServer(session)
        resources.callback(dhcp.close)
        tftp = TFTPServer(root / "tftp", session.server, {session.client, session.server})
        resources.callback(tftp.close)
        handler = partial(http_handler or HTTPHandler, files={"/recovery.iso": Path(image["iso_path"])},
                          allowed={session.client, session.server})
        http = ThreadingHTTPServer((session.server, session.http_port), handler)
        http.daemon_threads = True
        resources.callback(http.server_close)
        old_signals = {}
        def request_stop(signum, frame):
            stop.set()
        for sig in (signal.SIGINT, signal.SIGTERM):
            old_signals[sig] = signal.signal(sig, request_stop)
        resources.callback(lambda: [signal.signal(sig, previous) for sig, previous in old_signals.items()])
        if sys.platform == "win32":
            import ctypes
            if not ctypes.windll.kernel32.SetThreadExecutionState(0x80000001):
                raise RecoveryError("Windows could not inhibit idle sleep")
            resources.callback(lambda: ctypes.windll.kernel32.SetThreadExecutionState(0x80000000))
        elif sys.platform == "darwin":
            awake = subprocess.Popen(["caffeinate", "-i"])
            resources.callback(awake.terminate)
        failures = []
        def worker(function):
            try:
                function()
            except Exception as error:
                failures.append(error)
                stop.set()
        threads = [threading.Thread(target=worker, args=(operation,), daemon=True)
                   for operation in (lambda: dhcp.serve(stop), lambda: tftp.serve(stop), http.serve_forever)]
        for thread in threads:
            thread.start()
        logging.info("Serving ONLY target %s at %s. Keep this terminal open. Ctrl+C stops and restores host networking.",
                     session.target_mac, session.client)
        logging.info("HTTP %s:%s, TFTP 69, DHCP 67; %s", session.server, session.http_port,
                     "only non-erasing diagnostics may auto-start" if image.get("diagnostic_agent") else "STOP is the menu default")
        try:
            while not stop.wait(0.5):
                pass
        finally:
            stop.set()
            http.shutdown()
            tftp.stopping.set()
            for thread in threads:
                thread.join(timeout=5)
        if failures:
            raise RecoveryError(f"Recovery service failed: {failures[0]}")


def doctor(root: Path, session: Session) -> dict:
    checks = {"session": session.fingerprint, "platform": sys.platform,
              "server_ip_assigned": assigned(session.server), "network_journal": (root / "network.json").exists(),
              "ports": {}, "image_ready": False}
    for label, port, kind, host in (("dhcp", 67, socket.SOCK_DGRAM, "0.0.0.0"),  # noqa: S104 -- bind-only preflight
                                     ("tftp", 69, socket.SOCK_DGRAM, session.server),
                                     ("http", session.http_port, socket.SOCK_STREAM, session.server)):
        with socket.socket(socket.AF_INET, kind) as probe:
            try:
                probe.bind((host, port))
                checks["ports"][label] = "available"
            except OSError as error:
                checks["ports"][label] = str(error)
    try:
        image = image_manifest(root, session)
        check_active_menu(root, session, image)
        checks["image_ready"] = True
    except RecoveryError as error:
        checks["image_error"] = str(error)
    return checks
