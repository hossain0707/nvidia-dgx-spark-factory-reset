"""Standalone Linux target preflight/BootNext helper, usable locally or via SSH.

No formatting commands. Disk install is selected later at the recovery menu.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess


def execute(args):
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"Failed: {args[0]}")
    return result.stdout


def inventory():
    interfaces = {}
    for interface in Path("/sys/class/net").glob("*"):
        address = interface / "address"
        if address.is_file():
            interfaces[interface.name] = address.read_text().strip()
    board = []
    for path in ("/sys/class/dmi/id/sys_vendor", "/sys/class/dmi/id/product_name",
                 "/proc/device-tree/model", "/proc/device-tree/compatible"):
        try:
            board.append(Path(path).read_text().strip("\0\n"))
        except OSError:
            continue
    def identity(path):
        try:
            return Path(path).read_text().strip("\0\n")
        except OSError:
            return ""
    efi = Path("/sys/firmware/efi").is_dir()
    try:
        boots = execute(["efibootmgr", "-v"]) if efi else ""
    except (RuntimeError, OSError):
        boots = ""  # Missing read permissions must not prevent read-only identification.
    addresses = json.loads(execute(["ip", "-json", "address"]))
    return {"hostname": execute(["hostname"]).strip(), "machine": os.uname().machine,
            "board": board, "interfaces": interfaces,
            "manufacturer": identity("/sys/class/dmi/id/sys_vendor"),
            "product": identity("/sys/class/dmi/id/product_name"),
            "addresses": {item["ifname"]: [entry["local"] + "/" + str(entry["prefixlen"])
                          for entry in item.get("addr_info", [])] for item in addresses},
            "disks": json.loads(execute(["lsblk", "--json", "--bytes", "--nodeps", "--output",
                                         "NAME,TYPE,SIZE,MODEL,SERIAL"]))["blockdevices"],
            "efi": efi, "boots": boots}


def select_pxe(text: str, mac: str) -> str:
    compact = re.sub(r"[:-]", "", mac).lower()
    if not re.fullmatch(r"[0-9a-f]{12}", compact):
        raise ValueError("Expected six-octet target MAC")
    matches = []
    for line in text.splitlines():
        boot = re.match(r"Boot([0-9A-Fa-f]{4})\*?\s+", line)
        if (boot and "PXE" in line.upper() and "IPV4" in line.upper()
                and f"mac({compact}," in line.lower()):
            matches.append(boot.group(1))
    if len(matches) != 1:
        raise ValueError("Expected exactly one IPv4 PXE entry matching the target MAC; inspect firmware")
    return matches[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Linux GX10 target checks/one-time PXE boot; needs a separate recovery host")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--mac")
    parser.add_argument("--apply", action="store_true", help="Set one-time EFI BootNext, not permanent boot order")
    parser.add_argument("--reboot", action="store_true", help="Reboot AFTER setting BootNext; interrupts workloads")
    args = parser.parse_args(argv)
    if not os.sys.platform.startswith("linux"):
        parser.error("Run target checks on the Linux GX10, locally or through SSH")
    if args.reboot and not args.apply:
        parser.error("--reboot requires --apply")
    info = inventory()
    if args.apply:
        if os.geteuid() != 0 or not args.mac:
            parser.error("--apply requires sudo/root and --mac")
        if info["machine"] not in ("aarch64", "arm64"):
            parser.error("Target must be ARM64")
        if args.mac.lower().replace("-", ":") not in info["interfaces"].values():
            parser.error("MAC not present on this target")
        entry = select_pxe(info["boots"], args.mac)
        execute(["efibootmgr", "--bootnext", entry])
        print("One-time PXE BootNext set to " + entry, flush=True)
        if args.reboot:
            print("Rebooting. Requires a prepared separate recovery server; no erase selected here.", flush=True)
            execute(["systemctl", "reboot"])
    else:
        print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
