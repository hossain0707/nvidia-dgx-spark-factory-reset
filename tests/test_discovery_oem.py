from dataclasses import replace
import ipaddress
import json

import pytest

from spark_recovery.config import RecoveryError
from spark_recovery.discovery import (Adapter, Peer, candidates, choose_adapter, mac_adapters,
                                      network_plan, target_mac, windows_adapters)
from spark_recovery.oem import detect_oem, ethernet_profile


def test_select_dedicated_wired_link_never_wifi_or_gateway():
    wifi = Adapter("3", "Wi-Fi", True, False)
    wired = Adapter("14", "Ethernet", True, True)
    internet = Adapter("6", "Office", True, True, gateways=["10.0.0.1"])
    assert choose_adapter([wifi, internet, wired]) == wired
    for choices in ([wifi], [internet], [wired, replace(wired, id="20")]):
        with pytest.raises(RecoveryError):
            choose_adapter(choices)


def test_windows_singleton_data_and_wireless_detection():
    data = {"adapters": [{"ifIndex": 14, "Name": "Wired", "Status": "Up", "ifType": None, "InterfaceType": 6, "NdisPhysicalMedium": 14},
                         {"ifIndex": 3, "Name": "Wireless", "Status": "Up", "ifType": 71, "NdisPhysicalMedium": 9}],
            "addresses": {"InterfaceIndex": 14, "IPAddress": "192.168.50.1", "PrefixLength": 24},
            "neighbors": [{"InterfaceIndex": 14, "IPAddress": "192.168.50.7", "LinkLayerAddress": "02-00-00-00-00-07",
                           "State": "Reachable"}, {"InterfaceIndex": 14, "IPAddress": "192.168.50.8",
                                                   "LinkLayerAddress": "00-00-00-00-00-00", "State": 0}], "ics": "Running"}
    wired, wireless = windows_adapters(data)
    assert wired.wired and wired.ics_running and not wireless.wired
    assert candidates(wired) == ["192.168.50.7"]


def test_discovery_bounded_and_excludes_local_gateway_multicast():
    adapter = Adapter("14", "Wired", True, True, ["10.55.1.1/16", "fe80::1%14/64"], neighbors=[
        {"address": "10.55.1.1", "mac": ""}, {"address": "224.0.0.1", "mac": ""},
        {"address": "fe80::2", "mac": ""}, {"address": "10.55.1.8", "mac": ""},
        {"address": "192.168.99.9", "mac": ""}])
    passive = candidates(adapter)
    assert passive == ["10.55.1.8", "fe80::2%14"]
    active = candidates(adapter, active=True)
    assert len(active) <= 256 and "10.55.1.1" not in active and "10.55.9.1" not in active
    with pytest.raises(RecoveryError):
        candidates(replace(adapter, gateways=["10.55.1.254"]), active=True)


def test_reuse_real_network_and_allocate_without_route_collision():
    adapter = Adapter("en7", "USB Ethernet", True, True, ["192.168.42.10/24"])
    peer = Peer("192.168.42.88", "02:00:00:00:00:01", "en7")
    session = network_plan(adapter, peer, peer.mac)
    assert (session.server, session.client, session.interface) == ("192.168.42.10", "192.168.42.88", "en7")
    reserved = replace(adapter, neighbors=[{"address": "192.168.42.1", "mac": ""}])
    ipv6_peer = replace(peer, address="fe80::2%en7")
    reused = network_plan(reserved, ipv6_peer, peer.mac)
    assert reused.server == "192.168.42.10" and reused.client == "192.168.42.2"
    ipv6 = replace(adapter, addresses=["fe80::1%en7/64"], routes=["0.0.0.0/0", "172.16.0.0/12"])
    fallback = network_plan(ipv6, replace(peer, address="fe80::2%en7"), peer.mac)
    assert ipaddress.ip_address(fallback.server) in ipaddress.ip_network("10.0.0.0/8")
    ipv6.routes += ["10.0.0.0/8", "192.168.0.0/16"]
    with pytest.raises(RecoveryError, match="collision"):
        network_plan(ipv6, replace(peer, address="fe80::2%en7"), peer.mac)
    ipv6.routes = ["172.16.0.0/16", "10.0.0.0/8", "192.168.0.0/16"]
    session = network_plan(ipv6, replace(peer, address="fe80::2%en7"), peer.mac)
    assert not ipaddress.ip_network(f"{session.server}/24", strict=False).overlaps(ipaddress.ip_network("172.16.0.0/16"))


def test_mac_port_routes_and_neighbor_parsing():
    ports = "Hardware Port: Wi-Fi\nDevice: en0\nHardware Port: USB Ethernet\nDevice: en7\n"
    configs = {"en0": "status: active", "en7": "inet 192.168.42.1 netmask 0xffffff00\nstatus: active"}
    adapters = mac_adapters(ports, configs, "default 192.168.1.1 UGScg en0\n172.16/12 link#7 UCS en7",
        "? (192.168.42.3) at 2:0:0:0:0:3 on en7 ifscope [ethernet]", "fe80::3%en7 02:00:00:00:00:03 en7 23h R")
    assert not adapters[0].wired and adapters[1].wired
    assert adapters[0].gateways == ["192.168.1.1"]
    assert adapters[1].routes == ["172.16.0.0/12"]
    assert adapters[1].neighbors[0]["mac"] == "02:00:00:00:00:03"


@pytest.mark.parametrize(("board", "expected"), [(["ASUSTeK", "ASUS Ascent GX10"], "asus-gx10"),
    (["Dell Inc.", "Pro Max with GB10 FCM1253"], "dell-gb10"),
    (["HP", "ZGX Nano G1n"], "hp-zgx"), (["Lenovo", "ThinkStation PGX"], "lenovo-gb10"),
    (["MSI", "EdgeXpert"], "msi-edge"), (["NVIDIA DGX Spark Founders Edition"], "nvidia-founders")])
def test_oem_specific_routing_and_no_cross_flash(board, expected):
    inventory = {"machine": "aarch64", "board": board}
    profile = detect_oem(inventory)
    assert profile.id == expected
    if expected != "asus-gx10":
        assert profile.image_profile is None
        with pytest.raises(RecoveryError):
            ethernet_profile(inventory)


def test_unknown_conflicting_or_non_arm_device_stops():
    for machine, board in (("x86_64", ["ASUS GX10"]), ("aarch64", ["NVIDIA generic board"]),
                           ("arm64", ["ASUS GX10 Dell GB10"]), ("arm64", ["Dell laptop"])):
        with pytest.raises(RecoveryError):
            detect_oem({"machine": machine, "board": board})


def test_authenticated_address_to_mac_binding(prepared, session):
    inventory = json.loads((prepared / "target.json").read_text())
    inventory["addresses"] = {"en0": ["fe80::123/64"]}
    peer = Peer("fe80::123%en7", session.target_mac, "en7")
    assert target_mac(inventory, peer) == session.target_mac
    assert target_mac(inventory, replace(peer, mac="")) == session.target_mac
    with pytest.raises(RecoveryError):
        target_mac(inventory, replace(peer, mac="02:00:00:00:00:02"))
