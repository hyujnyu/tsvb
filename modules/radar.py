#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
═════════════════════════════════════════════════════════════════════
  XXNETRO Tool: radar — Signal Movement Tracker
  ═══════════════════════════════════════════════════════════════════
  Tracks signal strength over time for every visible network
  and reports: rising / falling / stable + estimated approach
  or retreat speed (m/s) using the log-distance model.

  HOW IT WORKS:
    Each scan (respecting Android throttle ~32s) records
    (bssid, dbm, timestamp). Delta between consecutive
    samples → dBm change per minute → distance change via
    the path-loss inverse → approach/retreat speed.

  DISPLAY:
    - Live table: network / dBm now / delta / trend arrow
    - Sparkline: last 12 samples per network
    - Alert: rapid movers flagged (±6dBm+ per minute)
═════════════════════════════════════════════════════════════════════
"""

import os
import time
import math
import threading
from collections import deque, defaultdict
from datetime import datetime

TOOL_NAME = "radar"
TOOL_DESC = "Track network movement via signal deltas"
TOOL_VERSION = "1.0"

HOME_DIR = os.environ.get("TERMUX_HOME",
                          os.path.expanduser("~"))
SNAPSHOT_FILE = os.path.join(HOME_DIR,
                             ".xxnetro_radar.json")

# physics constants (match core)
TX_POWER_DBM = 20.0
REF_LOSS_DB = 40.0
PATH_LOSS_N = 3.5

# tuning
SAMPLES_KEEP = 12
ALERT_DBM_PER_MIN = 6.0


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


# ══════════════════════════════════════════════════════════════════
#  L1 — TRACKER: samples, deltas, movement math
# ══════════════════════════════════════════════════════════════════

class TrackRecord:
    """Per-network signal timeline."""

    __slots__ = ("bssid", "ssid", "samples",
                 "last_dbm", "last_dist")

    def __init__(self, bssid: str, ssid: str):
        self.bssid = bssid
        self.ssid = ssid
        # (timestamp, dbm, distance_m)
        self.samples: deque = deque(maxlen=SAMPLES_KEEP)
        self.last_dbm = -100
        self.last_dist = 0.0

    def add(self, ts: float, dbm: int) -> None:
        dist = self._dbm_to_dist(dbm)
        self.samples.append((ts, dbm, dist))
        self.last_dbm = dbm
        self.last_dist = dist

    @staticmethod
    def _dbm_to_dist(dbm: int) -> float:
        if dbm >= 0:
            return 0.5
        exp = (TX_POWER_DBM - float(dbm)
               - REF_LOSS_DB) / (10.0 * PATH_LOSS_N)
        return max(0.5, 10 ** max(0, exp))

    # ─── analysis ───

    def dbm_per_min(self) -> float:
        """Signal change rate over the sample window."""
        if len(self.samples) < 2:
            return 0.0
        t0, d0, _ = self.samples[0]
        t1, d1, _ = self.samples[-1]
        span = t1 - t0
        if span < 5:
            return 0.0
        return (d1 - d0) * 60.0 / span

    def distance_delta_m(self) -> float:
        """How many meters closer/further vs first sample."""
        if len(self.samples) < 2:
            return 0.0
        _, _, dist0 = self.samples[0]
        _, _, dist1 = self.samples[-1]
        return dist0 - dist1   # + = closer

    def trend(self) -> str:
        rate = self.dbm_per_min()
        if rate >= ALERT_DBM_PER_MIN:
            return "RISING"
        if rate <= -ALERT_DBM_PER_MIN:
            return "FALLING"
        if abs(rate) >= 2.0:
            return "rising"
        if rate <= -2.0:
            return "falling"
        return "stable"

    def speed_m_per_min(self) -> float:
        """Estimated closing speed (m/min). + = approaching."""
        if len(self.samples) < 2:
            return 0.0
        t0 = self.samples[0][0]
        t1 = self.samples[-1][0]
        span = t1 - t0
        if span < 5:
            return 0.0
        return self.distance_delta_m() * 60.0 / span

    def sparkline(self, width: int = 12) -> str:
        """Unicode sparkline of the sample history."""
        if len(self.samples) < 2:
            return "·" * width
        vals = [s[1] for s in self.samples]
        lo, hi = min(vals), max(vals)
        rng = max(1, hi - lo)
        blocks = " ▁▂▃▄▅▆▇█"
        steps = []
        n = len(vals)
        # resample to width
        for i in range(width):
            idx = min(n - 1,
                      int(i * n / width))
            v = vals[idx]
            lvl = int((v - lo) / rng
                      * (len(blocks) - 1))
            steps.append(blocks[lvl])
        return "".join(steps)


class RadarTracker:
    """Maintains all track records across scans."""

    def __init__(self):
        self.tracks: dict = {}
        self._lock = threading.Lock()
        self.started = time.time()
        self.scan_count = 0

    def ingest(self, networks: list) -> None:
        """Feed a scan result (WiFiNetwork objects)."""
        with self._lock:
            now = time.time()
            self.scan_count += 1
            for net in networks:
                if not net.bssid:
                    continue
                if net.bssid not in self.tracks:
                    self.tracks[net.bssid] = TrackRecord(
                        net.bssid, net.display_name)
                rec = self.tracks[net.bssid]
                rec.add(now, net.signal_dbm)
                if net.ssid != "[Hidden]":
                    rec.ssid = net.display_name

    def movers(self) -> list:
        """Networks ordered by absolute movement rate."""
        out = []
        for rec in self.tracks.values():
            if len(rec.samples) >= 2:
                out.append(rec)
        out.sort(key=lambda r: abs(r.dbm_per_min()),
                 reverse=True)
        return out

    def rapid_movers(self) -> list:
        """Only networks above the alert threshold."""
        return [r for r in self.tracks.values()
                if abs(r.dbm_per_min()) >= ALERT_DBM_PER_MIN]

    def to_json(self) -> dict:
        out = {
            "saved_at": datetime.now().isoformat(),
            "session_start": self.started,
            "scans": self.scan_count,
            "networks": {}
        }
        for bssid, rec in self.tracks.items():
            out["networks"][bssid] = {
                "ssid": rec.ssid,
                "last_dbm": rec.last_dbm,
                "samples": [[t, d] for t, d, _
                            in rec.samples],
            }
        return out


# ══════════════════════════════════════════════════════════════════
#  L2 — RADAR VIEW (live table + alerts)
# ══════════════════════════════════════════════════════════════════

class RadarView:
    """Interactive loop: auto-scan + render table."""

    def __init__(self, scanner, tracker: RadarTracker):
        self.sc = scanner
        self.tracker = tracker
        self.running = False

    def loop(self) -> None:
        self.running = True
        print(f"\n  {C.C}Radar started. Sampling every "
              f"scan cycle (~32s Android limit).{C.R}")
        print(f"  {C.D}Ctrl+C to stop and save snapshot."
              f"{C.R}\n")
        while self.running:
            # scan (throttle-aware)
            nets = self.sc.scan(force=True)
            if nets:
                self.tracker.ingest(nets)
                self.render()
            else:
                print(f"  {C.Y}scan empty — retrying...{C.R}")
            # wait for next allowed scan
            wait = self.sc.next_scan_in() + 1
            self._countdown(wait)

    def _countdown(self, seconds: int) -> None:
        for remaining in range(seconds, 0, -1):
            print(f"\r  {C.D}next scan in "
                  f"{remaining:>2}s  ", end="", flush=True)
            time.sleep(1)
            if not self.running:
                break
        print("\r" + " " * 30 + "\r", end="")

    def render(self) -> None:
        movers = self.tracker.movers()
        if not movers:
            print(f"  {C.D}collecting samples "
                  f"(need 2+ scans)...{C.R}")
            return

        print(f"\n  {C.C}{C.BOLD}RADAR — "
              f"{len(movers)} tracked{C.R}")
        print(f"  {C.D}{'─' * 62}{C.R}")
        print(f"  {C.BOLD}{'NETWORK':<22} {'dBm':>4} "
              f"{'d/min':>7} {'Δm':>7} "
              f"{'SPARK':<14} TREND{C.R}")

        for rec in movers[:15]:
            rate = rec.dbm_per_min()
            dd = rec.distance_delta_m()
            spark = rec.sparkline(12)
            trend = rec.trend()

            if trend == "RISING":
                tcol, sym = C.G, "▲▲"
            elif trend == "FALLING":
                tcol, sym = C.R, "▼▼"
            elif trend == "rising":
                tcol, sym = C.G, "▲"
            elif trend == "falling":
                tcol, sym = C.R, "▼"
            else:
                tcol, sym = C.D, "─"

            name = rec.ssid[:21]
            print(f"  {C.W}{name:<22}{C.R} "
                  f"{C.C}{rec.last_dbm:>4}{C.R} "
                  f"{tcol}{rate:>+6.1f}{C.R} "
                  f"{C.M}{dd:>+6.1f}{C.R} "
                  f"{C.D}{spark:<14}{C.R} "
                  f"{tcol}{sym} {trend}{C.R}")

        print(f"  {C.D}{'─' * 62}{C.R}")

        alerts = self.tracker.rapid_movers()
        if alerts:
            for rec in alerts:
                speed = rec.speed_m_per_min()
                direction = ("APPROACHING" if speed > 0
                             else "RETREATING")
                print(f"  {C.Y}⚡ {rec.ssid}: "
                      f"{rec.dbm_per_min():+.1f}dBm/min — "
                      f"{direction} ~{abs(speed):.0f}m/min"
                      f"{C.R}")

    def stop(self) -> None:
        self.running = False


# ══════════════════════════════════════════════════════════════════
#  L3 — SNAPSHOT (save / load / compare)
# ══════════════════════════════════════════════════════════════════

class Snapshot:
    """Persist tracker state between sessions."""

    @staticmethod
    def save(tracker: RadarTracker) -> str:
        try:
            payload = tracker.to_json()
            with open(SNAPSHOT_FILE, "w",
                      encoding="utf-8") as f:
                import json
                json.dump(payload, f, indent=2)
            return SNAPSHOT_FILE
        except OSError:
            return ""

    @staticmethod
    def load() -> dict:
        try:
            import json
            with open(SNAPSHOT_FILE, "r",
                      encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    @staticmethod
    def show_saved() -> None:
        data = Snapshot.load()
        if not data:
            print(f"  {C.D}no saved snapshot{C.R}")
            return
        saved = data.get("saved_at", "?")
        nets = data.get("networks", {})
        print(f"\n  {C.C}Saved snapshot: {saved}{C.R}")
        print(f"  {C.D}scans: {data.get('scans', 0)} │ "
              f"networks: {len(nets)}{C.R}")
        for bssid, info in list(nets.items())[:10]:
            print(f"  {C.W}{info.get('ssid', '?'):<22}{C.R}"
                  f"  {info.get('last_dbm')}dBm"
                  f"  {C.D}{bssid}{C.R}")


# ══════════════════════════════════════════════════════════════════
#  L4 — TOOL INTERFACE
# ══════════════════════════════════════════════════════════════════

class RadarTool:
    """Menu wrapper — entry from XXNETRO core."""

    def __init__(self, scanner, connector):
        self.sc = scanner
        self.cx = connector
        self.tracker = RadarTracker()
        self.view = None

    def header(self):
        print(f"{C.M}{C.BOLD}")
        print("  ╔════════════════════════════════════════╗")
        print("  ║  RADAR — Network Movement Tracker      ║")
        print("  ╚════════════════════════════════════════╝")
        print(f"{C.R}")

    def run_tool(self):
        self.header()
        while True:
            print(f"""
  {C.C}[1]{C.R} Start live tracking   {C.D}(Ctrl+C stops){C.R}
  {C.C}[2]{C.R} Single sample + report
  {C.C}[3]{C.R} Show saved snapshot
  {C.C}[4]{C.R} Help — how it works
  {C.C}[0]{C.R} Back to XXNETRO
""")
            try:
                c = input(
                    f"  {C.M}{C.BOLD}radar>{C.R} ").strip()
            except (KeyboardInterrupt, EOFError):
                self._save_and_exit()
                return

            if c == '1':
                self.start_live()
            elif c == '2':
                self.single_sample()
            elif c == '3':
                Snapshot.show_saved()
            elif c == '4':
                self.show_help()
            elif c == '0':
                self._save_and_exit()
                return

    def start_live(self):
        self.view = RadarView(self.sc, self.tracker)
        try:
            self.view.loop()
        except KeyboardInterrupt:
            pass
        finally:
            self.view.stop()
            self._save_snapshot()

    def single_sample(self):
        print(f"  {C.C}scanning...{C.R}")
        nets = self.sc.scan(force=True)
        if not nets:
            print(f"  {C.R}[!] empty{C.R}")
            return
        self.tracker.ingest(nets)
        self.view = RadarView(self.sc, self.tracker)
        self.view.render()

    def show_help(self):
        print(f"""
  {C.C}{C.BOLD}HOW RADAR WORKS{C.R}
  {C.D}{'─' * 50}{C.R}
  Each scan records signal (dBm) per network.
  Between scans we compute:
    - d/min : dBm change per minute
    - Δm    : distance change (log-distance model)
    - speed : estimated approach/retreat rate

  {C.Y}Reading the trends:{C.R}
    {C.G}▲▲ RISING{C.R}   signal climbing fast —
                network moving toward you
    {C.R}▼▼ FALLING{C.R}  signal dropping fast —
                network moving away
    {C.D}─  stable{C.R}   no significant change

  {C.D}Alerts fire at ±{ALERT_DBM_PER_MIN}dBm/min.
  Physics: indoor log-distance, n={PATH_LOSS_N}.{C.R}
""")

    def _save_snapshot(self):
        path = Snapshot.save(self.tracker)
        if path:
            print(f"\n  {C.G}snapshot saved{C.R}")
            print(f"  {C.D}{path}{C.R}")

    def _save_and_exit(self):
        self._save_snapshot()
        print(f"  {C.D}bye — radar off.{C.R}")


# ══════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════

def run(scanner, connector):
    RadarTool(scanner, connector).run_tool()
