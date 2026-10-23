"""Cross-platform operator CLI. Read-only until an explicit action is requested."""
from __future__ import annotations

import argparse
import base64
import json
import logging
from pathlib import Path
import re
import shlex
import subprocess
import sys

from . import __version__
from .config import RecoveryError, Session, atomic_json
from .images import prepare
from .locking import ServiceLock
from .network import HostNetwork
from .safety import attest, authorize, image_manifest, write_menu
from .server import doctor, serve


def ssh_command(destination: str, source: str, args: list[str] | None = None, *, sudo=False) -> list[str]:
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9_.:-]*", destination):
        raise RecoveryError("SSH destination must be USER@HOST (configure ports/keys in your SSH config)")
    encoded = base64.b64encode(source.encode()).decode()
    remote = (["sudo", "-n"] if sudo else []) + ["python3", "-c",
              f"exec(__import__('base64').b64decode('{encoded}'))"] + (args or [])
    return ["ssh", "-o", "StrictHostKeyChecking=yes", destination, shlex.join(remote)]


def ssh_inventory(destination: str) -> dict:
    source = Path(__file__).with_name("target.py").read_text(encoding="utf-8")
    result = subprocess.run(ssh_command(destination, source), capture_output=True, text=True)
    if result.returncode:
        raise RecoveryError("SSH inspection failed; no host keys were changed. " + result.stderr.strip())
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise RecoveryError("SSH inventory did not return JSON") from error


def parser():
    app = argparse.ArgumentParser(description="Diagnostic-first Ethernet recovery. ASUS GX10 profile; no USB drive.")
    app.add_argument("--version", action="version", version=__version__)
    app.add_argument("--state", type=Path, default=Path("state"), help="Private local state directory; never commit it")
    commands = app.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create a non-destructive recovery plan")
    for flag in ("server", "client", "interface", "mac"):
        init.add_argument("--" + flag, required=True)
    init.add_argument("--prefix", type=int, default=24)
    init.add_argument("--http-port", type=int, default=8080)
    commands.add_parser("wizard", help="Interactive host setup; never erases or reboots")
    image = commands.add_parser("prepare", help="Verify official artifacts and create a diagnostic-only boot tree")
    image.add_argument("--iso", type=Path)
    image.add_argument("--package", type=Path)
    image.add_argument("--bootloader", type=Path)
    image.add_argument("--download", action="store_true", help="Download pinned official artifacts under their vendor terms")
    commands.add_parser("doctor", help="Read-only service/image/network checks")
    inspect = commands.add_parser("inspect-target", help="Record target identity and SSD serial before recovery")
    group = inspect.add_mutually_exclusive_group(required=True)
    group.add_argument("--ssh", metavar="USER@HOST")
    group.add_argument("--file", type=Path, help="JSON captured using target --json locally on GX10")
    check = commands.add_parser("attest-diagnostic", help="Record operator-verified diagnostic boot results")
    check.add_argument("--confirm", required=True, help="Exactly: VERIFIED <target-mac>")
    install = commands.add_parser("enable-install", help="Expose cancel-first erase menu AFTER diagnostic boot")
    install.add_argument("--disk", required=True)
    install.add_argument("--serial", required=True)
    install.add_argument("--confirm", required=True, help="Exactly: ERASE <target-mac> <disk-serial>")
    commands.add_parser("revoke-install", help="Remove the erase menu; does not interrupt a running installer")
    host = commands.add_parser("serve", help="Start scoped HTTP/TFTP/DHCP until Ctrl+C; requires administrator/root")
    host.add_argument("--manage-network", action="store_true", help="Journal and temporarily configure the host Ethernet IP")
    host.add_argument("--stop-ics", action="store_true", help="Windows: temporarily stop competing Internet Connection Sharing")
    commands.add_parser("restore", help="Restore host networking from this session's journal after services stop")
    local = commands.add_parser("target", help="Run preflight/BootNext on the GX10 itself; needs a separate server")
    local.add_argument("--json", action="store_true")
    local.add_argument("--mac")
    local.add_argument("--apply", action="store_true")
    local.add_argument("--reboot", action="store_true")
    remote = commands.add_parser("ssh-boot", help="Set target BootNext using existing SSH + noninteractive sudo")
    remote.add_argument("--ssh", required=True, metavar="USER@HOST")
    remote.add_argument("--apply", action="store_true", help="Required to change BootNext")
    remote.add_argument("--reboot", action="store_true", help="Explicitly interrupt workloads and reboot target")
    return app


def run(args):
    root = args.state.expanduser().resolve()
    if args.command == "target":
        from .target import main as target_main
        forwarded = (["--json"] if args.json else []) + (["--mac", args.mac] if args.mac else [])
        forwarded += (["--apply"] if args.apply else []) + (["--reboot"] if args.reboot else [])
        return target_main(forwarded)
    if args.command in ("init", "wizard"):
        if (root / "session.json").exists():
            raise RecoveryError("Session already exists. Reuse it, or choose a new empty --state directory")
        if args.command == "wizard":
            print("ASUS GX10 7.5.0-4 only. Dedicated Ethernet link; no USB storage. No erase/reboot in wizard.")
            print("Find Ethernet interface: Windows Get-NetAdapter; macOS networksetup -listallhardwareports; Linux ip -br link")
            server = input("Host Ethernet IPv4 [172.31.250.1]: ").strip() or "172.31.250.1"
            client = input("Target IPv4 [172.31.250.2]: ").strip() or "172.31.250.2"
            interface = input("Ethernet interface (Windows numeric index / macOS enX / Linux name): ").strip()
            mac = input("Target Ethernet MAC shown in GX10 firmware: ").strip()
            session = Session(server, client, mac, interface)
        else:
            session = Session(args.server, args.client, args.mac, args.interface, args.prefix, args.http_port)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        session.save(root)
        print(f"Session saved: {root}\nTarget: {session.target_mac}; plan {session.fingerprint[:16]}")
        if args.command == "wizard":
            print("Next: prepare --download, inspect-target, doctor, then serve. See README for exact OS commands.")
        return 0
    session = Session.load(root)
    if args.command == "prepare":
        if args.iso is None and args.package is None and not args.download:
            raise RecoveryError("Choose --download, --iso, or --package. Official vendor terms apply to downloads")
        with ServiceLock(root):
            print(json.dumps(prepare(root, session, args.iso, args.package, args.bootloader), indent=2))
    elif args.command == "doctor":
        print(json.dumps(doctor(root, session), indent=2))
    elif args.command == "inspect-target":
        inventory = ssh_inventory(args.ssh) if args.ssh else json.loads(args.file.read_text(encoding="utf-8"))
        if not isinstance(inventory, dict) or not inventory.get("interfaces") or not inventory.get("disks"):
            raise RecoveryError("Not a valid target inventory")
        atomic_json(root / "target.json", inventory)
        print(json.dumps(inventory, indent=2))
    elif args.command == "attest-diagnostic":
        attest(root, session, args.confirm)
        print("Recorded OPERATOR attestation: read-only ISO, OEM files visible, NVMe hidden. This is not an automated proof.")
    elif args.command == "enable-install":
        authorize(root, session, args.disk, args.serial, args.confirm)
        print("Erase submenu exposed. STOP remains default; user must select CONFIRM ERASE at the GX10.")
    elif args.command == "revoke-install":
        write_menu(root, session)
        (root / "install.json").unlink(missing_ok=True)
        print("Erase menu removed. A running installer is NOT stopped by this action.")
    elif args.command == "serve":
        with ServiceLock(root):
            serve(root, session, args.manage_network, args.stop_ics)
    elif args.command == "restore":
        with ServiceLock(root):
            HostNetwork(root, session).restore()
        print("Host network journal restored (or no journal existed). Stop all recovery processes before restoring.")
    elif args.command == "ssh-boot":
        if not args.apply:
            raise RecoveryError("--apply is required; --reboot additionally interrupts running workloads")
        image_manifest(root, session)
        source = Path(__file__).with_name("target.py").read_text(encoding="utf-8")
        flags = ["--mac", session.target_mac, "--apply"] + (["--reboot"] if args.reboot else [])
        result = subprocess.run(ssh_command(args.ssh, source, flags, sudo=True))
        if result.returncode:
            raise RecoveryError("Remote BootNext/reboot failed. Use the documented local sudo target command; never share passwords")
    return 0


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parser().parse_args(argv)
    try:
        return run(args)
    except (RecoveryError, OSError, ValueError, KeyError) as error:
        print(f"Stopped: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Stopped by operator", file=sys.stderr)
        return 130
