import json

import pytest

from spark_recovery.cli import main, parser, ssh_command
from spark_recovery.config import RecoveryError, Session, atomic_json, mac_address
from spark_recovery.locking import ServiceLock
from spark_recovery.menu import render
from spark_recovery.safety import attest, authorize, check_active_menu, image_manifest, validate_inventory
from spark_recovery.target import select_pxe


@pytest.mark.parametrize("mac", ["", "00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff", "not-a-mac", "01:00:00:00:00:01"])
def test_bad_mac(mac):
    with pytest.raises(RecoveryError):
        mac_address(mac)


@pytest.mark.parametrize("server,client", [("8.8.8.8", "8.8.8.9"), ("127.0.0.1", "127.0.0.2"),
    ("172.31.250.1", "172.31.250.1"), ("172.31.250.1", "172.31.251.2"),
    ("172.31.250.0", "172.31.250.2"), ("172.31.250.1", "172.31.250.255")])
def test_invalid_subnet(server, client):
    with pytest.raises(RecoveryError):
        Session(server, client, "02:00:00:00:00:01", "12")


def test_menu_is_diagnostic_first(session):
    menu = render(session)
    assert "set default=0" in menu and "set timeout=-1" in menu
    assert "module_blacklist=r8169,nvme,nvme_core" in menu
    assert "break=casper-bottom init=/bin/sh" in menu
    assert not any("autoinstall" in line.split() for line in menu.splitlines() if line.strip().startswith("linux"))
    install = render(session, "/dev/nvme0n1")
    assert "CANCEL" in install and "CONFIRM ERASE" in install
    assert "read erase" not in install  # unavailable in the tested signed netboot loader
    assert "autoinstall" in install and "force-bootdisk=/dev/nvme0n1" in install


@pytest.mark.parametrize("disk", ["/dev/sda", "/dev/nvme0n1p1", "/dev/nvme0n1; reboot", "$(id)", ""])
def test_disk_injection(session, disk):
    with pytest.raises(RecoveryError):
        render(session, disk) if disk else validate_inventory({}, session, disk, "X")


def test_consent_sequence(prepared, session):
    with pytest.raises(RecoveryError):
        authorize(prepared, session, "/dev/nvme0n1", "TEST-SERIAL", "anything")
    attest(prepared, session, "VERIFIED " + session.target_mac)
    with pytest.raises(RecoveryError):
        authorize(prepared, session, "/dev/nvme0n1", "WRONG-SERIAL", "anything")
    with pytest.raises(RecoveryError):
        authorize(prepared, session, "/dev/nvme0n1", "TEST-SERIAL", "yes")
    authorize(prepared, session, "/dev/nvme0n1", "TEST-SERIAL", "ERASE " + session.target_mac + " TEST-SERIAL")
    check_active_menu(prepared, session, image_manifest(prepared, session))
    assert "CONFIRM ERASE" in (prepared / "tftp/grub/grub.cfg").read_text()


def test_mutated_receipt_and_menu_refused(prepared, session):
    attest(prepared, session, "VERIFIED " + session.target_mac)
    receipt = json.loads((prepared / "diagnostic.json").read_text())
    receipt["nvme_hidden"] = False
    atomic_json(prepared / "diagnostic.json", receipt)
    with pytest.raises(RecoveryError, match="incomplete"):
        authorize(prepared, session, "/dev/nvme0n1", "TEST-SERIAL", "ERASE " + session.target_mac + " TEST-SERIAL")
    (prepared / "tftp/grub/grub.cfg").write_text("set timeout=0\n")
    with pytest.raises(RecoveryError):
        check_active_menu(prepared, session, image_manifest(prepared, session))


def test_changed_bootfile_refused(prepared, session):
    (prepared / "tftp/casper/initrd").write_bytes(b"modified")
    with pytest.raises(RecoveryError, match="changed"):
        image_manifest(prepared, session)


def test_wrong_oem_refused(prepared, session):
    inventory = json.loads((prepared / "target.json").read_text())
    inventory["board"] = ["NVIDIA Founders Edition"]
    with pytest.raises(RecoveryError, match="ASUS"):
        validate_inventory(inventory, session, "/dev/nvme0n1", "TEST-SERIAL")


def test_session_change_invalidates_image(prepared, session):
    other = Session(session.server, session.client, "02:00:00:00:00:02", session.interface)
    with pytest.raises(RecoveryError):
        image_manifest(prepared, other)


def test_lock_exclusion_and_release(tmp_path):
    with ServiceLock(tmp_path):
        with pytest.raises(RecoveryError), ServiceLock(tmp_path):
            pass
    with ServiceLock(tmp_path):
        pass


def test_cli_target_and_plan_only(tmp_path, capsys):
    args = parser().parse_args(["target", "--json"])
    assert args.json and args.command == "target"
    assert main(["--state", str(tmp_path), "init", "--server", "172.31.250.1", "--client", "172.31.250.2",
                 "--mac", "02:00:00:00:00:01", "--interface", "12"]) == 0
    assert (tmp_path / "session.json").is_file()
    assert not (tmp_path / "network.json").exists()
    assert main(["--state", str(tmp_path), "init", "--server", "172.31.250.1", "--client", "172.31.250.2",
                 "--mac", "02:00:00:00:00:01", "--interface", "12"]) == 2


def test_ssh_no_hostkey_bypass():
    command = ssh_command("operator@target.local", "print('hello')")
    assert "StrictHostKeyChecking=yes" in command
    for bad in ("-oProxyCommand=evil", "user@host;id", "user@host\n", "$(id)"):
        with pytest.raises(RecoveryError):
            ssh_command(bad, "")


def test_bootnext_matches_mac_not_hardcoded_number():
    text = "Boot0008* UEFI: PXE IPv4 Ethernet /MAC(020000000001,0)/IPv4(...)\nBoot0001* ubuntu\n"
    assert select_pxe(text, "02:00:00:00:00:01") == "0008"
    with pytest.raises(ValueError):
        select_pxe(text + text, "02:00:00:00:00:01")
