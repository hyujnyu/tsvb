#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XXNETRO WiFi Suite v3.1 — The Ghost Scanner
Author : XXNETRO
Date   : 2027-02-16
License: All Rights Reserved to XXNETRO
Built for unrooted Android. No root. No trace.
"""

import os
import re
import sys
import json
import time
import shutil
import curses
import random
import hashlib
import getpass
import importlib
import subprocess
import threading
from collections import deque, Counter
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional

class C:
    R = "\033[0m"
    B = "\033[94m"
    G = "\033[92m"
    Y = "\033[93m"
    C = "\033[96m"
    M = "\033[95m"
    W = "\033[97m"
    D = "\033[90m"
    BOLD = "\033[1m"

VERSION = "3.1"
AUTHOR = "XXNETRO"
YEAR = "2027"
BUILD = "2027-02-16"
CODENAME = "The Ghost Scanner"

TX_POWER_DBM = 20.0
REF_LOSS_DB = 40.0
PATH_LOSS_N = 3.5
SCAN_INTERVAL_S = 32
ASSOCIATION_TIMEOUT_S = 30

HOME_DIR = os.environ.get("TERMUX_HOME", os.path.expanduser("~"))
CACHE_FILE = os.path.join(HOME_DIR, ".xxnetro_cache.json")
EXPORT_DIR = os.path.join(HOME_DIR, "xxnetro_exports")
MODULES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modules")


def tool_exists(name):
    return shutil.which(name) is not None


def ensure_dirs():
    for d in (EXPORT_DIR, MODULES_DIR):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass


def clear_stale_pycache():
    cache = os.path.join(MODULES_DIR, "__pycache__")
    try:
        if os.path.isdir(cache):
            shutil.rmtree(cache, ignore_errors=True)
    except OSError:
        pass


ensure_dirs()
clear_stale_pycache()


@dataclass
class WiFiNetwork:
    bssid: str = ""
    ssid: str = "[Hidden]"
    frequency_mhz: int = 0
    channel: int = 0
    signal_dbm: int = -100
    security: str = "unknown"
    vendor: str = "Unknown"
    estimated_distance_m: float = 0.0
    quality_pct: int = 0
    band: str = "2.4GHz"
    capabilities: str = ""
    center_frequency_mhz: int = 0
    channel_bandwidth: str = ""
    last_seen: float = field(default_factory=time.time)
    first_seen: float = field(default_factory=time.time)
    seen_count: int = 1
    signal_history: list = field(default_factory=list)

    def compute_derived(self):
        self.band = "5GHz" if self.frequency_mhz >= 5000 else "2.4GHz"
        if self.channel == 0 and self.frequency_mhz > 0:
            if self.frequency_mhz == 2484:
                self.channel = 14
            elif 2412 <= self.frequency_mhz <= 2472:
                self.channel = (self.frequency_mhz - 2407) // 5
            elif 5160 <= self.frequency_mhz <= 5885:
                self.channel = (self.frequency_mhz - 5000) // 5
        c = max(-90, min(-30, self.signal_dbm))
        self.quality_pct = int(round(((c + 90) / 60.0) * 100))
        if self.signal_dbm >= 0:
            self.estimated_distance_m = 0.5
        else:
            exp = (TX_POWER_DBM - float(self.signal_dbm) - REF_LOSS_DB) / (10.0 * PATH_LOSS_N)
            self.estimated_distance_m = max(0.5, 10 ** max(0, exp))

    @property
    def quality_label(self):
        q = self.quality_pct
        if q >= 75:
            return "Excellent"
        if q >= 55:
            return "Good"
        if q >= 35:
            return "Fair"
        if q >= 15:
            return "Weak"
        return "Very weak"

    @property
    def display_name(self):
        return self.ssid if self.ssid != "[Hidden]" else "(hidden)"

    def record_signal(self):
        self.signal_history.append((time.time(), self.signal_dbm))
        if len(self.signal_history) > 20:
            self.signal_history = self.signal_history[-20:]

    @property
    def signal_stats(self):
        if not self.signal_history:
            return None
        vals = [v for _, v in self.signal_history]
        return {"min": min(vals), "max": max(vals),
                "avg": sum(vals) / len(vals),
                "samples": len(vals), "trend": self._trend}

    @property
    def _trend(self):
        if len(self.signal_history) < 3:
            return "n/a"
        recent = [v for _, v in self.signal_history[-3:]]
        delta = recent[-1] - recent[0]
        if delta >= 6:
            return "rising"
        if delta <= -6:
            return "falling"
        return "stable"

    def to_dict(self):
        d = asdict(self)
        d["signal_history"] = [[t, v] for t, v in self.signal_history]
        return d

    @classmethod
    def from_dict(cls, d):
        valid = {f for f in cls.__dataclass_fields__ if f in d}
        kwargs = {k: d[k] for k in valid}
        if "signal_history" in kwargs:
            kwargs["signal_history"] = [(t, v) for t, v in kwargs["signal_history"]]
        return cls(**kwargs)

    def full_report(self):
        lines = ["SSID          : " + self.display_name,
                 "BSSID         : " + self.bssid,
                 "Vendor        : " + self.vendor,
                 "Band          : " + self.band,
                 "Channel       : " + str(self.channel),
                 "Frequency     : " + str(self.frequency_mhz) + " MHz",
                 "Signal        : " + str(self.signal_dbm) + " dBm [" + str(self.quality_pct) + "% " + self.quality_label + "]",
                 "Est. distance : ~" + str(int(self.estimated_distance_m)) + " m",
                 "Security      : " + self.security,
                 "Capabilities  : " + self.capabilities[:60],
                 "Seen count    : " + str(self.seen_count)]
        stats = self.signal_stats
        if stats:
            lines.append("Signal stats  : min " + str(stats["min"]) + " max " + str(stats["max"]) + " avg " + str(int(stats["avg"])) + " trend " + stats["trend"])
        return lines


class OUI:
    _DB = {
        "00:11:32": "Apple", "00:1B:44": "Apple", "00:1E:58": "Apple",
        "28:E3:1F": "Apple", "3C:15:C2": "Apple", "F0:18:98": "Apple",
        "00:1D:7E": "Cisco", "04:18:D6": "Cisco", "00:00:0C": "Cisco",
        "00:25:9C": "Huawei", "08:57:00": "Huawei", "0C:80:63": "Huawei",
        "20:DC:E6": "Huawei", "30:B5:C2": "Huawei", "50:2B:73": "Huawei",
        "50:CC:F8": "Huawei", "58:8D:09": "Huawei", "78:11:DC": "Huawei",
        "88:36:6C": "Huawei", "90:0F:0C": "Huawei", "A0:AB:1B": "Huawei",
        "A8:57:4E": "Huawei", "AC:84:C6": "Huawei", "BC:54:36": "Huawei",
        "E4:6F:13": "Huawei", "FC:48:EF": "Huawei",
        "04:D4:C4": "TP-Link", "08:63:61": "TP-Link", "54:C8:0F": "TP-Link",
        "68:FF:7B": "TP-Link", "84:16:F9": "TP-Link", "C8:0C:C8": "TP-Link",
        "CC:32:E5": "TP-Link", "D4:6E:0E": "TP-Link", "EC:D0:9F": "TP-Link",
        "F4:28:53": "TP-Link", "F8:1A:67": "TP-Link", "50:C7:BF": "TP-Link",
        "18:A6:F7": "Xiaomi", "60:32:B1": "Xiaomi", "64:6C:B1": "Xiaomi",
        "28:6C:07": "Xiaomi Router",
        "24:6F:28": "Netgear", "64:D4:DA": "Netgear", "8C:EE:69": "Netgear",
        "A0:40:A0": "Netgear", "B0:39:56": "Netgear",
        "0C:9D:C7": "Ubiquiti", "44:D9:E7": "Ubiquiti", "4C:ED:FB": "Ubiquiti",
        "F0:9F:C2": "Ubiquiti", "24:5A:4C": "Ubiquiti",
        "00:17:9A": "D-Link", "34:CE:00": "D-Link", "5C:3C:27": "D-Link",
        "B8:D9:31": "D-Link", "6C:19:8F": "D-Link",
        "78:8C:B5": "Zyxel", "94:09:AC": "Zyxel", "B0:9A:7A": "Zyxel",
        "D8:07:B6": "Zyxel", "E8:DE:27": "Zyxel",
        "70:5D:1E": "ZTE", "7C:8C:E1": "ZTE", "F4:60:4F": "ZTE",
        "48:8C:14": "Samsung", "00:16:32": "Samsung", "00:25:66": "Samsung",
        "04:F0:21": "Asus", "00:0C:6E": "Asus",
        "10:2A:B3": "Tenda", "98:FA:E3": "Tenda",
        "10:5B:56": "Mercusys", "2C:AB:00": "Realtek",
        "3C:5A:B4": "Google", "A4:77:33": "Google",
        "40:4E:36": "Microsoft", "00:15:5D": "Hyper-V",
        "00:50:56": "VMware", "00:0C:29": "VMware", "08:00:27": "VirtualBox",
        "B8:27:EB": "Raspberry Pi", "DC:A6:32": "Raspberry Pi",
        "C0:3F:0E": "Sagemcom", "74:DA:38": "Edimax", "00:1A:2B": "Ayecom",
    }

    @classmethod
    def lookup(cls, mac):
        if not mac or len(mac) < 8:
            return "Unknown"
        mac = mac.upper().replace("-", ":").replace(".", ":")
        try:
            first = int(mac[0:2], 16)
            if first & 0x02:
                if mac[:8] not in ("00:0C:29", "00:50:56", "08:00:27"):
                    return "Randomized/Local"
        except ValueError:
            return "Unknown"
        return cls._DB.get(mac[:8], "Unknown")


class GhostMAC:
    @staticmethod
    def generate():
        first = random.randint(0x00, 0xFF) | 0x02
        first &= 0xFE
        rest = [random.randint(0x00, 0xFF) for _ in range(5)]
        return ":".join("%02x" % b for b in [first] + rest)

    @staticmethod
    def generate_stable(seed):
        h = int(hashlib.md5(seed.encode()).hexdigest()[:12], 16)
        first = (h >> 40) & 0xFF
        first = (first | 0x02) & 0xFE
        rest = [(h >> s) & 0xFF for s in (32, 24, 16, 8, 0)]
        return ":".join("%02x" % b for b in [first] + rest)

    @staticmethod
    def info():
        return "Android 10+ assigns a random MAC to every network automatically."


class SessionStats:
    def __init__(self):
        self.started = time.time()
        self.scans_performed = 0
        self.networks_seen = 0
        self.events_fired = 0
        self.connect_attempts = 0
        self.connect_successes = 0
        self.new_network_events = 0
        self.gone_network_events = 0

    def uptime_str(self):
        s = int(time.time() - self.started)
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        if h:
            return "%dh%02dm%02ds" % (h, m, sec)
        return "%02dm%02ds" % (m, sec)

    def report(self):
        return ("uptime " + self.uptime_str() +
                " | scans " + str(self.scans_performed) +
                " | nets " + str(self.networks_seen) +
                " | new " + str(self.new_network_events) +
                " gone " + str(self.gone_network_events) +
                " | connect " + str(self.connect_attempts) +
                " (" + str(self.connect_successes) + " ok)")

    def to_dict(self):
        return {"started": self.started, "scans": self.scans_performed,
                "networks": self.networks_seen,
                "connect_attempts": self.connect_attempts,
                "connect_ok": self.connect_successes}


class Scanner:
    def __init__(self):
        self.has_root = self._check_su()
        self.api_available = tool_exists("termux-wifi-scaninfo")
        self.iwlist_available = tool_exists("iwlist")
        self.tracked = {}
        self.last_source = "none"
        self._last_scan = 0.0
        self.interval_s = SCAN_INTERVAL_S
        self.stats = SessionStats()
        self._scan_lock = threading.Lock()

    def _check_su(self):
        try:
            r = subprocess.run(["su", "-c", "id"],
                               capture_output=True, text=True, timeout=2.5)
            return r.returncode == 0 and "uid=0" in (r.stdout or "")
        except Exception:
            return False

    def can_scan(self):
        return (time.time() - self._last_scan) >= self.interval_s

    def next_scan_in(self):
        return max(0, int(self.interval_s - (time.time() - self._last_scan)))

    @staticmethod
    def _sig(e):
        raw = e.get("signal_strength", e.get("rssi", -100))
        try:
            s = float(raw)
        except (TypeError, ValueError):
            s = -100
        if s > 0:
            s = (s / 2.0) - 100.0
        return int(max(-100, min(0, s)))

    @staticmethod
    def _sec_from_caps(caps):
        caps = caps.upper()
        if not caps:
            return "unknown"
        if "WPA3" in caps or "SAE" in caps:
            return "wpa3"
        if "RSN" in caps or "WPA2" in caps:
            return "wpa2"
        if "WPA" in caps:
            return "wpa"
        return "open"

    def scan(self, force=False):
        with self._scan_lock:
            if not force and not self.can_scan():
                return self.list_networks()
            results = []
            if self.api_available:
                results = self._scan_api()
                self.last_source = "termux-api"
            if not results and self.has_root and self.iwlist_available:
                results = self._scan_iwlist()
                self.last_source = "iwlist-root"
            self._last_scan = time.time()
            self.stats.scans_performed += 1
            if results:
                self._merge(results)
                self._save_cache(results)
            return self.list_networks()

    def _merge(self, results):
        now = time.time()
        for net in results:
            if not net.bssid:
                continue
            net.compute_derived()
            net.vendor = OUI.lookup(net.bssid)
            if net.bssid in self.tracked:
                p = self.tracked[net.bssid]
                p.seen_count += 1
                p.last_seen = now
                p.signal_dbm = net.signal_dbm
                p.quality_pct = net.quality_pct
                p.estimated_distance_m = net.estimated_distance_m
                if net.ssid != "[Hidden]":
                    p.ssid = net.ssid
                if net.security != "unknown":
                    p.security = net.security
                if net.capabilities:
                    p.capabilities = net.capabilities
                if net.channel:
                    p.channel = net.channel
                if net.frequency_mhz:
                    p.frequency_mhz = net.frequency_mhz
                p.record_signal()
            else:
                net.first_seen = now
                net.last_seen = now
                net.record_signal()
                self.tracked[net.bssid] = net
                self.stats.networks_seen += 1

    def list_networks(self, sort_by="signal"):
        nets = list(self.tracked.values())
        if sort_by == "signal":
            nets.sort(key=lambda n: n.signal_dbm, reverse=True)
        elif sort_by == "distance":
            nets.sort(key=lambda n: n.estimated_distance_m)
        elif sort_by == "ssid":
            nets.sort(key=lambda n: n.ssid.lower())
        elif sort_by == "channel":
            nets.sort(key=lambda n: (n.channel, n.signal_dbm), reverse=True)
        elif sort_by == "security":
            nets.sort(key=lambda n: n.security)
        return nets

    def channel_congestion(self):
        counter = Counter()
        for n in self.tracked.values():
            if n.channel:
                counter[n.channel] += 1
        return dict(counter)

    def best_channel(self, band="2.4GHz"):
        cong = self.channel_congestion()
        if band == "2.4GHz":
            candidates = [1, 6, 11]
        else:
            candidates = [36, 40, 44, 48, 149, 153, 157, 161]
        best, best_count = None, 99
        for ch in candidates:
            c = cong.get(ch, 0)
            if c < best_count:
                best, best_count = ch, c
        return best

    def _scan_api(self):
        nets = []
        try:
            r = subprocess.run(["termux-wifi-scaninfo"],
                               capture_output=True, text=True, timeout=25)
            if r.returncode != 0 or not (r.stdout or "").strip():
                return nets
            payload = json.loads(r.stdout)
            if not isinstance(payload, list):
                return nets
            for e in payload:
                caps = str(e.get("capabilities", "") or "")
                bw = str(e.get("channel_bandwidth_mhz", "") or "")
                cf = e.get("center_frequency_mhz", 0)
                nets.append(WiFiNetwork(
                    bssid=str(e.get("bssid", "")).lower(),
                    ssid=e.get("ssid") or "[Hidden]",
                    frequency_mhz=int(e.get("frequency_mhz", 0) or 0),
                    signal_dbm=self._sig(e),
                    capabilities=caps,
                    security=self._sec_from_caps(caps),
                    channel_bandwidth=bw,
                    center_frequency_mhz=int(cf or 0)))
        except Exception:
            pass
        return nets

    def _scan_iwlist(self):
        nets = []
        try:
            r = subprocess.run(["su", "-c", "iwlist scan 2>/dev/null"],
                               capture_output=True, text=True, timeout=30)
            if r.returncode != 0 or "Cell" not in (r.stdout or ""):
                return nets
            current = None
            for line in (r.stdout or "").splitlines():
                line = line.strip()
                if line.startswith("Cell") or "Address:" in line[:40]:
                    if current:
                        nets.append(current)
                    current = WiFiNetwork()
                    m = re.search(r"Address:\s*([0-9A-Fa-f:]{17})", line)
                    if m:
                        current.bssid = m.group(1).lower()
                    continue
                if current is None:
                    continue
                m = re.search(r'ESSID:"([^"]*)"', line)
                if m:
                    current.ssid = m.group(1) or "[Hidden]"
                    continue
                m = re.search(r"Signal level[=:]\s*(-?\d+)\s*dBm", line)
                if m:
                    current.signal_dbm = int(m.group(1))
                    continue
                m = re.search(r"Frequency[=:]\s*([\d.]+)", line)
                if m:
                    current.frequency_mhz = int(float(m.group(1)) * 1000)
                if "Encryption key:on" in line:
                    current.security = "wpa2"
                elif "Encryption key:off" in line:
                    current.security = "open"
            if current:
                nets.append(current)
        except Exception:
            pass
        return nets

    def current_connection(self):
        if not tool_exists("termux-wifi-connectioninfo"):
            return None
        try:
            r = subprocess.run(["termux-wifi-connectioninfo"],
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                e = json.loads(r.stdout)
                net = WiFiNetwork(
                    bssid=str(e.get("bssid", "")).lower(),
                    ssid=e.get("ssid") or "[Hidden]",
                    frequency_mhz=int(e.get("frequency_mhz", 0) or 0),
                    signal_dbm=self._sig(e))
                net.compute_derived()
                net.vendor = OUI.lookup(net.bssid)
                return net
        except Exception:
            pass
        return None

    def _save_cache(self, nets):
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({"saved_at": time.time(),
                           "source": self.last_source,
                           "networks": [n.to_dict() for n in nets]},
                          f, ensure_ascii=False)
        except OSError:
            pass

    def load_cache(self):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                payload = json.load(f)
            nets = [WiFiNetwork.from_dict(d) for d in payload.get("networks", [])
                    if d.get("bssid")]
            for n in nets:
                n.compute_derived()
                n.vendor = OUI.lookup(n.bssid)
            return nets
        except Exception:
            return []

    def cache_age(self):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                payload = json.load(f)
            return time.time() - payload.get("saved_at", 0)
        except Exception:
            return None
class Connector:
    def __init__(self, scanner):
        self.sc = scanner
        self.log = deque(maxlen=100)

    def _note(self, op, tgt, ok, msg):
        self.log.append((time.strftime("%H:%M:%S"), op, tgt,
                         "OK" if ok else "FAIL", msg))

    def _run_sh(self, cmd, timeout=12):
        try:
            r = subprocess.run(["sh", "-c", cmd],
                               capture_output=True, text=True, timeout=timeout)
            out = (r.stdout or "") + (r.stderr or "")
            return r.returncode, out
        except subprocess.TimeoutExpired:
            return 1, "timeout"
        except Exception as e:
            return 1, str(e)

    def connect(self, ssid, password, security):
        self.sc.stats.connect_attempts += 1
        print()
        print("  " + C.M + "[GHOST] MAC randomized per network" + C.R)
        preview = GhostMAC.generate_stable(ssid)
        print("  " + C.D + "Preview (Android assigns its own): " + preview + C.R)
        ok = False
        if security == "open":
            ok = self._connect_direct_open(ssid)
        else:
            ok = self._connect_direct_wpa(ssid, password)
        if not ok:
            ok = self._connect_settings(ssid)
        if ok:
            self.sc.stats.connect_successes += 1
        return ok

    def _connect_direct_wpa(self, ssid, password):
        esc_ssid = ssid.replace("'", "'\\''")
        esc_pass = password.replace("'", "'\\''")
        cmd = "cmd wifi connect-network '" + esc_ssid + "' wpa2 '" + esc_pass + "'"
        print("  " + C.C + "[DIRECT] connecting without panel..." + C.R)
        rc, out = self._run_sh(cmd, timeout=15)
        out_l = out.lower()
        ok = rc == 0 and "fail" not in out_l
        self._note("connect", ssid, ok, "direct" if ok else "direct-fail")
        if ok:
            print("  " + C.G + "[OK] direct connect issued" + C.R)
        else:
            print("  " + C.D + "direct: " + out[:60] + C.R)
        return ok

    def _connect_direct_open(self, ssid):
        esc_ssid = ssid.replace("'", "'\\''")
        cmd = "cmd wifi connect-network '" + esc_ssid + "' none"
        print("  " + C.C + "[DIRECT] open network..." + C.R)
        rc, out = self._run_sh(cmd, timeout=15)
        ok = rc == 0 and "fail" not in out.lower()
        self._note("connect", ssid, ok, "direct-open")
        return ok

    def _connect_settings(self, ssid):
        opened = False
        for action in ("android.settings.panel.action.WIFI",
                       "android.settings.WIFI_SETTINGS"):
            try:
                r = subprocess.run(["am", "start", "-a", action],
                                   capture_output=True, timeout=5)
                if r.returncode == 0:
                    opened = True
                    break
            except Exception:
                continue
        self._note("connect", ssid, opened, "panel" if opened else "panel failed")
        if opened:
            print()
            print("  " + C.G + "[OK] panel opened (fallback)" + C.R)
            print("  " + C.W + "STEPS: find '" + ssid + "', tap + enter password" + C.R)
        else:
            print()
            print("  " + C.R + "[FAIL] all methods failed" + C.R)
        return opened

    def _connect_root(self, ssid, password, security):
        if not self.sc.has_root:
            return False
        conf = None
        for p in ("/data/misc/wifi/wpa_supplicant.conf",
                  "/data/wifi/wpa_supplicant.conf"):
            try:
                r = subprocess.run(["su", "-c", "test -e " + p + " && echo Y"],
                                   capture_output=True, text=True, timeout=3)
                if "Y" in (r.stdout or ""):
                    conf = p
                    break
            except Exception:
                continue
        if not conf:
            return False

    def _connect_root(self, ssid, password, security):
        if not self.sc.has_root:
            return False
        conf = None
        for p in ("/data/misc/wifi/wpa_supplicant.conf",
                  "/data/wifi/wpa_supplicant.conf"):
            try:
                r = subprocess.run(["su", "-c", "test -e " + p + " && echo Y"],
                                   capture_output=True, text=True, timeout=3)
                if "Y" in (r.stdout or ""):
                    conf = p
                    break
            except Exception:
                continue
        if not conf:
            return False

        def su(cmd):
            try:
                return subprocess.run(["su", "-c", cmd],
                                      capture_output=True, timeout=10).returncode == 0
            except Exception:
                return False

        su("cp " + conf + " " + conf + ".xxnetro_backup")
        esc_ssid = ssid.replace('"', '\\"')
        esc_pass = password.replace('"', '\\"')
        block = 'network={\n'
        block += '    ssid="' + esc_ssid + '"\n'
        if security == "open":
            block += '    key_mgmt=NONE\n'
        else:
            block += '    psk="' + esc_pass + '"\n'
            block += '    key_mgmt=WPA-PSK\n'
        block += '    priority=1\n}\n'
        escaped = block.replace("'", "'\\''")
        ok = su("printf '%s' '" + escaped + "' >> " + conf)
        if not ok:
            su("cp " + conf + ".xxnetro_backup " + conf)
            return False
        su("svc wifi disable && sleep 1 && svc wifi enable")
        self._note("connect", ssid, True, "root-conf")
        return True
    def disconnect(self):
        rc, _ = self._run_sh("cmd wifi disconnect", timeout=8)
        if rc == 0:
            self._note("disconnect", "-", True, "direct")
            print("  " + C.G + "[OK] disconnected" + C.R)
            return True
        if tool_exists("termux-wifi-disable"):
            try:
                r = subprocess.run(["termux-wifi-disable"],
                                   capture_output=True, timeout=5)
                ok = r.returncode == 0
                self._note("disconnect", "-", ok, "radio")
                if ok:
                    print("  " + C.G + "[OK] radio off" + C.R)
                else:
                    print("  " + C.R + "[FAIL]" + C.R)
                return ok
            except Exception:
                pass
        print("  " + C.R + "[FAIL] no method" + C.R)
        return False

    def enable_radio(self):
        if tool_exists("termux-wifi-enable"):
            try:
                r = subprocess.run(["termux-wifi-enable"],
                                   capture_output=True, timeout=5)
                return r.returncode == 0
            except Exception:
                pass
        return False

    def forget_network(self, ssid):
        esc = ssid.replace("'", "'\\''")
        self._run_sh("cmd wifi forget-network '" + esc + "'", timeout=8)
        return True

    def wait_association(self, ssid, timeout_s=ASSOCIATION_TIMEOUT_S):
        print("  " + C.D + "Waiting for '" + ssid + "' (1s checks, max " + str(timeout_s) + "s)..." + C.R)
        deadline = time.time() + timeout_s
        reported_other = None
        while time.time() < deadline:
            cur = self.sc.current_connection()
            if cur and cur.ssid:
                if cur.ssid == ssid:
                    print("  " + C.G + "[OK] CONNECTED: " + cur.ssid + C.R)
                    print("  " + C.D + "  signal: " + str(cur.signal_dbm) + "dBm ch" + str(cur.channel) + " " + cur.vendor + C.R)
                    self._note("assoc", ssid, True, "connected")
                    return True
                elif reported_other != cur.ssid:
                    print("  " + C.Y + "[WAIT] still on: " + cur.ssid + " — join '" + ssid + "' first" + C.R)
                    reported_other = cur.ssid
            time.sleep(1)
        print("  " + C.Y + "[TIMEOUT] no association yet" + C.R)
        print("  " + C.D + "verify with [5] Status" + C.R)
        self._note("assoc", ssid, False, "timeout")
        return False

    def interactive_connect(self):
        nets = self.sc.list_networks("signal")
        if not nets:
            print("  " + C.R + "[!] No networks — scan first [2]." + C.R)
            return
        print()
        print("  " + C.C + C.BOLD + "AVAILABLE NETWORKS" + C.R)
        print("  " + C.D + "-" * 62 + C.R)
        for i, n in enumerate(nets[:30]):
            name = n.display_name
            dist = "~" + str(int(n.estimated_distance_m)) + "m"
            print("  " + C.B + "[" + str(i).rjust(2) + "]" + C.R + " " + name[:24].ljust(24) + " " + C.M + n.security.ljust(6) + C.R + " " + str(n.signal_dbm).rjust(4) + "dBm " + "ch" + str(n.channel).ljust(3) + " " + C.D + dist.ljust(7) + C.R + " " + C.Y + n.quality_label[:7] + C.R)
        print("  " + C.D + "-" * 62 + C.R)
        try:
            choice = input("  " + C.C + "Select ('q' cancel): " + C.R).strip()
            if choice.lower() == 'q':
                return
            idx = int(choice)
            if not (0 <= idx < min(30, len(nets))):
                print("  " + C.R + "Invalid." + C.R)
                return
        except (ValueError, KeyboardInterrupt):
            print("  " + C.D + "Cancelled." + C.R)
            return
        net = nets[idx]
        self._connect_flow(net)

    def _connect_flow(self, net):
        print()
        print("  " + C.C + C.BOLD + "TARGET" + C.R)
        for line in net.full_report():
            print("  " + C.W + line + C.R)
        ssid = net.ssid
        if ssid == "[Hidden]":
            print("  " + C.Y + "Hidden network detected." + C.R)
            ssid = input("  " + C.Y + "Real SSID to join: " + C.R).strip()
            if not ssid:
                print("  " + C.R + "Cancelled." + C.R)
                return
        if net.security == "open":
            input("  " + C.D + "Enter to connect (open) ..." + C.R)
            self.connect(ssid, "", "open")
            self.wait_association(ssid)
            return
        pw = input("  " + C.C + "Password " + C.D + "(visible)" + C.R + ": ")
        if not pw:
            print("  " + C.D + "Cancelled — empty." + C.R)
            return
        self.connect(ssid, pw, net.security)
        self.wait_association(ssid)

    def show_log(self):
        if not self.log:
            print("  " + C.D + "No operations yet." + C.R)
            return
        print()
        print("  " + C.C + C.BOLD + "CONNECTION LOG (last 20)" + C.R)
        print("  " + C.D + "-" * 58 + C.R)
        for ts, op, tgt, flag, msg in list(self.log)[-20:]:
            col = C.G if flag == "OK" else C.R
            print("  " + C.D + ts + C.R + " " + col + flag.ljust(4) + C.R + " " + C.W + op.ljust(10) + C.R + " " + C.C + tgt[:22].ljust(22) + C.R + " " + C.D + msg[:24] + C.R)
        print("  " + C.D + "-" * 58 + C.R)


class Dashboard:
    KEYS = " q:quit  r:rescan  s:sort  i:info  ENTER:connect  d:disconnect  p:pause "

    def __init__(self, scanner, connector):
        self.sc = scanner
        self.cx = connector
        self.events = deque(maxlen=200)
        self.sort_mode = "signal"
        self.paused = False
        self.running = False
        self.sel = 0
        self._scr = None
        self._lock = threading.Lock()
        self._known = set()
        self._cur = None

    def ev(self, tag, msg):
        self.events.append((time.strftime("%H:%M:%S"), tag, msg))
        self.sc.stats.events_fired += 1

    def _draw(self, stdscr, y, x, text, attr=0):
        try:
            stdscr.addstr(y, x, text, attr)
        except curses.error:
            pass

    def _worker(self):
        while self.running:
            if self.paused or not self.sc.can_scan():
                time.sleep(0.5)
                continue
            with self._lock:
                res = self.sc.scan(force=True)
            self._cur = self.sc.current_connection()
            if res:
                now = {n.bssid for n in res}
                for b in now - self._known:
                    n = self.sc.tracked.get(b)
                    if n:
                        self.ev("NEW", n.display_name + " " + str(n.signal_dbm) + "dBm")
                        self.sc.stats.new_network_events += 1
                for b in self._known - now:
                    n = self.sc.tracked.get(b)
                    name = n.display_name if n else b
                    self.ev("GONE", "lost: " + name)
                    self.sc.stats.gone_network_events += 1
                self._known = now
                self.ev("SCAN", str(len(res)) + " nets")
            else:
                self.ev("WARN", "empty scan")
            time.sleep(0.5)

    @staticmethod
    def _fit(s, w):
        if w <= 0:
            return ""
        if len(s) <= w:
            return s.ljust(w)
        return (s[:max(0, w - 1)] + "...").ljust(w)

    @staticmethod
    def _bar(dbm, w):
        c = max(-90, min(-30, dbm))
        f = int(((c + 90) / 60.0) * w)
        return "=" * f + "-" * (w - f)

    def _suspend(self):
        curses.def_prog_mode()
        curses.endwin()

    def _resume(self):
        try:
            curses.reset_prog_mode()
            if self._scr is not None:
                self._scr.keypad(True)
                curses.curs_set(0)
                self._scr.erase()
                self._scr.refresh()
        except curses.error:
            pass

    def _info_selected(self):
        nets = self.sc.list_networks(self.sort_mode)
        if not nets:
            return
        net = nets[max(0, min(self.sel, len(nets) - 1))]
        self._suspend()
        try:
            print()
            print("  " + C.C + C.BOLD + "NETWORK DETAIL" + C.R)
            print("  " + C.D + "=" * 46 + C.R)
            for line in net.full_report():
                print("  " + C.W + line + C.R)
            print("  " + C.D + "=" * 46 + C.R)
            input("  " + C.D + "Enter to return..." + C.R)
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            self._resume()

    def _connect_selected(self):
        nets = self.sc.list_networks(self.sort_mode)
        if not nets:
            return
        net = nets[max(0, min(self.sel, len(nets) - 1))]
        self._suspend()
        try:
            self.cx._connect_flow(net)
            self.ev("CONN", "connect " + net.display_name)
            input("  " + C.D + "Enter to return..." + C.R)
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            self._resume()

    def _disconnect_flow(self):
        self._suspend()
        try:
            cur = self.sc.current_connection()
            if cur and cur.ssid:
                print("  " + C.D + "Connected: " + cur.ssid + " (" + str(cur.signal_dbm) + "dBm)" + C.R)
                answer = input("  " + C.Y + "Disconnect? (y/n): " + C.R).strip().lower()
                if answer == 'y':
                    self.cx.disconnect()
                    self.ev("CONN", "disconnected")
            else:
                print("  " + C.D + "Not connected." + C.R)
                time.sleep(1)
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            self._resume()

    def run(self):
        curses.wrapper(self._main)

    def _render(self, stdscr):
        stdscr.erase()
        my, mx = stdscr.getmaxyx()
        if my < 12 or mx < 20:
            self._draw(stdscr, 0, 0, "Terminal too small (>=12x20)")
            stdscr.refresh()
            return
        nets = self.sc.list_networks(self.sort_mode)
        if nets:
            self.sel = max(0, min(self.sel, len(nets) - 1))
        conn = "none"
        if self._cur and self._cur.ssid:
            conn = self._cur.ssid + "(" + str(self._cur.signal_dbm) + "dBm)"
        api = "ok" if self.sc.api_available else "MISS"
        root = "y" if self.sc.has_root else "n"
        l1 = " XXNETRO v" + VERSION + "  api:" + api + "  root:" + root + "  GHOST:ON"
        live = "PAUSED" if self.paused else "LIVE"
        l2 = " nets:" + str(len(nets)) + "  sort:" + self.sort_mode + "  scan:" + str(self.sc.next_scan_in()) + "s  " + live + "  conn:" + conn
        self._draw(stdscr, 0, 1, self._fit(l1, mx - 2),
                   curses.A_BOLD | curses.color_pair(3))
        self._draw(stdscr, 1, 1, self._fit(l2, mx - 2),
                   curses.color_pair(4))
        self._draw(stdscr, 2, 0, "-" * min(mx - 1, 60))
        logh = max(5, (my - 4) // 4)
        body = my - 3 - logh - 1
        wide = mx >= 72
        lw = max(30, (mx * 55) // 100) if wide else mx
        rows = body - 1
        start = max(0, self.sel - rows + 1)
        vis = nets[start:start + rows]
        self._draw(stdscr, 3, 0, "+" + "-" * (lw - 2) + "+", curses.color_pair(3))
        t = " NETWORKS "
        self._draw(stdscr, 3, max(1, (lw - len(t)) // 2), t, curses.A_REVERSE)
        for i, n in enumerate(vis):
            sel = (start + i) == self.sel
            name = n.display_name
            dist = int(n.estimated_distance_m)
            line = " " + name[:20].ljust(20) + " " + n.bssid.ljust(17) + " " + str(n.signal_dbm).rjust(4) + "dBm ~" + str(dist).rjust(4) + "m"
            a = curses.A_REVERSE if sel else curses.A_NORMAL
            if not sel and n.security == "open":
                a |= curses.color_pair(5)
            self._draw(stdscr, 4 + i, 1, self._fit(line, lw - 2), a)
        if wide:
            rw = mx - lw
            self._draw(stdscr, 3, lw, "+" + "-" * (rw - 2) + "+", curses.color_pair(3))
            t2 = " SIGNAL / DISTANCE "
            self._draw(stdscr, 3, lw + max(1, (rw - len(t2)) // 2), t2, curses.A_REVERSE)
            bw = max(6, rw - 34)
            for i, n in enumerate(vis):
                bar = self._bar(n.signal_dbm, bw)
                line = " " + bar + " ch" + str(n.channel).ljust(3) + " " + n.band
                if n.signal_dbm > -55:
                    pair = 2
                elif n.signal_dbm > -68:
                    pair = 6
                else:
                    pair = 1
                self._draw(stdscr, 4 + i, lw + 1, self._fit(line, rw - 2),
                           curses.color_pair(pair))
        gy = 3 + body
        self._draw(stdscr, gy, 0, "+" + "-" * (mx - 2) + "+", curses.color_pair(4))
        t3 = " EVENT STREAM "
        self._draw(stdscr, gy, max(1, (mx - len(t3)) // 2), t3, curses.A_REVERSE)
        entries = list(self.events)[-(logh - 1):]
        tagcolors = {"NEW": 2, "GONE": 1, "WARN": 1, "SCAN": 4, "INFO": 3, "CONN": 5}
        for i, (ts, tag, msg) in enumerate(entries):
            pair = tagcolors.get(tag, 3)
            line = " " + ts + " [" + tag + "] " + msg
            self._draw(stdscr, gy + 1 + i, 1, self._fit(line, mx - 2),
                       curses.color_pair(pair))
        self._draw(stdscr, my - 1, 0, self._fit(self.KEYS, mx - 1),
                   curses.A_REVERSE | curses.color_pair(4))
        stdscr.refresh()

    def _main(self, stdscr):
        self._scr = stdscr
        curses.curs_set(0)
        stdscr.nodelay(True)
        stdscr.keypad(True)
        if curses.has_colors():
            curses.start_color()
            try:
                curses.use_default_colors()
            except curses.error:
                pass
            curses.init_pair(1, curses.COLOR_RED, -1)
            curses.init_pair(2, curses.COLOR_GREEN, -1)
            curses.init_pair(3, curses.COLOR_CYAN, -1)
            curses.init_pair(4, curses.COLOR_YELLOW, -1)
            curses.init_pair(5, curses.COLOR_MAGENTA, -1)
            curses.init_pair(6, curses.COLOR_YELLOW, -1)
        self.running = True
        with self._lock:
            res = self.sc.scan(force=True)
        self._cur = self.sc.current_connection()
        if res:
            self._known = {n.bssid for n in res}
            self.ev("SCAN", "initial: " + str(len(res)) + " nets")
        else:
            self.ev("WARN", "initial scan empty")
        threading.Thread(target=self._worker, daemon=True).start()
        try:
            while self.running:
                self._render(stdscr)
                end = time.time() + 0.5
                while time.time() < end:
                    k = stdscr.getch()
                    if k == -1:
                        time.sleep(0.03)
                        continue
                    if k in (ord('q'), ord('Q')):
                        self.running = False
                        break
                    elif k in (ord('r'), ord('R')):
                        with self._lock:
                            res = self.sc.scan(force=True)
                        self.ev("SCAN", "forced: " + str(len(res)))
                    elif k in (ord('s'), ord('S')):
                        o = ["signal", "distance", "ssid", "channel", "security"]
                        self.sort_mode = o[(o.index(self.sort_mode) + 1) % len(o)]
                    elif k in (ord('p'), ord('P')):
                        self.paused = not self.paused
                    elif k in (ord('d'), ord('D')):
                        self._disconnect_flow()
                    elif k in (ord('i'), ord('I')):
                        self._info_selected()
                    elif k == curses.KEY_UP:
                        self.sel = max(0, self.sel - 1)
                    elif k == curses.KEY_DOWN:
                        self.sel += 1
                    elif k in (10, 13, curses.KEY_ENTER):
                        self._connect_selected()
        finally:
            self.running = False
class ModuleLoader:
    def __init__(self, scanner, connector):
        self.sc = scanner
        self.cx = connector
        self.modules = []

    def _list_tool_files(self):
        if not os.path.isdir(MODULES_DIR):
            try:
                os.makedirs(MODULES_DIR, exist_ok=True)
            except OSError:
                return []
        entries = []
        for fname in os.listdir(MODULES_DIR):
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            path = os.path.join(MODULES_DIR, fname)
            try:
                mtime = os.path.getmtime(path)
                entries.append((mtime, fname))
            except OSError:
                continue
        entries.sort(key=lambda x: x[0])
        return [fname for _, fname in entries]

    def _import_tool(self, mod_name):
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        if MODULES_DIR not in sys.path:
            sys.path.insert(0, MODULES_DIR)
        return importlib.import_module(mod_name)

    def discover(self):
        self.modules = []
        files = self._list_tool_files()
        for fname in files:
            mod_name = fname[:-3]
            try:
                mod = self._import_tool(mod_name)
                if not hasattr(mod, "run"):
                    continue
                name = getattr(mod, "TOOL_NAME", mod_name)
                desc = getattr(mod, "TOOL_DESC", "")
                version = getattr(mod, "TOOL_VERSION", "1.0")
                info = {"module_name": mod_name,
                        "display_name": name,
                        "description": desc,
                        "version": version,
                        "file": fname,
                        "run": mod.run}
                self.modules.append(info)
            except Exception as e:
                print("  " + C.Y + "[TOOL] " + mod_name + ": load error: " + str(e) + C.R)
        return self.modules

    def count(self):
        return len(self.modules)

    def execute(self, module_info):
        try:
            print()
            print("  " + C.M + C.BOLD + "--- " + module_info["display_name"] + " v" + module_info["version"] + " ---" + C.R)
            print("  " + C.D + module_info["description"] + C.R)
            print()
            module_info["run"](self.sc, self.cx)
        except KeyboardInterrupt:
            print("  " + C.D + "tool interrupted" + C.R)
        except Exception as e:
            print("  " + C.R + "[TOOL ERROR] " + str(e) + C.R)
        try:
            input("  " + C.D + "Enter to return ... " + C.R)
        except (EOFError, KeyboardInterrupt):
            pass

    def find(self, key):
        try:
            num = int(key)
            idx = num - 10
            if 0 <= idx < len(self.modules):
                return self.modules[idx]
        except ValueError:
            for m in self.modules:
                if m["module_name"].lower() == key.lower():
                    return m
        return None


class Reports:
    def __init__(self, scanner):
        self.sc = scanner

    def quick_table(self):
        print("  " + C.C + "Scanning ..." + C.R)
        nets = self.sc.scan(force=True)
        if not nets:
            cached = self.sc.load_cache()
            if cached:
                age = self.sc.cache_age()
                if age:
                    age_s = str(int(age)) + "s ago"
                else:
                    age_s = "unknown"
                print("  " + C.Y + "(cache: " + age_s + ")" + C.R)
                nets = cached
            else:
                print("  " + C.R + "[!] Empty. Checks: location ON, api permission." + C.R)
                return
        print()
        count = len(nets)
        print("  " + C.G + "[OK] " + str(count) + " networks (" + self.sc.last_source + ")" + C.R)
        print("  " + C.D + "-" * 78 + C.R)
        print("  " + C.BOLD + "SSID".ljust(24) + " BSSID".ljust(19) + " dBm  CH   DIST   QUALITY    SEC" + C.R)
        print("  " + C.D + "-" * 78 + C.R)
        for n in nets:
            q = n.quality_pct
            if q >= 55:
                col = C.G
            elif q >= 35:
                col = C.Y
            else:
                col = C.R
            name = n.display_name[:23].ljust(24)
            bssid = n.bssid.ljust(19)
            dbm = str(n.signal_dbm).rjust(4)
            ch = str(n.channel).rjust(3)
            dist = (str(int(n.estimated_distance_m)) + "m").rjust(6)
            qual = n.quality_label[:9].ljust(10)
            print("  " + C.W + name + C.R + " " + C.C + bssid + C.R + " " + col + dbm + C.R + " " + C.W + ch + C.R + " " + C.M + dist + C.R + "  " + col + qual + C.R + " " + C.Y + n.security + C.R)
        print("  " + C.D + "-" * 78 + C.R)
        print("  " + C.D + "(distance = indoor log-model estimate)" + C.R)
        print()

    def detail_view(self):
        nets = self.sc.list_networks("signal")
        if not nets:
            print("  " + C.R + "[!] No networks — scan first." + C.R)
            return
        print()
        print("  " + C.C + C.BOLD + "NETWORKS" + C.R)
        for i, n in enumerate(nets[:30]):
            name = n.display_name[:28].ljust(28)
            print("  " + C.B + "[" + str(i).rjust(2) + "]" + C.R + " " + name + " " + str(n.signal_dbm).rjust(4) + "dBm")
        try:
            choice = input("  " + C.C + "Detail for ('q' cancel): " + C.R).strip()
            if choice.lower() == 'q':
                return
            idx = int(choice)
            if not (0 <= idx < min(30, len(nets))):
                print("  " + C.R + "Invalid." + C.R)
                return
        except (ValueError, KeyboardInterrupt):
            return
        net = nets[idx]
        print()
        print("  " + C.C + C.BOLD + "NETWORK DETAIL" + C.R)
        print("  " + C.D + "=" * 46 + C.R)
        for line in net.full_report():
            print("  " + C.W + line + C.R)
        print("  " + C.D + "=" * 46 + C.R)
        print()

    def channel_analysis(self):
        cong = self.sc.channel_congestion()
        if not cong:
            print("  " + C.R + "[!] No channel data — scan first." + C.R)
            return
        print()
        print("  " + C.C + C.BOLD + "CHANNEL ANALYSIS" + C.R)
        print("  " + C.D + "-" * 40 + C.R)
        for ch in sorted(cong):
            cnt = cong[ch]
            bar = "#" * cnt + "." * (10 - min(cnt, 10))
            if cnt <= 1:
                col = C.G
            elif cnt <= 3:
                col = C.Y
            else:
                col = C.R
            print("  ch" + str(ch).ljust(3) + " " + col + bar + C.R + " " + str(cnt) + " network(s)")
        print("  " + C.D + "-" * 40 + C.R)
        best2 = self.sc.best_channel("2.4GHz")
        best5 = self.sc.best_channel("5GHz")
        if best2:
            print("  " + C.G + "Best 2.4GHz channel: " + str(best2) + C.R)
        if best5:
            print("  " + C.G + "Best 5GHz channel: " + str(best5) + C.R)
        print()

    def export_json(self):
        nets = self.sc.list_networks("signal")
        if not nets:
            print("  " + C.R + "[!] Nothing to export." + C.R)
            return None
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = "xxnetro_scan_" + timestamp + ".json"
        path = os.path.join(EXPORT_DIR, filename)
        try:
            payload = {"exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       "tool": "XXNETRO v" + VERSION,
                       "source": self.sc.last_source,
                       "network_count": len(nets),
                       "networks": [n.to_dict() for n in nets],
                       "session": self.sc.stats.to_dict()}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            print()
            print("  " + C.G + "[OK] exported " + str(len(nets)) + " networks" + C.R)
            print("  " + C.C + path + C.R)
            return path
        except OSError as e:
            print("  " + C.R + "[FAIL] " + str(e) + C.R)
            return None

    def session_stats(self):
        s = self.sc.stats
        print()
        print("  " + C.C + C.BOLD + "SESSION STATISTICS" + C.R)
        print("  " + C.D + "=" * 46 + C.R)
        print("  Uptime        : " + s.uptime_str())
        print("  Scans        : " + str(s.scans_performed))
        print("  Networks seen: " + str(s.networks_seen))
        print("  NEW events   : " + str(s.new_network_events))
        print("  GONE events  : " + str(s.gone_network_events))
        print("  Connects     : " + str(s.connect_attempts) + " (" + str(s.connect_successes) + " ok)")
        print("  Events fired : " + str(s.events_fired))
        print("  " + C.D + "=" * 46 + C.R)
        print()


def banner(module_count=0):
    if module_count:
        tools_line = C.G + str(module_count) + " Tools loaded" + C.R
    else:
        tools_line = C.D + "no external tools" + C.R
    logo = (
        "  X  X XXXX X  X XXXXX XXXX  XXXXX XXXXXX XXXX  X  X\n"
        "   XX  X    XX X X    X    X X     X      X    X X  X\n"
        "    X  XXX  X XX XXX  XXXX  XXX   XXX    X    XX X\n"
        "    X  X    X X  X    X  X X     X      X    X XX\n"
        "   XXX XXXX X  X XXXXX X  X XXXXX XXXXXX XXXX X  X"
    )
    print()
    print(C.M + logo + C.R)
    print("  " + C.BOLD + "WiFi Suite v" + VERSION + " — " + CODENAME + C.R)
    print("  " + C.D + "by " + AUTHOR + " (c) " + YEAR + " — All Rights Reserved" + C.R)
    print("  " + C.D + "Build " + BUILD + " | " + tools_line + C.R)
    print("  " + C.D + "Built for the unrooted. No root. No trace." + C.R)
    print()


def status_report(sc, cx, loader):
    cur = sc.current_connection()
    print()
    print("  " + C.C + C.BOLD + "STATUS" + C.R)
    print("  " + C.D + "=" * 50 + C.R)
    if sc.has_root:
        root_s = "yes"
    else:
        root_s = "no"
    print("  Root       : " + root_s)
    if sc.api_available:
        api_s = "ok"
    else:
        api_s = "MISSING"
    print("  termux-api : " + api_s)
    print("  Networks   : " + str(len(sc.tracked)) + "  (source: " + sc.last_source + ")")
    print("  Next scan  : " + str(sc.next_scan_in()) + "s")
    age = sc.cache_age()
    if age:
        print("  Cache age  : " + str(int(age)) + "s")
    print("  Tools      : " + str(loader.count()))
    if cur and cur.ssid:
        cur_line = " " + cur.ssid + " (" + str(cur.signal_dbm) + "dBm ch" + str(cur.channel) + " " + cur.vendor + ")"
        print("  " + C.G + "Connected  :" + cur_line + C.R)
        print("  " + C.M + "Your MAC   : randomized by Android 10+ (per-network)" + C.R)
    else:
        print("  " + C.D + "Connected  : none" + C.R)
    print("  " + C.D + "Session    : " + sc.stats.report() + C.R)
    print("  " + C.D + "=" * 50 + C.R)
    print()


def print_menu(loader):
    print("  " + C.C + "[1]" + C.R + " Live dashboard   " + C.D + "(ENTER=connect, i=info)" + C.R)
    print("  " + C.C + "[2]" + C.R + " Quick scan       " + C.D + "(full table)" + C.R)
    print("  " + C.C + "[3]" + C.R + " Connect          " + C.D + "(visible password, direct)" + C.R)
    print("  " + C.C + "[4]" + C.R + " Disconnect       " + C.D + "(direct)" + C.R)
    print("  " + C.C + "[5]" + C.R + " Status           " + C.D + "(full state)" + C.R)
    print("  " + C.C + "[6]" + C.R + " Network detail   " + C.D + "(full report)" + C.R)
    print("  " + C.C + "[7]" + C.R + " Channel analysis " + C.D + "(congestion + best)" + C.R)
    print("  " + C.C + "[8]" + C.R + " Export scan      " + C.D + "(JSON file)" + C.R)
    print("  " + C.C + "[9]" + C.R + " Session stats    " + C.D + "(uptime + events)" + C.R)
    print("  " + C.C + "[a]" + C.R + " Connection log   " + C.D + "(history)" + C.R)
    if loader.modules:
        print("  " + C.D + "--- TOOLS ---" + C.R)
        for i, m in enumerate(loader.modules):
            num = str(10 + i)
            name = m["module_name"].ljust(18)
            print("  " + C.M + "[" + num + "]" + C.R + " " + name + " " + C.Y + "Tool" + C.R)
    print("  " + C.R + "[0]" + C.R + " Exit")
    print()


def handle_choice(choice, sc, cx, loader, reports):
    if choice == '1':
        Dashboard(sc, cx).run()
    elif choice == '2':
        reports.quick_table()
    elif choice == '3':
        cx.interactive_connect()
    elif choice == '4':
        cx.disconnect()
    elif choice == '5':
        status_report(sc, cx, loader)
    elif choice == '6':
        reports.detail_view()
    elif choice == '7':
        reports.channel_analysis()
    elif choice == '8':
        reports.export_json()
    elif choice == '9':
        reports.session_stats()
    elif choice.lower() == 'a':
        cx.show_log()
    elif choice == '0':
        return False
    else:
        mod = loader.find(choice)
        if mod:
            loader.execute(mod)
        else:
            print("  " + C.R + "invalid" + C.R)
    return True


def main():
    banner()
    sc = Scanner()
    cx = Connector(sc)
    loader = ModuleLoader(sc, cx)
    loader.discover()
    reports = Reports(sc)
    banner(loader.count())
    print("  " + C.C + "Pre-flight" + C.R)
    if sc.api_available:
        api_s = "ok"
    else:
        api_s = "MISSING"
    print("    termux-api : " + api_s)
    if sc.has_root:
        root_s = "yes"
    else:
        root_s = "no"
    print("    root       : " + root_s)
    print("    ghost-mode : " + C.M + "randomized MAC (Android 10+)" + C.R)
    print("    tools      : " + C.M + str(loader.count()) + " loaded" + C.R)
    if loader.modules:
        for m in loader.modules:
            print("      " + C.G + "|- " + m["module_name"] + C.R + " " + C.Y + "Tool" + C.R + " " + C.D + m["description"] + C.R)
    print()
    if not sc.api_available:
        print("  " + C.R + "[!] termux-wifi-scaninfo not found." + C.R)
        print("      pkg install termux-api   (pkg, NOT pip)" + C.R)
        return
    while True:
        print_menu(loader)
        try:
            choice = input("  " + C.M + C.BOLD + "xxnetro>" + C.R + " ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            print("  " + C.D + "bye." + C.R)
            break
        if not handle_choice(choice, sc, cx, loader, reports):
            print("  " + C.D + "bye — stay ghost." + C.R)
            print()
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        print("  " + C.D + "interrupted." + C.R)
        print()
    except Exception as e:
        print("  " + C.R + "fatal: " + str(e) + C.R)
        sys.exit(1)
