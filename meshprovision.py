#!/usr/bin/env python3
"""
Meshtastic USB Provisioner
- Applies profile (client/repeater) over USB.
- Enforces fw >= 2.6.11 (best-effort via CLI output).
- Optional factory reset (--fresh) to generate fresh node keypairs.
- Writes channels from channels.json (PSKs stored in-repo).
- Writes role and common module toggles.
- Outputs manifest & verify reports.
"""
import argparse, os, sys, json, subprocess, re, glob, time, csv, datetime

try:
    import yaml  # optional
    HAVE_YAML = True
except Exception:
    HAVE_YAML = False

EXPORT_DIR = os.path.join("provision","exports")
os.makedirs(EXPORT_DIR, exist_ok=True)

def run(cmd, check=True, capture=True):
    """Run a shell command, return (rc, out, err)."""
    if capture:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out, err = p.communicate()
        rc = p.returncode
    else:
        rc = subprocess.call(cmd)
        out, err = "", ""
    if check and rc != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\nstdout:\n{out}\nstderr:\n{err}")
    return rc, out, err

def find_port(user_port=None):
    if user_port:
        return user_port
    # macOS common device patterns
    cand = sorted(glob.glob("/dev/cu.usb*") + glob.glob("/dev/cu.SLAB*") + glob.glob("/dev/cu.wchusb*"))
    if not cand:
        raise RuntimeError("No USB serial device found. Pass --port /dev/cu.usbserial-XXXX")
    return cand[0]

def meshtastic_base_cmd(port):
    return ["meshtastic", "--port", port]

def meshtastic_get_version():
    rc, out, err = run(["meshtastic", "--version"], check=False)
    # Typical output: "meshtastic 2.6.12 ...", but tolerate variations
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    if not m:
        return None
    return tuple(map(int, m.groups()))

def enforce_min_version(min_tup=(2,6,11)):
    ver = meshtastic_get_version()
    if ver is None:
        print("WARN: Could not determine meshtastic CLI version; proceeding.")
        return
    if ver < min_tup:
        raise RuntimeError(f"Meshtastic CLI version {ver} < required {min_tup}. Please upgrade.")

def load_json_or_yaml(path):
    with open(path, "r") as f:
        data = f.read()
    # Try JSON first, fall back to YAML
    try:
        return json.loads(data), "json"
    except Exception:
        if not HAVE_YAML:
            raise RuntimeError(f"File {path} is not JSON and PyYAML not installed. Install PyYAML or use JSON.")
        return yaml.safe_load(data), "yaml"

def factory_reset(port):
    # Try a couple of known resets; tolerate failures.
    base = meshtastic_base_cmd(port)
    tried = []
    for attempt in [
        base + ["--factoryreset"],
        base + ["--erase"],
        base + ["--set", "factory_reset", "true"]
    ]:
        tried.append(" ".join(attempt))
        rc, out, err = run(attempt, check=False)
        if rc == 0:
            print("Factory reset issued; waiting for reboot...")
            time.sleep(8)
            return True
    print("WARN: Could not issue factory reset via CLI. Tried:\n  " + "\n  ".join(tried))
    return False

def set_region(port, region):
    cmd = meshtastic_base_cmd(port) + ["--set", "lora.region", region]
    run(cmd)

def set_modem_preset(port, preset):
    cmd = meshtastic_base_cmd(port) + ["--set", "lora.modem_preset", preset.upper()]
    run(cmd)

def set_role(port, role):
    cmd = meshtastic_base_cmd(port) + ["--set", "device.role", role.upper()]
    run(cmd)

def set_ble(port, enabled, auto_off_minutes):
    base = meshtastic_base_cmd(port)
    run(base + ["--set", "ble.enabled", str(bool(enabled)).lower()])
    run(base + ["--set", "ble.timeout_minutes", str(int(auto_off_minutes))])

def set_screen_timeout(port, seconds):
    base = meshtastic_base_cmd(port)
    run(base + ["--set", "display.screen_timeout_secs", str(int(seconds))])

def set_gps(port, cfg):
    base = meshtastic_base_cmd(port)
    mode = cfg.get("mode","smart")
    if mode == "off":
        run(base + ["--set", "position.gps_enabled", "false"])
        return
    run(base + ["--set", "position.gps_enabled", "true"])
    if mode == "interval":
        run(base + ["--set", "position.fixed_interval_sec", str(int(cfg.get("interval_sec",300)))])
    else:
        # smart mode with thresholds
        run(base + ["--set", "position.smart_enabled", "true"])
        run(base + ["--set", "position.min_interval_sec", str(int(cfg.get("min_interval_sec",120)))])
        run(base + ["--set", "position.min_distance_m", str(int(cfg.get("min_distance_m",50)))])

def set_modules(port, modules):
    base = meshtastic_base_cmd(port)
    # Store-and-Forward
    saf = bool(modules.get("store_forward", False))
    run(base + ["--set", "store_forward.enabled", str(saf).lower()])
    # Traceroute
    tr = bool(modules.get("traceroute", False))
    run(base + ["--set", "traceroute.enabled", str(tr).lower()])
    # Telemetry
    tel = bool(modules.get("telemetry", False))
    run(base + ["--set", "telemetry.enabled", str(tel).lower()])

def apply_channels(port, channels_cfg):
    base = meshtastic_base_cmd(port)
    for ch in channels_cfg.get("channels", []):
        idx = int(ch.get("index",0))
        name = ch.get("name","private")
        psk = ch.get("psk","")
        fmt = ch.get("psk_format","base64").lower()
        run(base + ["--ch-index", str(idx), "--ch-set", "name", name])
        # set PSK
        if fmt == "base64":
            run(base + ["--ch-index", str(idx), "--ch-set", "psk", f"base64:{psk}"])
        elif fmt in ("hex", "0x"):
            run(base + ["--ch-index", str(idx), "--ch-set", "psk", f"hex:{psk}"])
        else:
            raise RuntimeError(f"Unsupported psk_format: {fmt}")
        # optional extras
        if "allow_location" in ch:
            run(base + ["--ch-index", str(idx), "--ch-set", "position_broadcast", str(bool(ch['allow_location'])).lower()])
        if "position_precision" in ch:
            run(base + ["--ch-index", str(idx), "--ch-set", "position_precision", str(ch["position_precision"]).lower()])

def read_back_config(port):
    # Minimal verification: pull a few keys
    base = meshtastic_base_cmd(port)
    verify = {}
    keys = [
        "lora.region","lora.modem_preset","device.role",
        "ble.enabled","ble.timeout_minutes",
        "display.screen_timeout_secs",
        "position.gps_enabled","position.smart_enabled","position.min_interval_sec","position.min_distance_m",
        "store_forward.enabled","traceroute.enabled","telemetry.enabled"
    ]
    for k in keys:
        rc, out, err = run(base + ["--get", k], check=False)
        if rc == 0:
            verify[k] = out.strip()
    return verify

def write_manifest_row(name, profile, port, status, note=""):
    manifest_path = os.path.join(EXPORT_DIR, "manifest.csv")
    exists = os.path.exists(manifest_path)
    with open(manifest_path, "a", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["timestamp","name","profile","port","status","note"])
        w.writerow([datetime.datetime.utcnow().isoformat(), name, profile, port, status, note])

def cmd_provision(args):
    enforce_min_version((2,6,11))
    port = find_port(args.port)
    # Load configs
    prof_path = f"profiles/{args.profile}.json" if os.path.exists(f"profiles/{args.profile}.json") else f"profiles/{args.profile}.yaml"
    channels_path = "channels.json" if os.path.exists("channels.json") else "channels.yaml"
    profile, _ = load_json_or_yaml(prof_path)
    channels, _ = load_json_or_yaml(channels_path)

    if args.fresh:
        factory_reset(port)
        # After reset, give device time to come back
        time.sleep(5)
    # Region/preset, then role and modules
    set_region(port, profile.get("region","US_902_928"))
    set_modem_preset(port, profile.get("modem_preset","long_fast"))
    set_role(port, profile.get("role","CLIENT"))
    # BLE
    ble_cfg = profile.get("ble", {})
    set_ble(port, ble_cfg.get("enabled", True), ble_cfg.get("auto_off_minutes", 10))
    # Screen
    sc = profile.get("screen", {})
    set_screen_timeout(port, sc.get("auto_off_seconds", 60))
    # GPS
    set_gps(port, profile.get("gps", {"mode":"smart"}))
    # Modules
    set_modules(port, profile.get("modules", {}))
    # Channels
    apply_channels(port, channels)
    # Verify summary
    verify = read_back_config(port)
    outpath = os.path.join(EXPORT_DIR, f"verify_{args.profile}_{int(time.time())}.json")
    with open(outpath,"w") as f:
        json.dump(verify, f, indent=2)
    print(f"Wrote verify report: {outpath}")
    write_manifest_row(args.name or "", args.profile, port, "OK", "provisioned")

def cmd_verify(args):
    enforce_min_version((2,6,11))
    port = find_port(args.port)
    verify = read_back_config(port)
    outpath = os.path.join(EXPORT_DIR, f"verify_read_{int(time.time())}.json")
    with open(outpath,"w") as f:
        json.dump(verify, f, indent=2)
    print(json.dumps(verify, indent=2))
    print(f"Wrote verify report: {outpath}")

def cmd_rotate_psk(args):
    enforce_min_version((2,6,11))
    port = find_port(args.port)
    channels_path = "channels.json" if os.path.exists("channels.json") else "channels.yaml"
    channels, _ = load_json_or_yaml(channels_path)
    apply_channels(port, channels)
    write_manifest_row(args.name or "", "n/a", port, "OK", "psk-rotated")
    print("Applied PSKs from repo to attached node.")

def main():
    ap = argparse.ArgumentParser(description="Meshtastic USB Provisioner")
    sub = ap.add_subparsers()

    ap.add_argument("--port", help="Serial port (e.g., /dev/cu.usbserial-XXXX)")
    ap.add_argument("--profile", help="Profile name (client, repeater)", default="client")
    ap.add_argument("--name", help="Human-friendly node name for manifest", default="")
    ap.add_argument("--fresh", action="store_true", help="Factory reset before applying settings")
    ap.add_argument("--verify", action="store_true", help="Verify only")
    ap.add_argument("--rotate-psk", action="store_true", help="Apply PSKs from channels file to node")

    args = ap.parse_args()

    if args.rotate_psk:
        cmd_rotate_psk(args)
        return
    if args.verify:
        cmd_verify(args)
        return
    cmd_provision(args)

if __name__ == "__main__":
    main()
