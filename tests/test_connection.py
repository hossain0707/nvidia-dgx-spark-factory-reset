import json
from pathlib import Path

import pytest

from spark_recovery.config import RecoveryError
from spark_recovery.connection import DeviceConnection, fingerprint, key_status, trust_key
from spark_recovery.discovery import Adapter, Peer

paramiko = pytest.importorskip("paramiko")


def test_fingerprint_trust_and_rotation_never_edit_global_file(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    key = paramiko.RSAKey.generate(2048)
    old = paramiko.RSAKey.generate(2048)
    global_path = tmp_path / ".ssh/known_hosts"
    global_path.parent.mkdir()
    keys = paramiko.HostKeys()
    keys.add("192.168.50.2", old.get_name(), old)
    keys.save(str(global_path))
    original = global_path.read_bytes()
    class Socket:
        def close(self):
            pass
    class Transport:
        def __init__(self, connection):
            pass
        def start_client(self, **kwargs):
            pass
        def get_remote_server_key(self):
            return key
        def close(self):
            pass
    monkeypatch.setattr("spark_recovery.connection.endpoint_socket", lambda *args: Socket())
    monkeypatch.setattr(paramiko, "Transport", Transport)
    peer = Peer("192.168.50.2", "", "14")
    adapter = Adapter("14", "Wired", True, True)
    known = tmp_path / "app/known_hosts"
    observed = key_status(peer, adapter, known)
    assert observed["state"] == "changed" and observed["fingerprint"] == fingerprint(key)
    with pytest.raises(RecoveryError):
        trust_key(peer, observed, known)
    trust_key(peer, observed, known, replace=True)
    assert key_status(peer, adapter, known)["state"] == "trusted"
    assert global_path.read_bytes() == original
    assert "password" not in json.dumps(observed)


def test_username_validation_precedes_authentication(tmp_path):
    peer = Peer("192.168.50.2", "", "14")
    for account in ("-root", "a;reboot", "x\ny", "", "$(id)"):
        with pytest.raises(RecoveryError):
            DeviceConnection(peer, Adapter("14", "Wired", True, True), account, tmp_path / "known")


def test_bad_observed_key_fingerprint_refuses_trust(tmp_path):
    key = paramiko.RSAKey.generate(2048)
    observed = {"state": "unknown", "kind": key.get_name(), "key": key.get_base64(), "fingerprint": "wrong"}
    with pytest.raises(RecoveryError):
        trust_key(Peer("192.168.50.2", "", "14"), observed, tmp_path / "known")
    assert not (tmp_path / "known").exists()
