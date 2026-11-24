# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The esp32-tailscale Authors
"""Unit tests for scripts/tm.py (no board, no ESP-IDF needed).

  make test   |   .\\tm test   |   python -m unittest discover -s tests
"""
import base64
import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location("tm", Path(__file__).resolve().parent.parent / "scripts" / "tm.py")
tm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tm)

h = bytes.fromhex


class X25519(unittest.TestCase):
    def test_rfc7748_5_2_vectors(self):
        self.assertEqual(
            tm.x25519(h("a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4"),
                      h("e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c")),
            h("c3da55379de9c6908e94ea4df28d084f32eccf03491c71f754b4075577a28552"))
        self.assertEqual(
            tm.x25519(h("4b66e9d4d1b4673c5ad22691957d6af5c11b6421e0ea01d42ca4169e7918ba0d"),
                      h("e5210f12786811d3f4b7959d0538ae2c31dbe7106fc03c3efc4cd549c715a493")),
            h("95cbde9476e8907d7aade45cb4b873f88b595a68799fa152e6f8f7647aac7957"))

    def test_rfc7748_6_1_diffie_hellman(self):
        a = h("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
        b = h("5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb")
        nine = (9).to_bytes(32, "little")
        a_pub, b_pub = tm.x25519(a, nine), tm.x25519(b, nine)
        self.assertEqual(a_pub, h("8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a"))
        self.assertEqual(b_pub, h("de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f"))
        shared = h("4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742")
        self.assertEqual(tm.x25519(a, b_pub), shared)
        self.assertEqual(tm.x25519(b, a_pub), shared)

    def test_genkey_is_clamped_wireguard_key(self):
        for _ in range(20):
            k = tm.wg_genkey()
            self.assertTrue(tm.is_key(k), k)
            raw = base64.b64decode(k)
            self.assertEqual(raw[0] & 7, 0)
            self.assertEqual(raw[31] & 0xC0, 0x40)
            self.assertTrue(tm.is_key(tm.wg_pubkey(k)))

    def test_pubkey_base64(self):
        self.assertEqual(tm.wg_pubkey(base64.b64encode(h(
            "77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")).decode()),
            base64.b64encode(h("8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a")).decode())


class EnvParser(unittest.TestCase):
    def test_shell_quoting(self):
        env = tm.parse_env_text(
            "# comment\n"
            "\n"
            "A='plain'\n"
            "B=''\n"
            "C='it'\\''s'\n"
            'D="dq \\"x\\""\n'
            "E='has # hash'   # trailing comment\n"
            "F='C:\\Espressif\\x'\n"
            "G=bare\n"
            "H='sp ace'\n")
        self.assertEqual(env, {"A": "plain", "B": "", "C": "it's", "D": 'dq "x"', "E": "has # hash",
                               "F": "C:\\Espressif\\x", "G": "bare", "H": "sp ace"})

    def test_errors(self):
        for bad_text in ("A='unterminated\n", "A=two words\n", "no_equals\n", "1A='x'\n"):
            with self.subTest(bad_text=bad_text), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    tm.parse_env_text(bad_text)

    def test_shipped_files_parse(self):
        form = tm.read_env_file(tm.SECRETS_FORM)
        self.assertEqual(set(form), set(tm.SECRET_DEFAULTS))
        proj = tm.read_env_file(tm.ROOT / "config" / "project.env")
        self.assertIn("IDF_VERSION", proj)


class Validation(unittest.TestCase):
    def good(self, **kw):
        s = dict(tm.SECRET_DEFAULTS, WIFI_SSID="lab", WIFI_PASSWORD="password123")
        s.update(kw)
        return s

    def test_defaults_valid(self):
        self.assertEqual(tm.validate_secrets(self.good()), [])

    def test_catches_errors(self):
        cases = {
            "WIFI_SSID": "", "WIFI_PASSWORD": "short", "ESP_TUNNEL_IP": "10.66.0.300",
            "WG_PORT": "70000", "WIFI_POWER_SAVE": "max", "ESP_PRIVATE_KEY": "notakey",
            "PRESHARED_KEY": "nope", "WG_KEEPALIVE": "-1", "TUNNEL_PREFIX": "33",
        }
        for k, v in cases.items():
            with self.subTest(k=k):
                self.assertTrue(tm.validate_secrets(self.good(**{k: v})))
        self.assertTrue(tm.validate_secrets(self.good(ESP_TUNNEL_IP="10.66.0.1")))

    def test_kconfig_escape(self):
        self.assertEqual(tm.kconfig_str('a"b\\c'), '"a\\"b\\\\c"')


class GenConfig(unittest.TestCase):
    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
            d = Path(d)
            sec, fw = d / "secrets", d / "fw"
            fw.mkdir()
            s = dict(tm.SECRET_DEFAULTS, WIFI_SSID='my "lab"', WIFI_PASSWORD="pa\\ss1234",
                     WG_ENDPOINT="192.168.1.10", PRESHARED_KEY="auto")
            self.assertEqual(tm.gen_config(s, sec, fw), "192.168.1.10")
            esp_priv = (sec / "esp32.key").read_text().strip()
            peer_priv = (sec / "linux.key").read_text().strip()
            psk = (sec / "psk").read_text().strip()

            conf = (sec / "wg-esp.conf").read_text()
            self.assertIn(f"PrivateKey = {peer_priv}\n", conf)
            self.assertIn(f"PublicKey = {tm.wg_pubkey(esp_priv)}\n", conf)
            self.assertIn(f"PresharedKey = {psk}\n", conf)
            self.assertIn("AllowedIPs = 10.66.0.2/32\n", conf)
            self.assertIn("ListenPort = 51820\n", conf)
            self.assertNotIn("\r", conf)

            sdk = (fw / "sdkconfig.secrets").read_text()
            self.assertIn('CONFIG_POC_WIFI_SSID="my \\"lab\\""\n', sdk)
            self.assertIn('CONFIG_POC_WIFI_PASSWORD="pa\\\\ss1234"\n', sdk)
            self.assertIn(f'CONFIG_POC_WG_PRIVATE_KEY="{esp_priv}"\n', sdk)
            self.assertIn(f'CONFIG_POC_WG_PEER_PUBLIC_KEY="{tm.wg_pubkey(peer_priv)}"\n', sdk)
            self.assertIn('CONFIG_POC_WG_PEER_ENDPOINT="192.168.1.10"\n', sdk)
            self.assertIn("CONFIG_POC_WIFI_PS_NONE=y\n", sdk)

            # Re-running keeps the generated keys.
            tm.gen_config(s, sec, fw)
            self.assertEqual((sec / "esp32.key").read_text().strip(), esp_priv)
            self.assertEqual((sec / "psk").read_text().strip(), psk)

    def test_invalid_form_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            d = Path(d)
            with self.assertRaises(SystemExit):
                tm.gen_config(dict(tm.SECRET_DEFAULTS, WG_ENDPOINT="1.2.3.4"), d / "s", d / "fw")
            self.assertFalse((d / "s").exists())


class Misc(unittest.TestCase):
    def test_idf_series(self):
        self.assertEqual(tm._idf_series("v5.3.6"), "idf5.3")

    def test_serial_port_override(self):
        self.assertEqual(tm.serial_port(dict(tm.SECRET_DEFAULTS, SERIAL_PORT="COM9")), "COM9")

    def test_usb_serial_ports_runs(self):
        self.assertIsInstance(tm.usb_serial_ports(), list)


if __name__ == "__main__":
    unittest.main()
