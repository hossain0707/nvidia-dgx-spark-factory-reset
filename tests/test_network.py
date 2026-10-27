import json

import pytest

from spark_recovery.config import RecoveryError
from spark_recovery.network import HostNetwork
import spark_recovery.network as network


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_additive_network_cleanup(tmp_path, session, monkeypatch, platform):
    monkeypatch.setattr(network.sys, "platform", platform)
    monkeypatch.setattr(network, "require_admin", lambda: None)
    monkeypatch.setattr(network, "interface_index", lambda _: 12)
    present = {"value": False}
    calls = []
    monkeypatch.setattr(network, "assigned", lambda _: present["value"])
    monkeypatch.setattr(network, "interface_has_address", lambda _: present["value"])
    def command(args):
        assert (tmp_path / "network.json").exists(), "journal must precede every mutation"
        calls.append(args)
        present["value"] = "add" in args or "alias" in args
        return ""
    monkeypatch.setattr(network, "command", command)
    manager = HostNetwork(tmp_path, session)
    manager.apply()
    manager.restore()
    assert len(calls) == 2
    assert not (tmp_path / "network.json").exists()
    assert all(session.interface in args for args in calls)


def test_powershell_wrapper_has_explicit_exit_and_plain_errors(monkeypatch):
    import base64
    calls = []
    monkeypatch.setattr(network, "command", lambda args: calls.append(args) or "ok")
    assert network.powershell("Get-Service SharedAccess") == "ok"
    source = base64.b64decode(calls[0][-1]).decode("utf-16-le")
    assert "$ProgressPreference='SilentlyContinue'" in source
    assert "; exit 0 } catch" in source
    assert "[Console]::Error.WriteLine($_.Exception.Message); exit 1" in source


def test_different_interface_refused(tmp_path, session, monkeypatch):
    monkeypatch.setattr(network, "require_admin", lambda: None)
    monkeypatch.setattr(network, "interface_index", lambda _: 12)
    monkeypatch.setattr(network, "assigned", lambda _: True)
    monkeypatch.setattr(network, "interface_has_address", lambda _: False)
    with pytest.raises(RecoveryError, match="another interface"):
        HostNetwork(tmp_path, session).apply()
    assert not (tmp_path / "network.json").exists()


def test_windows_ics_requires_explicit_ack(tmp_path, session, monkeypatch):
    monkeypatch.setattr(network.sys, "platform", "win32")
    monkeypatch.setattr(network, "require_admin", lambda: None)
    monkeypatch.setattr(network, "interface_index", lambda _: 12)
    monkeypatch.setattr(network, "assigned", lambda _: True)
    monkeypatch.setattr(network, "interface_has_address", lambda _: True)
    details = {"dhcp": "Disabled", "ics": "Running", "addresses": [{"IPAddress": session.server, "PrefixLength": 24}]}
    monkeypatch.setattr(network, "powershell", lambda _: json.dumps(details))
    with pytest.raises(RecoveryError, match="--stop-ics"):
        HostNetwork(tmp_path, session).apply()
    assert not (tmp_path / "network.json").exists()


def test_windows_failure_restores_ics(tmp_path, session, monkeypatch):
    monkeypatch.setattr(network.sys, "platform", "win32")
    monkeypatch.setattr(network, "require_admin", lambda: None)
    monkeypatch.setattr(network, "interface_index", lambda _: 12)
    monkeypatch.setattr(network, "assigned", lambda _: True)
    monkeypatch.setattr(network, "interface_has_address", lambda _: True)
    calls = []
    details = {"dhcp": "Disabled", "ics": "Running", "addresses": []}
    def ps(script):
        calls.append(script)
        if len(calls) == 1:
            return json.dumps(details)
        if "New-NetFirewallRule" in script:
            raise RecoveryError("simulated firewall failure")
        return ""
    monkeypatch.setattr(network, "powershell", ps)
    with pytest.raises(RecoveryError, match="simulated"):
        HostNetwork(tmp_path, session).apply(stop_ics=True)
    assert any("Remove-NetFirewallRule" in script for script in calls)
    assert any("Start-Service SharedAccess" in script for script in calls)
    assert not (tmp_path / "network.json").exists()
