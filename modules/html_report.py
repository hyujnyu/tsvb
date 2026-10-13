#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
═════════════════════════════════════════════════════════════════════
  XXNETRO Tool: html_report — Professional Scan Report
  ═══════════════════════════════════════════════════════════════════
  Generates a self-contained HTML report (dark theme,
  RTL-ready, mobile-responsive) from the current scan.

  Sections:
    1. Header with tool branding + timestamp
    2. Summary cards (total / hidden / open / strongest)
    3. Full network table (sortable-looking, colored)
    4. Signal ranking chart (CSS bars)
    5. Channel congestion map
    6. Security breakdown (pie-style with CSS)
    7. Footer with session stats

  Output: ~/xxnetro_exports/report_<timestamp>.html
  Open with: termux-open <file>
═════════════════════════════════════════════════════════════════════
"""

import os
import time
import html
from datetime import datetime
from collections import Counter

TOOL_NAME = "html_report"
TOOL_DESC = "Generate professional HTML scan report"
TOOL_VERSION = "1.0"

HOME_DIR = os.environ.get("TERMUX_HOME",
                          os.path.expanduser("~"))
EXPORT_DIR = os.path.join(HOME_DIR, "xxnetro_exports")

BRAND = "XXNETRO"
VERSION = "3.0"
AUTHOR = "XXNETRO"
YEAR = "2027"


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


os.makedirs(EXPORT_DIR, exist_ok=True)


# ══════════════════════════════════════════════════════════════════
#  L1 — DATA PREPARATION
# ══════════════════════════════════════════════════════════════════

class ReportData:
    """Extract statistics from scan results."""

    def __init__(self, networks: list,
                 session_stats=None):
        self.nets = networks
        self.stats = session_stats
        self.total = len(networks)
        self.hidden = sum(
            1 for n in networks
            if n.ssid == "[Hidden]")
        self.open_nets = sum(
            1 for n in networks
            if n.security == "open")
        self.secured = self.total - self.open_nets
        self.strongest = max(
            networks, key=lambda n: n.signal_dbm
        ) if networks else None
        self.weakest = min(
            networks, key=lambda n: n.signal_dbm
        ) if networks else None
        self.channels = Counter(
            n.channel for n in networks
            if n.channel)
        self.security_types = Counter(
            n.security for n in networks)
        self.bands = Counter(
            "5GHz" if n.frequency_mhz >= 5000
            else "2.4GHz" for n in networks)

    def quality_class(self, dbm: int) -> str:
        if dbm > -55:
            return "excellent"
        if dbm > -68:
            return "good"
        if dbm > -80:
            return "fair"
        return "weak"


# ══════════════════════════════════════════════════════════════════
#  L2 — HTML TEMPLATE (self-contained, no external assets)
# ══════════════════════════════════════════════════════════════════

CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: 'Segoe UI', system-ui, sans-serif;
    background: #0d1117;
    color: #c9d1d9;
    padding: 20px;
    max-width: 900px;
    margin: 0 auto;
}
.container { animation: fadein 0.5s; }
@keyframes fadein { from {opacity:0} to {opacity:1} }

/* header */
.header {
    background: linear-gradient(135deg,
        #1a1a2e 0%, #16213e 50%, #0f3460 100%);
    border-radius: 16px;
    padding: 30px;
    text-align: center;
    border: 1px solid #30363d;
    margin-bottom: 20px;
}
.header h1 {
    color: #00d9ff;
    font-size: 2.2em;
    letter-spacing: 3px;
    text-shadow: 0 0 20px rgba(0,217,255,0.5);
}
.header .subtitle {
    color: #8b949e;
    margin-top: 8px;
    font-size: 0.95em;
}
.header .meta {
    color: #58a6ff;
    margin-top: 12px;
    font-size: 0.85em;
}

/* summary cards */
.cards {
    display: grid;
    grid-template-columns: repeat(auto-fit,
        minmax(180px, 1fr));
    gap: 14px;
    margin-bottom: 22px;
}
.card {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 12px;
    padding: 18px;
    text-align: center;
    transition: transform 0.2s;
}
.card:hover { transform: translateY(-3px); }
.card .num {
    font-size: 2.4em;
    font-weight: bold;
    display: block;
}
.card .lbl {
    color: #8b949e;
    font-size: 0.85em;
    margin-top: 4px;
    text-transform: uppercase;
    letter-spacing: 1px;
}
.c-blue .num  { color: #58a6ff; }
.c-green .num { color: #3fb950; }
.c-yellow .num{ color: #d29922; }
.c-red .num   { color: #f85149; }

/* section titles */
h2 {
    color: #00d9ff;
    font-size: 1.3em;
    margin: 26px 0 12px;
    padding-bottom: 8px;
    border-bottom: 2px solid #21262d;
}

/* table */
table {
    width: 100%;
    border-collapse: collapse;
    background: #161b22;
    border-radius: 10px;
    overflow: hidden;
    font-size: 0.88em;
}
th {
    background: #21262d;
    padding: 12px 10px;
    text-align: left;
    color: #00d9ff;
    font-weight: 600;
}
td {
    padding: 10px;
    border-bottom: 1px solid #21262d;
}
tr:hover td { background: #1c2128; }
.hidden-row td { color: #f0883e; font-style: italic; }

/* signal bars */
.bar-cell { width: 120px; }
.sigbar {
    height: 10px;
    border-radius: 5px;
    background: #21262d;
    overflow: hidden;
}
.sigbar .fill {
    height: 100%;
    border-radius: 5px;
    transition: width 0.6s ease;
}
.excellent .fill { background: #3fb950; }
.good .fill      { background: #d29922; }
.fair .fill      { background: #f0883e; }
.weak .fill      { background: #f85149; }
td.excellent { color: #3fb950; font-weight: 600; }
td.good      { color: #d29922; font-weight: 600; }
td.fair      { color: #f0883e; }
td.weak      { color: #f85149; }

/* channel map */
.chmap {
    display: flex;
    gap: 6px;
    align-items: flex-end;
    height: 120px;
    padding: 12px;
    background: #161b22;
    border-radius: 10px;
    border: 1px solid #30363d;
    overflow-x: auto;
}
.chcol {
    flex: 1;
    min-width: 38px;
    display: flex;
    flex-direction: column;
    align-items: center;
    height: 100%;
    justify-content: flex-end;
}
.chcol .stack {
    width: 100%;
    display: flex;
    flex-direction: column-reverse;
    gap: 2px;
}
.chbar {
    height: 8px;
    border-radius: 2px;
    background: #58a6ff;
}
.chbar.warn { background: #d29922; }
.chbar.crit { background: #f85149; }
.chcol .chlabel {
    margin-top: 6px;
    color: #8b949e;
    font-size: 0.75em;
}
.chcol .chcount {
    color: #00d9ff;
    font-size: 0.8em;
    margin-bottom: 3px;
}

/* security pie (CSS conic) */
.pie-wrap {
    display: flex;
    gap: 20px;
    align-items: center;
    flex-wrap: wrap;
    background: #161b22;
    padding: 16px;
    border-radius: 10px;
    border: 1px solid #30363d;
}
.pie {
    width: 140px;
    height: 140px;
    border-radius: 50%;
    flex-shrink: 0;
}
.legend { font-size: 0.9em; }
.legend .li {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 6px 0;
}
.legend .dot {
    width: 12px;
    height: 12px;
    border-radius: 3px;
}
.legend .cnt { color: #8b949e; margin-left: auto; }

/* footer */
.footer {
    margin-top: 30px;
    text-align: center;
    color: #484f58;
    font-size: 0.8em;
    padding: 16px;
    border-top: 1px solid #21262d;
}
.footer .brand { color: #00d9ff; }

@media (max-width: 600px) {
    body { padding: 10px; }
    .header h1 { font-size: 1.6em; }
    .card .num { font-size: 1.8em; }
    table { font-size: 0.78em; }
    .bar-cell { width: 80px; }
}
"""


def build_html(data: ReportData) -> str:
    """Assemble the full HTML document."""
    ts = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S")

    # ── summary cards ──
    strongest_name = (data.strongest.display_name
                      if data.strongest else "-")
    strongest_dbm = (data.strongest.signal_dbm
                     if data.strongest else "-")

    cards = f"""
    <div class="cards">
        <div class="card c-blue">
            <span class="num">{data.total}</span>
            <span class="lbl">Networks Found</span>
        </div>
        <div class="card c-yellow">
            <span class="num">{data.hidden}</span>
            <span class="lbl">Hidden SSIDs</span>
        </div>
        <div class="card c-red">
            <span class="num">{data.open_nets}</span>
            <span class="lbl">Open / Unsecured</span>
        </div>
        <div class="card c-green">
            <span class="num">{strongest_dbm} dBm</span>
            <span class="lbl">Strongest: {strongest_name}
            </span>
        </div>
    </div>"""

    # ── network table ──
    rows = ""
    for n in sorted(data.nets,
                    key=lambda x: x.signal_dbm,
                    reverse=True):
        q = data.quality_class(n.signal_dbm)
        pct = max(0, min(100,
                int(((n.signal_dbm + 90) / 60) * 100)))
        esc_name = html.escape(n.display_name)
        hidden_cls = ("hidden-row"
                      if n.ssid == "[Hidden]" else "")
        rows += f"""
        <tr class="{hidden_cls} {q}">
            <td>{esc_name}</td>
            <td>{n.bssid}</td>
            <td class="{q}">{n.signal_dbm} dBm</td>
            <td class="bar-cell {q}">
                <div class="sigbar">
                    <div class="fill"
                         style="width:{pct}%"></div>
                </div>
            </td>
            <td>{n.channel}</td>
            <td>{n.band}</td>
            <td>{n.security}</td>
        </tr>"""

    table = f"""
    <h2>Network Inventory</h2>
    <table>
        <tr>
            <th>SSID</th><th>BSSID</th>
            <th>Signal</th><th>Quality</th>
            <th>Ch</th><th>Band</th><th>Security</th>
        </tr>
        {rows}
    </table>"""

    # ── channel map ──
    ch_cols = ""
    for ch in sorted(data.channels):
        cnt = data.channels[ch]
        cls = ("crit" if cnt >= 4 else
               "warn" if cnt >= 2 else "")
        bars = "".join(
            f'<div class="chbar {cls}"></div>'
            for _ in range(cnt))
        ch_cols += f"""
        <div class="chcol">
            <span class="chcount">{cnt}</span>
            <div class="stack">{bars}</div>
            <span class="chlabel">ch{ch}</span>
        </div>"""

    chmap = f"""
    <h2>Channel Congestion Map</h2>
    <div class="chmap">{ch_cols}</div>"""

    # ── security pie (conic-gradient) ──
    colors = {"wpa2": "#3fb950", "wpa3": "#00d9ff",
              "wpa": "#d29922", "open": "#f85149",
              "unknown": "#8b949e"}
    total = max(1, data.total)
    segments = []
    start = 0
    for sec, cnt in data.security_types.items():
        pct = cnt / total * 100
        col = colors.get(sec, "#8b949e")
        segments.append(
            f"{col} {start}% {start + pct}%")
        start += pct
    pie_css = ("background: conic-gradient("
               + ", ".join(segments) + ");")

    legend_items = ""
    for sec, cnt in data.security_types.items():
        col = colors.get(sec, "#8b949e")
        legend_items += f"""
        <div class="li">
            <span class="dot"
                  style="background:{col}"></span>
            {sec.upper()}
            <span class="cnt">{cnt}</span>
        </div>"""

    pie = f"""
    <h2>Security Breakdown</h2>
    <div class="pie-wrap">
        <div class="pie" style="{pie_css}"></div>
        <div class="legend">{legend_items}</div>
    </div>"""

    # ── session stats (if provided) ──
    stats_html = ""
    if data.stats:
        s = data.stats
        stats_html = f"""
    <h2>Session Statistics</h2>
    <table>
        <tr><th>Metric</th><th>Value</th></tr>
        <tr><td>Uptime</td>
            <td>{s.uptime_str()}</td></tr>
        <tr><td>Scans performed</td>
            <td>{s.scans_performed}</td></tr>
        <tr><td>Networks ever seen</td>
            <td>{s.networks_seen}</td></tr>
        <tr><td>Connect attempts</td>
            <td>{s.connect_attempts}</td></tr>
    </table>"""

    # ── full document ──
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport"
      content="width=device-width, initial-scale=1.0">
<title>XXNETRO Scan Report — {ts}</title>
<style>{CSS}</style>
</head>
<body>
<div class="container">

    <div class="header">
        <h1>⚡ {BRAND} SCAN REPORT</h1>
        <div class="subtitle">
            WiFi Reconnaissance Report — Ghost Scanner
        </div>
        <div class="meta">
            Generated {ts} │ Engine v{VERSION}
        </div>
    </div>

    {cards}
    {table}
    {chmap}
    {pie}
    {stats_html}

    <div class="footer">
        <span class="brand">{BRAND}</span>
        WiFi Suite v{VERSION} — by {AUTHOR} © {YEAR}
        <br>Built for the unrooted. No root. No trace.
    </div>
</div>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════════
#  L3 — TOOL INTERFACE
# ══════════════════════════════════════════════════════════════════

class HtmlReportTool:

    def __init__(self, scanner, connector):
        self.sc = scanner
        self.cx = connector

    def header(self):
        print(f"{C.M}{C.BOLD}")
        print("  ╔════════════════════════════════════════╗")
        print("  ║  HTML REPORT — Export Engine           ║")
        print("  ╚════════════════════════════════════════╝")
        print(f"{C.R}")

    def run_tool(self):
        self.header()
        while True:
            print(f"""
  {C.C}[1]{C.R} Scan + generate report now
  {C.C}[2]{C.R} Generate from last scan  {C.D}(cached){C.R}
  {C.C}[3]{C.R} List generated reports
  {C.C}[4]{C.R} Open report in browser   {C.D}(termux-open){C.R}
  {C.C}[0]{C.R} Back to XXNETRO
""")
            try:
                c = input(
                    f"  {C.M}{C.BOLD}report>{C.R} ").strip()
            except (KeyboardInterrupt, EOFError):
                return

            if c == '1':
                self.do_fresh_report()
            elif c == '2':
                self.do_cached_report()
            elif c == '3':
                self.do_list_reports()
            elif c == '4':
                self.do_open_report()
            elif c == '0':
                return

    # ─── actions ───

    def do_fresh_report(self):
        print(f"  {C.C}scanning...{C.R}")
        nets = self.sc.scan(force=True)
        if not nets:
            print(f"  {C.R}[!] empty scan{C.R}")
            return
        self._generate(nets)

    def do_cached_report(self):
        nets = self.sc.load_cache()
        if not nets:
            print(f"  {C.Y}no cache — run fresh [1]{C.R}")
            return
        print(f"  {C.D}(from cache: "
              f"{len(nets)} networks){C.R}")
        self._generate(nets)

    def _generate(self, nets):
        print(f"  {C.C}building report...{C.R}")
        data = ReportData(nets,
                          self.sc.stats)
        html_doc = build_html(data)

        ts = time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(
            EXPORT_DIR, f"report_{ts}.html")
        try:
            with open(path, "w",
                      encoding="utf-8") as f:
                f.write(html_doc)
            size_kb = os.path.getsize(path) / 1024
            print(f"\n  {C.G}[OK] report generated{C.R}")
            print(f"  {C.C}{path}{C.R}")
            print(f"  {C.D}({size_kb:.1f} KB, "
                  f"self-contained){C.R}")
            open_now = input(
                f"\n  {C.C}open in browser now? "
                f"(y/n): {C.R}").strip().lower()
            if open_now == 'y':
                self._open(path)
        except OSError as e:
            print(f"  {C.R}[FAIL] {e}{C.R}")

    def do_list_reports(self):
        files = [f for f in
                 sorted(os.listdir(EXPORT_DIR),
                        reverse=True)
                 if f.endswith(".html")]
        if not files:
            print(f"  {C.D}no reports yet{C.R}")
            return
        print(f"\n  {C.C}{C.BOLD}REPORTS{C.R}")
        print(f"  {C.D}{'─' * 44}{C.R}")
        for i, f in enumerate(files[:15]):
            path = os.path.join(EXPORT_DIR, f)
            size = os.path.getsize(path) / 1024
            print(f"  {C.B}[{i}]{C.R} "
                  f"{C.W}{f[:34]:<34}{C.R} "
                  f"{C.D}{size:.0f}KB{C.R}")
        print(f"  {C.D}{'─' * 44}{C.R}\n")

    def do_open_report(self):
        files = [f for f in
                 sorted(os.listdir(EXPORT_DIR),
                        reverse=True)
                 if f.endswith(".html")]
        if not files:
            print(f"  {C.D}no reports yet{C.R}")
            return
        for i, f in enumerate(files[:15]):
            print(f"  {C.B}[{i}]{C.R} {f}")
        try:
            c = input(
                f"  {C.C}open which? {C.R}").strip()
            idx = int(c)
            path = os.path.join(
                EXPORT_DIR, files[idx])
            self._open(path)
        except (ValueError, IndexError):
            print(f"  {C.R}invalid{C.R}")

    @staticmethod
    def _open(path: str):
        try:
            subprocess.run(
                ["termux-open", path],
                timeout=10)
            print(f"  {C.G}opened{C.R}")
        except Exception:
            print(f"  {C.Y}termux-open unavailable —"
                  f" open manually:{C.R}")
            print(f"  {C.C}{path}{C.R}")


# ══════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════

def run(scanner, connector):
    HtmlReportTool(scanner, connector).run_tool()
