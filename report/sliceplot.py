#!/usr/bin/env python3
"""Draw a simulation slice: the net under test, its neighbours and ports.

Reads the slice `.kicad_pcb` (as fix_ports left it) and draws every track
with its real width, coloured by layer for the simulated nets and grey for
every other net in the slice (named in the legend), whatever its layer.
Ports are drawn as filled strips with their number, vias of the simulated
nets as rings; the edge-stitching GND vias are left out so the picture is
the routing, not the fixture.
"""

import math
import pathlib
import re

import matplotlib
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.patches import Circle, Rectangle

matplotlib.use("Agg")

LAYER_COLOUR = {"F.Cu": "#eb6834", "In2.Cu": "#2a78d6", "In5.Cu": "#4a3aa7",
                "B.Cu": "#1baf7a"}
LAYER_LABEL = {"F.Cu": "L1", "In1.Cu": "L2", "In2.Cu": "L3", "In3.Cu": "L4",
               "In4.Cu": "L5", "In5.Cu": "L6", "In6.Cu": "L7", "B.Cu": "L8"}
NEIGHBOUR = "#b8b7b0"
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e2e1dc"
PORT = "#d03b3b"
TRACK_RE = re.compile(
    r'\((segment|arc)\s*\(start ([-\d.]+) ([-\d.]+)\)'
    r'(?:\s*\(mid ([-\d.]+) ([-\d.]+)\))?\s*\(end ([-\d.]+) ([-\d.]+)\)'
    r'\s*\(width ([-\d.]+)\)\s*\(layer "([^"]+)"\)(?:.*?\n)*?[^\n]*'
    r'\(net "([^"]+)"\)')
VIA_RE = re.compile(r'\(via\s*\(at ([-\d.]+) ([-\d.]+)\)\s*\(size ([-\d.]+)\)'
                    r'(?:.*?\n)*?[^\n]*\(net "([^"]+)"\)')
PORT_RE = re.compile(r'\(footprint "Simulation_Port"(?:.*?\n)*?'
                     r'\t\t\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)'
                     r'(?:.*?\n)*?\t\t\(property "Reference" "SP(\d+)"')
EDGE_RE = re.compile(r'\(gr_rect\s*\(start ([-\d.]+) ([-\d.]+)\)'
                     r'\s*\(end ([-\d.]+) ([-\d.]+)\)')


def arc_points(a, m, b, n=12):
  """Points along a circular arc through a, m, b."""
  ax, ay = a
  mx, my = m
  bx, by = b
  d = 2 * (ax * (my - by) + mx * (by - ay) + bx * (ay - my))
  if abs(d) < 1e-9:
    return [a, b]
  ux = ((ax * ax + ay * ay) * (my - by) + (mx * mx + my * my) * (by - ay)
        + (bx * bx + by * by) * (ay - my)) / d
  uy = ((ax * ax + ay * ay) * (bx - mx) + (mx * mx + my * my) * (ax - bx)
        + (bx * bx + by * by) * (mx - ax)) / d
  r = math.hypot(ax - ux, ay - uy)
  t0, t1 = math.atan2(ay - uy, ax - ux), math.atan2(by - uy, bx - ux)
  tm = math.atan2(my - uy, mx - ux)
  # go the way that passes through m
  if (t0 < tm < t1) or (t1 < tm < t0):
    pass
  elif t1 > t0:
    t1 -= 2 * math.pi
  else:
    t1 += 2 * math.pi
  return [(ux + r * math.cos(t0 + (t1 - t0) * i / n),
           uy + r * math.sin(t0 + (t1 - t0) * i / n)) for i in range(n + 1)]


def parse(pcb_text):
  """Tracks, vias, ports and outline from a slice board."""
  tracks = []
  for m in TRACK_RE.finditer(pcb_text):
    kind, x1, y1, mx, my, x2, y2, w, layer, net = m.groups()
    a, b = (float(x1), float(y1)), (float(x2), float(y2))
    pts = arc_points(a, (float(mx), float(my)), b) if kind == "arc" else [a, b]
    tracks.append(dict(pts=pts, w=float(w), layer=layer, net=net))
  vias = [dict(x=float(x), y=float(y), size=float(s), net=n)
          for x, y, s, n in VIA_RE.findall(pcb_text)]
  ports = [dict(x=float(x), y=float(y), rot=int(float(r or 0)), n=int(n))
           for x, y, r, n in PORT_RE.findall(pcb_text)]
  e = EDGE_RE.search(pcb_text)
  edge = tuple(float(v) for v in e.groups()) if e else None
  return tracks, vias, ports, edge


def draw(pcb_path, nets, out, port_len=None, title=None):
  """Render the slice to `out` (PNG)."""
  tracks, vias, ports, edge = parse(pathlib.Path(pcb_path).read_text())
  fig, ax = plt.subplots(figsize=(3.2, 3.2))
  x0, y0, x1, y1 = edge
  ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False,
                         edgecolor=GRID, linewidth=0.8))
  ax.set_xlim(x0 - 0.3, x1 + 0.3)
  ax.set_ylim(y1 + 0.3, y0 - 0.3)
  ax.set_aspect("equal")
  fig.canvas.draw()
  pts_per_mm = (ax.get_window_extent().width / (x1 - x0 + 0.6)) * 72 / fig.dpi
  used_layers = []
  for net_first in (False, True):
    for t in tracks:
      mine = t["net"] in nets
      if mine != net_first:
        continue
      colour = LAYER_COLOUR.get(t["layer"], "#888") if mine else NEIGHBOUR
      if mine and t["layer"] not in used_layers:
        used_layers.append(t["layer"])
      lc = LineCollection([t["pts"]], colors=[colour],
                          linewidths=[t["w"] * pts_per_mm], capstyle="round",
                          joinstyle="round", alpha=1.0 if mine else 0.8)
      ax.add_collection(lc)
  for v in vias:
    if v["net"] == "GND":
      continue
    ax.add_patch(Circle((v["x"], v["y"]), v["size"] / 2, facecolor="white",
                        edgecolor=INK2 if v["net"] in nets else NEIGHBOUR,
                        linewidth=0.8))
  grow = {0: (0, -1), 180: (0, 1), 90: (-1, 0), 270: (1, 0)}
  for p in ports:
    gx, gy = grow[p["rot"] % 360]
    L = port_len or 0.3
    w = 0.16
    cx, cy = p["x"] + gx * L / 2, p["y"] + gy * L / 2
    rect = Rectangle((cx - (L if gx else w) / 2, cy - (L if gy else w) / 2),
                     L if gx else w, L if gy else w, facecolor=PORT,
                     edgecolor="white", linewidth=0.5, alpha=0.9)
    ax.add_patch(rect)
    ax.text(p["x"] - gx * 0.25, p["y"] - gy * 0.25, str(p["n"]), color=INK,
            fontsize=7, ha="center", va="center", fontweight="bold")
  # legend: layers of the net under test
  handles = [plt.Line2D([], [], color=LAYER_COLOUR.get(ly, "#888"),
                        linewidth=3, label=f"{LAYER_LABEL.get(ly, ly)} ({ly})")
             for ly in used_layers]
  others = sorted({t["net"] for t in tracks if t["net"] not in nets})
  if others:
    handles.append(plt.Line2D([], [], color=NEIGHBOUR, linewidth=3,
                              label="other nets: " + ", ".join(others[:3])
                              + (" …" if len(others) > 3 else "")))
  handles.append(plt.Line2D([], [], color=PORT, linewidth=4, label="port"))
  ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.02),
            ncol=min(4, len(handles)), frameon=False, fontsize=7,
            handlelength=1.2)
  # scale bar, 1 mm
  ax.plot([x1 - 1.3, x1 - 0.3], [y1 - 0.4, y1 - 0.4], color=INK2,
          linewidth=1.5, solid_capstyle="butt")
  ax.text(x1 - 0.8, y1 - 0.55, "1 mm", color=INK2, fontsize=6, ha="center",
          va="bottom")
  ax.set_xticks([])
  ax.set_yticks([])
  for s in ax.spines.values():
    s.set_visible(False)
  if title:
    ax.set_title(title, loc="left", fontsize=9, color=INK)
  fig.tight_layout(pad=0.3)
  fig.savefig(out, dpi=200)
  plt.close(fig)
