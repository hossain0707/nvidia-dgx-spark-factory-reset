"""Scoped, journaled host network changes. Never edits the target's disks."""
from __future__ import annotations

import base64
import ipaddress
import json
import os
from pathlib import Path
import socket
import re
import subprocess
import sys

from .config import RecoveryError, Session, atomic_json


def command(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    if result.returncode:
        raise RecoveryError(f"Host command failed ({args[0]}): {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def powershell(source: str) -> str:
    source = ("$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; "
              "[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false); "
              "try { " + source + "; exit 0 } catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }")
    encoded = base64.b64encode(source.encode("utf-16-le")).decode()
    return command(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded])


def require_admin() -> None:
    if sys.platform == "win32":
        import ctypes
        elevated = ctypes.windll.shell32.IsUserAnAdmin()
    else:
        elevated = os.geteuid() == 0
    if not elevated:
        raise RecoveryError("Run serve/network restore as Administrator (Windows) or with sudo (macOS/Linux)")


def interface_index(session: Session) -> int:
    if sys.platform == "win32":
        if not session.interface.isdecimal():
            raise RecoveryError("Windows --interface must be the numeric Ethernet interface index")
        return int(session.interface)
    return socket.if_nametoindex(session.interface)


def assigned(address: str) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((address, 0))
            return True
        except OSError:
            return False


def interface_has_address(session: Session) -> bool:
    index = interface_index(session)
    if sys.platform == "win32":
        output = powershell(f"@(Get-NetIPAddress -InterfaceIndex {index} -AddressFamily IPv4 | Select-Object -ExpandProperty IPAddress) | ConvertTo-Json -Compress")
        addresses = json.loads(output) or []
        return session.server in ([addresses] if isinstance(addresses, str) else addresses)
    if sys.platform == "darwin":
        return session.server in re.findall(r"\binet\s+(\d+\.\d+\.\d+\.\d+)", command(["ifconfig", session.interface]))
    data = json.loads(command(["ip", "-json", "address", "show", "dev", session.interface]))
    return any(item.get("local") == session.server for device in data for item in device.get("addr_info", []))


class HostNetwork:
    def __init__(self, root: Path, session: Session):
        self.path = root / "network.json"
        self.session = session

    def apply(self, *, stop_ics: bool = False):
        require_admin()
        config = self.session
        index = interface_index(config)
        if assigned(config.server) and not interface_has_address(config):
            raise RecoveryError("Server IP belongs to another interface; choose a different private subnet")
        if self.path.exists():
            raise RecoveryError("Existing network journal: run restore before managing a new session")
        state = {"session": config.fingerprint, "platform": sys.platform,
                 "added_address": not assigned(config.server), "interface": config.interface,
                 "server": config.server}
        if sys.platform == "win32":
            details = json.loads(powershell(f"""
$a = Get-NetAdapter -InterfaceIndex {index};
if ($a.Status -ne 'Up') {{ throw 'Ethernet interface is not Up' }};
$i = Get-NetIPInterface -InterfaceIndex {index} -AddressFamily IPv4;
@{{dhcp=[string]$i.Dhcp; ics=[string](Get-Service SharedAccess).Status;
addresses=@(Get-NetIPAddress -InterfaceIndex {index} -AddressFamily IPv4 | Select-Object IPAddress,PrefixLength)}} |
ConvertTo-Json -Depth 5 -Compress
"""))
            if details["ics"] == "Running" and not stop_ics:
                raise RecoveryError("ICS is running. Use --stop-ics with --manage-network to replace it temporarily")
            state["windows"] = details
        atomic_json(self.path, state)  # saved BEFORE the first mutation, also used after a crash
        try:
            if sys.platform == "win32":
                powershell(f"""
if ((Get-Service SharedAccess).Status -eq 'Running') {{ Stop-Service SharedAccess }};
Set-NetIPInterface -InterfaceIndex {index} -AddressFamily IPv4 -Dhcp Disabled;
if (-not (Get-NetIPAddress -InterfaceIndex {index} -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object IPAddress -eq '{config.server}')) {{
New-NetIPAddress -InterfaceIndex {index} -IPAddress '{config.server}' -PrefixLength {config.prefix} | Out-Null }}
""")
                python = str(Path(sys.executable).resolve()).replace("'", "''")
                name = "SparkRecovery-" + config.session_id
                powershell(f"""
$alias = (Get-NetAdapter -InterfaceIndex {index}).Name;
New-NetFirewallRule -Name '{name}-HTTP' -DisplayName '{name} HTTP' -Direction Inbound -Action Allow -Protocol TCP -LocalPort {config.http_port} -LocalAddress '{config.server}' -RemoteAddress '{config.client}' -InterfaceAlias $alias -Program '{python}' -Profile Any | Out-Null;
New-NetFirewallRule -Name '{name}-TFTP' -DisplayName '{name} TFTP' -Direction Inbound -Action Allow -Protocol UDP -LocalAddress '{config.server}' -RemoteAddress '{config.client}' -InterfaceAlias $alias -Program '{python}' -Profile Any | Out-Null;
New-NetFirewallRule -Name '{name}-DHCP' -DisplayName '{name} DHCP' -Direction Inbound -Action Allow -Protocol UDP -LocalPort 67 -InterfaceAlias $alias -Program '{python}' -Profile Any | Out-Null
""")
            elif state["added_address"]:
                if sys.platform == "darwin":
                    mask = str(ipaddress.IPv4Network(f"0.0.0.0/{config.prefix}").netmask)
                    command(["ifconfig", config.interface, "inet", config.server, "netmask", mask, "alias"])
                else:
                    command(["ip", "address", "add", f"{config.server}/{config.prefix}", "dev", config.interface])
            if not interface_has_address(config):
                raise RecoveryError("Expected server IP is not available after network configuration")
        except BaseException:
            self.restore()
            raise

    def restore(self):
        if not self.path.exists():
            return
        require_admin()
        config = self.session
        state = json.loads(self.path.read_text(encoding="utf-8"))
        if state["session"] != config.fingerprint or state["platform"] != sys.platform:
            raise RecoveryError("Network journal does not belong to this session/OS; manual review required")
        index = interface_index(config)
        if sys.platform == "win32":
            name = "SparkRecovery-" + config.session_id
            powershell(f"""
Get-NetFirewallRule -ErrorAction Stop | Where-Object {{ $_.Name -in @('{name}-HTTP','{name}-TFTP','{name}-DHCP') }} | Remove-NetFirewallRule;
""")
            if state["added_address"]:
                powershell(f"Get-NetIPAddress -InterfaceIndex {index} -AddressFamily IPv4 | Where-Object IPAddress -eq '{config.server}' | Remove-NetIPAddress -Confirm:$false")
            for original in state["windows"]["addresses"]:
                ip = ipaddress.IPv4Address(original["IPAddress"])
                if ip.is_link_local:
                    continue
                prefix = int(original["PrefixLength"])
                powershell(f"""
if (-not (Get-NetIPAddress -InterfaceIndex {index} -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object IPAddress -eq '{ip}')) {{
New-NetIPAddress -InterfaceIndex {index} -IPAddress '{ip}' -PrefixLength {prefix} | Out-Null }}
""")
            if state["windows"]["dhcp"] == "Enabled":
                powershell(f"Set-NetIPInterface -InterfaceIndex {index} -AddressFamily IPv4 -Dhcp Enabled")
            if state["windows"]["ics"] == "Running":
                powershell("Start-Service SharedAccess")
        elif state["added_address"] and assigned(config.server):
            if sys.platform == "darwin":
                command(["ifconfig", config.interface, "-alias", config.server])
            else:
                command(["ip", "address", "del", f"{config.server}/{config.prefix}", "dev", config.interface])
        self.path.unlink()

