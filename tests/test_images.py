import hashlib
import io
import tarfile
import zipfile

import pytest

from spark_recovery.config import RecoveryError
from spark_recovery.images import download, extract_package, inspect_initrd
from spark_recovery.profiles import ASUS


def test_no_extractall_path_traversal(tmp_path, monkeypatch):
    iso = b"test fixture"
    tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=tar_bytes, mode="w:gz") as archive:
        symlink = tarfile.TarInfo("../../outside")
        symlink.type = tarfile.SYMTYPE
        symlink.linkname = "/etc/passwd"
        archive.addfile(symlink)
        member = tarfile.TarInfo("../../escape.iso")
        member.size = len(iso)
        archive.addfile(member, io.BytesIO(iso))
    package = tmp_path / "package.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("payload.tar.gz", tar_bytes.getvalue())
    monkeypatch.setitem(ASUS, "package_sha256", hashlib.sha256(package.read_bytes()).hexdigest())
    monkeypatch.setitem(ASUS, "iso_sha256", hashlib.sha256(iso).hexdigest())
    monkeypatch.setitem(ASUS, "iso_bytes", len(iso))
    output = tmp_path / "safe/recovery.iso"
    extract_package(package, output)
    assert output.read_bytes() == iso
    assert not (tmp_path / "outside").exists()
    assert not (tmp_path / "escape.iso").exists()


def test_bad_image_refused(tmp_path):
    package = tmp_path / "bad.zip"
    package.write_bytes(b"wrong")
    with pytest.raises(RecoveryError, match="SHA256"):
        extract_package(package, tmp_path / "out.iso")
    initrd = tmp_path / "initrd"
    initrd.write_bytes(b"bad initrd")
    with pytest.raises(RecoveryError):
        inspect_initrd(initrd)


def test_download_progress_counts_verified_bytes(tmp_path, monkeypatch):
    payload = b"approved test artifact" * 100
    class Response(io.BytesIO):
        headers = {"Content-Length": str(len(payload))}
        def geturl(self):
            return "https://vendor.example/artifact"
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: Response(payload))
    events = []
    destination = download("https://vendor.example/artifact", tmp_path / "artifact",
                           hashlib.sha256(payload).hexdigest(), progress=events.append)
    assert destination.read_bytes() == payload
    assert events[-1]["received"] == events[-1]["total"] == len(payload)
    with pytest.raises(RecoveryError, match="SHA256"):
        download("https://vendor.example/artifact", tmp_path / "rejected", "0" * 64, progress=events.append)
    assert not (tmp_path / "rejected").exists()
