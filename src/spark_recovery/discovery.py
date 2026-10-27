"""Bounded discovery on a selected wired link. No login guesses or host mutations."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
import ipaddress
from itertools import islice
import json
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys

from .config import RecoveryError, Session, mac_address
from .network import command, powershell


def private_v4(address):
    return address.version == 4 and any(address in ipaddress.ip_network(value)
        for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


@dataclass
class Adapter:
    id: str
    name: str
    up: bool
    wired: bool
    addresses: list[str] = field(default_factory=list)
    gateways: list[str] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)
    neighbors: list[dict] = field(default_factory=list)
    ics_running: bool = False


@dataclass(frozen=True)
class Peer:
    address: str
    mac: str
    adapter: str
    banner: str = ""


def rows(value):
    return value if isinstance(value, list) else ([value] if isinstance(value, dict) else [])


def windows_adapters(data: dict) -> list[Adapter]:
    routes = [str(item["DestinationPrefix"]) for item in rows(data.get("routes"))]
    result = []
    for item in rows(data.get("adapters")):
        index = str(item["ifIndex"])
        addresses = [f'{entry["IPAddress"]}/{entry["PrefixLength"]}' for entry in rows(data.get("addresses"))
                     if str(entry["InterfaceIndex"]) == index]
        gateways = [entry["NextHop"] for entry in rows(data.get("routes"))
                    if str(entry["InterfaceIndex"]) == index and entry["DestinationPrefix"] in ("0.0.0.0/0", "::/0")]
        neighbors = [{"address": entry["IPAddress"], "mac": entry.get("LinkLayerAddress", "")}
                     for entry in rows(data.get("neighbors")) if str(entry["InterfaceIndex"]) == index
                     and str(entry.get("State", "")) not in ("Unreachable", "Incomplete", "Permanent", "0", "1", "6")]
        # Windows NDIS physical-medium 9 is native Wi-Fi; ifType 6 is Ethernet.
        wired = int(item.get("InterfaceType") or item.get("ifType") or 0) == 6 and int(item.get("NdisPhysicalMedium") or 0) not in (1, 9)
        result.append(Adapter(index, item["Name"], str(item.get("Status")) == "Up", wired,
                              addresses, gateways, routes, neighbors, data.get("ics") == "Running"))
    return result


def mac_adapters(ports: str, configurations: dict[str, str], route_text: str, arp_text: str, ndp_text: str):
    result = []
    label = ""
    routes = []
    gateways = {}
    for line in route_text.splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[0] == "default":
            gateways.setdefault(fields[-2] if fields[-1].isdigit() else fields[-1], []).append(fields[1])
        elif fields and re.fullmatch(r"\d+(?:\.\d+){0,3}(?:/\d+)?", fields[0]):
            address, _, prefix = fields[0].partition("/")
            octets = address.split(".")
            routes.append(".".join(octets + ["0"] * (4 - len(octets))) + "/" + (prefix or str(8 * len(octets))))
    for line in ports.splitlines():
        if line.startswith("Hardware Port: "):
            label = line.split(": ", 1)[1]
        if not line.startswith("Device: "):
            continue
        name = line.split(": ", 1)[1]
        text = configurations.get(name, "")
        addresses = []
        for address, mask in re.findall(r"\binet\s+(\d+\.\d+\.\d+\.\d+)\s+netmask\s+(0x[0-9a-fA-F]+)", text):
            prefix = bin(int(mask, 16)).count("1")
            addresses.append(f"{address}/{prefix}")
        addresses += [f"{address}/{prefix}" for address, prefix in
                      re.findall(r"\binet6\s+([0-9a-fA-F:%\w.-]+)\s+prefixlen\s+(\d+)", text)]
        neighbors = []
        for record in arp_text.splitlines():
            match = re.search(r"\(([^)]+)\) at ([0-9a-fA-F:]+) on (\S+)", record)
            if match and match[3] == name:
                # macOS omits leading zeroes in ARP octets.
                mac = ":".join(octet.zfill(2) for octet in match[2].split(":"))
                neighbors.append({"address": match[1], "mac": mac})
        for record in ndp_text.splitlines():
            fields = record.split()
            if len(fields) >= 3 and fields[2] == name and ":" in fields[0]:
                neighbors.append({"address": fields[0].split("%")[0] + "%" + name, "mac": fields[1]})
        wired = not any(word in label.lower() for word in ("wi-fi", "wi fi", "airport", "bluetooth", "wireless", "wlan", "802.11"))
        result.append(Adapter(name, label or name, "status: active" in text, wired, addresses,
                              gateways.get(name, []), routes, neighbors))
    return result


def collect_adapters() -> list[Adapter]:
    if sys.platform == "win32":
        output = powershell("""
@{adapters=@(Get-NetAdapter -Physical | Select-Object ifIndex,Name,Status,InterfaceType,NdisPhysicalMedium);
addresses=@(Get-NetIPAddress | Select-Object InterfaceIndex,IPAddress,PrefixLength);
routes=@(Get-NetRoute | Select-Object InterfaceIndex,DestinationPrefix,NextHop);
neighbors=@(Get-NetNeighbor | Select-Object InterfaceIndex,IPAddress,LinkLayerAddress,State);
ics=[string](Get-Service SharedAccess -ErrorAction SilentlyContinue).Status} |
ConvertTo-Json -Depth 5 -Compress
""")
        return windows_adapters(json.loads(output))
    if sys.platform == "darwin":
        ports = command(["/usr/sbin/networksetup", "-listallhardwareports"])
        names = re.findall(r"^Device: ([A-Za-z0-9_.:-]+)$", ports, re.MULTILINE)
        configs = {name: command(["/sbin/ifconfig", name]) for name in names}
        routes = command(["/usr/sbin/netstat", "-rn", "-f", "inet"]) + "\n" + command(["/usr/sbin/netstat", "-rn", "-f", "inet6"])
        arp = command(["/usr/sbin/arp", "-an"])
        ndp = command(["/usr/sbin/ndp", "-an"])
        return mac_adapters(ports, configs, routes, arp, ndp)
    links = json.loads(command(["ip", "-json", "address"]))
    routes = json.loads(command(["ip", "-json", "route", "show", "table", "all"]))
    neighbors = json.loads(command(["ip", "-json", "neighbor"]))
    all_routes = [str(item["dst"]) for item in routes if item.get("dst") not in (None, "default")]
    return [Adapter(item["ifname"], item["ifname"], item.get("operstate") == "UP",
                    item.get("link_type") == "ether" and not (Path("/sys/class/net") / item["ifname"] / "wireless").exists(),
                    [f'{address["local"]}/{address["prefixlen"]}' for address in item.get("addr_info", [])],
                    [entry["gateway"] for entry in routes if entry.get("dev") == item["ifname"]
                     and entry.get("dst") == "default" and entry.get("gateway")], all_routes,
                    [{"address": entry["dst"] + ("%" + item["ifname"] if ":" in entry["dst"] else ""),
                      "mac": entry.get("lladdr", "")} for entry in neighbors if entry.get("dev") == item["ifname"]
                     and entry.get("lladdr")]) for item in links if item["ifname"] != "lo"]


def choose_adapter(adapters: list[Adapter]) -> Adapter:
    choices = [item for item in adapters if item.up and item.wired and not item.gateways]
    if len(choices) != 1:
        raise RecoveryError("Choose the dedicated wired adapter: " + str(len(choices)) +
                            " active non-gateway Ethernet links were found. Wi-Fi is never chosen automatically")
    return choices[0]


def ip(value):
    return ipaddress.ip_address(value.split("%")[0])


def candidates(adapter: Adapter, *, active=False) -> list[str]:
    local = {str(ip(value.split("/")[0])) for value in adapter.addresses}
    result = set()
    networks = []
    for value in adapter.addresses:
        interface = ipaddress.ip_interface(value.split("%")[0] if "%" not in value else
                                           value.split("%")[0] + "/" + value.rsplit("/", 1)[1])
        if interface.version == 4:
            networks.append(interface.network)
    for neighbor in adapter.neighbors:
        try:
            address = ip(neighbor["address"])
        except ValueError:
            continue
        if (not address.is_multicast and not address.is_unspecified and not address.is_loopback
                and str(address) not in local and str(address) not in adapter.gateways):
            if address.version == 6 and address.is_link_local:
                result.add(str(address) + "%" + adapter.id)
            elif address.version == 4 and any(address in network for network in networks):
                result.add(str(address))
    if active:
        if adapter.gateways:
            raise RecoveryError("Broad discovery is disabled on a gateway-bearing/shared link")
        for value in adapter.addresses:
            interface = ipaddress.ip_interface(value)
            if private_v4(interface.ip):
                # A large assigned subnet never expands into an enterprise-wide scan.
                network = interface.network if interface.network.prefixlen >= 24 else ipaddress.ip_network(
                    f"{interface.ip}/24", strict=False)
                result.update(str(address) for address in network.hosts()
                              if str(address) not in local and str(address) not in adapter.gateways)
    return sorted(result)[:256]


def probe_peer(address: str, adapter: Adapter) -> Peer | None:
    version = ip(address).version
    family = socket.AF_INET6 if version == 6 else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.6)
        try:
            if version == 4:
                local = next(str(ipaddress.ip_interface(value).ip) for value in adapter.addresses
                             if ":" not in value and ip(address) in ipaddress.ip_interface(value).network)
                connection.bind((local, 0))
            elif not ip(address).is_link_local:
                return None
            connection.connect((address, 22))
            raw = b""
            while len(raw) < 256 and b"\n" not in raw:
                chunk = connection.recv(256 - len(raw))
                if not chunk:
                    break
                raw += chunk
            banner = raw.decode("ascii", errors="replace").strip()
            if not banner.startswith("SSH-"):
                return None
            mac = next((item.get("mac", "") for item in adapter.neighbors
                        if ip(item["address"]) == ip(address)), "")
            try:
                mac = mac_address(mac)
            except RecoveryError:
                mac = ""
            return Peer(address, mac, adapter.id, banner[:120])
        except (OSError, ValueError, StopIteration):
            return None


def refresh_link_local(adapter: Adapter):
    """A bounded local-link multicast ping refreshes NDP; no authentication or mutation."""
    if sys.platform == "win32":
        args = ["ping", "-6", "-n", "1", "-w", "800", "ff02::1%" + adapter.id]
    elif sys.platform == "darwin":
        args = ["/sbin/ping6", "-c", "1", "-I", adapter.id, "ff02::1%" + adapter.id]
    else:
        args = ["ping", "-6", "-c", "1", "-W", "1", "-I", adapter.id, "ff02::1"]
    try:
        subprocess.run(args, capture_output=True, timeout=3, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass


def discover(adapter: Adapter, *, active=False) -> list[Peer]:
    if not adapter.up or not adapter.wired:
        raise RecoveryError("Discovery requires an active wired Ethernet adapter")
    addresses = candidates(adapter, active=active)
    with ThreadPoolExecutor(max_workers=24) as pool:
        peers = list(pool.map(lambda address: probe_peer(address, adapter), addresses))
    return [peer for peer in peers if peer is not None]


def target_mac(inventory: dict, peer: Peer) -> str:
    matches = set()
    for interface, addresses in inventory.get("addresses", {}).items():
        if any(ip(value.split("/")[0]) == ip(peer.address) for value in addresses):
            value = inventory.get("interfaces", {}).get(interface)
            if value:
                matches.add(mac_address(value))
    if peer.mac:
        if peer.mac not in {mac_address(value) for value in inventory.get("interfaces", {}).values()
                            if value != "00:00:00:00:00:00"}:
            raise RecoveryError("SSH identity does not match the Ethernet neighbor MAC")
        if matches and peer.mac not in matches:
            raise RecoveryError("Neighbor MAC conflicts with the interface owning this address")
        return peer.mac
    if len(matches) != 1:
        raise RecoveryError("Cannot identify the target Ethernet MAC unambiguously")
    return matches.pop()


def network_plan(adapter: Adapter, peer: Peer, mac: str) -> Session:
    for value in adapter.addresses:
        interface = ipaddress.ip_interface(value)
        if private_v4(interface.ip):
            if 16 <= interface.network.prefixlen <= 30 and ip(peer.address).version == 4 and ip(peer.address) in interface.network:
                return Session(str(interface.ip), str(ip(peer.address)), mac, adapter.id, interface.network.prefixlen)
    if ip(peer.address).version == 6:
        occupied_hosts = {str(ip(value.split("/")[0])) for value in adapter.addresses}
        occupied_hosts.update(str(ip(item["address"])) for item in adapter.neighbors)
        occupied_hosts.update(adapter.gateways)
        for value in adapter.addresses:
            interface = ipaddress.ip_interface(value)
            if private_v4(interface.ip) and 16 <= interface.network.prefixlen <= 30:
                for address in islice(interface.network.hosts(), 1024):
                    if str(address) not in occupied_hosts:
                        return Session(str(interface.ip), str(address), mac, adapter.id, interface.network.prefixlen)
    occupied = []
    for value in [*adapter.routes, *adapter.addresses]:
        try:
            network = ipaddress.ip_network(value.split("%")[0], strict=False)
        except ValueError:
            continue
        if network.version == 4 and network.prefixlen:
            occupied.append(network)
    for pool_text in ("172.16.0.0/12", "10.0.0.0/8", "192.168.0.0/16"):
        pool = ipaddress.ip_network(pool_text)
        if any(pool.subnet_of(existing) for existing in occupied):
            continue
        count = 1 << (24 - pool.prefixlen)
        start = secrets.randbelow(count)
        for offset in range(count):
            number = (start + offset) % count
            network = ipaddress.ip_network((int(pool.network_address) + number * 256, 24))
            if not any(network.overlaps(existing) for existing in occupied):
                return Session(str(network[1]), str(network[2]), mac, adapter.id)
    raise RecoveryError("No collision-free private recovery subnet found")


def public_view(adapters: list[Adapter]):
    return [asdict(adapter) for adapter in adapters]
