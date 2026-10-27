import json
from pathlib import Path
import subprocess
import sys

import pytest

from spark_recovery.config import RecoveryError
from spark_recovery.desktop import inspect_ssh, inventory_disks
from spark_recovery.desktop_service import (ServiceStatus, app_command, launch,
                                           prepare_shared_state, read_service_status)
from spark_recovery.timing import RecoveryTiming, duration


def test_monotonic_stage_measurements_and_privacy(tmp_path):
    now = [100.0]
    timer = RecoveryTiming(tmp_path / "timing.json", clock=lambda: now[0])
    timer.mark("session_created")
    timer.mark("preparation_started")
    now[0] = 220
    timer.mark("preparation_complete")
    now[0] = 250
    timer.mark("services_ready")
    now[0] = 310
    timer.mark("diagnostic_verified", evidence="operator_console_attestation")
    now[0] = 330
    timer.mark("install_started", evidence="operator_console_confirmation")
    now[0] = 530
    timer.mark("first_boot_confirmed", evidence="operator_first_boot_confirmation")
    report = timer.report()
    assert report["durations"] == {"preparation_seconds": 120, "recovery_session_seconds": 280,
                                   "operator_observed_install_seconds": 200}
    assert report["outcome"] == "operator_confirmed_success"
    for private in ("target_mac", "serial", "password", "iso_path", "server"):
        assert private not in report
    timer.finish("canceled")
    assert timer.data["outcome"] == "operator_confirmed_success"
    assert duration(3662.5) == "01:01:02"


def test_timing_never_equates_transfer_or_restart_with_success(tmp_path):
    timer = RecoveryTiming(tmp_path / "timing.json")
    timer.mark("session_created")
    timer.mark("iso_downloaded")
    assert not timer.report()["durations"]
    resumed = RecoveryTiming.load(timer.path)
    assert resumed.data["outcome"] == "interrupted"
    with pytest.raises(RecoveryError):
        resumed.mark("first_boot_confirmed")


def test_missing_start_duplicate_and_failed_run(tmp_path):
    timer = RecoveryTiming(tmp_path / "timing.json")
    with pytest.raises(RecoveryError, match="sequence"):
        timer.mark("first_boot_confirmed")
    timer.mark("preparation_started")
    with pytest.raises(RecoveryError, match="already"):
        timer.mark("preparation_started")
    timer.finish("failed")
    assert timer.report()["outcome"] == "failed"
    assert "operator_observed_install_seconds" not in timer.report()["durations"]


def test_ssh_inspection_is_noninteractive_bounded_and_strict(monkeypatch):
    calls = []
    def execute(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, '{"machine":"aarch64"}', "")
    monkeypatch.setattr(subprocess, "run", execute)
    assert inspect_ssh("operator@target.local")["machine"] == "aarch64"
    args, options = calls[0]
    assert "BatchMode=yes" in args and "StrictHostKeyChecking=yes" in args
    assert options["timeout"] == 35
    with pytest.raises(RecoveryError):
        inspect_ssh("operator@target;unsafe")


def test_helper_rejects_arbitrary_operation_and_frozen_entry(monkeypatch, tmp_path):
    with pytest.raises(RecoveryError):
        launch(tmp_path, "format-disk")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert app_command() == [sys.executable]


def test_helper_readiness_only_on_server_message(tmp_path):
    import logging
    status = ServiceStatus(tmp_path, "serve")
    status.emit(logging.makeLogRecord({"msg": "HTTP downloaded"}))
    assert json.loads((tmp_path / "desktop-service.json").read_text())["phase"] == "starting"
    status.emit(logging.makeLogRecord({"msg": "Recovery stage: Image verified; configuring host"}))
    progress = json.loads((tmp_path / "desktop-service.json").read_text())
    assert progress["phase"] == "starting" and progress["message"] == "Image verified; configuring host"
    status.emit(logging.makeLogRecord({"msg": "Serving ONLY target"}))
    assert json.loads((tmp_path / "desktop-service.json").read_text())["phase"] == "ready"


def test_windows_helper_grants_only_session_owner_inheritable_access(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("spark_recovery.desktop_service.powershell", lambda source: "S-1-5-21-100-200-300-1001")
    calls = []
    monkeypatch.setattr("spark_recovery.desktop_service.command", calls.append)
    prepare_shared_state(tmp_path)
    assert calls == [["icacls.exe", str(tmp_path.resolve()), "/grant", "*S-1-5-21-100-200-300-1001:(OI)(CI)F"]]
    monkeypatch.setattr("spark_recovery.desktop_service.powershell", lambda source: "Everyone")
    with pytest.raises(RecoveryError, match="session owner"):
        prepare_shared_state(tmp_path)
    assert len(calls) == 1


def test_unreadable_status_is_not_treated_as_helper_still_starting(monkeypatch, tmp_path):
    assert read_service_status(tmp_path) is None
    path = tmp_path / "desktop-service.json"
    path.write_text("[]")
    with pytest.raises(RecoveryError, match="Invalid"):
        read_service_status(tmp_path)
    def denied(*args, **kwargs):
        raise PermissionError("administrator-owned status")
    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(RecoveryError, match="denied access.*do not reboot"):
        read_service_status(tmp_path)


def test_imported_identity_is_validated_before_display(prepared, session):
    inventory = json.loads((prepared / "target.json").read_text())
    assert inventory_disks(inventory, session)[0]["serial"] == "TEST-SERIAL"
    inventory["board"] = ["ASUS different model"]
    with pytest.raises(RecoveryError):
        inventory_disks(inventory, session)
    for value in (None, [], {"board": [None], "interfaces": {}, "disks": []}):
        with pytest.raises(RecoveryError):
            inventory_disks(value, session)


def test_desktop_smoke_without_network_mutations(tmp_path):
    pytest.importorskip("PySide6.QtWidgets")
    import os
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", SPARK_RECOVERY_SMOKE_SCREENSHOT=str(tmp_path / "ui.png"))
    result = subprocess.run([sys.executable, "-m", "spark_recovery.desktop", "--smoke-test"],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert Path(tmp_path / "ui.png").stat().st_size > 1000
