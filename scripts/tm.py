#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The esp32-tailscale Authors
"""tailmote task runner: setup / config / build / flash / peer / bench.

Chạy được trên Linux, macOS và Windows, chỉ cần Python 3.8+ (thư viện chuẩn).

  Linux/macOS:  make <lệnh>            (hoặc: python3 scripts/tm.py <lệnh>)
  Windows:      .\\tm <lệnh>            (hoặc: py scripts\\tm.py <lệnh>)

`tm help` liệt kê các lệnh.
"""
import argparse
import base64
import glob
import hashlib
import json
import os
import re
import secrets as _rand
import shlex
import shutil
import socket
import subprocess
import sys
from pathlib import Path

WINDOWS = os.name == "nt"
MACOS = sys.platform == "darwin"
ROOT = Path(__file__).resolve().parent.parent
SECRETS_DIR = ROOT / "secrets"
SECRETS_FILE = SECRETS_DIR / "secrets.env"
SECRETS_FORM = ROOT / "config" / "secrets.env.example"
RESULTS_DIR = ROOT / "results"
BENCH_DIR = ROOT / "tools" / "bench"

# USB-UART bridges found on ESP32 dev boards (VID -> name).
USB_UART_VIDS = {"10C4": "CP210x", "1A86": "CH34x", "0403": "FTDI", "303A": "Espressif USB"}

SECRET_DEFAULTS = {
    "WIFI_SSID": "", "WIFI_PASSWORD": "", "WG_ENDPOINT": "", "WG_PORT": "51820",
    "LINUX_TUNNEL_IP": "10.66.0.1", "ESP_TUNNEL_IP": "10.66.0.2",
    "TUNNEL_NETMASK": "255.255.255.0", "TUNNEL_PREFIX": "24",
    "ESP_PRIVATE_KEY": "", "LINUX_PRIVATE_KEY": "", "PRESHARED_KEY": "",
    "WG_KEEPALIVE": "25", "WIFI_POWER_SAVE": "none", "SERIAL_PORT": "", "FLASH_BAUD": "460800",
}


# ---------------------------------------------------------------- output ----
def _setup_console():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if WINDOWS:
        os.system("")  # enable ANSI escape processing in the Windows console


_COLOR = False


def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def ok(msg):
    print(f"  {_c(32, '✔')} {msg}")


def bad(msg):
    print(f"  {_c(31, '✘')} {msg}")


def info(msg):
    print(f"{_c(36, '==>')} {msg}")


class Die(SystemExit):
    pass


def die(msg, code=1):
    print(f"{_c(31, 'error:')} {msg}", file=sys.stderr)
    raise Die(code)


# ------------------------------------------------------------ env files ----
def parse_env_text(text, name="env"):
    """Parse a KEY='value' file (the bash-compatible subset used by the forms).

    Quoting follows POSIX shell rules ('...', "...", 'it'\\''s'); full-line and
    trailing # comments are allowed; no variable expansion.
    """
    out = {}
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        try:
            toks = shlex.split(s, comments=False, posix=True)
        except ValueError as e:
            die(f"{name}:{n}: {e} (thiếu dấu nháy?)")
        if len(toks) > 1 and not toks[1].startswith("#"):
            die(f"{name}:{n}: cần dạng KEY='value' (giá trị có dấu cách phải nằm trong nháy)")
        if not toks or "=" not in toks[0]:
            die(f"{name}:{n}: cần dạng KEY='value'")
        k, v = toks[0].split("=", 1)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k):
            die(f"{name}:{n}: tên biến không hợp lệ: {k}")
        out[k] = v
    return out


def read_env_file(path):
    return parse_env_text(path.read_text(encoding="utf-8"), path.name)


def project():
    """config/project.env + environment overrides + per-OS defaults."""
    p = read_env_file(ROOT / "config" / "project.env")
    for k in list(p):
        if os.environ.get(k):
            p[k] = os.environ[k]
    ver = p["IDF_VERSION"]
    if not p.get("IDF_PATH"):
        p["IDF_PATH"] = (rf"C:\Espressif\frameworks\esp-idf-{ver}" if WINDOWS
                         else str(Path.home() / "esp" / f"esp-idf-{ver}"))
    if not p.get("IDF_TOOLS_PATH"):
        p["IDF_TOOLS_PATH"] = r"C:\Espressif" if WINDOWS else str(Path.home() / ".espressif")
    p["FW_DIR"] = str(ROOT / p["FW_PROJECT"])
    return p


def load_secrets(required=True):
    if not SECRETS_FILE.is_file():
        if required:
            die(f"chưa có {SECRETS_FILE} — chạy: tm secrets")
        return dict(SECRET_DEFAULTS)
    out = dict(SECRET_DEFAULTS)
    for k, v in read_env_file(SECRETS_FILE).items():
        out[k] = v if v != "" else SECRET_DEFAULTS.get(k, "")  # ${X:-default}: empty means default
    return out


# --------------------------------------------------------------- X25519 ----
# RFC 7748 Montgomery ladder; used only to generate WireGuard keys so that no
# external `wg` binary is needed. Not constant-time (keys never leave this host
# at generation time; the ESP32 and the peer do the real crypto).
_P = 2 ** 255 - 19
_A24 = 121665


def _clamp(k):
    b = bytearray(k)
    b[0] &= 248
    b[31] &= 127
    b[31] |= 64
    return bytes(b)


def x25519(k, u):
    k_int = int.from_bytes(_clamp(k), "little")
    x1 = int.from_bytes(u, "little") & ((1 << 255) - 1)
    x2, z2, x3, z3, swap = 1, 0, x1, 1, 0
    for t in reversed(range(255)):
        kt = (k_int >> t) & 1
        swap ^= kt
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = kt
        a = (x2 + z2) % _P
        aa = a * a % _P
        b = (x2 - z2) % _P
        bb = b * b % _P
        e = (aa - bb) % _P
        c = (x3 + z3) % _P
        d = (x3 - z3) % _P
        da = d * a % _P
        cb = c * b % _P
        x3 = (da + cb) ** 2 % _P
        z3 = x1 * (da - cb) ** 2 % _P
        x2 = aa * bb % _P
        z2 = e * (aa + _A24 * e) % _P
    if swap:
        x2, x3, z2, z3 = x3, x2, z3, z2
    return (x2 * pow(z2, _P - 2, _P) % _P).to_bytes(32, "little")


def wg_genkey():
    """Same as `wg genkey`: 32 random bytes, clamped, base64."""
    return base64.b64encode(_clamp(_rand.token_bytes(32))).decode()


def wg_pubkey(priv_b64):
    return base64.b64encode(x25519(base64.b64decode(priv_b64), (9).to_bytes(32, "little"))).decode()


def wg_genpsk():
    return base64.b64encode(_rand.token_bytes(32)).decode()


_KEY_RE = re.compile(r"^[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=$")
_IP_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")


def is_key(s):
    return bool(_KEY_RE.match(s))


def is_ip(s):
    return bool(_IP_RE.match(s)) and all(int(x) <= 255 for x in s.split("."))


def lan_ip():
    """IPv4 of the interface that routes to the Internet (no packet is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("1.1.1.1", 80))
            return s.getsockname()[0]
    except OSError:
        return None


def kconfig_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# --------------------------------------------------------------- config ----
def validate_secrets(s):
    errs = []
    if not s["WIFI_SSID"]:
        errs.append("WIFI_SSID trống")
    if len(s["WIFI_SSID"].encode()) > 32:
        errs.append("WIFI_SSID dài hơn 32 byte")
    if s["WIFI_PASSWORD"] and not 8 <= len(s["WIFI_PASSWORD"]) <= 63:
        errs.append("WIFI_PASSWORD phải 8–63 ký tự (hoặc để trống cho mạng mở)")
    for v in ("LINUX_TUNNEL_IP", "ESP_TUNNEL_IP", "TUNNEL_NETMASK"):
        if not is_ip(s[v]):
            errs.append(f"{v} không phải IPv4: '{s[v]}'")
    if s["LINUX_TUNNEL_IP"] == s["ESP_TUNNEL_IP"]:
        errs.append("LINUX_TUNNEL_IP trùng ESP_TUNNEL_IP")
    if not (s["WG_PORT"].isdigit() and 0 < int(s["WG_PORT"]) < 65536):
        errs.append("WG_PORT không hợp lệ")
    if not (s["TUNNEL_PREFIX"].isdigit() and 0 <= int(s["TUNNEL_PREFIX"]) <= 32):
        errs.append("TUNNEL_PREFIX phải 0–32")
    if not s["WG_KEEPALIVE"].isdigit():
        errs.append("WG_KEEPALIVE phải là số giây")
    if s["WIFI_POWER_SAVE"] not in ("none", "min_modem"):
        errs.append("WIFI_POWER_SAVE: none | min_modem")
    for v in ("ESP_PRIVATE_KEY", "LINUX_PRIVATE_KEY"):
        if s[v] and not is_key(s[v]):
            errs.append(f"{v} không phải khóa WireGuard base64")
    if s["PRESHARED_KEY"] not in ("", "auto") and not is_key(s["PRESHARED_KEY"]):
        errs.append("PRESHARED_KEY không hợp lệ")
    return errs


def _write_private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    if not WINDOWS:
        os.chmod(path, 0o600)


def gen_config(s, secrets_dir, fw_dir):
    """Generate keys, the peer config and sdkconfig.secrets. Returns endpoint."""
    errs = validate_secrets(s)
    endpoint = s["WG_ENDPOINT"]
    if not endpoint:
        endpoint = lan_ip() or ""
        if endpoint:
            ok(f"WG_ENDPOINT tự động: {endpoint}")
        else:
            errs.append("không tự lấy được IP LAN, điền WG_ENDPOINT")
    if errs:
        for e in errs:
            bad(e)
        die(f"form có {len(errs)} lỗi — sửa {SECRETS_FILE} rồi chạy lại")
    ok("form hợp lệ")

    def key_for(name, val):
        kf = secrets_dir / f"{name}.key"
        if val:
            _write_private(kf, val + "\n")
        elif not kf.is_file():
            _write_private(kf, wg_genkey() + "\n")
            ok(f"sinh khóa mới: {name}")
        priv = kf.read_text(encoding="utf-8").strip()
        if not is_key(priv):
            die(f"{kf} không chứa khóa WireGuard hợp lệ")
        pub = wg_pubkey(priv)
        _write_private(secrets_dir / f"{name}.pub", pub + "\n")
        return priv, pub

    esp_priv, esp_pub = key_for("esp32", s["ESP_PRIVATE_KEY"])
    peer_priv, peer_pub = key_for("linux", s["LINUX_PRIVATE_KEY"])

    psk = ""
    if s["PRESHARED_KEY"] == "auto":
        pf = secrets_dir / "psk"
        if not pf.is_file():
            _write_private(pf, wg_genpsk() + "\n")
        psk = pf.read_text(encoding="utf-8").strip()
    elif s["PRESHARED_KEY"]:
        psk = s["PRESHARED_KEY"]

    conf = [
        "# Generated by scripts/tm.py config — do not commit.",
        "# Linux/macOS: sudo wg-quick up <this file>  (hoặc: make peer-up)",
        "# Windows:     WireGuard for Windows → Import tunnel  (hoặc: .\\tm peer-up, quyền admin)",
        "[Interface]",
        f"Address = {s['LINUX_TUNNEL_IP']}/{s['TUNNEL_PREFIX']}",
        f"ListenPort = {s['WG_PORT']}",
        f"PrivateKey = {peer_priv}",
        "",
        "[Peer]",
        "# ESP32 DOIT DevKit V1",
        f"PublicKey = {esp_pub}",
    ]
    if psk:
        conf.append(f"PresharedKey = {psk}")
    conf.append(f"AllowedIPs = {s['ESP_TUNNEL_IP']}/32")
    _write_private(secrets_dir / "wg-esp.conf", "\n".join(conf) + "\n")
    ok(f"peer: {secrets_dir / 'wg-esp.conf'}")

    ps = "CONFIG_POC_WIFI_PS_MIN_MODEM=y" if s["WIFI_POWER_SAVE"] == "min_modem" else "CONFIG_POC_WIFI_PS_NONE=y"
    sdk = [
        "# Generated by scripts/tm.py config from secrets/secrets.env — do not commit.",
        f"CONFIG_POC_WIFI_SSID={kconfig_str(s['WIFI_SSID'])}",
        f"CONFIG_POC_WIFI_PASSWORD={kconfig_str(s['WIFI_PASSWORD'])}",
        ps,
        f"CONFIG_POC_WG_PRIVATE_KEY=\"{esp_priv}\"",
        f"CONFIG_POC_WG_PEER_PUBLIC_KEY=\"{peer_pub}\"",
        f"CONFIG_POC_WG_PRESHARED_KEY=\"{psk}\"",
        f"CONFIG_POC_WG_PEER_ENDPOINT={kconfig_str(endpoint)}",
        f"CONFIG_POC_WG_PEER_PORT={s['WG_PORT']}",
        f"CONFIG_POC_WG_LOCAL_IP=\"{s['ESP_TUNNEL_IP']}\"",
        f"CONFIG_POC_WG_LOCAL_NETMASK=\"{s['TUNNEL_NETMASK']}\"",
        f"CONFIG_POC_WG_KEEPALIVE={s['WG_KEEPALIVE']}",
    ]
    _write_private(fw_dir / "sdkconfig.secrets", "\n".join(sdk) + "\n")
    ok(f"firmware: {fw_dir / 'sdkconfig.secrets'}")
    return endpoint


# ---------------------------------------------------------------- serial ----
def _windows_usb_ports():
    """[(COMx, bridge-name)] for present USB-UART bridges, via the registry."""
    import winreg
    present = set()
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM") as k:
            i = 0
            while True:
                try:
                    present.add(winreg.EnumValue(k, i)[1])
                except OSError:
                    break
                i += 1
    except OSError:
        return []
    found = []
    try:
        usb = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum\USB")
    except OSError:
        return []
    with usb:
        i = 0
        while True:
            try:
                dev = winreg.EnumKey(usb, i)
            except OSError:
                break
            i += 1
            m = re.match(r"VID_([0-9A-F]{4})&PID_[0-9A-F]{4}", dev, re.I)
            if not m or m.group(1).upper() not in USB_UART_VIDS:
                continue
            with winreg.OpenKey(usb, dev) as dk:
                j = 0
                while True:
                    try:
                        inst = winreg.EnumKey(dk, j)
                    except OSError:
                        break
                    j += 1
                    try:
                        with winreg.OpenKey(dk, inst + r"\Device Parameters") as pk:
                            port = winreg.QueryValueEx(pk, "PortName")[0]
                    except OSError:
                        continue
                    if port in present:
                        found.append((port, USB_UART_VIDS[m.group(1).upper()]))
    return sorted(set(found))


def usb_serial_ports():
    if WINDOWS:
        return _windows_usb_ports()
    pats = (["/dev/cu.usbserial*", "/dev/cu.wchusbserial*", "/dev/cu.SLAB_USBtoUART*", "/dev/cu.usbmodem*"]
            if MACOS else ["/dev/ttyUSB*", "/dev/ttyACM*"])
    return [(p, "") for pat in pats for p in sorted(glob.glob(pat))]


def serial_port(s=None):
    """SERIAL_PORT from the form, or the first USB-UART bridge found."""
    if s is None:
        s = load_secrets(required=False)
    port = s.get("SERIAL_PORT", "")
    if port and port != "auto":
        return port
    ports = usb_serial_ports()
    if ports:
        if len(ports) > 1:
            info(f"nhiều cổng USB-serial: {', '.join(p for p, _ in ports)} — dùng {ports[0][0]} (đặt SERIAL_PORT để chọn)")
        return ports[0][0]
    return None


# --------------------------------------------------------------- ESP-IDF ----
def _idf_series(ver):
    m = re.match(r"v?(\d+)\.(\d+)", ver)
    return f"idf{m.group(1)}.{m.group(2)}" if m else "idf*"


def check_idf_paths(p):
    if WINDOWS:
        for k in ("IDF_PATH", "IDF_TOOLS_PATH"):
            if " " in p[k]:
                die(f"{k} có dấu cách ({p[k]}) — ESP-IDF trên Windows không hỗ trợ; đặt {k} khác trong config/project.env")


_IDF_ENV = None


def idf_env(p):
    """Environment with ESP-IDF exported (export.sh / export.bat), cached."""
    global _IDF_ENV
    if _IDF_ENV is not None:
        return _IDF_ENV
    check_idf_paths(p)
    idf_path = Path(p["IDF_PATH"])
    if (os.environ.get("IDF_PYTHON_ENV_PATH") and os.environ.get("IDF_PATH")
            and Path(os.environ["IDF_PATH"]).resolve() == idf_path.resolve()):
        _IDF_ENV = dict(os.environ)
        return _IDF_ENV
    if not (idf_path / "tools" / "idf.py").is_file():
        die(f"không thấy ESP-IDF tại {idf_path} — chạy: tm idf")
    env = dict(os.environ, IDF_PATH=str(idf_path), IDF_TOOLS_PATH=p["IDF_TOOLS_PATH"])
    dump = "import os,json;print(json.dumps(dict(os.environ)))"
    if WINDOWS:
        cmd = f'cmd /d /s /c ""{idf_path / "export.bat"}" >nul 2>&1 && python -c "{dump}""'
        r = subprocess.run(cmd, env=env, capture_output=True, text=True)
    else:
        r = subprocess.run(["bash", "-c", f'. "$IDF_PATH/export.sh" >/dev/null 2>&1 && python -c \'{dump}\''],
                           env=env, capture_output=True, text=True)
    try:
        _IDF_ENV = json.loads(r.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        die("export ESP-IDF lỗi — chạy lại: tm idf")
    return _IDF_ENV


def idf_python(env):
    venv = env.get("IDF_PYTHON_ENV_PATH")
    if venv:
        exe = Path(venv) / ("Scripts/python.exe" if WINDOWS else "bin/python")
        if exe.is_file():
            return str(exe)
    exe = shutil.which("python", path=env.get("PATH"))
    if not exe:
        die("không tìm thấy python của ESP-IDF — chạy lại: tm idf")
    return exe


def run(cmd, **kw):
    cmd = [str(c) for c in cmd]
    try:
        r = subprocess.run(cmd, **kw)
    except FileNotFoundError:
        die(f"không chạy được: {cmd[0]}")
    if r.returncode:
        raise Die(r.returncode)


def idf(p, *args):
    env = idf_env(p)
    run([idf_python(env), Path(env["IDF_PATH"]) / "tools" / "idf.py", *args], env=env, cwd=p["FW_DIR"])


# -------------------------------------------------------------- commands ----
COMMANDS = {}


def command(name, help_):
    def deco(fn):
        COMMANDS[name] = (fn, help_)
        return fn
    return deco


@command("doctor", "Kiểm tra môi trường (không thay đổi gì)")
def cmd_doctor(args):
    p = project()
    missing = 0

    def need(cond, good, fail, counts=True):
        nonlocal missing
        if cond:
            ok(good)
        else:
            bad(fail)
            missing += counts
        return cond

    info(f"Hệ thống ({'Windows' if WINDOWS else 'macOS' if MACOS else 'Linux'})")
    need(shutil.which("git"), "git", "git (Windows: winget install Git.Git)")
    need(sys.version_info >= (3, 8), f"python {sys.version.split()[0]}", "python >= 3.8")
    if not WINDOWS:
        try:
            import ensurepip  # noqa: F401
            import venv  # noqa: F401
            ok("python3-venv")
        except ImportError:
            need(False, "", "python3-venv")
        if shutil.which("dpkg"):
            for pkg in ("flex", "bison", "gperf", "libffi-dev", "libssl-dev", "libusb-1.0-0", "dfu-util", "ccache"):
                r = subprocess.run(["dpkg", "-s", pkg], capture_output=True)
                need(r.returncode == 0, pkg, f"{pkg} (make deps)")
    base = Path(p["IDF_PATH"])
    while not base.exists() and base.parent != base:
        base = base.parent
    free_gb = shutil.disk_usage(base).free // 2 ** 30
    need(free_gb >= 4, f"dung lượng trống {free_gb} GB ({base})", f"dung lượng trống {free_gb} GB (< 4 GB) tại {base}")

    info(f"ESP-IDF {p['IDF_VERSION']} ({p['IDF_PATH']})")
    if WINDOWS:
        for k in ("IDF_PATH", "IDF_TOOLS_PATH"):
            need(" " not in p[k], f"{k} không có dấu cách", f"{k} có dấu cách — đổi trong config/project.env")
    tools = Path(p["IDF_TOOLS_PATH"])
    if need((Path(p["IDF_PATH"]) / "tools" / "idf.py").is_file(), "source", "chưa có (tm idf)"):
        gcc = "xtensa-esp32-elf-gcc" + (".exe" if WINDOWS else "")
        need(glob.glob(str(tools / "tools" / "xtensa-esp-elf" / "*" / "xtensa-esp-elf" / "bin" / gcc)),
             "toolchain xtensa-esp32", "toolchain chưa cài (tm idf)")
        need(glob.glob(str(tools / "python_env" / f"{_idf_series(p['IDF_VERSION'])}_py*_env")),
             "python env", "python env chưa tạo (tm idf)")

    info("Cấu hình")
    need(SECRETS_FILE.is_file(), f"form {SECRETS_FILE}", "form chưa có (tm secrets)", counts=False)
    need((Path(p["FW_DIR"]) / "sdkconfig.secrets").is_file(), "sdkconfig.secrets",
         "sdkconfig.secrets chưa sinh (tm config) — build vẫn được nhưng firmware không kết nối", counts=False)

    info("Chạy (không cần để build)")
    need(_wg_tool(), "WireGuard", "WireGuard " + ("for Windows (winget install WireGuard.WireGuard)"
                                                if WINDOWS else "tools (make deps)"), counts=False)
    if not WINDOWS and not MACOS:
        r = subprocess.run(["id", "-nG"], capture_output=True, text=True)
        need("dialout" in r.stdout.split(), "user thuộc nhóm dialout",
             "user chưa thuộc nhóm dialout: sudo usermod -aG dialout $USER", counts=False)
    s = load_secrets(required=False)
    port = serial_port(s)
    ports = {pp for pp, _ in usb_serial_ports()}
    if port:
        present = port in ports or (not WINDOWS and Path(port).exists())
        need(present, f"serial {port}", f"không thấy {port} (chưa cắm board / thiếu driver?)", counts=False)
    else:
        need(False, "", "không thấy board USB-serial (chưa cắm / thiếu driver CH340/CP210x?)", counts=False)

    print()
    if missing:
        info(f"thiếu {missing} mục để build — xem ✘ ở trên")
        raise Die(1)
    info("đủ điều kiện build")


@command("deps", "Cài gói hệ thống cần thiết (Linux: apt + sudo; Windows: hướng dẫn)")
def cmd_deps(args):
    if WINDOWS:
        info("Windows không cần gói hệ thống riêng: `tm idf` tự tải cmake, ninja, toolchain.")
        print("    Cần có sẵn: Git (winget install Git.Git), Python 3.8+ (winget install Python.Python.3.12)")
        print("    Driver USB-UART của board: CH340 (WCH CH341SER) hoặc CP210x (Silicon Labs)")
        print("    Để chạy peer/bench: WireGuard for Windows (winget install WireGuard.WireGuard)")
        return
    run(["bash", ROOT / "scripts" / "install-deps.sh"])


@command("idf", "Clone + cài ESP-IDF và toolchain esp32")
def cmd_idf(args):
    p = project()
    check_idf_paths(p)
    idf_path = Path(p["IDF_PATH"])
    if not shutil.which("git"):
        die("chưa có git")
    if not (idf_path / "tools" / "idf.py").is_file():
        info(f"clone ESP-IDF {p['IDF_VERSION']} → {idf_path} (~650 MB)")
        idf_path.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--depth", "1", "--branch", p["IDF_VERSION"], "--recurse-submodules",
             "--shallow-submodules", "https://github.com/espressif/esp-idf.git", idf_path])
    else:
        ok(f"đã có ESP-IDF tại {idf_path}")
    info(f"install {p['IDF_TARGET']} (toolchain ~1.5 GB vào {p['IDF_TOOLS_PATH']})")
    env = dict(os.environ, IDF_PATH=str(idf_path), IDF_TOOLS_PATH=p["IDF_TOOLS_PATH"])
    try:
        if WINDOWS:
            run(["cmd", "/d", "/c", idf_path / "install.bat", p["IDF_TARGET"]], env=env, cwd=idf_path)
        else:
            run([idf_path / "install.sh", p["IDF_TARGET"]], env=env, cwd=idf_path)
    except Die:
        if not os.environ.get("IDF_GITHUB_ASSETS"):
            info("nếu lỗi là CERTIFICATE_VERIFY_FAILED khi tải từ github.com, tải qua mirror của Espressif:")
            print("    Windows:      $env:IDF_GITHUB_ASSETS='dl.espressif.com/github_assets'; .\\tm idf")
            print("    Linux/macOS:  IDF_GITHUB_ASSETS=dl.espressif.com/github_assets make idf")
        raise
    ok("xong. Kiểm tra: tm doctor")


@command("secrets", "Tạo form secrets/secrets.env (không ghi đè)")
def cmd_secrets(args):
    if SECRETS_FILE.is_file():
        info(f"đã có {SECRETS_FILE} (giữ nguyên). Sửa file đó rồi chạy: tm config")
        return
    _write_private(SECRETS_FILE, SECRETS_FORM.read_text(encoding="utf-8"))
    if not WINDOWS:
        os.chmod(SECRETS_DIR, 0o700)
    info(f"đã tạo form: {SECRETS_FILE}")
    print("    Điền ít nhất WIFI_SSID / WIFI_PASSWORD, sau đó chạy: tm config")


@command("config", "Kiểm tra form, sinh khóa + cấu hình peer + sdkconfig.secrets")
def cmd_config(args):
    p = project()
    s = load_secrets()
    info(f"kiểm tra {SECRETS_FILE}")
    endpoint = gen_config(s, SECRETS_DIR, Path(p["FW_DIR"]))
    print(f"\n  Peer {s['LINUX_TUNNEL_IP']}  ←UDP {endpoint}:{s['WG_PORT']}→  ESP32 {s['ESP_TUNNEL_IP']}")
    print("  Tiếp theo:  tm build   (và  tm peer-up  khi có board)")


SDKCONFIG_STAMP = "build/.sdkconfig-inputs.sha"


def _refresh_sdkconfig(fw):
    """sdkconfig.defaults/.secrets only apply when sdkconfig does not exist:
    delete sdkconfig whenever either input changed since the last build.
    Returns the inputs' hash; the caller stores it once build/ is a CMake dir
    (idf.py set-target runs fullclean, which refuses a non-CMake build/)."""
    h = hashlib.sha256()
    for name in ("sdkconfig.defaults", "sdkconfig.secrets"):
        f = fw / name
        if f.is_file():
            h.update(f.read_bytes())
    stamp = fw / SDKCONFIG_STAMP
    if (fw / "sdkconfig").is_file() and (not stamp.is_file() or stamp.read_text().strip() != h.hexdigest()):
        info("sdkconfig.defaults/secrets đã đổi → tạo lại sdkconfig (thay đổi menuconfig cũ sẽ mất)")
        (fw / "sdkconfig").unlink()
    build = fw / "build"
    if build.is_dir() and not (build / "CMakeCache.txt").exists() and \
            all(c.name == stamp.name for c in build.iterdir()):
        shutil.rmtree(build)  # only a stale stamp left: let set-target create build/
    return h.hexdigest()


@command("build", "Build firmware")
def cmd_build(args):
    p = project()
    fw = Path(p["FW_DIR"])
    if not (fw / "sdkconfig.secrets").is_file():
        info("CẢNH BÁO: chưa có sdkconfig.secrets (tm config) — firmware sẽ không kết nối Wi-Fi/WireGuard")
    idf_env(p)
    digest = _refresh_sdkconfig(fw)
    if not (fw / "sdkconfig").is_file():
        idf(p, "set-target", p["IDF_TARGET"])
    (fw / SDKCONFIG_STAMP).parent.mkdir(parents=True, exist_ok=True)
    (fw / SDKCONFIG_STAMP).write_text(digest + "\n")
    idf(p, "build")


@command("size", "Báo cáo Flash/RAM tĩnh → results/")
def cmd_size(args):
    p = project()
    RESULTS_DIR.mkdir(exist_ok=True)
    env = idf_env(p)
    base = [idf_python(env), str(Path(env["IDF_PATH"]) / "tools" / "idf.py")]
    for sub, out, echo in (("size", "size.txt", True), ("size-components", "size-components.txt", False)):
        r = subprocess.run(base + [sub], env=env, cwd=p["FW_DIR"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode:
            sys.stderr.write(r.stdout + r.stderr)
            raise Die(r.returncode)
        (RESULTS_DIR / out).write_text(r.stdout, encoding="utf-8")
        if echo:
            print(r.stdout, end="")
    ok("lưu: results/size.txt, results/size-components.txt")


def _port_args():
    s = load_secrets(required=False)
    port = serial_port(s)
    if not port:
        info("không thấy board USB-serial — để idf.py tự dò cổng")
        return ["-b", s["FLASH_BAUD"]]
    info(f"cổng: {port}")
    return ["-p", port, "-b", s["FLASH_BAUD"]]


@command("flash", "Nạp firmware (SERIAL_PORT trong form, trống = tự dò)")
def cmd_flash(args):
    p = project()
    idf(p, *_port_args(), "flash")


@command("monitor", "Mở serial monitor (Ctrl+] để thoát)")
def cmd_monitor(args):
    p = project()
    idf(p, *_port_args(), "monitor")


@command("flash-monitor", "Nạp rồi mở monitor")
def cmd_flash_monitor(args):
    p = project()
    idf(p, *_port_args(), "flash", "monitor")


@command("menuconfig", "idf.py menuconfig")
def cmd_menuconfig(args):
    idf(project(), "menuconfig")


@command("clean", "idf.py clean")
def cmd_clean(args):
    idf(project(), "clean")


@command("fullclean", "Xóa build + sdkconfig")
def cmd_fullclean(args):
    p = project()
    idf(p, "fullclean")
    for f in ("sdkconfig", "sdkconfig.old"):
        (Path(p["FW_DIR"]) / f).unlink(missing_ok=True)


@command("docker-build", "Build bằng image espressif/idf (chỉ cần Docker)")
def cmd_docker_build(args):
    p = project()
    if not shutil.which("docker"):
        die("chưa cài docker")
    if not (Path(p["FW_DIR"]) / "sdkconfig.secrets").is_file():
        info("CẢNH BÁO: chưa có sdkconfig.secrets (tm config)")
    info(f"espressif/idf:{p['IDF_VERSION']}  →  {p['FW_PROJECT']}/build")
    user = [] if WINDOWS else ["-u", f"{os.getuid()}:{os.getgid()}"]
    run(["docker", "run", "--rm", *user, "-e", "HOME=/tmp", "-v", f"{p['FW_DIR']}:/project", "-w", "/project",
         f"espressif/idf:{p['IDF_VERSION']}", "bash", "-c",
         f"[ -f sdkconfig ] || idf.py set-target {p['IDF_TARGET']}; idf.py build"])


# ------------------------------------------------------------------ peer ----
_WG_WIN_DIR = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "WireGuard"
TUNNEL = "wg-esp"
FW_RULE = "tailmote-wg-esp"


def _wg_tool():
    if WINDOWS:
        return (_WG_WIN_DIR / "wireguard.exe").is_file() or shutil.which("wireguard")
    return shutil.which("wg")


def _win_admin():
    import ctypes
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def _peer(action):
    conf = SECRETS_DIR / f"{TUNNEL}.conf"
    if not conf.is_file():
        die(f"chưa có {conf} — chạy: tm config")
    if not WINDOWS:
        if action == "status":
            run(["sudo", "wg", "show", TUNNEL])
        else:
            run(["sudo", "wg-quick", action, conf])
        return
    wg_exe = _WG_WIN_DIR / "wireguard.exe"
    if not wg_exe.is_file():
        die("chưa cài WireGuard for Windows: winget install WireGuard.WireGuard")
    if not _win_admin():
        die("cần quyền admin: mở PowerShell bằng 'Run as administrator' rồi chạy lại")
    if action == "status":
        run([_WG_WIN_DIR / "wg.exe", "show", TUNNEL])
        return
    port = load_secrets()["WG_PORT"]
    subprocess.run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={FW_RULE}"], capture_output=True)
    if action == "up":
        run([wg_exe, "/installtunnelservice", conf])
        run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={FW_RULE}", "dir=in", "action=allow",
             "protocol=UDP", f"localport={port}"], stdout=subprocess.DEVNULL)
        ok(f"tunnel {TUNNEL} đang chạy; firewall mở UDP {port} (rule {FW_RULE})")
    else:
        run([wg_exe, "/uninstalltunnelservice", TUNNEL])
        ok(f"đã tắt tunnel {TUNNEL} và xóa rule firewall {FW_RULE}")


@command("peer-up", "Bật WireGuard peer trên máy này (Linux: sudo; Windows: admin)")
def cmd_peer_up(args):
    _peer("up")


@command("peer-down", "Tắt WireGuard peer")
def cmd_peer_down(args):
    _peer("down")


@command("peer-status", "wg show")
def cmd_peer_status(args):
    _peer("status")


# ----------------------------------------------------------------- bench ----
def _python_with_pyserial():
    try:
        import serial  # noqa: F401
        return sys.executable
    except ImportError:
        return idf_python(idf_env(project()))


@command("log", "Reset board và ghi log → results/serial.log")
def cmd_log(args):
    port = serial_port()
    if not port:
        die("không thấy board USB-serial — đặt SERIAL_PORT trong secrets/secrets.env")
    run([_python_with_pyserial(), BENCH_DIR / "serial_capture.py", "--port", port, "--reset",
         "--out", RESULTS_DIR / "serial.log", *args.rest])


@command("bench", "Chạy bộ đo qua tunnel (--host <ip> để đo baseline LAN, --label ...)")
def cmd_bench(args):
    ap = argparse.ArgumentParser(prog="tm bench")
    ap.add_argument("--host")
    ap.add_argument("--label")
    a, rest = ap.parse_known_args(args.rest)
    s = load_secrets()
    host = a.host or s["ESP_TUNNEL_IP"]
    label = a.label or f"{'baseline LAN' if a.host else 'tunnel'} PS={s['WIFI_POWER_SAVE']}"
    run([sys.executable, BENCH_DIR / "wg_bench.py", "all", "--host", host, "--label", label,
         "--out", RESULTS_DIR / "bench.jsonl", *rest])


@command("metrics", "Tóm tắt results/serial.log → results/periodic.csv")
def cmd_metrics(args):
    run([sys.executable, BENCH_DIR / "parse_metrics.py", RESULTS_DIR / "serial.log",
         "--csv", RESULTS_DIR / "periodic.csv", *args.rest])


@command("test", "Unit test cho tooling (không cần board / ESP-IDF)")
def cmd_test(args):
    run([sys.executable, "-m", "unittest", "discover", "-s", ROOT / "tests", "-v", *args.rest],
        cwd=ROOT)


@command("help", "Danh sách lệnh")
def cmd_help(args):
    print(__doc__.strip().split("\n\n")[0])
    for name, (_, h) in COMMANDS.items():
        print(f"  {_c(36, f'{name:<14}')} {h}")


def main(argv=None):
    global _COLOR
    _setup_console()
    _COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    argv = list(sys.argv[1:] if argv is None else argv)
    name = argv.pop(0) if argv else "help"
    if name in ("-h", "--help"):
        name = "help"
    if name not in COMMANDS:
        cmd_help(None)
        die(f"không rõ lệnh: {name}")
    try:
        COMMANDS[name][0](argparse.Namespace(rest=argv))
    except KeyboardInterrupt:
        raise Die(130)


if __name__ == "__main__":
    main()
