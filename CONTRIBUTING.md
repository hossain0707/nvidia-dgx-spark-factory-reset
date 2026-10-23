# Contributing

Read [LICENSE](LICENSE) before use or contribution. Personal noncommercial use is free; commercial/organizational use and redistribution require MKD's prior written permission. Public-platform viewing/forking rights are preserved. Do not describe this custom license as MIT or OSI open source.

Use the primary repository for issues and pull requests. Keep the maintainer mirror on the same reviewed commits. Do not add a workflow that stores a personal access token in the repository to synchronize mirrors.

## Development

```sh
python -m venv .venv
python -m pip install -e ".[test]"
python -m pytest
python -m ruff check src tests
python -m build
```

Activate the virtual environment or invoke its Python explicitly. Tests must use fixtures/loopback/high ports and must not require real DHCP, firmware writes, reboots or disk erasure. Any live hardware trial needs the operator's explicit authorization, recoverable host changes, backups and a documented diagnostic stop before erase.

Report exact expected/actual behavior, host OS/Python, target OEM/image, adapter/driver and the phase where it failed. Redact MAC/IP/serial, usernames, personal paths, keys and tokens. Never attach vendor images or private state. A failing diagnostic must be fixed, not bypassed.

New OEM support requires a separate profile, pinned official artifact verification, demonstrably non-installing diagnostic mode, OEM-specific driver/installer inspection and actual hardware evidence. A similar-looking device is not sufficient.
