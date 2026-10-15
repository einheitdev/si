#!/usr/bin/env python3
"""Print-style plots for SI results (matplotlib, light surface).

Reads gerber2ems `Port_<k>_data.csv` files and draws:
  impedance vs frequency with the tolerance band,
  S-parameters in dB (return loss, insertion loss, crosstalk),
  the reflection on a Smith chart and the group delay through the trace,
  for pairs: differential impedance, mixed-mode S-parameters, mode
  conversion, each leg's own impedance and the leg-to-leg coupling.

Style: 2 px lines, hairline grey grid, text in ink tones, series colours
from the reference palette, a marker line at the LPDDR4X Nyquist frequency.
"""

import cmath
import csv
import glob
import math
import pathlib

import matplotlib
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt

matplotlib.use("Agg")

# reference palette (dataviz skill), light surface
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#4a3aa7"]
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#8b8a84", "#e2e1dc"
GOOD, CRITICAL = "#0ca30c", "#d03b3b"
NYQUIST_DEFAULT_GHZ, NYQUIST_DEFAULT_LABEL = 2.133, " Nyquist 4266 MT/s"
# build_report sets these per case from the board's `report` spec
NYQUIST_GHZ, NYQUIST_LABEL = NYQUIST_DEFAULT_GHZ, NYQUIST_DEFAULT_LABEL


def setup_style():
  """Register Fira Sans if present and set the print look."""
  for path in glob.glob(str(pathlib.Path.home() / ".local/share/fonts"
                            / "fira-sans" / "*.ttf")) \
      + glob.glob("/home/karl/claude-work/.local/share/fonts/fira-sans/*.ttf"):
    fm.fontManager.addfont(path)
  names = {f.name for f in fm.fontManager.ttflist}
  family = "Fira Sans" if "Fira Sans" in names else "DejaVu Sans"
  plt.rcParams.update({
      "font.family": family, "font.size": 9, "axes.titlesize": 10,
      "axes.titleweight": "medium", "axes.labelsize": 9,
      "axes.edgecolor": GRID, "axes.labelcolor": INK2,
      "axes.spines.top": False, "axes.spines.right": False,
      "xtick.color": INK2, "ytick.color": INK2, "xtick.labelsize": 8,
      "ytick.labelsize": 8, "grid.color": GRID, "grid.linewidth": 0.6,
      "axes.grid": True, "axes.axisbelow": True, "lines.linewidth": 1.6,
      "legend.frameon": False, "legend.fontsize": 8,
      "figure.facecolor": "white", "axes.facecolor": "white",
      "savefig.dpi": 200, "text.color": INK,
  })


def read_sparams(raw_dir):
  """(freqs_ghz, S) with S[(i, k)] complex arrays, from Port_*_data.csv."""
  freqs, s_params, _ = read_all(raw_dir)
  return freqs, s_params


def read_all(raw_dir):
  """(freqs_ghz, S, delays) with delays[(i, k)] in ps, from Port_*_data.csv."""
  s_params, delays, freqs = {}, {}, None
  for path in sorted(pathlib.Path(raw_dir).glob("Port_*_data.csv")):
    k = int(path.stem.split("_")[1])
    rows = list(csv.reader(open(path)))
    hdr, data = rows[0], [[float(x) for x in r] for r in rows[1:]]
    freqs = [r[0] / 1000 for r in data]
    for col, name in enumerate(hdr):
      if name.startswith("|S"):
        i = int(name[2:].split("-")[0])
        arg = hdr.index(f"Arg(S{i}-{k}) [rad]")
        s_params[(i, k)] = [cmath.rect(r[col], r[arg]) for r in data]
      elif name.startswith("Delay "):
        i = int(name.split(">")[1].split()[0])
        delays[(i, k)] = [r[col] * 1e12 for r in data]
  return freqs, s_params, delays


def db(values):
  """Magnitude in dB, floored."""
  return [20 * math.log10(max(abs(v), 1e-12)) for v in values]


def z_from_gamma(gammas, z_ref):
  """|Z| from a reflection coefficient series."""
  return [abs(z_ref * (1 + g) / (1 - g)) for g in gammas]


def _nyquist(ax):
  ax.axvline(NYQUIST_GHZ, color=MUTED, linewidth=0.8, linestyle=(0, (3, 3)))
  ax.text(NYQUIST_GHZ, ax.get_ylim()[1], NYQUIST_LABEL, color=INK2,
          fontsize=7, va="top", ha="left")


def _finish(fig, ax, out, xlabel="Frequency (GHz)"):
  ax.set_xlabel(xlabel)
  ax.margins(x=0)
  fig.tight_layout(pad=0.6)
  fig.savefig(out)
  plt.close(fig)


def plot_impedance(freqs, z, target, tol, out, title="Impedance"):
  """|Z| vs f with the target band shaded."""
  fig, ax = plt.subplots(figsize=(3.6, 2.3))
  lo, hi = target * (1 - tol), target * (1 + tol)
  ax.axhspan(lo, hi, color=SERIES[0], alpha=0.10, linewidth=0)
  ax.axhline(target, color=SERIES[0], alpha=0.35, linewidth=0.8)
  ax.plot(freqs, z, color=SERIES[0])
  ax.set_ylim(min(lo - 10, min(z) - 2), max(hi + 10, max(z) + 2))
  ax.set_ylabel("|Z| (Ω)")
  ax.set_title(title, loc="left")
  _nyquist(ax)
  ax.text(freqs[-1], z[-1], f" {z[-1]:.0f} Ω", color=INK2, fontsize=7,
          va="center", ha="left", clip_on=False)
  _finish(fig, ax, out)


def plot_sparams(freqs, series, out, title="S-parameters", floor=-60):
  """Named S-parameter magnitudes in dB; series = [(label, values_db)]."""
  fig, ax = plt.subplots(figsize=(3.6, 2.3))
  for i, (label, vals) in enumerate(series):
    ax.plot(freqs, [max(v, floor) for v in vals], color=SERIES[i % 6],
            label=label)
  ax.set_ylim(floor, 5)
  ax.set_ylabel("dB")
  ax.set_title(title, loc="left")
  if len(series) > 1:
    ax.legend(loc="lower left", ncol=2)
  _nyquist(ax)
  _finish(fig, ax, out)


def plot_smith(freqs, gammas, out, title="Smith chart", label="S11"):
  """Reflection coefficient on a Smith chart, start to stop, Nyquist marked.

  A trace sitting near the centre is matched; a loop away from it is a
  reflection, and its angle says whether the mismatch is inductive
  (upper half) or capacitive (lower half).
  """
  fig, ax = plt.subplots(figsize=(3.6, 2.3))
  ax.set_aspect("equal")
  ax.grid(False)
  for sp in ax.spines.values():
    sp.set_visible(False)
  ax.set_xticks([])
  ax.set_yticks([])
  t = [2 * math.pi * i / 200 for i in range(201)]
  ax.plot([math.cos(v) for v in t], [math.sin(v) for v in t], color=GRID,
          linewidth=0.8)
  # constant-resistance circles r = 0.5, 1, 2 and reactance arcs x = ±1
  for r in (0.5, 1, 2):
    c, rad = r / (1 + r), 1 / (1 + r)
    ax.plot([c + rad * math.cos(v) for v in t],
            [rad * math.sin(v) for v in t], color=GRID, linewidth=0.6)
  for x in (1, -1):
    pts = [(1 + 1j * x * math.tan(v)) for v in
           [-math.pi / 2 + math.pi * i / 400 for i in range(1, 400)]]
    g = [(z - 1) / (z + 1) for z in pts]
    g = [v for v in g if abs(v) <= 1.001]
    ax.plot([v.real for v in g], [v.imag for v in g], color=GRID,
            linewidth=0.6)
  ax.plot([-1, 1], [0, 0], color=GRID, linewidth=0.6)
  ax.plot([g.real for g in gammas], [g.imag for g in gammas], color=SERIES[0],
          label=label)
  k = min(range(len(freqs)), key=lambda i: abs(freqs[i] - NYQUIST_GHZ))
  ax.plot(gammas[0].real, gammas[0].imag, "o", color=SERIES[0], markersize=4)
  ax.plot(gammas[k].real, gammas[k].imag, "o", color=INK, markersize=4)
  ax.text(gammas[k].real, gammas[k].imag, " Nyquist", color=INK2, fontsize=7,
          va="bottom", ha="left")
  ax.text(gammas[-1].real, gammas[-1].imag, f" {freqs[-1]:.0f} GHz",
          color=INK2, fontsize=7, va="top", ha="left")
  ax.set_xlim(-1.15, 1.35)
  ax.set_ylim(-1.1, 1.1)
  ax.set_title(title, loc="left")
  fig.tight_layout(pad=0.6)
  fig.savefig(out)
  plt.close(fig)


def plot_delay(freqs, series, out, title="Group delay"):
  """Group delay in ps vs f; series = [(label, values_ps)].

  Flat means a clean line; a ripple at the via or meander frequencies is
  the same reflection the impedance plot shows, seen as timing.
  """
  fig, ax = plt.subplots(figsize=(3.6, 2.3))
  vals_all = []
  for i, (label, vals) in enumerate(series):
    ax.plot(freqs, vals, color=SERIES[i % 6], label=label)
    vals_all += vals[len(vals) // 10:]
  if vals_all:
    med = sorted(vals_all)[len(vals_all) // 2]
    ax.set_ylim(max(0, med - 60), med + 60)
  ax.set_ylabel("delay (ps)")
  ax.set_title(title, loc="left")
  if len(series) > 1:
    ax.legend(loc="upper left", ncol=2)
  _nyquist(ax)
  _finish(fig, ax, out)


def se_figures(raw_dir, out_dir, prefix, z_ref, tol, far_port, others):
  """Impedance, S-parameter, Smith and delay plots for one excitation."""
  freqs, s, delays = read_all(raw_dir)
  out_dir = pathlib.Path(out_dir)
  z = z_from_gamma(s[(0, 0)], z_ref)
  plot_impedance(freqs, z, z_ref, tol, out_dir / f"{prefix}_z.png",
                 f"{prefix}: impedance at the driver end")
  series = [("return loss S11", db(s[(0, 0)])),
            ("insertion loss", db(s[(far_port, 0)]))]
  for i in others:
    series.append((f"to port {i}", db(s[(i, 0)])))
  plot_sparams(freqs, series, out_dir / f"{prefix}_s.png",
               f"{prefix}: S-parameters")
  plot_smith(freqs, s[(0, 0)], out_dir / f"{prefix}_smith.png",
             f"{prefix}: S11 on the Smith chart")
  plot_delay(freqs, [("through the trace", delays[(far_port, 0)])],
             out_dir / f"{prefix}_delay.png", f"{prefix}: group delay")
  return [f"{prefix}_z.png", f"{prefix}_s.png", f"{prefix}_smith.png",
          f"{prefix}_delay.png"]


def pair_figures(raw_dir, out_dir, prefix, z_diff, tol, legs, use_only=None):
  """Differential impedance, mixed-mode and leg coupling plots for a pair.

  `legs` = (p_near, n_near, p_far, n_far). With `use_only` set to one near
  port, the symmetric single-excitation estimate is drawn instead.
  """
  freqs, s, delays = read_all(raw_dir)
  out_dir = pathlib.Path(out_dir)
  pn, nn, pf, nf = legs
  mm = mixed_mode(s, legs, use_only)
  sdd11, sdd21 = mm["sdd11"], mm["sdd21"]
  plot_impedance(freqs, z_from_gamma(sdd11, z_diff), z_diff, tol,
                 out_dir / f"{prefix}_zdiff.png",
                 f"{prefix}: differential impedance")
  plot_sparams(freqs, [("SDD11", db(sdd11)), ("SDD21", db(sdd21))],
               out_dir / f"{prefix}_sdd.png", f"{prefix}: mixed-mode")
  k = pn if use_only in (None, pn) else nn
  o, of = (nn, nf) if k == pn else (pn, pf)
  plot_sparams(freqs, [("leg NEXT", db(s[(o, k)])),
                       ("leg FEXT", db(s[(of, k)]))],
               out_dir / f"{prefix}_legs.png",
               f"{prefix}: leg-to-leg coupling")
  plot_smith(freqs, sdd11, out_dir / f"{prefix}_smith.png",
             f"{prefix}: SDD11 on the Smith chart", "SDD11")
  legz = [("P leg", z_from_gamma(s[(pn, pn)], z_diff / 2))]
  if use_only is None:
    legz.append(("N leg", z_from_gamma(s[(nn, nn)], z_diff / 2)))
  plot_legs_z(freqs, legz, z_diff / 2, tol, out_dir / f"{prefix}_legz.png",
              f"{prefix}: each leg alone")
  out = [f"{prefix}_zdiff.png", f"{prefix}_sdd.png", f"{prefix}_legs.png",
         f"{prefix}_smith.png", f"{prefix}_legz.png"]
  if "scd21" in mm:
    plot_sparams(freqs, [("SCD21 diff→common", db(mm["scd21"])),
                         ("SDC21 common→diff", db(mm["sdc21"]))],
                 out_dir / f"{prefix}_conv.png",
                 f"{prefix}: mode conversion")
    out.append(f"{prefix}_conv.png")
  k_far = kf_of(legs, k)
  plot_delay(freqs, [("through the leg", delays[(k_far, k)])],
             out_dir / f"{prefix}_delay.png", f"{prefix}: group delay")
  out.append(f"{prefix}_delay.png")
  return out


def kf_of(legs, k):
  """Far port of near port k in (p_near, n_near, p_far, n_far)."""
  pn, nn, pf, nf = legs
  return pf if k == pn else nf


def mixed_mode(s, legs, use_only=None):
  """Mixed-mode series from the 4-port S; keys sdd11, sdd21 and, with
  both excitations available, scd21 / sdc21 (mode conversion)."""
  pn, nn, pf, nf = legs
  n = len(s[(pn, pn)])
  if use_only is None:
    out = {
        "sdd11": [(s[(pn, pn)][i] - s[(pn, nn)][i] - s[(nn, pn)][i]
                   + s[(nn, nn)][i]) / 2 for i in range(n)],
        "sdd21": [(s[(pf, pn)][i] - s[(pf, nn)][i] - s[(nf, pn)][i]
                   + s[(nf, nn)][i]) / 2 for i in range(n)],
        "scd21": [(s[(pf, pn)][i] - s[(pf, nn)][i] + s[(nf, pn)][i]
                   - s[(nf, nn)][i]) / 2 for i in range(n)],
        "sdc21": [(s[(pf, pn)][i] + s[(pf, nn)][i] - s[(nf, pn)][i]
                   - s[(nf, nn)][i]) / 2 for i in range(n)],
    }
    return out
  k, kf, o, of = ((pn, pf, nn, nf) if use_only == pn else (nn, nf, pn, pf))
  return {"sdd11": [s[(k, k)][i] - s[(o, k)][i] for i in range(n)],
          "sdd21": [s[(kf, k)][i] - s[(of, k)][i] for i in range(n)]}


def plot_legs_z(freqs, series, target, tol, out, title):
  """Each leg's single-ended |Z| against the odd-mode target (Z_diff / 2)."""
  fig, ax = plt.subplots(figsize=(3.6, 2.3))
  lo, hi = target * (1 - tol), target * (1 + tol)
  ax.axhspan(lo, hi, color=SERIES[0], alpha=0.10, linewidth=0)
  zs = []
  for i, (label, z) in enumerate(series):
    ax.plot(freqs, z, color=SERIES[i % 6], label=label)
    zs += z
  ax.set_ylim(min(lo - 10, min(zs) - 2), max(hi + 10, max(zs) + 2))
  ax.set_ylabel("|Z| (Ω)")
  ax.set_title(title, loc="left")
  ax.legend(loc="lower left", ncol=2)
  _nyquist(ax)
  _finish(fig, ax, out)
