#!/usr/bin/env python3
"""Routed length of every DDR data net against its strobe, as KiCad counts.

Track length plus the via barrels, each via counted between the layers
it actually joins (a ball via to L6 is about 0.95 mm, to L3 about
0.23 mm), compared with the mean of the two strobe legs per byte lane.
The lanes come from board.json (`lanes`: the data and strobe net name
patterns, channels, lanes per channel and the limit; Rockchip's limit for
DQ/DM to DQS is 0.635 mm), the layer depths from the stackup.

Usage: lanes.py --board DIR
"""

import argparse
import math
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "report"))
import board as board_mod  # noqa: E402
from sliceplot import arc_points  # noqa: E402
ITEM_RE = re.compile(r'\n\t\((segment|arc|via)\n((?:\t\t[^\n]*\n)+)\t\)')
XY_RE = re.compile(r'\((?:start|mid|end) ([-\d.]+) ([-\d.]+)\)')


def routed(items, net, z):
  """(track length, via length, via count) of `net` in mm; `z` maps a
  copper layer to its depth."""
  tracks, ends, vias = 0.0, [], []
  for kind, body in items:
    if f'(net "{net}")' not in body:
      continue
    if kind == "via":
      x, y = (float(v) for v in re.search(
          r'\(at ([-\d.]+) ([-\d.]+)\)', body).groups())
      vias.append((x, y))
      continue
    g = [(float(x), float(y)) for x, y in XY_RE.findall(body)]
    layer = re.search(r'\(layer "([^"]+)"\)', body).group(1)
    pts = arc_points(g[0], g[1], g[2], 32) if kind == "arc" else [g[0], g[1]]
    tracks += sum(math.hypot(b[0] - a[0], b[1] - a[1])
                  for a, b in zip(pts, pts[1:]))
    ends += [(g[0], layer), (g[-1], layer)]
  vlen = 0.0
  for x, y in vias:
    layers = {lay for (px, py), lay in ends
              if math.hypot(px - x, py - y) < 0.05}
    if len(layers) == 1:
      # a via-in-pad: the ball pad on F.Cu is the other end
      layers.add("F.Cu")
    zs = [z[lay] for lay in layers]
    vlen += max(zs) - min(zs) if len(zs) > 1 else 0.0
  return tracks, vlen, len(vias)


def lane_table(items, z, strobe_p, strobe_n, data, limit):
  """(reference, strobe legs, rows) for one byte lane; a row is
  (net, tracks, vias, routed, delta, ok)."""
  strobe = [routed(items, n, z) for n in (strobe_p, strobe_n)]
  ref = sum(t + v for t, v, _ in strobe) / 2
  rows = []
  for net in data:
    t, v, _ = routed(items, net, z)
    rows.append((net, t, v, t + v, t + v - ref, abs(t + v - ref) <= limit))
  return ref, strobe, rows


def main(argv=None):
  """CLI."""
  ap = argparse.ArgumentParser(description=__doc__)
  ap.add_argument("--board", type=pathlib.Path, default=pathlib.Path("."))
  args = ap.parse_args(argv)
  board = board_mod.Board(args.board)
  if not board.lanes:
    raise SystemExit("board.json has no `lanes` entry")
  items = ITEM_RE.findall(board.pcb.read_text())
  z = board.layer_z()
  limit = board.lanes.get("limit", 0.635)
  for strobe_p, strobe_n, data in board.lane_nets():
    ref, strobe, rows = lane_table(items, z, strobe_p, strobe_n, data, limit)
    legs = " / ".join(f"{t + v:.2f}" for t, v, _ in strobe)
    print(f"== {strobe_p[:-2]}: strobe {legs} mm, reference {ref:.2f}")
    for net, t, v, r, d, ok in rows:
      print(f"   {net:12s} tracks {t:6.2f} vias {v:4.2f} routed {r:6.2f}"
            f"  {d:+.2f}{'' if ok else f'  <-- outside {limit}'}")


if __name__ == "__main__":
  main()
