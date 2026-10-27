"""Authenticated device inspection. No password persistence or automatic key trust."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import socket
import threading

from .config import RecoveryError
from .discovery import Adapter, Peer, ip


def fingerprint(key) -> str:
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


def endpoint_socket(peer: Peer, adapter: Adapter):
    family = socket.AF_INET6 if ip(peer.address).version == 6 else socket.AF_INET
    connection = socket.socket(family, socket.SOCK_STREAM)
    connection.settimeout(10)
    try:
        if family == socket.AF_INET:
            import ipaddress
            local = next(str(ipaddress.ip_interface(value).ip) for value in adapter.addresses
                         if ":" not in value and ip(peer.address) in ipaddress.ip_interface(value).network)
            connection.bind((local, 0))
        connection.connect((peer.address, 22))
        return connection
    except BaseException:
        connection.close()
        raise


def key_status(peer: Peer, adapter: Adapter, known_hosts: Path) -> dict:
    import paramiko
    connection = endpoint_socket(peer, adapter)
    transport = paramiko.Transport(connection)
    try:
        transport.start_client(timeout=10)
        key = transport.get_remote_server_key()
        keys = paramiko.HostKeys()
        for path in (Path.home() / ".ssh/known_hosts", known_hosts):
            if path.is_file():
                current = paramiko.HostKeys(str(path))
                for host, entries in current.items():
                    for kind, value in entries.items():
                        keys.add(host, kind, value)
        previous = keys.lookup(peer.address)
        state = "trusted" if previous and key.get_name() in previous and previous[key.get_name()] == key else "unknown"
        if previous and key.get_name() in previous and state != "trusted":
            state = "changed"
        return {"state": state, "fingerprint": fingerprint(key), "kind": key.get_name(), "key": key.get_base64()}
    finally:
        transport.close()
        connection.close()


def trust_key(peer: Peer, observed: dict, known_hosts: Path, *, replace=False):
    import paramiko
    if observed["state"] == "changed" and not replace:
        raise RecoveryError("Existing SSH host key changed. Verify and repair trust outside this recovery flow")
    key = paramiko.PKey.from_type_string(observed["kind"], base64.b64decode(observed["key"], validate=True))
    if fingerprint(key) != observed["fingerprint"]:
        raise RecoveryError("Host-key evidence is inconsistent")
    known_hosts.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    keys = paramiko.HostKeys(str(known_hosts)) if known_hosts.exists() else paramiko.HostKeys()
    keys.add(peer.address, key.get_name(), key)
    temporary = known_hosts.with_suffix(".pending")
    keys.save(str(temporary))
    os.chmod(temporary, 0o600)
    temporary.replace(known_hosts)


def configured_user(address: str) -> str:
    import paramiko
    path = Path.home() / ".ssh/config"
    if not path.is_file():
        return ""
    config = paramiko.SSHConfig.from_path(str(path))
    # Never execute ProxyCommand or infer credentials from environment variables.
    value = config.lookup(address).get("user", "")
    return value if re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}", value) else ""


class DeviceConnection:
    def __init__(self, peer: Peer, adapter: Adapter, username: str, known_hosts: Path, password: str | None = None):
        import paramiko
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}", username):
            raise RecoveryError("Enter the account name on the connected Spark")
        self.peer = peer
        self.lock = threading.Lock()
        self.client = paramiko.SSHClient()
        for path in (Path.home() / ".ssh/known_hosts", known_hosts):
            if path.is_file():
                # Explicitly verified app-local replacements supersede this app's
                # view of old keys without deleting/editing global OpenSSH trust.
                keys = paramiko.HostKeys(str(path))
                for host, entries in keys.items():
                    for kind, key in entries.items():
                        self.client.get_host_keys().add(host, kind, key)
        self.client.set_missing_host_key_policy(paramiko.RejectPolicy())
        connection = endpoint_socket(peer, adapter)
        try:
            self.client.connect(peer.address, username=username, password=password or None, sock=connection,
                                timeout=10, auth_timeout=15, banner_timeout=10,
                                allow_agent=not bool(password), look_for_keys=not bool(password))
            self.client.get_transport().set_keepalive(15)
        except BaseException:
            self.client.close()
            connection.close()
            raise

    def run_python(self, source: str, args=None, *, sudo=False, sudo_password=None, reboot=False):
        encoded = base64.b64encode(source.encode()).decode()
        command = ["python3", "-c", f"exec(__import__('base64').b64decode('{encoded}'))", *(args or [])]
        if sudo:
            command = (["sudo", "-S", "-p", ""] if sudo_password is not None else ["sudo", "-n"]) + command
        with self.lock:
            stdin, stdout, stderr = self.client.exec_command(shlex.join(command), timeout=35)
            try:
                if sudo_password is not None:
                    if "\n" in sudo_password or "\r" in sudo_password:
                        raise RecoveryError("Password contains an unsupported newline")
                    stdin.write(sudo_password + "\n")
                    stdin.flush()
                stdin.channel.shutdown_write()
                output = stdout.read(1024 * 1024).decode("utf-8", errors="replace")
                errors = stderr.read(8192).decode("utf-8", errors="replace")
                code = stdout.channel.recv_exit_status()
                # Reboot confirmation must be printed before SSH disconnect; EOF alone is not success.
                if reboot and "Rebooting." in output and code in (0, -1):
                    return output
                if code:
                    raise RecoveryError("Target command refused. Check account/sudo access. " + errors.strip())
                return output
            finally:
                stdin.close()
                stdout.close()
                stderr.close()

    def inventory(self):
        source = Path(__file__).with_name("target.py").read_text(encoding="utf-8")
        return json.loads(self.run_python(source, ["--json"]))

    def boot(self, mac: str, *, sudo_password=None):
        source = Path(__file__).with_name("target.py").read_text(encoding="utf-8")
        return self.run_python(source, ["--mac", mac, "--apply", "--reboot"], sudo=True,
                               sudo_password=sudo_password, reboot=True)

    def close(self):
        self.client.close()
