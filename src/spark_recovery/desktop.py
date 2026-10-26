"""MKD Spark Recovery desktop. Optional Qt frontend over the existing recovery API."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from . import __version__
from .cli import ssh_command
from .config import RecoveryError, Session, atomic_json
from .desktop_service import launch, run_service
from .images import prepare
from .locking import ServiceLock
from .network import command, powershell
from .safety import attest, authorize, image_manifest, validate_inventory, write_menu
from .timing import RecoveryTiming, duration


def inspect_ssh(destination: str):
    source = Path(__file__).with_name("target.py").read_text(encoding="utf-8")
    args = ssh_command(destination, source)
    args[1:1] = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    result = subprocess.run(args, capture_output=True, text=True, timeout=35)
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
        from PySide6.QtCore import QStandardPaths, QThread, QTimer, Qt, Signal
        from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap
        from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame,
                                      QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
                                      QProgressBar, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)
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
            navigation.addWidget(QLabel("ASUS GX10 only\nCommunity preview · No telemetry"))
            row.addWidget(rail)
            content = QVBoxLayout()
            content.setContentsMargins(30, 26, 30, 24)
            eyebrow = QLabel("SPARK RECOVERY  /  DESKTOP PREVIEW")
            eyebrow.setObjectName("eyebrow")
            content.addWidget(eyebrow)
            self.heading = QLabel("A guided path to a fresh start.")
            self.heading.setObjectName("heading")
            content.addWidget(self.heading)
            subtitle = QLabel("Dedicated Ethernet. Verified ASUS media. You control the final erase.")
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
            layout = self.page("01 / Connect & identify", "Connect the GX10 directly to this computer. Keep its monitor "
                               "and keyboard connected. Back up its data. Select Ethernet, not Wi-Fi.")
            form = QFormLayout()
            form.setVerticalSpacing(12)
            self.interface = QComboBox()
            self.interface.setEditable(True)
            form.addRow("Host Ethernet adapter", self.interface)
            self.server = QLineEdit("172.31.250.1")
            self.client = QLineEdit("172.31.250.2")
            self.mac = QLineEdit()
            self.mac.setPlaceholderText("Target Ethernet MAC from firmware, e.g. 02:00:00:00:00:01")
            form.addRow("Host IPv4 /24", self.server)
            form.addRow("GX10 recovery IPv4", self.client)
            form.addRow("GX10 Ethernet MAC", self.mac)
            layout.addLayout(form)
            self.refresh = self.button("Refresh adapter list", self.refresh_interfaces, layout)
            self.backup = QCheckBox("I have backed up the GX10 and am using an isolated Ethernet link.")
            layout.addWidget(self.backup)
            self.create = self.button("Create safe recovery session →", self.create_session, layout)
            self.resume = self.button("Open an existing desktop session…", self.open_session, layout)
            self.location = QLabel("Sessions and timing reports stay on this computer.")
            self.location.setWordWrap(True)
            layout.addWidget(self.location)
            layout.addStretch()

        def media_page(self):
            layout = self.page("02 / Prepare & inspect", "Download pinned official ASUS media (~10 GB) or choose an "
                               "existing ISO. Preparation verifies hashes and creates a diagnostic-only boot menu.")
            self.iso = QLineEdit()
            self.iso.setPlaceholderText("Leave empty to download directly from ASUS")
            layout.addWidget(self.iso)
            self.browse = self.button("Choose existing ASUS ISO…", self.choose_iso, layout)
            self.vendor = QCheckBox("I accept the vendor download terms and have at least 24 GiB free disk space.")
            layout.addWidget(self.vendor)
            self.prepare_button = self.button("Verify and prepare recovery image", self.prepare_media, layout)
            layout.addSpacing(12)
            self.ssh = QLineEdit()
            self.ssh.setPlaceholderText("Existing SSH key connection: operator@current-target-address")
            layout.addWidget(self.ssh)
            self.inspect_button = self.button("Inspect GX10 over trusted SSH", self.inspect_target, layout)
            self.import_button = self.button("Import target inventory JSON…", self.import_target, layout)
            self.inventory_label = QLabel("Inventory required: ASUS GX10 identity, target MAC, whole SSD and serial.\n"
                                           "Without SSH, run target.py --json on the GX10 and import the saved file.")
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
            instructions = QPlainTextEdit("1. Wait for 'services ready' below.\n"
                "2. On the GX10, reboot into its UEFI PXE IPv4 entry.\n"
                "3. Select VERIFY ONLY (STOP is the default).\n"
                "4. At (initramfs), run one command at a time:\n\n"
                "mount | grep /cdrom\nls /cdrom/oemdata\nls /dev/nvme*\n\n"
                "Expect: /cdrom is ISO9660 and ro; oem-iso-cfg.sh and debs exist;\n"
                "no /dev/nvme* devices exist. Do not type exit to install.")
            instructions.setReadOnly(True)
            instructions.setMinimumHeight(225)
            layout.addWidget(instructions)
            self.checks = [QCheckBox(text) for text in ("ISO is mounted read-only at /cdrom.",
                            "Both ASUS OEM files/directories are visible.", "No NVMe devices are exposed.")]
            for check in self.checks:
                layout.addWidget(check)
            self.attest_button = self.button("Record diagnostic checks →", self.record_diagnostic, layout)
            layout.addStretch()

        def install_page(self):
            layout = self.page("04 / Authorize the exact SSD", "This step enables the erase menu. Installation still "
                               "requires a separate console selection on the GX10. All existing target SSD data will be lost.")
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
                "The app cannot inspect the GX10's disk-writing progress remotely.")
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
            self.completed = self.button("GX10 has reached fresh first-run setup / desktop", self.complete, layout)
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

        def work(self, function, done):
            if self.job:
                return
            self.job = Job(function)
            self.job.done.connect(done)
            self.job.failed.connect(self.problem)
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
                self.timing = RecoveryTiming.load(root / "timing.json") if (root / "timing.json").exists() else None
                self.location.setText(str(root))
                self.load_disks()
                self.seen_phase = ""
                self.show_step(4)
                self.notice("Session reopened. Previous active timing is marked interrupted; no duration is invented.")
                self.buttons()
            self.guarded(load)

        def choose_iso(self):
            path, _ = QFileDialog.getOpenFileName(self, "Official ASUS recovery ISO", "", "ISO images (*.iso)")
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
                    return prepare(self.root, self.session, iso=iso, progress=self.job.progress.emit)
            def done(value):
                self.mark("preparation_complete")
                self.notice("Official image and boot files verified. Inspect the target before starting services.")
            self.work(operation, done)

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
                self.erase_label.setText("This will erase every file on " + value[0] + ". Type exactly:\nERASE " +
                                         self.session.target_mac + " " + value[1])

        def start_services(self):
            def start():
                image_manifest(self.root, self.session)
                if not (self.root / "target.json").exists():
                    raise RecoveryError("Inspect the target first")
                if (self.root / "network.json").exists():
                    raise RecoveryError("A network journal exists. Confirm the old helper is stopped, then restore first")
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
                authorize(self.root, self.session, value[0], value[1], self.erase_phrase.text())
                self.notice("Erase menu enabled. STOP remains default. Final confirmation is at the GX10 console.")
                self.buttons()
            self.guarded(enable)

        def mark_install(self):
            answer = QMessageBox.question(self, "Start observed installation interval?", "Have you just selected "
                "CONFIRM ERASE on the GX10? This records the observed start time; it does not issue another boot or erase.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer == QMessageBox.StandardButton.Yes:
                self.guarded(lambda: self.mark("install_started", "operator_console_confirmation"))
                atomic_json(self.root / "desktop-install-started.json", {"observed_at": time.time()})
                self.show_step(4)
                self.notice("Installation interval running. Watch the GX10 console; keep power and Ethernet connected.")
                self.buttons()

        def revoke_install(self):
            def revoke():
                write_menu(self.root, self.session)
                (self.root / "install.json").unlink(missing_ok=True)
                self.notice("Erase removed from future menus. This does not stop an already running installer.")
                self.buttons()
            self.guarded(revoke)

        def complete(self):
            answer = QMessageBox.question(self, "Confirm the GX10's first boot", "Does the GX10 show fresh first-run "
                "setup or the new desktop, with no installation error? This is an operator-confirmed result.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.guarded(lambda: self.mark("first_boot_confirmed", "operator_first_boot_confirmation"))
            atomic_json(self.root / "desktop-completed.json", {"observed_at": time.time()})
            self.revoke_install()
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
                           self.refresh, self.resume):
                widget.setEnabled(not busy and not active and not root)
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
    if args.smoke_test:
        import pycdlib
        import zstandard
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
