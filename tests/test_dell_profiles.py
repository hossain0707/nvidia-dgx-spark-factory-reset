"""OEM/region boundaries are safety properties, not interchangeable filenames."""
from dataclasses import replace
import io
import json

import pycdlib
import pytest

from spark_recovery.config import RecoveryError, atomic_json, digest
from spark_recovery.images import prepare
from spark_recovery.menu import render
from spark_recovery.oem import detect_oem
from spark_recovery.profiles import ASUS, DELL_WW, DELL_US
from spark_recovery.safety import image_manifest, validate_inventory


def test_dell_requires_exact_model_and_matching_oem(session):
    inventory = {"machine": "arm64", "efi": True, "board": ["Dell Inc.", "FCM1253"],
                 "interfaces": {"en0": session.target_mac},
                 "disks": [{"name": "nvme0n1", "type": "disk", "serial": "TEST"}]}
    dell = replace(session, profile=DELL_WW["id"])
    validate_inventory(inventory, dell, "/dev/nvme0n1", "TEST")
    with pytest.raises(RecoveryError, match="OEM"):
        validate_inventory(inventory, session, "/dev/nvme0n1", "TEST")
    inventory["board"] = ["ASUS GX10"]
    with pytest.raises(RecoveryError, match="OEM"):
        validate_inventory(inventory, dell, "/dev/nvme0n1", "TEST")
    with pytest.raises(RecoveryError):
        detect_oem({"machine": "arm64", "board": ["Dell GB10 unknown model"]})


def test_region_receipt_cannot_be_reused(prepared, session):
    dell = replace(session, profile=DELL_US["id"])
    manifest = json.loads((prepared / "image.json").read_text())
    manifest.update(session=dell.fingerprint, profile=dell.profile, iso_sha256=DELL_WW["iso_sha256"])
    atomic_json(prepared / "image.json", manifest)
    with pytest.raises(RecoveryError, match="OEM/region"):
        image_manifest(prepared, dell)
    assert ASUS["iso_sha256"] != DELL_WW["iso_sha256"] != DELL_US["iso_sha256"]
    menu = render(dell, "/dev/nvme0n1")
    assert "Dell DGX OS" in menu and "ASUS DGX OS" not in menu
    assert "set default=0\nset timeout=-1" in menu
    assert "force-bootdisk=/dev/nvme0n1" in menu


def test_direct_dell_preparation_and_wrong_region_refusal(tmp_path, session, monkeypatch):
    iso = tmp_path / "fixture.iso"
    image = pycdlib.PyCdlib()
    image.new(interchange_level=3, rock_ridge="1.09")
    for path, rr in (("/CASPER", "casper"), ("/OEMDATA", "oemdata"), ("/OEMDATA/DEBS", "debs")):
        image.add_directory(path, rr_name=rr)
    files = [("/CASPER/INITRD.;1", "initrd"), ("/CASPER/VMLINUZ.;1", "vmlinuz"),
             ("/OEMDATA/OEM.SH;1", "oem-iso-cfg.sh"),
             ("/OEMDATA/DEBS/DELL.DEB;1", "dell-dgx-wallpaper_1.0-2_all.deb")]
    streams = [io.BytesIO(b"fixture") for _ in files]
    for (path, rr), stream in zip(files, streams, strict=True):
        image.add_fp(stream, 7, iso_path=path, rr_name=rr)
    image.write(str(iso))
    image.close()
    monkeypatch.setitem(DELL_WW, "iso_bytes", iso.stat().st_size)
    monkeypatch.setitem(DELL_WW, "iso_sha256", digest(iso))
    monkeypatch.setattr("spark_recovery.images.inspect_initrd", lambda path: {"test_fixture": True})
    monkeypatch.setattr("spark_recovery.images.verify_bootloader", lambda *args: None)
    loader = tmp_path / "loader"
    loader.write_bytes(b"fixture loader")
    dell = replace(session, profile=DELL_WW["id"])
    root = tmp_path / "session"
    result = prepare(root, dell, iso=iso, bootloader=loader)
    assert result["profile"] == DELL_WW["id"]
    assert image_manifest(root, dell)["iso_sha256"] == digest(iso)
    with pytest.raises(RecoveryError, match="OEM/model/region"):
        prepare(tmp_path / "wrong", replace(session, profile=DELL_US["id"]), iso=iso, bootloader=loader)
    with pytest.raises(RecoveryError, match="direct ISO"):
        prepare(tmp_path / "wrong-package", dell, package=tmp_path / "asus.zip")
