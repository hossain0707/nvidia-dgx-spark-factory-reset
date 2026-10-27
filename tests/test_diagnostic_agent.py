import gzip
import hashlib
import json
from functools import partial
from http.server import ThreadingHTTPServer
import threading
import urllib.error
import urllib.request

import pytest

from spark_recovery.config import RecoveryError, atomic_json, digest
from spark_recovery.diagnostic_agent import (DiagnosticHTTPHandler, agent_script, configure, cpio_entry,
                                            read_initrd_files, receive, records)
from spark_recovery.menu import render
from spark_recovery.safety import authorize, check_active_menu, image_manifest


@pytest.fixture
def observed(prepared, session):
    initrd = prepared / "tftp/casper/initrd"
    archive = cpio_entry("scripts/init-premount/ORDER", b"/scripts/init-premount/existing-hook\n")
    archive += cpio_entry("scripts/casper", b"maybe_break casper-bottom\n")
    archive += cpio_entry("usr/bin/wget", b"fixture GET-only wget")
    archive += cpio_entry("TRAILER!!!", b"", 0)
    initrd.write_bytes(gzip.compress(archive))
    image = json.loads((prepared / "image.json").read_text())
    image["boot_files"]["casper/initrd"] = digest(initrd)
    atomic_json(prepared / "image.json", image)
    configure(prepared, session)
    return prepared


def test_observer_preserves_original_and_never_enters_installer(observed, session):
    original = observed / "tftp/casper/initrd"
    changed = observed / "tftp/casper/initrd-diagnostic"
    assert changed.read_bytes().startswith(original.read_bytes())
    appended = changed.read_bytes()[len(original.read_bytes()):].lstrip(b"\0")
    added = {name: content for name, mode, content in records(appended)}
    assert b"existing-hook" in added["scripts/init-premount/ORDER"]
    script = added["scripts/init-premount/mkd-diagnostic"]
    assert b"/dev/nvme" in script and b"iso9660" in script and b"/report/" in script
    assert b"mkd.diagnostic=1" in script and b"*autoinstall*) exit 0" in script
    assert not any(command in script for command in (b"reboot", b"mkfs", b"dd if", b"efibootmgr"))
    image = image_manifest(observed, session)
    check_active_menu(observed, session, image)
    menu = (observed / "tftp/grub/grub.cfg").read_text()
    assert "set default=1\nset timeout=5" in menu and " autoinstall " not in menu
    installed = render(session, "/dev/nvme0n1", agent=True)
    assert installed.startswith("# Generated") and "set default=0\nset timeout=-1" in installed
    assert "initrd /casper/initrd\n" in installed


def test_token_bound_guard_results_and_explicit_erase_consent(observed, session):
    token = json.loads((observed / "diagnostic-agent.json").read_text())["token"]
    checks = {name: True for name in ("iso_read_only", "oem_present", "nvme_hidden", "mac_matched")}
    with pytest.raises(RecoveryError):
        receive(observed, session, "0" * 64, checks)
    assert not (observed / "diagnostic.json").exists()
    receive(observed, session, token, checks)
    receipt = json.loads((observed / "diagnostic.json").read_text())
    assert receipt["agent_verified"] and not receipt["operator_attestation"]
    assert receipt["agent_token_sha256"] == hashlib.sha256(token.encode()).hexdigest()
    with pytest.raises(RecoveryError):
        authorize(observed, session, "/dev/nvme0n1", "TEST-SERIAL", "wrong consent")
    authorize(observed, session, "/dev/nvme0n1", "TEST-SERIAL", f"ERASE {session.target_mac} TEST-SERIAL")
    with pytest.raises(RecoveryError):
        receive(observed, session, token, {**checks, "nvme_hidden": False})
    assert not (observed / "diagnostic.json").exists() and not (observed / "install.json").exists()


def test_boolean_strings_and_stale_reports_refused(observed, session):
    descriptor = json.loads((observed / "diagnostic-agent.json").read_text())
    checks = {name: True for name in ("iso_read_only", "oem_present", "nvme_hidden", "mac_matched")}
    with pytest.raises(RecoveryError):
        receive(observed, session, descriptor["token"], {**checks, "nvme_hidden": "true"})
    descriptor["created_at"] = 0
    atomic_json(observed / "diagnostic-agent.json", descriptor)
    with pytest.raises(RecoveryError, match="stale"):
        receive(observed, session, descriptor["token"], checks)
    assert not (observed / "diagnostic.json").exists()


def test_initrd_parser_and_generated_token_validation(tmp_path, session):
    path = tmp_path / "initrd"
    path.write_bytes(cpio_entry("one", b"safe") + cpio_entry("TRAILER!!!", b"", 0))
    assert read_initrd_files(path, {"one"}) == {"one": b"safe"}
    with pytest.raises(RecoveryError):
        agent_script(session, "shell;unsafe")


def test_get_only_wget_report_endpoint_is_narrow_and_token_bound(observed, session):
    class LoopbackSession:
        client = "127.0.0.1"
        def __getattr__(self, name):
            return getattr(session, name)
    token = json.loads((observed / "diagnostic-agent.json").read_text())["token"]
    handler = partial(DiagnosticHTTPHandler, root=observed, session=LoopbackSession(), files={}, allowed={"127.0.0.1"})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        for path in ("/_mkd/commands/reboot", "/_mkd/diagnostic/" + "0" * 64 + "/report/1111"):
            with pytest.raises(urllib.error.HTTPError):
                opener.open(base + path, timeout=3)
        assert not (observed / "diagnostic.json").exists()
        with opener.open(base + f"/_mkd/diagnostic/{token}/report/1111", timeout=3) as response:
            assert response.read() == b"ok\n"
        assert json.loads((observed / "diagnostic.json").read_text())["agent_verified"] is True
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()
