"""Strict session configuration; no personal machine defaults."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import uuid


class RecoveryError(Exception):
    """Actionable refusal; callers must not continue with installation."""


def mac_address(value: str) -> str:
    compact = value.replace(":", "").replace("-", "").lower()
    if not re.fullmatch(r"[0-9a-f]{12}", compact):
        raise RecoveryError("Target MAC must contain exactly six hexadecimal octets")
    raw = bytes.fromhex(compact)
    if raw[0] & 1 or raw == b"\0" * 6:
        raise RecoveryError("Target MAC must be a nonzero unicast hardware address")
    return ":".join(compact[i:i + 2] for i in range(0, 12, 2))


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".pending")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, path)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@dataclass(frozen=True)
class Session:
    server: str
    client: str
    target_mac: str
    interface: str
    prefix: int = 24
    http_port: int = 8080
    profile: str = "asus-gx10-7.5.0-4"
    session_id: str = ""

    def __post_init__(self):
        for item in (self.server, self.client):
            try:
                address = ipaddress.IPv4Address(item)
            except ValueError as error:
                raise RecoveryError("Only IPv4 literal addresses are supported") from error
            private = any(address in ipaddress.IPv4Network(cidr)
                          for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
            if not private:
                raise RecoveryError("Use a private RFC1918 IPv4 subnet on a dedicated Ethernet link")
        if not 16 <= self.prefix <= 30:
            raise RecoveryError("Ethernet prefix must be /16 through /30")
        network = ipaddress.IPv4Network(f"{self.server}/{self.prefix}", strict=False)
        if self.server == self.client or ipaddress.IPv4Address(self.client) not in network:
            raise RecoveryError("Host and target must be different addresses in the same subnet")
        if any(ipaddress.IPv4Address(item) in (network.network_address, network.broadcast_address)
               for item in (self.server, self.client)):
            raise RecoveryError("Host and target cannot be subnet/broadcast addresses")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", self.interface) or self.interface in ("lo", "lo0"):
            raise RecoveryError("Use a dedicated Ethernet interface name (Windows: numeric interface index)")
        if not 1024 <= self.http_port <= 65535:
            raise RecoveryError("HTTP port must be between 1024 and 65535")
        from .profiles import image_profile
        image_profile(self.profile)
        object.__setattr__(self, "target_mac", mac_address(self.target_mac))
        if not self.session_id:
            object.__setattr__(self, "session_id", uuid.uuid4().hex)
        if not re.fullmatch(r"[a-f0-9]{32}", self.session_id):
            raise RecoveryError("Invalid session identifier")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()

    def save(self, root: Path):
        atomic_json(root / "session.json", asdict(self))

    @classmethod
    def load(cls, root: Path) -> Session:
        try:
            return cls(**json.loads((root / "session.json").read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError) as error:
            raise RecoveryError(f"Invalid/missing session; run init first: {error}") from error

