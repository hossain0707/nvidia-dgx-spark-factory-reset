# Compatibility and limits

| Area | Evidence | Status |
|---|---|---|
| Original Windows host → ASUS GX10 7.5.0-4 | Complete diagnostic PXE boot and successful reinstall observed in an operator-assisted session | Reference workflow verified |
| Packaged Windows host | Protocol tests and mocked host-network mutations; vendor-image preparation test | Preview; new end-to-end hardware trial needed |
| Packaged macOS host | Portable tests, macOS CI and mocked alias changes | Preview; physical Mac/Ethernet/GX10 test needed |
| Packaged Linux host | Portable tests, Linux CI and mocked alias changes | Preview; physical host/GX10 test needed |
| Local Linux target inventory and BootNext | Parsing/input checks and matching-MAC selection tests | Requires ARM64 UEFI, efibootmgr, lsblk; reboot needs root |
| SSH target control | Command construction and strict-key behavior tested | Existing trusted SSH; root changes need preauthorized noninteractive sudo |
| NVIDIA DGX Spark Founders Edition | Different recovery media and firmware flow | Not implemented |
| Dell/HP/Lenovo/MSI/other OEM variants | No corresponding image adapter/qualification | Not implemented |

Passing the CI matrix means the software's **automated tests** passed on that runner, not that it erased and recovered real hardware there. A preview badge is intentional. Do not turn this document into a universal-support claim without documented qualification.

## Required equipment

- A supported ASUS Ascent GX10 with its original Ethernet adapter, matching official image, and UEFI IPv4 PXE enabled.
- A separate Windows/Mac/Linux host with dedicated Ethernet, power, administrator/root access and Python 3.11+.
- Console monitor and working keyboard for the GX10. A USB keyboard or USB-C hub may be needed; USB **storage** is not required.
- Enough RAM on the GX10 to hold the ISO/live installer. Host preparation recommends 16 GiB RAM and needs about 24 GiB free disk space for download/extraction.
- Reliable power and a cable/adapter combination that remains working through firmware and kernel boot.

## Unsupported shortcuts

No generic ISO injection, unknown OEM profile override, secure-boot bypass, automatic host-key deletion, password collection, fully unattended SSD erase, or same-machine-only PXE recovery. A local/SSH target command cannot remove the need for a separate recovery server and final console confirmation.

## Adding another image

Add a separate profile and adapter, not a renamed ASUS profile. Obtain official media and permitted distribution terms, pin checksums, inspect its initrd and installer control flow, design a demonstrably non-installing diagnostic boot, verify its NIC/storage drivers, test disk-target mapping and completion/rollback, and document an actual hardware trial. NVIDIA's `usb.shell` FastOS flow is different from ASUS's Casper ISO flow; see the upstream [PXE reference](https://docs.nvidia.com/dgx/dgx-spark/pxe.html).
