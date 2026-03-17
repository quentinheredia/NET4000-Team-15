#!/usr/bin/env python3
"""
ntn_dashboard.py — NTN Interactive Dashboard
=============================================
Visualises simulation results.csv with:
  • KPI cards  (sim run, tick, active links, average delay)
  • Delay over time per link  (line chart)
  • Average delay per link    (horizontal bar chart)
  • Link up/down timeline     (Gantt-style)
  • Satellite position map    (square orbits)

Runs as a matplotlib GUI by default.
Pass --cli to print a formatted table to the terminal instead.

Usage
-----
  python3 ntn_dashboard.py                       # interactive GUI
  python3 ntn_dashboard.py --cli                 # terminal table, all ticks
  python3 ntn_dashboard.py --cli --tick 3        # terminal table, single tick
  python3 ntn_dashboard.py --csv other.csv       # custom CSV path
"""

import argparse
import math
import os
import sys

import matplotlib
import matplotlib.gridspec as gridspec
import matplotlib.lines
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator
from matplotlib.widgets import Button, Slider
import numpy as np
import pandas as pd


# ═══════════════════════════════════════════════════════════════════════════════
# 1.  CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

TOPOLOGY = [
    ("Sat1", "Sat2"), ("Sat1", "Sat3"),
    ("Sat2", "Sat3"),
    ("Sat2", "Sat4"), ("Sat2", "Sat5"),
    ("Sat3", "Sat5"), ("Sat3", "Sat6"),
    ("Sat4", "Sat5"),
    ("Sat5", "Sat6"),
]
ALL_LINKS = [f"{a}-{b}" for a, b in TOPOLOGY]

LINK_LAYER = {
    "Sat1-Sat2": "L3↔L2", "Sat1-Sat3": "L3↔L2",
    "Sat2-Sat3": "L2↔L2",
    "Sat2-Sat4": "L2↔L1", "Sat2-Sat5": "L2↔L1",
    "Sat3-Sat5": "L2↔L1", "Sat3-Sat6": "L2↔L1",
    "Sat4-Sat5": "L1↔L1",
    "Sat5-Sat6": "L1↔L1",
}
SAT_LAYER = {
    "Sat1": "L3", "Sat2": "L2", "Sat3": "L2",
    "Sat4": "L1", "Sat5": "L1", "Sat6": "L1",
}

PLANET_ROOT = 3
GRID        = (PLANET_ROOT + 15) ** 2   # = 324
CENTER      = GRID / 2                  # = 162.0

# ── colour palette ────────────────────────────────────────────────────────────
BG     = '#0d1117'
PANEL  = '#161b22'
BORDER = '#30363d'
TEXT   = '#c9d1d9'
MUTED  = '#8b949e'
ACCENT = '#58a6ff'
GREEN  = '#3fb950'
RED    = '#f85149'
YELLOW = '#d29922'
PURPLE = '#a371f7'

SAT_COLORS = {'L1': '#ffd700', 'L2': '#4fc3f7', 'L3': '#ce93d8'}

LINK_PALETTE = [
    '#58a6ff', '#3fb950', '#ffd700', '#f85149',
    '#a371f7', '#79c0ff', '#56d364', '#ffa657', '#ff7b72',
]
LINK_COLOR = {lnk: LINK_PALETTE[i] for i, lnk in enumerate(ALL_LINKS)}


# ═══════════════════════════════════════════════════════════════════════════════
# 2.  DATA LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def _delay(alt_a, alt_b, x1, y1, x2, y2):
    return round((alt_a + alt_b) / 2 * 8 + math.hypot(x2 - x1, y2 - y1) * 0.05, 1)


def load_data(csv_path):
    """
    Returns
    -------
    sim_nums  : sorted list[int]
    tick_data : { sim: { tick: { link: float|None } } }
    pos_data  : { sim: { tick: { sat: {x,y,alt} } } }
    time_data : { sim: { tick: float } }
    """
    df = pd.read_csv(csv_path)
    df['can_see'] = df['can_see'].fillna('')
    sim_nums  = sorted(df['sim_number'].unique().tolist())
    tick_data, pos_data, time_data = {}, {}, {}

    for sim in sim_nums:
        sdf = df[df['sim_number'] == sim]
        tick_data[sim] = {}
        pos_data[sim]  = {}
        time_data[sim] = {}

        for tick in sorted(sdf['tick'].unique().tolist()):
            tdf  = sdf[sdf['tick'] == tick]
            sats = {}
            for _, row in tdf.iterrows():
                can_see = [s.strip() for s in str(row['can_see']).split(',') if s.strip()]
                sats[row['sat_name']] = {
                    'x': float(row['x']), 'y': float(row['y']),
                    'alt': int(row['orbit_altitude']),
                    'can_see': can_see,
                }
            time_data[sim][tick] = float(tdf['time_s'].iloc[0])
            pos_data[sim][tick]  = sats

            delays = {}
            for (a, b) in TOPOLOGY:
                link = f"{a}-{b}"
                if a in sats and b in sats:
                    sa, sb = sats[a], sats[b]
                    delays[link] = (
                        _delay(sa['alt'], sb['alt'], sa['x'], sa['y'], sb['x'], sb['y'])
                        if b in sa['can_see'] else None
                    )
                else:
                    delays[link] = None
            tick_data[sim][tick] = delays

    return sim_nums, tick_data, pos_data, time_data


# ═══════════════════════════════════════════════════════════════════════════════
# 3.  CLI TABLE MODE
# ═══════════════════════════════════════════════════════════════════════════════

def _bar(value, max_val, width=20):
    filled = int(round(value / max_val * width)) if max_val else 0
    return '█' * filled + '░' * (width - filled)


def print_cli(sim_nums, tick_data, pos_data, time_data, tick_filter=None):
    W = 66
    for sim in sim_nums:
        ticks = sorted(tick_data[sim].keys())
        if tick_filter is not None:
            if tick_filter not in ticks:
                print(f"[warn] Tick {tick_filter} not found in simulation #{sim}")
                continue
            ticks = [tick_filter]

        for tick in ticks:
            delays  = tick_data[sim][tick]
            active  = {k: v for k, v in delays.items() if v is not None}
            avg     = round(sum(active.values()) / len(active), 1) if active else 0.0
            time_s  = time_data[sim].get(tick, 0)
            max_dly = max(active.values()) if active else 1

            print('╔' + '═' * W + '╗')
            title = f'NTN DASHBOARD  —  Simulation #{sim}'
            print(f'║{title:^{W}}║')
            print('╠' + '═' * W + '╣')
            kpi = (f'  TICK: {tick}  │  TIME: {time_s:.0f}s  │  '
                   f'ACTIVE: {len(active)}/{len(ALL_LINKS)}  │  '
                   f'AVG DELAY: {avg} ms')
            print(f'║{kpi:<{W}}║')
            print('╠' + '═' * W + '╣')
            hdr = f'  {"LINK":<14}  {"STATUS":<6}  {"DELAY":>8}  {"BAR":<22}  {"LAYER"}'
            print(f'║{hdr:<{W}}║')
            print('║  ' + '─' * (W - 2) + '  ║')

            for link in ALL_LINKS:
                d = delays[link]
                if d is not None:
                    status = 'UP  '
                    delay_str = f'{d:>6.1f} ms'
                    bar = _bar(d, max_dly)
                else:
                    status = 'DOWN'
                    delay_str = '       —'
                    bar = '░' * 20
                layer = LINK_LAYER.get(link, '')
                row = f'  {link:<14}  {status}  {delay_str}  {bar}  {layer}'
                print(f'║{row:<{W}}║')

            print('╚' + '═' * W + '╝')
            print()


# ═══════════════════════════════════════════════════════════════════════════════
# 4.  MATPLOTLIB GUI
# ═══════════════════════════════════════════════════════════════════════════════

def _style(ax, title=None, xlabel=None, ylabel=None, grid_x=True, grid_y=True):
    ax.set_facecolor(PANEL)
    for sp in ax.spines.values():
        sp.set_color(BORDER)
    ax.tick_params(colors=MUTED, labelsize=7)
    ax.xaxis.label.set_color(MUTED)
    ax.yaxis.label.set_color(MUTED)
    if title:
        ax.set_title(title, color=TEXT, fontsize=9, fontweight='bold', pad=5)
    if xlabel:
        ax.set_xlabel(xlabel, color=MUTED, fontsize=8)
    if ylabel:
        ax.set_ylabel(ylabel, color=MUTED, fontsize=8)
    if grid_x:
        ax.grid(True, axis='x', color=BORDER, linewidth=0.5, alpha=0.6)
    if grid_y:
        ax.grid(True, axis='y', color=BORDER, linewidth=0.5, alpha=0.6)


def _kpi(ax, label, value, unit='', color=ACCENT):
    ax.set_facecolor(PANEL)
    for sp in ax.spines.values():
        sp.set_color(color)
        sp.set_linewidth(1.8)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.text(0.5, 0.73, label,
            transform=ax.transAxes, ha='center', va='center',
            color=MUTED, fontsize=8, fontweight='bold')
    ax.text(0.5, 0.38, str(value),
            transform=ax.transAxes, ha='center', va='center',
            color=color, fontsize=22, fontweight='bold')
    if unit:
        ax.text(0.5, 0.10, unit,
                transform=ax.transAxes, ha='center', va='center',
                color=MUTED, fontsize=8)


class NTNDashboard:

    def __init__(self, sim_nums, tick_data, pos_data, time_data):
        self.sim_nums  = sim_nums
        self.tick_data = tick_data
        self.pos_data  = pos_data
        self.time_data = time_data
        self.sim_idx   = 0
        self.cur_tick  = 0
        self._build()
        self._widgets()
        self.render()

    @property
    def sim(self):
        return self.sim_nums[self.sim_idx]

    @property
    def ticks(self):
        return sorted(self.tick_data[self.sim].keys())

    # ── layout ────────────────────────────────────────────────────────────────

    def _build(self):
        self.fig = plt.figure(figsize=(18, 11), facecolor=BG)
        try:
            self.fig.canvas.manager.set_window_title('NTN Dashboard')
        except Exception:
            pass

        outer = gridspec.GridSpec(
            4, 1, figure=self.fig,
            height_ratios=[0.12, 0.37, 0.38, 0.05],
            hspace=0.40, left=0.06, right=0.97, top=0.94, bottom=0.09,
        )

        # Row 0 — KPI cards
        kpi_gs = gridspec.GridSpecFromSubplotSpec(1, 4, subplot_spec=outer[0], wspace=0.16)
        self.ax_kpi = [self.fig.add_subplot(kpi_gs[0, i]) for i in range(4)]

        # Row 1 — delay line + avg bar
        mid = gridspec.GridSpecFromSubplotSpec(
            1, 2, subplot_spec=outer[1], wspace=0.26, width_ratios=[2.5, 1])
        self.ax_line = self.fig.add_subplot(mid[0, 0])
        self.ax_bar  = self.fig.add_subplot(mid[0, 1])

        # Row 2 — timeline + satellite map
        bot = gridspec.GridSpecFromSubplotSpec(
            1, 2, subplot_spec=outer[2], wspace=0.26, width_ratios=[1.8, 1])
        self.ax_timeline = self.fig.add_subplot(bot[0, 0])
        self.ax_map      = self.fig.add_subplot(bot[0, 1])

        # Row 3 — tick slider + sim nav buttons
        self.ax_slider = self.fig.add_axes([0.12, 0.028, 0.58, 0.022], facecolor=PANEL)
        self.ax_prev   = self.fig.add_axes([0.74, 0.015, 0.07, 0.042])
        self.ax_next   = self.fig.add_axes([0.82, 0.015, 0.07, 0.042])

        # Title bar
        self.ax_title = self.fig.add_axes([0, 0.96, 1, 0.04], facecolor=BG)
        self.ax_title.set_xticks([]); self.ax_title.set_yticks([])
        for sp in self.ax_title.spines.values():
            sp.set_visible(False)

    # ── widgets ───────────────────────────────────────────────────────────────

    def _widgets(self):
        max_t = max(self.ticks) if self.ticks else 1
        self.slider = Slider(
            self.ax_slider, 'Tick', 0, max_t,
            valinit=0, valstep=1, color=ACCENT, track_color=PANEL,
        )
        self.slider.label.set_color(TEXT)
        self.slider.valtext.set_color(TEXT)

        for ax, label in [(self.ax_prev, '◀  Prev Sim'), (self.ax_next, 'Next Sim  ▶')]:
            ax.set_facecolor(PANEL)
            for sp in ax.spines.values():
                sp.set_color(BORDER)

        self.btn_prev = Button(self.ax_prev, '◀  Prev Sim', color=PANEL, hovercolor='#21262d')
        self.btn_next = Button(self.ax_next, 'Next Sim  ▶', color=PANEL, hovercolor='#21262d')
        self.btn_prev.label.set_color(TEXT)
        self.btn_next.label.set_color(TEXT)

        self.slider.on_changed(self._on_tick)
        self.btn_prev.on_clicked(self._prev_sim)
        self.btn_next.on_clicked(self._next_sim)

    def _on_tick(self, val):
        self.cur_tick = int(round(val))
        t = self.ticks
        if self.cur_tick not in t:
            self.cur_tick = min(t, key=lambda x: abs(x - self.cur_tick))
        self.render()

    def _prev_sim(self, _):
        self.sim_idx = (self.sim_idx - 1) % len(self.sim_nums)
        self._reset_slider()
        self.render()

    def _next_sim(self, _):
        self.sim_idx = (self.sim_idx + 1) % len(self.sim_nums)
        self._reset_slider()
        self.render()

    def _reset_slider(self):
        self.cur_tick = 0
        max_t = max(self.ticks) if self.ticks else 1
        self.slider.valmax = max_t
        self.slider.ax.set_xlim(0, max_t)
        self.slider.set_val(0)

    # ── render ────────────────────────────────────────────────────────────────

    def render(self):
        self._title()
        self._kpis()
        self._delay_line()
        self._avg_bar()
        self._timeline()
        self._sat_map()
        self.fig.canvas.draw_idle()

    # ── title ─────────────────────────────────────────────────────────────────

    def _title(self):
        ax = self.ax_title
        ax.clear()
        ax.set_facecolor(BG)
        ax.set_xticks([]); ax.set_yticks([])
        for sp in ax.spines.values():
            sp.set_visible(False)
        ax.text(0.5, 0.5,
                f'NTN Dashboard  —  Simulation Run #{self.sim}  '
                f'({len(self.sim_nums)} run{"s" if len(self.sim_nums) != 1 else ""} loaded)',
                transform=ax.transAxes, ha='center', va='center',
                color=TEXT, fontsize=13, fontweight='bold')

    # ── KPI cards ─────────────────────────────────────────────────────────────

    def _kpis(self):
        delays = self.tick_data[self.sim][self.cur_tick]
        active = [v for v in delays.values() if v is not None]
        avg    = round(sum(active) / len(active), 1) if active else 0.0
        t_s    = self.time_data[self.sim].get(self.cur_tick, 0)
        n      = len(self.sim_nums)

        link_col = GREEN if len(active) >= 6 else (YELLOW if len(active) >= 3 else RED)
        cards = [
            ('Simulation Run',  f'#{self.sim}',
             f'{self.sim_idx + 1} of {n}',  ACCENT),
            ('Current Tick',    str(self.cur_tick),
             f'time = {t_s:.0f} s',          YELLOW),
            ('Active Links',    f'{len(active)} / {len(ALL_LINKS)}',
             'links currently up',           link_col),
            ('Avg Link Delay',  str(avg),
             'ms (active links only)',       PURPLE),
        ]
        for ax, (lbl, val, unit, col) in zip(self.ax_kpi, cards):
            ax.clear()
            _kpi(ax, lbl, val, unit, col)

    # ── delay over time ───────────────────────────────────────────────────────

    def _delay_line(self):
        ax = self.ax_line
        ax.clear()
        _style(ax, 'Link Delay over Time', 'Tick', 'Delay (ms)')

        ticks = self.ticks
        handles = []
        for link in ALL_LINKS:
            vals = [self.tick_data[self.sim][t].get(link) for t in ticks]
            # split into continuous segments
            segs_x, segs_y, cur_x, cur_y = [], [], [], []
            for t, d in zip(ticks, vals):
                if d is not None:
                    cur_x.append(t); cur_y.append(d)
                else:
                    if cur_x:
                        segs_x.append(cur_x); segs_y.append(cur_y)
                    cur_x, cur_y = [], []
            if cur_x:
                segs_x.append(cur_x); segs_y.append(cur_y)

            col = LINK_COLOR[link]
            first = True
            for sx, sy in zip(segs_x, segs_y):
                lbl = link if first else '_'
                line, = ax.plot(sx, sy, color=col, linewidth=1.6,
                                alpha=0.85, label=lbl)
                first = False
            if segs_x:
                handles.append(matplotlib.lines.Line2D(
                    [], [], color=col, linewidth=1.6, label=link))

        ax.axvline(self.cur_tick, color=TEXT, linewidth=1.3,
                   linestyle='--', alpha=0.75, zorder=10)
        if handles:
            ax.legend(handles=handles, fontsize=6, loc='upper right',
                      framealpha=0.25, labelcolor=TEXT,
                      facecolor=PANEL, edgecolor=BORDER, ncol=2, handlelength=1.2)
        ax.xaxis.set_major_locator(MaxNLocator(integer=True))

    # ── avg delay bar ─────────────────────────────────────────────────────────

    def _avg_bar(self):
        ax = self.ax_bar
        ax.clear()
        _style(ax, 'Avg Delay per Link', 'ms', grid_y=False)

        avgs = []
        for link in ALL_LINKS:
            vals = [self.tick_data[self.sim][t][link]
                    for t in self.ticks
                    if self.tick_data[self.sim][t].get(link) is not None]
            avgs.append((link, round(sum(vals) / len(vals), 1) if vals else 0.0))
        avgs.sort(key=lambda x: x[1], reverse=True)

        names  = [a[0] for a in avgs]
        values = [a[1] for a in avgs]
        y_pos  = list(range(len(names)))

        bars = ax.barh(y_pos, values, color=[LINK_COLOR[n] for n in names],
                       alpha=0.82, height=0.62, edgecolor=BG, linewidth=0.4)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(names, fontsize=7, color=TEXT)
        ax.tick_params(axis='y', length=0)

        mx = max(values) if values else 1
        for bar, val in zip(bars, values):
            if val > 0:
                xpos = val + mx * 0.015
                ax.text(xpos, bar.get_y() + bar.get_height() / 2,
                        f'{val:.0f}', va='center', ha='left',
                        color=MUTED, fontsize=7)

        ax.set_xlim(0, mx * 1.18)
        ax.grid(True, axis='x', color=BORDER, linewidth=0.5, alpha=0.6)
        ax.grid(False, axis='y')

    # ── link up/down timeline ─────────────────────────────────────────────────

    def _timeline(self):
        ax = self.ax_timeline
        ax.clear()
        _style(ax, 'Link Up/Down Timeline', 'Tick', grid_x=False, grid_y=False)

        ticks = self.ticks
        for i, link in enumerate(ALL_LINKS):
            for t in ticks:
                d = self.tick_data[self.sim][t].get(link)
                col = GREEN if d is not None else RED
                ax.barh(i, 1, left=t - 0.5, color=col, alpha=0.78,
                        height=0.72, edgecolor=BG, linewidth=0.4)
                if d is not None:
                    ax.text(t, i, f'{d:.0f}', ha='center', va='center',
                            fontsize=5.5, color=BG, fontweight='bold')

        # Highlight current tick column
        ax.axvline(self.cur_tick, color=TEXT, linewidth=1.5,
                   linestyle='--', alpha=0.85, zorder=5)
        ax.axvspan(self.cur_tick - 0.5, self.cur_tick + 0.5,
                   alpha=0.10, color=ACCENT, zorder=4)

        ax.set_yticks(range(len(ALL_LINKS)))
        ax.set_yticklabels(ALL_LINKS, fontsize=7, color=TEXT)
        ax.tick_params(axis='y', length=0)
        ax.set_xlim(min(ticks) - 0.6, max(ticks) + 0.6)
        ax.xaxis.set_major_locator(MaxNLocator(integer=True))

        up_p   = mpatches.Patch(color=GREEN, alpha=0.78, label='Up')
        down_p = mpatches.Patch(color=RED,   alpha=0.78, label='Down')
        ax.legend(handles=[up_p, down_p], fontsize=7, loc='upper right',
                  facecolor=PANEL, edgecolor=BORDER, labelcolor=TEXT)

    # ── satellite position map ────────────────────────────────────────────────

    def _sat_map(self):
        ax = self.ax_map
        ax.clear()
        ax.set_facecolor('#080d14')
        for sp in ax.spines.values():
            sp.set_color(BORDER)
        ax.set_aspect('equal')
        ax.tick_params(colors=MUTED, labelsize=6)
        ax.set_title('Satellite Position Map', color=TEXT,
                     fontsize=9, fontweight='bold', pad=5)

        # Orbital shells (nested squares)
        for alt, col, lbl in [(5, '#ffd700', 'L1'), (10, '#4fc3f7', 'L2'), (15, '#ce93d8', 'L3')]:
            side = (PLANET_ROOT + alt) ** 2
            h    = side / 2
            rect = plt.Rectangle((CENTER - h, CENTER - h), side, side,
                                  fill=False, edgecolor=col,
                                  linewidth=1.2, linestyle='--', alpha=0.55)
            ax.add_patch(rect)
            ax.text(CENTER - h + 3, CENTER + h - 9, lbl,
                    color=col, fontsize=7, alpha=0.75)

        # Planet marker
        planet = plt.Circle((CENTER, CENTER), 9, color='#0d2137', zorder=3)
        ax.add_patch(planet)
        ax.text(CENTER, CENTER, '⊕', ha='center', va='center',
                color='#79c0ff', fontsize=11, zorder=4)

        # Active links
        positions = self.pos_data[self.sim][self.cur_tick]
        delays    = self.tick_data[self.sim][self.cur_tick]
        for link, delay in delays.items():
            if delay is not None:
                a, b = link.split('-', 1)
                if a in positions and b in positions:
                    x1, y1 = positions[a]['x'], positions[a]['y']
                    x2, y2 = positions[b]['x'], positions[b]['y']
                    ax.plot([x1, x2], [y1, y2], '-',
                            color=LINK_COLOR[link], alpha=0.45, linewidth=1.0, zorder=2)

        # Satellites
        markers = {'L3': 'D', 'L2': '^', 'L1': 'o'}
        for sat, info in positions.items():
            layer = SAT_LAYER.get(sat, 'L1')
            col   = SAT_COLORS[layer]
            ax.scatter(info['x'], info['y'], s=62, c=col,
                       marker=markers[layer], zorder=5,
                       edgecolors=BG, linewidths=0.8)
            ax.text(info['x'] + 5, info['y'] + 4, sat,
                    color=col, fontsize=6, zorder=6, alpha=0.90)

        pad = 22
        ax.set_xlim(-pad, GRID + pad)
        ax.set_ylim(-pad, GRID + pad)
        ax.grid(True, color='#111825', linewidth=0.5)


# ═══════════════════════════════════════════════════════════════════════════════
# 5.  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description='NTN Interactive Dashboard')
    ap.add_argument('--csv',  default='simulation results.csv',
                    help='Path to simulation results CSV  (default: "simulation results.csv")')
    ap.add_argument('--cli',  action='store_true',
                    help='Print formatted table to terminal instead of opening GUI')
    ap.add_argument('--tick', type=int, default=None,
                    help='(CLI mode) show only this tick number')
    ap.add_argument('--sim',  type=int, default=None,
                    help='(CLI mode) show only this simulation run number')
    args = ap.parse_args()

    # locate CSV relative to script if not found at CWD
    csv_path = args.csv
    if not os.path.exists(csv_path):
        alt = os.path.join(os.path.dirname(os.path.abspath(__file__)), args.csv)
        if os.path.exists(alt):
            csv_path = alt
        else:
            print(f'[error] CSV not found: {csv_path}')
            print('        Run python3 simulation/NTN.py first to generate data.')
            sys.exit(1)

    print(f'Loading {csv_path} …')
    sim_nums, tick_data, pos_data, time_data = load_data(csv_path)
    print(f'  Found {len(sim_nums)} simulation run(s): {sim_nums}')

    # optionally filter to a single sim
    if args.sim is not None:
        if args.sim not in sim_nums:
            print(f'[error] Simulation run #{args.sim} not found. Available: {sim_nums}')
            sys.exit(1)
        sim_nums  = [args.sim]
        tick_data = {args.sim: tick_data[args.sim]}
        pos_data  = {args.sim: pos_data[args.sim]}
        time_data = {args.sim: time_data[args.sim]}

    if args.cli:
        print_cli(sim_nums, tick_data, pos_data, time_data, tick_filter=args.tick)
        return

    # ── GUI mode ──────────────────────────────────────────────────────────────
    print('Opening dashboard … (close the window to exit)')
    matplotlib.rcParams.update({
        'figure.facecolor': BG,
        'axes.facecolor':   PANEL,
        'text.color':       TEXT,
        'xtick.color':      MUTED,
        'ytick.color':      MUTED,
        'axes.edgecolor':   BORDER,
        'grid.color':       BORDER,
        'font.family':      'monospace',
    })
    NTNDashboard(sim_nums, tick_data, pos_data, time_data)
    plt.show()


if __name__ == '__main__':
    main()
