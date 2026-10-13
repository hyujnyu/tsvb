#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
═════════════════════════════════════════════════════════════════════
  XXNETRO Tool: network_diff — Change Monitor
  ═══════════════════════════════════════════════════════════════════
  Compares the current scan against a stored baseline.
  Detects:
    NEW      : network appeared (was not in baseline)
    GONE     : network vanished
    MOVED    : changed channel
    CHANGED  : signal shifted significantly (±8dBm+)
    RENAMED  : same BSSID, different SSID

  Baselines are saved per-name so you can keep several
  (e.g. "home", "school", "cafe") and diff any of them.
═════════════════════════════════════════════════════════════════════
"""

import os
import time
import json
from datetime import datetime

TOOL_NAME = "network_diff"
TOOL_DESC = "Detect new/gone/moved/renamed networks vs baseline"
TOOL_VERSION = "1.0"

HOME_DIR = os.environ.get("TERMUX_HOME",
                          os.path.expanduser("~"))
BASELINE_DIR = os.path.join(HOME_DIR,
                            "xxnetro_baselines")

SIGNAL_THRESHOLD = 8      # dBm delta = "CHANGED"


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


os.makedirs(BASELINE_DIR, exist_ok=True)


# ══════════════════════════════════════════════════════════════════
#  L1 — BASELINE STORE
# ══════════════════════════════════════════════════════════════════

class BaselineStore:
    """Named baseline save/load/list/delete."""

    @staticmethod
    def _path(name: str) -> str:
        safe = "".join(
            ch for ch in name if ch.isalnum() or ch in "_-"
        ) or "default"
        return os.path.join(BASELINE_DIR,
                            f"{safe}.json")

    @staticmethod
    def save(name: str, networks: list) -> bool:
        """Store a scan as named baseline."""
        payload = {
            "name": name,
            "saved_at": datetime.now().isoformat(),
            "networks": {}
        }
        for n in networks:
            payload["networks"][n.bssid] = {
                "ssid": n.ssid,
                "signal_dbm": n.signal_dbm,
                "channel": n.channel,
                "security": n.security,
            }
        try:
            with open(BaselineStore._path(name), "w",
                      encoding="utf-8") as f:
                json.dump(payload, f, indent=2,
                          ensure_ascii=False)
            return True
        except OSError:
            return False

    @staticmethod
    def load(name: str) -> dict:
        try:
            with open(BaselineStore._path(name), "r",
                      encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    @staticmethod
    def list_all() -> list:
        out = []
        try:
            for f in sorted(os.listdir(BASELINE_DIR)):
                if f.endswith(".json"):
                    name = f[:-5]
                    data = BaselineStore.load(name)
                    ts = data.get("saved_at", "?")
                    count = len(data.get("networks", {}))
                    out.append((name, ts, count))
        except OSError:
            pass
        return out

    @staticmethod
    def delete(name: str) -> bool:
        path = BaselineStore._path(name)
        try:
            if os.path.exists(path):
                os.remove(path)
                return True
        except OSError:
            pass
        return False


# ══════════════════════════════════════════════════════════════════
#  L2 — DIFF ENGINE
# ══════════════════════════════════════════════════════════════════

class DiffEngine:
    """Compute changes between baseline and current scan."""

    @staticmethod
    def compute(baseline: dict,
                current: list) -> dict:
        """
        Returns dict of event lists:
        new, gone, moved, changed, renamed
        """
        base_nets = baseline.get("networks", {})

        # index current by bssid
        cur_by_bssid = {}
        for n in current:
            if n.bssid:
                cur_by_bssid[n.bssid] = n

        events = {
            "new": [],
            "gone": [],
            "moved": [],
            "changed": [],
            "renamed": [],
        }

        # ── networks in current, compare to base ──
        for bssid, net in cur_by_bssid.items():
            if bssid not in base_nets:
                events["new"].append({
                    "bssid": bssid,
                    "ssid": net.display_name,
                    "signal": net.signal_dbm,
                    "channel": net.channel,
                })
                continue

            b = base_nets[bssid]

            # channel moved?
            if (b.get("channel")
                    and net.channel
                    and b["channel"] != net.channel):
                events["moved"].append({
                    "bssid": bssid,
                    "ssid": net.display_name,
                    "from_ch": b["channel"],
                    "to_ch": net.channel,
                })

            # renamed? (same BSSID, different SSID)
            if (net.ssid != "[Hidden]"
                    and b.get("ssid")
                    and b["ssid"] != "[Hidden]"
                    and b["ssid"] != net.ssid):
                events["renamed"].append({
                    "bssid": bssid,
                    "from_ssid": b["ssid"],
                    "to_ssid": net.ssid,
                })

            # signal change?
            if b.get("signal_dbm") is not None:
                delta = (net.signal_dbm
                         - b["signal_dbm"])
                if abs(delta) >= SIGNAL_THRESHOLD:
                    direction = ("stronger"
                                 if delta > 0
                                 else "weaker")
                    events["changed"].append({
                        "bssid": bssid,
                        "ssid": net.display_name,
                        "delta": delta,
                        "direction": direction,
                        "now": net.signal_dbm,
                    })

        # ── networks in base but gone now ──
        for bssid, b in base_nets.items():
            if bssid not in cur_by_bssid:
                events["gone"].append({
                    "bssid": bssid,
                    "ssid": (b.get("ssid")
                             or "(hidden)"),
                    "was_signal": b.get("signal_dbm"),
                })

        return events


# ══════════════════════════════════════════════════════════════════
#  L3 — RENDER
# ══════════════════════════════════════════════════════════════════

class DiffRenderer:
    """Colored diff report."""

    @staticmethod
    def render(baseline_name: str, events: dict,
               current_count: int) -> None:
        print(f"\n  {C.C}{C.BOLD}NETWORK DIFF{C.R}")
        print(f"  {C.D}baseline: '{baseline_name}' → now "
              f"({current_count} networks){C.R}")
        print(f"  {C.D}{'═' * 58}{C.R}")

        total_changes = sum(len(v)
                            for v in events.values())
        if total_changes == 0:
            print(f"  {C.G}✓ No changes detected."
                  f"{C.R}")
            print(f"  {C.D}environment identical to "
                  f"baseline{C.R}")
        else:
            print(f"  {C.Y}{total_changes} change(s) "
                  f"detected:{C.R}\n")

        # ── NEW ──
        if events["new"]:
            print(f"  {C.G}{C.BOLD}+ NEW "
                  f"({len(events['new'])}){C.R}")
            for e in events["new"]:
                print(f"    {C.G}+{C.R} "
                      f"{e['ssid'][:24]:<24} "
                      f"{C.C}{e['bssid']}{C.R} "
                      f"{e['signal']:>4}dBm "
                      f"ch{e['channel']}")

        # ── GONE ──
        if events["gone"]:
            print(f"\n  {C.R}{C.BOLD}- GONE "
                  f"({len(events['gone'])}){C.R}")
            for e in events["gone"]:
                was = (f"(was {e['was_signal']}dBm)"
                       if e["was_signal"] is not None
                       else "")
                print(f"    {C.R}-{C.R} "
                      f"{e['ssid'][:24]:<24} "
                      f"{C.D}{e['bssid']} {was}{C.R}")

        # ── MOVED ──
        if events["moved"]:
            print(f"\n  {C.Y}{C.BOLD}→ MOVED CHANNEL "
                  f"({len(events['moved'])}){C.R}")
            for e in events["moved"]:
                print(f"    {C.Y}→{C.R} "
                      f"{e['ssid'][:24]:<24} "
                      f"ch{e['from_ch']} → "
                      f"{C.W}ch{e['to_ch']}{C.R}")

        # ── RENAMED ──
        if events["renamed"]:
            print(f"\n  {C.M}{C.BOLD}⟳ RENAMED "
                  f"({len(events['renamed'])}){C.R}")
            for e in events["renamed"]:
                print(f"    {C.M}⟳{C.R} "
                      f"{e['from_ssid'][:20]} → "
                      f"{C.W}{e['to_ssid'][:20]}{C.R} "
                      f"{C.D}{e['bssid']}{C.R}")

        # ── SIGNAL CHANGED ──
        if events["changed"]:
            print(f"\n  {C.B}{C.BOLD}± SIGNAL SHIFT "
                  f"({len(events['changed'])}){C.R}")
            for e in events["changed"]:
                col = (C.G if e["delta"] > 0
                       else C.R)
                print(f"    {col}{e['delta']:+d}{C.R}dBm "
                      f"{e['ssid'][:22]:<22} "
                      f"{C.D}now {e['now']}dBm — "
                      f"{e['direction']}{C.R}")

        print(f"  {C.D}{'═' * 58}{C.R}\n")


# ══════════════════════════════════════════════════════════════════
#  L4 — TOOL INTERFACE
# ══════════════════════════════════════════════════════════════════

class DiffTool:
    """Menu wrapper for network_diff."""

    def __init__(self, scanner, connector):
        self.sc = scanner
        self.cx = connector

    def header(self):
        print(f"{C.M}{C.BOLD}")
        print("  ╔════════════════════════════════════════╗")
        print("  ║  NETWORK DIFF — Change Monitor         ║")
        print("  ╚════════════════════════════════════════╝")
        print(f"{C.R}")

    def run_tool(self):
        self.header()
        while True:
            print(f"""
  {C.C}[1]{C.R} Save current scan as baseline
  {C.C}[2]{C.R} Diff now vs a baseline
  {C.C}[3]{C.R} Live watch  {C.D}(auto-diff every scan){C.R}
  {C.C}[4]{C.R} List baselines
  {C.C}[5]{C.R} Delete a baseline
  {C.C}[0]{C.R} Back to XXNETRO
""")
            try:
                c = input(
                    f"  {C.M}{C.BOLD}diff>{C.R} ").strip()
            except (KeyboardInterrupt, EOFError):
                return

            if c == '1':
                self.do_save_baseline()
            elif c == '2':
                self.do_diff()
            elif c == '3':
                self.do_live_watch()
            elif c == '4':
                self.do_list()
            elif c == '5':
                self.do_delete()
            elif c == '0':
                return

    # ─── actions ───

    def do_save_baseline(self):
        print(f"  {C.C}scanning...{C.R}")
        nets = self.sc.scan(force=True)
        if not nets:
            print(f"  {C.R}[!] empty scan{C.R}")
            return
        name = input(
            f"  {C.C}baseline name (e.g. home): {C.R}"
        ).strip()
        if not name:
            name = "default"
        if BaselineStore.save(name, nets):
            print(f"  {C.G}[OK] saved {len(nets)} networks "
                  f"as '{name}'{C.R}")
        else:
            print(f"  {C.R}[FAIL] could not save{C.R}")

    def do_diff(self):
        baselines = BaselineStore.list_all()
        if not baselines:
            print(f"  {C.Y}no baselines — save one "
                  f"first [1]{C.R}")
            return
        print(f"\n  {C.C}baselines:{C.R}")
        for i, (name, ts, count) in enumerate(baselines):
            print(f"  {C.B}[{i}]{C.R} {name:<16} "
                  f"{C.D}{ts[:19]} │ {count} nets{C.R}")
        try:
            c = input(
                f"\n  {C.C}which? ('q' cancel): {C.R}"
            ).strip()
            if c.lower() == 'q':
                return
            idx = int(c)
            name = baselines[idx][0]
        except (ValueError, IndexError,
                KeyboardInterrupt):
            print(f"  {C.R}invalid{C.R}")
            return

        print(f"  {C.C}scanning current...{C.R}")
        current = self.sc.scan(force=True)
        if not current:
            print(f"  {C.R}[!] empty scan{C.R}")
            return

        baseline = BaselineStore.load(name)
        events = DiffEngine.compute(baseline, current)
        DiffRenderer.render(name, events,
                            len(current))

    def do_live_watch(self):
        """Continuous diff against newest baseline."""
        baselines = BaselineStore.list_all()
        if not baselines:
            print(f"  {C.Y}no baselines — save one "
                  f"first [1]{C.R}")
            return
        name = baselines[-1][0]
        baseline = BaselineStore.load(name)
        print(f"\n  {C.C}watching vs '{name}' — "
              f"Ctrl+C to stop{C.R}")
        print(f"  {C.D}auto-diff every scan "
              f"(~32s){C.R}\n")

        prev_alerts = set()
        try:
            while True:
                current = self.sc.scan(force=True)
                if current:
                    events = DiffEngine.compute(
                        baseline, current)
                    alerts = []
                    if events["new"]:
                        for e in events["new"]:
                            alerts.append(
                                ("NEW",
                                 e["ssid"],
                                 f"{e['signal']}dBm"))
                    if events["gone"]:
                        for e in events["gone"]:
                            alerts.append(
                                ("GONE",
                                 e["ssid"], ""))
                    if alerts:
                        stamp = time.strftime(
                            "%H:%M:%S")
                        print(f"  {C.D}[{stamp}]{C.R}")
                        for kind, ssid, extra in alerts:
                            col = (C.G
                                   if kind == "NEW"
                                   else C.R)
                            print(f"    {col}{kind:<5}"
                                  f"{C.R} "
                                  f"{C.W}{ssid[:24]}"
                                  f"{C.R} {C.D}{extra}"
                                  f"{C.R}")
                        print()
                        prev_alerts = set(alerts)
                wait = self.sc.next_scan_in() + 1
                print(f"\r  {C.D}next scan {wait}s  ",
                      end="", flush=True)
                time.sleep(wait)
        except KeyboardInterrupt:
            print(f"\n  {C.D}watch stopped{C.R}")

    def do_list(self):
        baselines = BaselineStore.list_all()
        if not baselines:
            print(f"  {C.D}no baselines{C.R}")
            return
        print(f"\n  {C.C}{C.BOLD}BASELINES{C.R}")
        print(f"  {C.D}{'─' * 50}{C.R}")
        for name, ts, count in baselines:
            print(f"  {C.W}{name:<18}{C.R} "
                  f"{C.D}{ts[:19]}{C.R} "
                  f"{C.C}{count} nets{C.R}")
        print(f"  {C.D}{'─' * 50}{C.R}\n")

    def do_delete(self):
        baselines = BaselineStore.list_all()
        if not baselines:
            print(f"  {C.D}no baselines{C.R}")
            return
        for i, (name, _, _) in enumerate(baselines):
            print(f"  {C.B}[{i}]{C.R} {name}")
        try:
            c = input(
                f"  {C.C}delete which? {C.R}").strip()
            idx = int(c)
            name = baselines[idx][0]
        except (ValueError, IndexError):
            print(f"  {C.R}invalid{C.R}")
            return
        if BaselineStore.delete(name):
            print(f"  {C.G}deleted '{name}'{C.R}")
        else:
            print(f"  {C.R}[FAIL]{C.R}")


# ══════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════

def run(scanner, connector):
    DiffTool(scanner, connector).run_tool()
