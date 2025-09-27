# Meshtastic USB Provisioner

A tiny, opinionated utility to **provision Meshtastic nodes over USB** from repeatable config profiles.

## What it does
- Applies a selected **profile** (`client`, `repeater`, …) from JSON (YAML optionally supported if PyYAML is installed).
- Enforces firmware **>= 2.6.11** (via CLI-reported version string).
- (Optional) **factory reset** to force fresh node keypairs (`--fresh`).
- Sets **LoRa region = US_902_928**, **modem preset = long_fast** (configurable per profile).
- Writes **channels** and **AES-256 PSKs** from `channels.json` (PSKs are stored in-repo per your requirement).
- Writes **device role** and common module toggles (GPS, BLE, screen timeout, Store-and-Forward for repeater).
- Produces a **manifest CSV** and **verify report** in `provision/exports`.
- Supports **PSK rotation** from the repo (`--rotate-psk`).

> Assumes the official `meshtastic` CLI is installed and your device is connected over USB (one at a time).

## Quickstart

```bash
# 1) Install Python deps (optional, for YAML support)
python3 -m pip install pyyaml

# 2) Dry-run read/verify against attached node
python3 meshprovision.py --profile client --verify

# 3) Provision a new node (fresh erase + apply configs)
python3 meshprovision.py --profile client --fresh

# 4) Provision a repeater
python3 meshprovision.py --profile repeater --fresh

# 5) Rotate PSKs across nodes (applies PSKs from channels.json)
python3 meshprovision.py --rotate-psk
```

### USB port selection
The script auto-detects a Meshtastic serial port on macOS. You can override with `--port /dev/cu.usbserial-xxxxx`.

### Files
- `meshprovision.py` — main script
- `channels.json` — channel plan with PSKs (shared within your private repo)
- `profiles/client.json` — profile for handheld/clients
- `profiles/repeater.json` — profile for simple store-and-forward repeater
- `devices.csv` — roster (name, profile); optional

---

**Security notes**

- PSKs are stored **in-repo** by design (private repo). Treat this repo like credentials.
- Direct Message (DM) keypairs are generated on-device after a **fresh** reset; we do not export private keys.
- No QR/URL export (per your requirement). All nodes should be provisioned via this script.
