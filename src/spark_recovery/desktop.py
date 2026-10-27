"""MKD Spark Recovery desktop. Optional Qt frontend over the existing recovery API."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from . import __version__
from .cli import ssh_command
from .config import RecoveryError, Session, atomic_json
from .connection import DeviceConnection, configured_user, key_status, trust_key
from .discovery import choose_adapter, collect_adapters, discover, network_plan, refresh_link_local, target_mac
from .desktop_service import launch, run_service
from .images import prepare
from .locking import ServiceLock
from .network import command, powershell
from .oem import detect_oem
from .profiles import DELL_WW, DELL_US, image_profile
from .safety import attest, authorize, image_manifest, validate_inventory, write_menu
from .timing import RecoveryTiming, duration


def inspect_ssh(destination: str):
    source = Path(__file__).with_name("target.py").read_text(encoding="utf-8")
    args = ssh_command(destination, source)
    args[1:1] = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=35)
    if result.returncode:
        raise RecoveryError("SSH inspection failed. Configure a trusted host key and SSH key first, or import local "
                            "inventory JSON. Passwords are not collected by this app. " + result.stderr.strip())
    return json.loads(result.stdout)


def interfaces():
    if sys.platform == "win32":
        value = powershell("@(Get-NetAdapter -Physical | Select-Object ifIndex,Name,InterfaceDescription,Status) "
                           "| ConvertTo-Json -Compress")
        data = json.loads(value) or []
        if isinstance(data, dict):
            data = [data]
        return [(str(item["ifIndex"]), f'{item["Name"]} · {item["Status"]} · index {item["ifIndex"]}')
                for item in data]
    if sys.platform == "darwin":
        output = command(["/usr/sbin/networksetup", "-listallhardwareports"])
        result, label = [], ""
        for line in output.splitlines():
            if line.startswith("Hardware Port: "):
                label = line.split(": ", 1)[1]
            if line.startswith("Device: "):
                name = line.split(": ", 1)[1]
                result.append((name, f"{label} · {name}"))
        return result
    return [(path.name, path.name) for path in Path("/sys/class/net").glob("*") if path.name != "lo"]


def inventory_disks(inventory, session):
    if (not isinstance(inventory, dict) or not isinstance(inventory.get("disks"), list)
            or not isinstance(inventory.get("interfaces"), dict) or not isinstance(inventory.get("board"), list)
            or not all(isinstance(value, str) for value in inventory["interfaces"].values())
            or not all(isinstance(value, str) for value in inventory["board"])):
        raise RecoveryError("Invalid target inventory structure; import output from target.py --json")
    candidates = []
    for item in inventory["disks"]:
        if (not isinstance(item, dict) or not isinstance(item.get("name"), str)
                or not isinstance(item.get("type"), str) or not isinstance(item.get("serial") or "", str)):
            raise RecoveryError("Invalid disk identity in target inventory")
        if item["type"] == "disk" and item["name"].startswith("nvme") and item.get("serial"):
            if not re.fullmatch(r"[ -~]{1,128}", item["serial"].strip()):
                raise RecoveryError("Disk serial contains unsupported control/formatting characters")
            validate_inventory(inventory, session, "/dev/" + item["name"], item["serial"].strip())
            candidates.append(item)
    if not candidates:
        raise RecoveryError("Inventory contains no approved whole NVMe SSD with a serial")
    return candidates


def main(argv=None):
    parser = argparse.ArgumentParser(description="MKD Spark Recovery desktop")
    parser.add_argument("--desktop-service", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--operation", choices=["serve", "restore"], default="serve", help=argparse.SUPPRESS)
    parser.add_argument("--stop-ics", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--smoke-test", action="store_true", help="Open and close the UI without network changes")
    args = parser.parse_args(argv)
    if args.desktop_service:
        return run_service(args.desktop_service, args.operation, stop_ics=args.stop_ics)
    try:
        from PySide6.QtCore import QStandardPaths, QThread, QTimer, Qt, Signal, QUrl
        from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontDatabase, QIcon, QPainter, QPixmap
        from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame,
                                      QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QInputDialog,
                                      QProgressBar, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
                                      QDialog, QDialogButtonBox, QGroupBox)
    except ImportError:
        print('Install the desktop extra: python -m pip install ".[desktop]"', file=sys.stderr)
        return 2

    class Job(QThread):
        done = Signal(object)
        failed = Signal(str)
        progress = Signal(object)

        def __init__(self, function):
            super().__init__()
            self.function = function

        def run(self):
            try:
                self.done.emit(self.function())
            except Exception as error:
                self.failed.emit(str(error))

    class Window(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle(f"MKD Spark Recovery · {__version__} Preview")
            self.resize(1120, 810)
            self.setMinimumSize(960, 710)
            self.root = None
            self.session = None
            self.timing = None
            self.job = None
            self.helper = None
            self.pending = False
            self.launch_at = 0.0
            self.services = False
            self.seen_phase = ""
            self.current_step = 0
            self.adapters = []
            self.peers = []
            self.device = None
            self.detected_adapter = None
            self.auto_flow = False
            self.reboot_offered = False
            self.follow_up = None
            self.prompted_peers = set()
            self.discovery_due = time.monotonic() + 20
            self.periodic_discovery = False
            self.data_dir = Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation))
            outer = QWidget()
            self.setCentralWidget(outer)
            row = QHBoxLayout(outer)
            row.setContentsMargins(0, 0, 0, 0)
            rail = QFrame()
            rail.setObjectName("rail")
            rail.setFixedWidth(225)
            navigation = QVBoxLayout(rail)
            navigation.setContentsMargins(22, 28, 22, 24)
            brand = QLabel("MKD  /  RECOVERY")
            brand.setObjectName("brand")
            navigation.addWidget(brand)
            navigation.addWidget(QLabel("ETHERNET EDITION"))
            navigation.addSpacing(35)
            self.steps = []
            for index, title in enumerate(("Connect & identify", "Prepare recovery", "Verify network boot",
                                           "Authorize installation", "Measure & finish")):
                button = QPushButton(f"0{index + 1}   {title.replace('&', '&&')}")
                button.setObjectName("nav")
                button.clicked.connect(lambda checked=False, i=index: self.show_step(i))
                navigation.addWidget(button)
                self.steps.append(button)
            navigation.addStretch()
            self.clock_label = QLabel("00:00:00")
            self.clock_label.setObjectName("clock")
            navigation.addWidget(self.clock_label)
            navigation.addWidget(QLabel("Elapsed since session creation\nIncludes preparation and waiting"))
            navigation.addSpacing(20)
            navigation.addWidget(QLabel("OEM identity checks\nASUS / Dell PXE previews\nPreview · No telemetry"))
            row.addWidget(rail)
            content = QVBoxLayout()
            content.setContentsMargins(30, 26, 30, 24)
            eyebrow = QLabel("SPARK RECOVERY  /  DESKTOP PREVIEW")
            eyebrow.setObjectName("eyebrow")
            content.addWidget(eyebrow)
            self.heading = QLabel("A guided path to a fresh start.")
            self.heading.setObjectName("heading")
            content.addWidget(self.heading)
            subtitle = QLabel("Detect your device. Use its manufacturer's recovery path. Confirm before erasing.")
            subtitle.setWordWrap(True)
            content.addWidget(subtitle)
            self.pages = QStackedWidget()
            content.addWidget(self.pages, 1)
            self.message = QLabel("Start by choosing the host's dedicated Ethernet adapter.")
            self.message.setWordWrap(True)
            self.message.setObjectName("status")
            content.addWidget(self.message)
            self.activity = QProgressBar()
            self.activity.setRange(0, 1)
            self.activity.setValue(0)
            self.activity.setTextVisible(False)
            self.activity.setFixedHeight(4)
            content.addWidget(self.activity)
            row.addLayout(content, 1)
            self.setup_page()
            self.media_page()
            self.verify_page()
            self.install_page()
            self.finish_page()
            for label in self.findChildren(QLabel):
                label.setTextFormat(Qt.TextFormat.PlainText)
            self.show_step(0)
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.tick)
            self.timer.start(500)
            self.buttons()

        def page(self, title, description):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            frame = QWidget()
            layout = QVBoxLayout(frame)
            layout.setContentsMargins(0, 24, 6, 12)
            label = QLabel(title)
            label.setObjectName("pageTitle")
            layout.addWidget(label)
            label = QLabel(description)
            label.setWordWrap(True)
            layout.addWidget(label)
            layout.addSpacing(12)
            scroll.setWidget(frame)
            self.pages.addWidget(scroll)
            return layout

        def button(self, text, callback, layout, *, danger=False):
            button = QPushButton(text.replace("&", "&&"))
            button.setMinimumHeight(40)
            if danger:
                button.setObjectName("danger")
            button.clicked.connect(callback)
            layout.addWidget(button)
            return button

        def setup_page(self):
            layout = self.page("01 / Detect the connected device", "Connect your Spark-class device directly by Ethernet. "
                               "The app detects the link, address, MAC, manufacturer and SSD after authenticated access. "
                               "Credentials and firmware access cannot be guessed or bypassed.")
            self.discovery_label = QLabel("Waiting to detect your wired connection…")
            self.discovery_label.setWordWrap(True)
            layout.addWidget(self.discovery_label)
            self.detect = self.button("Detect connected device", self.auto_discover, layout)
            self.isolated = QCheckBox("This is a direct cable / isolated link. Allow a bounded search if needed.")
            self.isolated.toggled.connect(lambda checked: self.after_job(self.auto_discover)
                if checked and not self.root else None)
            layout.addWidget(self.isolated)
            self.peer_list = QComboBox()
            layout.addWidget(self.peer_list)
            self.connect_button = self.button("Connect securely and auto-fill recovery details", self.connect_discovered, layout)
            self.brand_label = QLabel("A network response is not proof of device brand. Identity is checked after login.")
            self.brand_label.setWordWrap(True)
            layout.addWidget(self.brand_label)
            self.advanced = QGroupBox("Advanced / manual network settings")
            advanced_layout = QVBoxLayout(self.advanced)
            form = QFormLayout()
            form.setVerticalSpacing(12)
            self.interface = QComboBox()
            self.interface.setEditable(True)
            form.addRow("Host Ethernet adapter", self.interface)
            self.server = QLineEdit()
            self.server.setPlaceholderText("Detected automatically")
            self.client = QLineEdit()
            self.client.setPlaceholderText("Detected or allocated automatically")
            self.mac = QLineEdit()
            self.mac.setPlaceholderText("Target Ethernet MAC from firmware, e.g. 02:00:00:00:00:01")
            form.addRow("Host IPv4 /24", self.server)
            form.addRow("target recovery IPv4", self.client)
            form.addRow("target Ethernet MAC", self.mac)
            advanced_layout.addLayout(form)
            self.refresh = self.button("Refresh adapter list", self.refresh_interfaces, advanced_layout)
            self.backup = QCheckBox("I have backed up the target and am using an isolated Ethernet link.")
            advanced_layout.addWidget(self.backup)
            self.create = self.button("Create manual recovery session →", self.create_session, advanced_layout)
            toggle = QPushButton("Show / hide advanced settings")
            toggle.clicked.connect(lambda: self.advanced.setVisible(not self.advanced.isVisible()))
            layout.addWidget(toggle)
            layout.addWidget(self.advanced)
            self.advanced.hide()
            self.resume = self.button("Open an existing desktop session…", self.open_session, layout)
            self.location = QLabel("Sessions and timing reports stay on this computer.")
            self.location.setWordWrap(True)
            layout.addWidget(self.location)
            layout.addStretch()

        def media_page(self):
            layout = self.page("02 / Prepare & inspect", "Download pinned official OEM media (~10 GB) or choose an "
                               "existing ISO. Preparation verifies hashes and creates a diagnostic-only boot menu.")
            self.iso = QLineEdit()
            self.iso.setPlaceholderText("Leave empty to download the selected manufacturer's pinned image")
            layout.addWidget(self.iso)
            self.browse = self.button("Choose existing matching OEM ISO…", self.choose_iso, layout)
            self.vendor = QCheckBox("I accept the vendor download terms and have at least 24 GiB free disk space.")
            self.vendor.toggled.connect(lambda checked: self.after_job(self.prepare_media)
                if checked and self.auto_flow and self.root and not (self.root / "image.json").exists() else None)
            layout.addWidget(self.vendor)
            self.prepare_button = self.button("Verify and prepare recovery image", self.prepare_media, layout)
            layout.addSpacing(12)
            self.ssh = QLineEdit()
            self.ssh.setPlaceholderText("Existing SSH key connection: operator@current-target-address")
            layout.addWidget(self.ssh)
            self.inspect_button = self.button("Inspect target over trusted SSH", self.inspect_target, layout)
            self.import_button = self.button("Import target inventory JSON…", self.import_target, layout)
            self.inventory_label = QLabel("Inventory required: matching manufacturer/model, target MAC, whole SSD and serial.\n"
                                           "Without SSH, run target.py --json on the target and import the saved file.")
            self.inventory_label.setWordWrap(True)
            layout.addWidget(self.inventory_label)
            layout.addStretch()

        def verify_page(self):
            layout = self.page("03 / Prove the recovery path", "Starting the server requests host administrator "
                               "permission. It configures only the selected recovery interface; no target reboot is automatic.")
            self.ics = QCheckBox("Windows: allow temporary interruption of Internet Connection Sharing (ICS).")
            self.ics.setVisible(sys.platform == "win32")
            layout.addWidget(self.ics)
            self.start = self.button("Start Ethernet recovery services", self.start_services, layout)
            self.reboot_button = self.button("Restart the verified target into diagnostic PXE…", self.reboot_target, layout)
            instructions = QPlainTextEdit("1. Wait for 'services ready' below.\n"
                "2. On the target, reboot into its UEFI PXE IPv4 entry.\n"
                "3. Select VERIFY ONLY (STOP is the default).\n"
                "4. At (initramfs), run one command at a time:\n\n"
                "mount | grep /cdrom\nls /cdrom/oemdata\nls /dev/nvme*\n\n"
                "Expect: /cdrom is ISO9660 and ro; oem-iso-cfg.sh and debs exist;\n"
                "no /dev/nvme* devices exist. Do not type exit to install.")
            instructions.setReadOnly(True)
            instructions.setMinimumHeight(225)
            self.diagnostic_instructions = instructions
            layout.addWidget(instructions)
            self.checks = [QCheckBox(text) for text in ("ISO is mounted read-only at /cdrom.",
                            "Both OEM files/directories are visible.", "No NVMe devices are exposed.")]
            for check in self.checks:
                layout.addWidget(check)
            self.attest_button = self.button("Record diagnostic checks →", self.record_diagnostic, layout)
            self.manual_check_button = self.button("Show manual diagnostic fallback", self.manual_diagnostic, layout)
            layout.addStretch()

        def install_page(self):
            layout = self.page("04 / Authorize the exact SSD", "This step enables the erase menu. Installation still "
                               "requires a separate console selection on the target. All existing target SSD data will be lost.")
            self.disk = QComboBox()
            layout.addWidget(self.disk)
            self.erase_label = QLabel("Record the diagnostic checks first.")
            self.erase_label.setWordWrap(True)
            layout.addWidget(self.erase_label)
            self.erase_phrase = QLineEdit()
            self.erase_phrase.setPlaceholderText("Type the exact confirmation phrase shown above")
            layout.addWidget(self.erase_phrase)
            self.authorize_button = self.button("Enable cancel-first erase menu", self.enable_install, layout, danger=True)
            instructions = QLabel("After authorization: reboot from the diagnostic shell using reboot -f, then select "
                "PXE IPv4 again. In GRUB select ERASE SSD → CONFIRM ERASE.\n\n"
                "Click 'Installation started' immediately when you select that final console confirmation. "
                "The app cannot inspect the target's disk-writing progress remotely.")
            instructions.setWordWrap(True)
            layout.addWidget(instructions)
            self.install_started = self.button("I selected CONFIRM ERASE — start installation timer", self.mark_install, layout,
                                               danger=True)
            self.revoke = self.button("Remove erase option from future boots", self.revoke_install, layout)
            layout.addStretch()

        def finish_page(self):
            layout = self.page("05 / Measure & finish", "Keep power and Ethernet connected until installation completes. "
                               "No estimated percentage is shown for disk installation: the target console is authoritative.")
            self.install_clock = QLabel("Installation interval: not started")
            self.install_clock.setObjectName("pageTitle")
            layout.addWidget(self.install_clock)
            self.completed = self.button("target has reached fresh first-run setup / desktop", self.complete, layout)
            self.failed = self.button("Record an installation failure", self.failure, layout)
            self.stop = self.button("Stop services & restore host networking", self.stop_services, layout)
            self.restore = self.button("Restore networking after a stopped/crashed helper", self.restore_network, layout)
            self.export = self.button("Export timing report…", self.export_report, layout)
            self.results = QPlainTextEdit()
            self.results.setReadOnly(True)
            self.results.setMinimumHeight(155)
            layout.addWidget(self.results)
            layout.addStretch()

        def show_step(self, index):
            self.current_step = index
            self.pages.setCurrentIndex(index)
            for i, button in enumerate(self.steps):
                button.setProperty("active", i == index)
                button.style().unpolish(button)
                button.style().polish(button)

        def notice(self, text):
            self.message.setText(text)

        def problem(self, text):
            self.notice("Stopped: " + text)
            QMessageBox.warning(self, "Action stopped", text)

        def work(self, function, done, error=None):
            if self.job:
                return
            self.job = Job(function)
            self.job.done.connect(done)
            self.job.failed.connect(error or self.problem)
            self.job.progress.connect(self.show_progress)
            self.job.finished.connect(self.job_finished)
            self.activity.setRange(0, 0)
            self.buttons()
            self.job.start()

        def show_progress(self, value):
            received, total = value.get("received", 0), value.get("total", 0)
            message = value["stage"]
            if total > 0:
                self.activity.setRange(0, 1000)
                self.activity.setValue(min(1000, int(received / total * 1000)))
                message += f" · {received / 10**9:.2f} / {total / 10**9:.2f} GB"
            else:
                self.activity.setRange(0, 0)
            self.notice(message)

        def job_finished(self):
            self.job.deleteLater()
            self.job = None
            self.activity.setRange(0, 1)
            self.buttons()
            callback, self.follow_up = self.follow_up, None
            if callback:
                QTimer.singleShot(0, callback)

        def after_job(self, callback):
            if self.job:
                self.follow_up = callback
            else:
                QTimer.singleShot(0, callback)

        def consent(self, title, text, checklist, *, phrase="", password=False):
            dialog = QDialog(self)
            dialog.setWindowTitle(title)
            dialog.setMinimumWidth(620)
            layout = QVBoxLayout(dialog)
            label = QLabel(text)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(label)
            checks = [QCheckBox(item) for item in checklist]
            for check in checks:
                layout.addWidget(check)
            field = QLineEdit()
            if phrase:
                field.setPlaceholderText("Type exactly: " + phrase)
                layout.addWidget(field)
            secret = QLineEdit()
            secret.setEchoMode(QLineEdit.EchoMode.Password)
            if password:
                secret.setPlaceholderText("Target sudo password (optional if sudo is already authorized)")
                layout.addWidget(secret)
            actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            actions.button(QDialogButtonBox.StandardButton.Ok).setText("I understand — continue")
            actions.button(QDialogButtonBox.StandardButton.Ok).setDefault(False)
            actions.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
            actions.accepted.connect(dialog.accept)
            actions.rejected.connect(dialog.reject)
            layout.addWidget(actions)
            def enable():
                actions.button(QDialogButtonBox.StandardButton.Ok).setEnabled(all(item.isChecked() for item in checks)
                    and (not phrase or field.text() == phrase))
            for check in checks:
                check.toggled.connect(enable)
            field.textChanged.connect(enable)
            enable()
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            value = secret.text() if accepted and password else None
            secret.clear()
            dialog.deleteLater()
            return accepted, value

        def auto_discover(self):
            if self.root or self.job:
                return
            isolated = self.isolated.isChecked()
            if self.periodic_discovery:
                isolated = False
            self.periodic_discovery = False
            self.discovery_due = time.monotonic() + 20
            selected = self.interface.currentData()
            def scan():
                adapters = collect_adapters()
                if selected:
                    adapter = next((item for item in adapters if item.id == selected), None)
                    if adapter is None or not adapter.wired or not adapter.up:
                        raise RecoveryError("Selected adapter is not an active wired link")
                else:
                    try:
                        adapter = choose_adapter(adapters)
                    except RecoveryError:
                        return adapters, None, []
                refresh_link_local(adapter)
                refreshed = collect_adapters()
                adapter = next((item for item in refreshed if item.id == adapter.id), adapter)
                return adapters, adapter, discover(adapter, active=isolated)
            def found(value):
                adapters, adapter, peers = value
                self.adapters, self.detected_adapter, self.peers = adapters, adapter, peers
                self.interface.clear()
                for item in adapters:
                    if item.wired:
                        self.interface.addItem(f"{item.name} · {item.id}", item.id)
                self.interface.setCurrentIndex(self.interface.findData(adapter.id) if adapter else -1)
                self.peer_list.clear()
                for peer in peers:
                    self.peer_list.addItem(peer.address + " · SSH device (identity not yet verified)")
                if adapter is None:
                    self.advanced.show()
                    self.discovery_label.setText("Multiple or no dedicated links found. Choose the connected wired adapter below, then detect again.")
                    return
                self.discovery_label.setText(f"Ethernet: {adapter.name} · {len(peers)} SSH endpoint(s) found.")
                self.notice("Select a device and connect to verify its model." if peers else
                    "No accessible SSH endpoint found. Confirm the isolated-link checkbox and retry. "
                    "If SSH is disabled or the device has no usable network address, enable it on the target first.")
                if len(peers) == 1 and peers[0].address not in self.prompted_peers:
                    self.prompted_peers.add(peers[0].address)
                    self.after_job(self.connect_discovered)
            self.notice("Detecting the wired link and checking bounded, interface-scoped SSH endpoints…")
            self.work(scan, found, error=self.notice)

        def connect_discovered(self):
            if self.job or self.root or not self.peers:
                return
            peer = self.peers[self.peer_list.currentIndex()]
            known_hosts = self.data_dir / "known_hosts"
            def inspect_trust():
                return key_status(peer, self.detected_adapter, known_hosts), configured_user(peer.address)
            def trust_and_login(value):
                observed, configured = value
                if observed["state"] != "trusted":
                    changed = observed["state"] == "changed"
                    approved, _ = self.consent("Verify device SSH identity", f"Device address: {peer.address}\n"
                        f"SSH key: {observed['kind']}\nFingerprint: {observed['fingerprint']}\n\n" +
                        ("WARNING: this key differs from a previously trusted key. A reset or an impostor can cause this.\n" if changed else "This is the first verified connection to this key.\n") +
                        "Compare the fingerprint on the physical target (ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub "
                        "for an Ed25519 key). A network address alone does not establish identity. "
                        "This app will record trust locally without changing global OpenSSH keys.",
                        ["I compared this fingerprint on the intended physical device."],
                        phrase="TRUST CHANGED KEY" if changed else "")
                    if not approved:
                        return
                    try:
                        trust_key(peer, observed, known_hosts, replace=changed)
                    except (RecoveryError, OSError, ValueError) as error:
                        self.problem(str(error))
                        return
                dialog = QDialog(self)
                dialog.setWindowTitle("Sign in to the detected device")
                layout = QFormLayout(dialog)
                label = QLabel("Use the account on the Spark, not this computer's account. "
                               "Existing SSH keys are tried first. Passwords are not saved to disk.")
                label.setWordWrap(True)
                layout.addRow(label)
                username, password = QLineEdit(configured), QLineEdit()
                password.setEchoMode(QLineEdit.EchoMode.Password)
                password.setPlaceholderText("Optional when an existing SSH key works")
                layout.addRow("Device account", username)
                layout.addRow("Device password", password)
                actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
                actions.accepted.connect(dialog.accept)
                actions.rejected.connect(dialog.reject)
                layout.addRow(actions)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    password.clear()
                    dialog.deleteLater()
                    return
                account, secret = username.text().strip(), password.text()
                password.clear()
                dialog.deleteLater()
                def authenticate():
                    device = DeviceConnection(peer, self.detected_adapter, account, known_hosts, secret)
                    try:
                        return device, device.inventory()
                    except BaseException:
                        device.close()
                        raise
                # The trust-inspection worker's finished signal is queued after its result.
                self.after_job(lambda: self.work(authenticate, self.accept_detected))
            self.work(inspect_trust, trust_and_login)

        def accept_detected(self, value):
            device, inventory = value
            def accept():
                profile = detect_oem(inventory)
                self.brand_label.setText(f"Verified model: {profile.manufacturer} {profile.model}\n{profile.explanation}")
                if not profile.image_profile:
                    device.close()
                    box = QMessageBox(self)
                    box.setWindowTitle(profile.manufacturer + " recovery profile")
                    box.setText(profile.explanation + "\n\nThis device will not receive an ASUS image. "
                                "Open the manufacturer's own recovery instructions?")
                    box.setStandardButtons(QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Cancel)
                    if box.exec() == QMessageBox.StandardButton.Open:
                        QDesktopServices.openUrl(QUrl(profile.recovery_url))
                    return
                selected_image = profile.image_profile
                if profile.id == "dell-gb10":
                    region, accepted = QInputDialog.getItem(self, "Dell recovery region",
                        "Choose the recovery region matching this Dell FCM1253 device.\n"
                        "Check Dell's support record if unsure; do not infer it from this PC's location.\n"
                        "These images were inspected offline. A physical Dell Ethernet reset is not yet qualified.",
                        ["Cancel — verify the device's region first", DELL_WW["region"], DELL_US["region"]], 0, False)
                    if not accepted or region not in (DELL_WW["region"], DELL_US["region"]):
                        device.close()
                        return
                    approved, _ = self.consent("Dell Ethernet preview",
                        profile.explanation,
                        ["I verified this device is Dell FCM1253 and selected its matching recovery region.",
                         "I understand Dell documents USB recovery and this Ethernet preview lacks a physical Dell reset test."])
                    if not approved:
                        device.close()
                        return
                    selected_image = DELL_US["id"] if region == DELL_US["region"] else DELL_WW["id"]
                mac = target_mac(inventory, device.peer)
                session = network_plan(self.detected_adapter, device.peer, mac, profile=selected_image)
                inventory_disks(inventory, session)
                root = self.data_dir / "sessions" / uuid.uuid4().hex
                root.mkdir(parents=True, mode=0o700)
                session.save(root)
                self.root, self.session, self.device = root, session, device
                self.auto_flow = True
                self.erase_phrase.hide()
                self.server.setText(session.server)
                self.client.setText(session.client)
                self.mac.setText(session.target_mac)
                self.timing = RecoveryTiming(root / "timing.json")
                self.timing.mark("session_created")
                self.location.setText(str(root))
                self.save_inventory(inventory)
                self.iso.clear()
                for manifest in sorted((self.data_dir / "sessions").glob("*/image.json"), key=lambda item: item.stat().st_mtime,
                                       reverse=True):
                    try:
                        image = json.loads(manifest.read_text(encoding="utf-8"))
                        if image.get("iso_sha256") == image_profile(session.profile)["iso_sha256"] and Path(image["iso_path"]).is_file():
                            self.iso.setText(image["iso_path"])
                            break
                    except (OSError, ValueError, KeyError):
                        continue
                self.show_step(1)
                self.notice("Device, MAC, SSD and network plan filled from authenticated evidence. "
                            "Accept the vendor terms, then preparation and safe host setup will be guided automatically.")
                self.buttons()
            try:
                accept()
            except Exception as error:
                device.close()
                self.problem(str(error))

        def reboot_target(self):
            if not self.device or not self.services:
                self.problem("A live authenticated target connection and ready host services are required")
                return
            approved, secret = self.consent("Restart target for diagnostic recovery",
                f"Restart {self.session.target_mac} into its matching UEFI PXE IPv4 entry.\n"
                "This stops its running applications and workloads. The diagnostic boot blocks NVMe and does not install. "
                "If firmware/Secure Boot prevents PXE, the app cannot bypass that setting.",
                ["I saved work and can interrupt workloads on this exact target.",
                 "I understand this is a diagnostic reboot; no SSD erase is authorized."], password=True)
            if approved:
                self.work(lambda: self.device.boot(self.session.target_mac, sudo_password=secret or None),
                          lambda value: self.notice("Diagnostic reboot requested. Watch the target's PXE/VERIFY ONLY screen."))

        def guarded(self, function):
            try:
                return function()
            except (RecoveryError, OSError, ValueError, KeyError, TypeError) as error:
                self.problem(str(error))
                return None

        def mark(self, name, evidence="host_observed"):
            if self.timing and self.timing.data["outcome"] == "in_progress":
                if name not in {item["name"] for item in self.timing.data["events"]}:
                    self.timing.mark(name, evidence=evidence)

        def refresh_interfaces(self):
            def display(items):
                self.interface.clear()
                for value, label in items:
                    self.interface.addItem(label, value)
                self.notice("Choose the dedicated Ethernet adapter. Do not select Wi-Fi or a shared office LAN.")
            self.work(interfaces, display)

        def create_session(self):
            def create():
                if not self.backup.isChecked():
                    raise RecoveryError("Confirm backup and isolated Ethernet before creating the session")
                adapter = self.interface.currentData()
                if self.interface.currentText() != self.interface.itemText(self.interface.currentIndex()):
                    adapter = self.interface.currentText().strip()
                session = Session(self.server.text().strip(), self.client.text().strip(), self.mac.text().strip(),
                                  adapter or self.interface.currentText().strip())
                if sys.platform == "win32" and not session.interface.isdecimal():
                    raise RecoveryError("Choose a Windows adapter or enter its numeric interface index")
                root = self.data_dir / "sessions" / uuid.uuid4().hex
                root.mkdir(parents=True, mode=0o700)
                session.save(root)
                self.root, self.session = root, session
                self.auto_flow = (root / "diagnostic-agent.json").is_file()
                self.erase_phrase.setVisible(not self.auto_flow)
                self.timing = RecoveryTiming(root / "timing.json")
                self.timing.mark("session_created")
                self.location.setText(str(root))
                self.notice("Safe session created. No reboot or erase performed.")
                self.show_step(1)
                self.buttons()
            self.guarded(create)

        def open_session(self):
            path = QFileDialog.getExistingDirectory(self, "Open session folder", str(self.data_dir / "sessions"))
            if not path:
                return
            def load():
                root = Path(path)
                session = Session.load(root)
                self.root, self.session = root, session
                self.auto_flow = (root / "diagnostic-agent.json").is_file()
                self.erase_phrase.setVisible(not self.auto_flow)
                self.timing = RecoveryTiming.load(root / "timing.json") if (root / "timing.json").exists() else None
                self.location.setText(str(root))
                self.load_disks()
                self.seen_phase = ""
                self.show_step(4)
                self.notice("Session reopened. Previous active timing is marked interrupted; no duration is invented.")
                self.buttons()
            self.guarded(load)

        def choose_iso(self):
            path, _ = QFileDialog.getOpenFileName(self, "Official matching OEM recovery ISO", "", "ISO images (*.iso)")
            if path:
                self.iso.setText(path)

        def prepare_media(self):
            if not self.vendor.isChecked():
                self.problem("Accept the vendor terms and storage requirement before preparation")
                return
            self.mark("preparation_started")
            self.notice("Downloading / hashing / inspecting official media. This may take several minutes. No disk erase.")
            iso = Path(self.iso.text().strip()) if self.iso.text().strip() else None
            def operation():
                with ServiceLock(self.root):
                    cache = self.data_dir / "cache" / self.session.profile
                    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with ServiceLock(cache):
                        image = prepare(self.root, self.session, iso=iso, progress=self.job.progress.emit, cache_dir=cache)
                    if self.auto_flow:
                        from .diagnostic_agent import configure
                        self.job.progress.emit({"stage": "Preparing a read-only temporary RAM diagnostic observer"})
                        configure(self.root, self.session)
                    return image
            def done(value):
                self.mark("preparation_complete")
                self.notice("Official image and boot files verified. Inspect the target before starting services.")
                if self.auto_flow:
                    self.diagnostic_instructions.setPlainText("The temporary RAM observer will report diagnostic checks "
                        "automatically.\n\nAfter the consented reboot, VERIFY ONLY starts after five seconds. "
                        "It blocks NVMe and cannot start installation.\n\nThe app waits for a session-bound report of: "
                        "read-only ISO, matching OEM content, hidden NVMe and the matching MAC.\n\n"
                        "No report or any failed check keeps installation blocked. Firmware/secure-boot failures "
                        "still need attention at the target console.")
                    for check in self.checks:
                        check.hide()
                    self.attest_button.hide()
                    self.show_step(2)
                    self.after_job(self.start_services)
            self.work(operation, done)

        def manual_diagnostic(self):
            self.diagnostic_instructions.setPlainText("Manual fallback: select VERIFY ONLY and wait at (initramfs).\n"
                "Run separately:\n\nmount | grep /cdrom\nls /cdrom/oemdata\nls /dev/nvme*\n\n"
                "Expect ISO9660 ro at /cdrom; oem-iso-cfg.sh and debs; no NVMe devices.\n"
                "Only confirm checks you actually observed. Do not type exit to start installation.")
            for check in self.checks:
                check.show()
            self.attest_button.show()

        def save_inventory(self, inventory):
            inventory_disks(inventory, self.session)
            atomic_json(self.root / "target.json", inventory)
            self.load_disks()
            self.notice("Target identity and SSD serial verified. Ready for a diagnostic network boot.")
            self.buttons()

        def inspect_target(self):
            destination = self.ssh.text().strip()
            self.notice("Inspecting over existing trusted SSH; no reboot or disk changes.")
            self.work(lambda: inspect_ssh(destination), lambda value: self.guarded(lambda: self.save_inventory(value)))

        def import_target(self):
            path, _ = QFileDialog.getOpenFileName(self, "Target inventory JSON", "", "JSON (*.json)")
            if path:
                self.guarded(lambda: self.save_inventory(json.loads(Path(path).read_text(encoding="utf-8"))))

        def load_disks(self):
            self.disk.clear()
            if not self.root or not (self.root / "target.json").exists():
                return
            inventory = json.loads((self.root / "target.json").read_text(encoding="utf-8"))
            for item in inventory_disks(inventory, self.session):
                path, serial = "/dev/" + item["name"], item["serial"].strip()
                size = int(item.get("size") or 0) / 10**9
                self.disk.addItem(f"{path} · {size:.0f} GB · serial {serial}", (path, serial))
            self.disk.currentIndexChanged.connect(self.update_phrase)
            self.update_phrase()
            self.inventory_label.setText("Verified target: " + ", ".join(inventory.get("board", [])) +
                                         f"\n{self.disk.count()} whole NVMe SSD(s) with serials recorded.")

        def update_phrase(self):
            value = self.disk.currentData()
            if value and self.session:
                self.erase_label.setText("Selected device MAC: " + self.session.target_mac + "\nWhole SSD: " + value[0] +
                    "\nSerial: " + value[1] + "\nEnabling installation can erase every existing file." if self.auto_flow else
                    "This will erase every file on " + value[0] + ". Type exactly:\nERASE " + self.session.target_mac + " " + value[1])

        def start_services(self):
            def start():
                image_manifest(self.root, self.session)
                if not (self.root / "target.json").exists():
                    raise RecoveryError("Inspect the target first")
                if (self.root / "network.json").exists():
                    raise RecoveryError("A network journal exists. Confirm the old helper is stopped, then restore first")
                approved, _ = self.consent("Allow temporary recovery network changes",
                    f"Host interface: {self.session.interface}\nHost: {self.session.server}/{self.session.prefix}\n"
                    f"Target lease: {self.session.client}\nTarget MAC: {self.session.target_mac}\n\n"
                    "The privileged helper will temporarily configure this Ethernet link, serve DHCP/TFTP/HTTP, "
                    "and restore its saved settings when stopped. Windows Internet Connection Sharing may be paused; "
                    "that interrupts devices using it. Wi-Fi is not selected for recovery.",
                    ["I backed up the target's data and this cable is isolated from other computers.",
                     "I permit these temporary settings and any interruption of Windows ICS on this link."])
                if not approved:
                    return
                atomic_json(self.root / "host-consent.json", {"session": self.session.fingerprint,
                    "temporary_network_changes": True, "ics_interruption": True, "at": time.time()})
                if sys.platform == "win32":
                    self.ics.setChecked(True)
                self.launch_helper("serve")
            self.guarded(start)

        def launch_helper(self, operation):
            for name in ("desktop-stop", "desktop-service.json"):
                (self.root / name).unlink(missing_ok=True)
            self.helper = launch(self.root, operation, stop_ics=self.ics.isChecked())
            self.pending = True
            self.launch_at = time.monotonic()
            self.seen_phase = ""
            self.notice("Authorize the host helper in the operating system prompt. Verifying media before serving…")
            self.buttons()

        def record_diagnostic(self):
            def record():
                if not self.services or not all(check.isChecked() for check in self.checks):
                    raise RecoveryError("Services must be ready and all three console checks must be confirmed")
                attest(self.root, self.session, "VERIFIED " + self.session.target_mac)
                self.mark("diagnostic_verified", "operator_console_attestation")
                self.show_step(3)
                self.notice("Diagnostic attestation recorded. Review the target disk before authorizing erase.")
                self.buttons()
            self.guarded(record)

        def enable_install(self):
            def enable():
                value = self.disk.currentData()
                if not value:
                    raise RecoveryError("Choose the inspected whole SSD")
                phrase = self.erase_phrase.text()
                if self.auto_flow:
                    approved, _ = self.consent("Permanent target SSD erasure",
                        f"Target Ethernet MAC: {self.session.target_mac}\nWhole SSD: {value[0]}\nSerial: {value[1]}\n"
                        f"Recovery image: {image_profile(self.session.profile)['title']}\n\n"
                        "Installation permanently removes this SSD's files, accounts, applications and settings. "
                        "The app cannot undo it. Enabling this menu is followed by a final confirmation at the target console.",
                        ["I checked the device and this exact SSD serial.", "I have backed up everything I need.",
                         "I understand that existing data will be lost and recovery cannot be guaranteed."],
                        phrase="ERASE " + value[1])
                    if not approved:
                        return
                    phrase = "ERASE " + self.session.target_mac + " " + value[1]
                authorize(self.root, self.session, value[0], value[1], phrase)
                self.notice("Erase menu enabled. STOP remains default. Final confirmation is at the target console.")
                self.buttons()
            self.guarded(enable)

        def mark_install(self):
            answer = QMessageBox.question(self, "Start observed installation interval?", "Have you just selected "
                "CONFIRM ERASE on the target? This records the observed start time; it does not issue another boot or erase.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer == QMessageBox.StandardButton.Yes:
                self.guarded(lambda: self.mark("install_started", "operator_console_confirmation"))
                atomic_json(self.root / "desktop-install-started.json", {"observed_at": time.time()})
                self.show_step(4)
                self.notice("Installation interval running. Watch the target console; keep power and Ethernet connected.")
                self.buttons()

        def revoke_install(self):
            def revoke():
                write_menu(self.root, self.session)
                (self.root / "install.json").unlink(missing_ok=True)
                self.notice("Erase removed from future menus. This does not stop an already running installer.")
                self.buttons()
            self.guarded(revoke)

        def complete(self):
            answer = QMessageBox.question(self, "Confirm the target's first boot", "Does the target show fresh first-run "
                "setup or the new desktop, with no installation error? This is an operator-confirmed result.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.guarded(lambda: self.mark("first_boot_confirmed", "operator_first_boot_confirmation"))
            atomic_json(self.root / "desktop-completed.json", {"observed_at": time.time()})
            self.revoke_install()
            if self.auto_flow:
                (self.root / "desktop-stop").write_text("stop\n", encoding="utf-8")
            self.notice("First boot confirmed. Export the timing report, then stop services to restore host networking.")
            self.buttons()

        def failure(self):
            if QMessageBox.question(self, "Record failure", "Record this run as failed? This does not stop the "
                "installer or recovery services. Inspect the target console before any restart.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
                if self.timing:
                    self.timing.finish("failed")
                self.notice("Failure recorded. Services remain running; no target reboot was issued.")

        def stop_services(self):
            if QMessageBox.warning(self, "Stop recovery services?", "Only stop after the installer has finished, "
                "or after confirming no installation is writing. Stopping during recovery can interrupt it. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            if self.timing:
                self.timing.finish("canceled")
            self.guarded(lambda: (self.root / "desktop-stop").write_text("stop\n", encoding="utf-8"))
            self.notice("Graceful stop requested. Wait for networking restoration before closing.")

        def restore_network(self):
            if QMessageBox.warning(self, "Restore saved host networking?", "Confirm all recovery helper processes "
                "have stopped and the target installer is not running. Restore this session's journal?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
                self.guarded(lambda: self.launch_helper("restore"))

        def export_report(self):
            if not self.timing:
                return
            path, _ = QFileDialog.getSaveFileName(self, "Save timing report", "spark-recovery-timing.json", "JSON (*.json)")
            if path:
                self.guarded(lambda: atomic_json(Path(path), self.timing.report()))
                self.notice("Timing report exported. No MAC, IP, disk serial, account or local path is included.")

        def buttons(self):
            busy = self.job is not None
            active = self.services or self.pending
            root = self.root
            def exists(name):
                return bool(root and (root / name).exists())
            started = exists("desktop-install-started.json")
            completed = exists("desktop-completed.json")
            for widget in (self.interface, self.server, self.client, self.mac, self.backup, self.create,
                           self.refresh, self.resume, self.detect, self.isolated):
                widget.setEnabled(not busy and not active and not root)
            self.connect_button.setEnabled(not busy and not active and not root and bool(self.peers))
            self.peer_list.setEnabled(not busy and not root)
            self.vendor.setEnabled(bool(root) and not busy and not active)
            self.reboot_button.setEnabled(self.services and self.device is not None and not busy and not started)
            self.prepare_button.setEnabled(bool(root) and not busy and not active and not started)
            self.browse.setEnabled(not busy and not active)
            self.inspect_button.setEnabled(bool(root) and not busy and not active and not started)
            self.import_button.setEnabled(bool(root) and not busy and not active and not started)
            self.start.setEnabled(exists("image.json") and exists("target.json") and not active and not busy
                                  and not completed)
            self.attest_button.setEnabled(self.services and not busy and not started)
            self.authorize_button.setEnabled(self.services and exists("diagnostic.json") and not started and not busy)
            self.install_started.setEnabled(self.services and exists("install.json") and not started and not busy)
            self.revoke.setEnabled(exists("install.json") and not busy)
            self.completed.setEnabled(started and not completed and not busy)
            self.failed.setEnabled(started and not completed and not busy)
            self.stop.setEnabled(active and not busy)
            self.restore.setEnabled(exists("network.json") and not active and not busy)
            self.export.setEnabled(bool(self.timing) and not busy)
            for i, button in enumerate(self.steps):
                button.setEnabled(not busy and (i == 0 or bool(root)))

        def tick(self):
            if (not args.smoke_test and not self.root and not self.job and QApplication.activeModalWidget() is None
                    and time.monotonic() >= self.discovery_due):
                self.periodic_discovery = True
                self.auto_discover()
            if self.timing:
                data = self.timing.data
                events = {item["name"]: item["elapsed_seconds"] for item in data["events"]}
                current = time.monotonic() - self.timing.origin
                running = data["outcome"] == "in_progress"
                elapsed = current if running else data.get("elapsed_seconds_at_end", max(events.values(), default=0))
                self.clock_label.setText(duration(elapsed))
                if "install_started" in events:
                    end = events.get("first_boot_confirmed", current if running else elapsed)
                    self.install_clock.setText("Observed installation: " + duration(end - events["install_started"]))
                report = self.timing.report()
                lines = ["Outcome: " + report["outcome"], ""]
                for label, seconds in report["durations"].items():
                    lines.append(label.replace("_", " ") + ": " + duration(seconds))
                lines += ["", "Console start/completion are operator observations.",
                          "Image transfer alone never counts as installation success."]
                self.results.setPlainText("\n".join(lines))
            if not self.root:
                return
            if self.auto_flow and (self.root / "diagnostic-agent-result.json").exists():
                try:
                    result = json.loads((self.root / "diagnostic-agent-result.json").read_text(encoding="utf-8"))
                    manifest = json.loads((self.root / "image.json").read_text(encoding="utf-8"))
                    proof = manifest.get("diagnostic_agent", {}).get("token_sha256")
                    if (result.get("session") == self.session.fingerprint and proof and result.get("token_sha256") == proof
                        and result.get("image") == manifest.get("iso_sha256") and all(result.get("checks", {}).get(key) is True
                        for key in ("iso_read_only", "oem_present", "nvme_hidden", "mac_matched"))):
                        if (self.root / "diagnostic.json").exists() and self.current_step == 2:
                            for check in self.checks:
                                check.setChecked(True)
                            self.mark("diagnostic_verified", "temporary_ram_observer")
                            self.show_step(3)
                            self.notice("Temporary RAM observer verified ISO read-only, OEM content, hidden NVMe and MAC. "
                                        "Review the exact SSD before authorizing installation.")
                except (OSError, ValueError, KeyError):
                    pass
            try:
                status = json.loads((self.root / "desktop-service.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                status = None
            if status:
                phase = status.get("phase", "")
                recent = time.time() - status.get("heartbeat", 0) < 12
                self.services = phase == "ready" and recent
                if phase != self.seen_phase:
                    self.seen_phase = phase
                    self.notice(status.get("message", phase))
                    if phase == "ready":
                        self.mark("services_ready")
                        if self.auto_flow and self.device and not self.reboot_offered:
                            self.reboot_offered = True
                            self.after_job(self.reboot_target)
                    if phase in ("ready", "stopped", "error"):
                        self.pending = False
                if not recent and phase in ("starting", "ready"):
                    self.pending = False
                    self.notice("Helper heartbeat lost. Do not reboot the target. Check its console before restarting services.")
            if self.pending and time.monotonic() - self.launch_at > 120 and status is None:
                self.pending = False
                self.notice("No helper status received. Administrator authorization may have been canceled. Nothing was erased.")
            if self.helper and self.helper.poll() is not None:
                code = self.helper.returncode
                self.helper = None
                if code and self.pending:
                    self.pending = False
                    self.notice("Host authorization failed or was canceled. No target disk operation was requested.")
            self.buttons()

        def closeEvent(self, event):
            if self.job or self.services or self.pending:
                QMessageBox.information(self, "Recovery task is active", "Keep this app open during recovery. "
                    "After installation finishes, use Stop services and wait for host networking restoration.")
                event.ignore()
                return
            if self.timing:
                self.timing.finish("interrupted")
            if self.device:
                self.device.close()
            event.accept()

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setOrganizationName("MKD")
    app.setApplicationName("Spark Recovery")
    app.setApplicationVersion(__version__)
    if sys.platform == "win32" and os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("segoeui.ttf", "segoeuib.ttf", "arial.ttf"):
            QFontDatabase.addApplicationFont(str(fonts / name))
    app.setFont(QFont("Segoe UI" if sys.platform == "win32" else "Arial", 10))
    app.setStyle("Fusion")
    app.setStyleSheet("""
        QWidget { background: #111b2c; color: #e6edf7; }
        #rail { background: #0a1220; border-right: 1px solid #273449; }
        #rail QLabel { background: transparent; color: #91a4bd; font-size: 11px; }
        #rail #brand { color: #66e1b9; font-weight: bold; font-size: 16px; }
        #rail #clock { color: #ffffff; font-size: 31px; font-weight: bold; }
        #heading { font-size: 29px; font-weight: bold; color: #ffffff; padding: 8px 0; }
        #eyebrow { color: #66e1b9; font-size: 11px; font-weight: bold; }
        #pageTitle { font-size: 19px; font-weight: bold; padding-bottom: 6px; }
        QLabel { color: #b9c8dc; }
        #status { background: #172a3b; color: #96e6d3; padding: 13px; border-radius: 7px; }
        QPushButton { background: #21354c; border: 1px solid #38516b; border-radius: 6px; padding: 10px 14px;
                      color: #f2f6fc; text-align: left; }
        QPushButton:hover { background: #2d4965; border-color: #66e1b9; }
        QPushButton:focus { border: 2px solid #66e1b9; }
        QPushButton:disabled { color: #6d7b90; background: #182437; border-color: #253449; }
        #nav { background: transparent; border: none; font-size: 12px; padding: 14px 0; }
        #nav[active="true"] { color: #66e1b9; font-weight: bold; }
        #danger { background: #5a2934; border-color: #b66570; }
        #danger:hover { background: #773747; }
        QLineEdit, QComboBox, QPlainTextEdit { background: #0d1625; border: 1px solid #34475f;
                      border-radius: 5px; padding: 9px; selection-background-color: #21675e; }
        QLineEdit:focus, QComboBox:focus { border-color: #66e1b9; }
        QCheckBox { spacing: 9px; padding: 5px 0; }
        QCheckBox::indicator { width: 17px; height: 17px; }
        QProgressBar { border: none; background: #243249; }
        QProgressBar::chunk { background: #66e1b9; }
        QScrollArea { border: none; }
        QToolTip { background: #243249; color: white; }
    """)
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor("#0a1220"))
    painter = QPainter(pixmap)
    painter.setPen(QColor("#66e1b9"))
    painter.setFont(QFont("Arial", 30, QFont.Weight.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "M")
    painter.end()
    app.setWindowIcon(QIcon(pixmap))
    window = Window()
    window.show()
    if not args.smoke_test:
        QTimer.singleShot(400, window.auto_discover)
    if args.smoke_test:
        import pycdlib
        import zstandard
        import paramiko
        assert paramiko.SSHClient
        assert isinstance(pycdlib.PyCdlib(), pycdlib.PyCdlib)
        payload = zstandard.ZstdCompressor().compress(b"bundle dependency check")
        assert zstandard.ZstdDecompressor().decompress(payload) == b"bundle dependency check"
        assert Path(__file__).with_name("target.py").is_file(), "Bundled target helper missing"
        assert window.pages.count() == 5
        assert not window.start.isEnabled() and not window.authorize_button.isEnabled()
        assert not window.install_started.isEnabled() and window.root is None
        screenshot = os.environ.get("SPARK_RECOVERY_SMOKE_SCREENSHOT")
        if screenshot:
            QTimer.singleShot(150, lambda: window.grab().save(screenshot))
        QTimer.singleShot(300, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
