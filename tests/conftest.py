import pytest

from spark_recovery.config import Session, atomic_json, digest
from spark_recovery.menu import render
from spark_recovery.profiles import ASUS


@pytest.fixture
def session():
    return Session("172.31.250.1", "172.31.250.2", "02:00:00:00:00:01", "12",
                   session_id="a" * 32)


@pytest.fixture
def prepared(tmp_path, session, monkeypatch):
    session.save(tmp_path)
    tftp = tmp_path / "tftp"
    (tftp / "casper").mkdir(parents=True)
    (tftp / "grub").mkdir()
    files = {}
    for name in ("casper/vmlinuz", "casper/initrd", "grubnetaa64.efi.signed"):
        path = tftp / name
        path.write_bytes(b"test boot file " + name.encode())
        files[name] = digest(path)
    iso = tmp_path / "fixture.iso"
    iso.write_bytes(b"test-only fixture ISO, never used for real recovery")
    monkeypatch.setitem(ASUS, "iso_sha256", digest(iso))
    atomic_json(tmp_path / "image.json", {"session": session.fingerprint, "profile": session.profile,
        "iso_path": str(iso), "iso_sha256": digest(iso), "boot_files": files,
        "guards": {"http_iso_loading": True, "nvme_modular": True}})
    (tftp / "grub/grub.cfg").write_text(render(session), encoding="utf-8")
    inventory = {"machine": "aarch64", "efi": True, "board": ["ASUS Ascent GX10"],
                 "interfaces": {"en0": session.target_mac},
                 "disks": [{"name": "nvme0n1", "type": "disk", "serial": "TEST-SERIAL", "size": 1000000000000}]}
    atomic_json(tmp_path / "target.json", inventory)
    return tmp_path
