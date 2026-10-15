#!/usr/bin/env python3
"""Move si-wrapper simulation ports onto the trace ends of a slice.

si-wrapper places `Simulation_Port` footprints next to the (hidden) pads of
the designated nets. On this board the pads are BGA balls with via-in-pad
or the trace meets the pad at an angle, so the gerber2ems port strip never
overlaps the copper and the simulation sees open ports. `fix_slice` walks
each designated net from its free ends inward to the first axis-aligned
segment, deletes the diagonal remainder and the via-in-pad there (or, when
that would cut more than MAX_CUT, keeps the end and adds a short straight
stub for the port), drops the port footprint on that end with the rotation
that lays the port strip along the trace, rewrites the port
layer/width/length in simulation.json, puts the aux (drill/place) origin
at the slice's bottom-left corner so Gerbers, drills and port positions
share one positive, y-up frame, and finally refills the zones with
kicad-cli. The refill matters: the fills copied from the board are stale
(the board is saved without refilling after routing) and si-wrapper keeps
them as they are, so a layer-change via went through solid plane copper
and shorted the net. Refilling gives every remaining via its antipad and
closes the plane where the via-in-pad under a port was removed.

CLI: fix_ports.py <slice.kicad_pcb> [<net1> [<net2>]]  (run inside the
slice dir). With no nets it only sets the aux origin. Export afterwards:
  kicad-cli pcb export gerbers --no-protel-ext --use-drill-file-origin \\
      -o fab/ X.kicad_pcb
  kicad-cli pcb export drill --format excellon --excellon-separate-th \\
      --drill-origin plot -o fab/ X.kicad_pcb
  kicad-cli pcb export pos --format csv --units mm --side front \\
      --use-drill-file-origin -o fab/X-top-pos.csv X.kicad_pcb
"""

import json
import math
import pathlib
import re
import subprocess
import sys

LAYER_INDEX = {"F.Cu": 0, "In1.Cu": 1, "In2.Cu": 2, "In3.Cu": 3, "In4.Cu": 4,
               "In5.Cu": 5, "In6.Cu": 6, "B.Cu": 7}
# copper layers of the board top to bottom (gerber2ems metal index order)
# and the plane a port on a layer is referenced to; run_si sets both from
# board.json so a 4-layer board gets B.Cu = 3 and its own plane instead of
# the 8-layer rule (L2 for L1-L4, L7 for L5-L8)
COPPER_LAYERS = list(LAYER_INDEX)
PORT_PLANE = {}


def layer_plane(layer):
  """(metal index of `layer`, metal index of the plane its port uses)."""
  idx = COPPER_LAYERS.index(layer)
  if layer in PORT_PLANE:
    return idx, COPPER_LAYERS.index(PORT_PLANE[layer])
  return idx, (1 if idx <= 3 else 6)


# a port needs a straight uniform run under it: shorter straight pieces at a
# trace end are cut off and the port moves inward (mm)
MIN_PORT_SEG = 0.8
PORT_LEN = 0.3
# at most this much trace may be cut off an end looking for a straight run;
# beyond it the end is kept and the port goes on a stub (see find_ports)
MAX_CUT = 0.6
# straight stub added beyond a free end that cannot be cut. Both BGAs are
# on a 0.65 mm pitch and the stub points back into the ball field, so it
# must end before the next ball's via-in-pad (often GND): 0.35 + half the
# trace width + the via ring (0.125) stays 85 µm clear of 0.65
STUB_LEN = 0.35
MIN_STUB = 0.2
# the port strip keeps this much from the ring of any via beside the stub
STUB_CLEAR = 0.15
# an own via this close to a port end is its via-in-pad and is removed
VIA_AT_PORT = 0.3
# a dangling side branch longer than this is not an editing spur but a
# leftover route (seen: 5 mm on DDR_DQ9_B) and is reported, not pruned
MAX_SPUR = 1.0
# plane fills are clamped this far inside the slice outline (mm)
FILL_INSET = 0.15
# set False to leave the plane layers unstitched (debugging only)
STITCH = True
# experiment: a GND return via this far (mm) from every kept layer-change
# via of the simulated nets, on the freest of its four sides; None = off
# (run_si --return-via). The board has almost no GND vias yet, so this
# shows what the Rockchip "return via within 25 mil" rule buys.
RETURN_VIA = None
# zone clearance (mm) written into the slice's zones before the refill;
# None keeps the board's own. The board's GND zone was poured with KiCad's
# 0.5 mm default while the DDR netclass clearance is 0.1 mm, which gives
# every DDR via a 1.25 mm antipad through three planes (--zone-clearance)
ZONE_CLEARANCE = None
ZONE_CLR_RE = re.compile(r'(\(connect_pads[^()]*\(clearance )[\d.]+(\))')
# port strip grows along: 0 -> KiCad -y, 180 -> +y, 90 -> -x, 270 -> +x
ROT = {(0, 1): 180, (0, -1): 0, (-1, 0): 90, (1, 0): 270}
_TAIL = r'\s*\(width ([-\d.]+)\)\s*\(layer "([^"]+)"\)(?:.*?\n)*?\t\)'
SEG_RE = re.compile(r'\n\t\(segment\s*\(start ([-\d.]+) ([-\d.]+)\)'
                    r'\s*\(end ([-\d.]+) ([-\d.]+)\)' + _TAIL)
ARC_RE = re.compile(r'\n\t\(arc\s*\(start ([-\d.]+) ([-\d.]+)\)'
                    r'\s*\(mid [-\d.]+ [-\d.]+\)\s*\(end ([-\d.]+) ([-\d.]+)\)'
                    + _TAIL)
VIA_RE = re.compile(r'\n\t\(via\s*\(at [-\d.]+ [-\d.]+\)(?:.*?\n)*?'
                    r'[^\n]*\(net "([^"]+)"\)(?:.*?\n)*?\t\)')
EDGE_RE = re.compile(r'\(gr_(?:line|rect|arc|circle)(.*?)\(layer "Edge.Cuts"\)',
                     re.S)
POINT_RE = re.compile(r'\((?:start|end|mid|center) ([-\d.]+) ([-\d.]+)\)')
POINT_XY_RE = re.compile(r'\(xy ([-\d.]+) ([-\d.]+)\)')
FILL_RE = re.compile(r'(\(filled_polygon\s*\(layer "[^"]+"\)\s*\(pts)'
                     r'(.*?\n\t\t\t\))', re.S)
# the two BGA ball pads si-wrapper keeps for its own ports; ours sit on
# stubs, and on an outer-layer trace the pad disc would merge into the
# port strip
PAD_RE = re.compile(r'\n\t\t\(pad "[^"]*"(?:.*?\n)*?\t\t\)')
FOOTPRINT_RE = re.compile(r'\(footprint "Simulation_Port"(?:.*?\n)*?'
                          r'\t\t\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)'
                          r'(?:.*?\n)*?\t\t\(property "Reference" "SP(\d+)"')


def key(x, y):
  """Rounded point usable as a dict key."""
  return (round(x, 4), round(y, 4))


def set_origin(s):
  """Put the aux origin at the bottom-left of the Edge.Cuts bounding box."""
  xs, ys = [], []
  for m in EDGE_RE.finditer(s):
    for x, y in POINT_RE.findall(m.group(1)):
      xs.append(float(x))
      ys.append(float(y))
  ox, oy = min(xs), max(ys)
  s = re.sub(r'\n\t\t\(aux_axis_origin [^)]*\)', '', s)
  origin = f'\n\t(setup\n\t\t(aux_axis_origin {ox} {oy})\n'
  s = s.replace('\n\t(setup\n', origin, 1)
  return s


def add_stitching(s, pitch=1.0, inset=0.35):
  """Add GND vias along the slice edge so all planes share one potential.

  Without them the plane layers are floating sheets in the solver and
  the slice rings instead of settling.
  """
  xs, ys = [], []
  for m in EDGE_RE.finditer(s):
    for x, y in POINT_RE.findall(m.group(1)):
      xs.append(float(x))
      ys.append(float(y))
  x0, x1 = min(xs) + inset, max(xs) - inset
  y0, y1 = min(ys) + inset, max(ys) - inset
  pts = set()
  n = max(1, int((x1 - x0) / pitch))
  for i in range(n + 1):
    pts.add((round(x0 + (x1 - x0) * i / n, 3), round(y0, 3)))
    pts.add((round(x0 + (x1 - x0) * i / n, 3), round(y1, 3)))
  n = max(1, int((y1 - y0) / pitch))
  for i in range(n + 1):
    pts.add((round(x0, 3), round(y0 + (y1 - y0) * i / n, 3)))
    pts.add((round(x1, 3), round(y0 + (y1 - y0) * i / n, 3)))
  vias = "".join(gnd_via(x, y) for x, y in sorted(pts))
  i = s.rfind("\n)")
  return s[:i] + vias + s[i:]


def gnd_via(x, y):
  """Text of a 0.4/0.2 through via on GND."""
  return (f'\n\t(via\n\t\t(at {x} {y})\n\t\t(size 0.4)\n\t\t(drill 0.2)'
          f'\n\t\t(layers "F.Cu" "B.Cu")\n\t\t(net "GND")\n\t)')


def return_vias(s, nets, dist):
  """GND vias `dist` from each via of `nets`, each on its freest side.

  The side is the one of +x, -x, +y, -y whose point is farthest from any
  other track end or via in the slice.
  """
  occupied = []
  for g in tracks(s):
    occupied += [g["a"], g["b"]]
  own = []
  for m in VIA_RE.finditer(s):
    x, y = (float(v) for v in re.search(r'\(at ([-\d.]+) ([-\d.]+)\)',
                                        m.group(0)).groups())
    occupied.append((x, y))
    if m.group(1) in nets:
      own.append((x, y))
  out = ""
  for x, y in own:
    others = [q for q in occupied if q != (x, y)]
    sides = [(round(x + dx * dist, 3), round(y + dy * dist, 3))
             for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))]
    best = max(sides, key=lambda p: min(
        [math.hypot(p[0] - q[0], p[1] - q[1]) for q in others] or [1e9]))
    out += gnd_via(*best)
  return out


def clip_fills(s):
  """Clamp zone fill polygons to just inside the Edge.Cuts rectangle.

  The fill copied from the board overshoots the slice outline. gerbv
  sizes each layer's raster to its own content, so plane layers come out
  bigger than signal layers and gerber2ems, which assumes one frame for
  all layers, places the planes shifted by that overshoot. Vias then
  pierce the plane copper instead of their antipads.
  """
  xs, ys = [], []
  for m in EDGE_RE.finditer(s):
    for x, y in POINT_RE.findall(m.group(1)):
      xs.append(float(x))
      ys.append(float(y))
  # stay clear of the outline stroke: gerber2ems finds the frame by
  # measuring that line, so it must look the same on every layer
  x0, x1 = min(xs) + FILL_INSET, max(xs) - FILL_INSET
  y0, y1 = min(ys) + FILL_INSET, max(ys) - FILL_INSET

  def clamp(m):
    x = min(max(float(m.group(1)), x0), x1)
    y = min(max(float(m.group(2)), y0), y1)
    return f"(xy {x:g} {y:g})"

  def fix_poly(m):
    return m.group(1) + POINT_XY_RE.sub(clamp, m.group(2))

  return FILL_RE.sub(fix_poly, s)


def tracks(s):
  """All segments and arcs as dicts with endpoints, width, layer and net."""
  out = []
  for m in list(SEG_RE.finditer(s)) + list(ARC_RE.finditer(s)):
    net = re.search(r'\(net "([^"]+)"\)', m.group(0)).group(1)
    out.append(dict(span=m.span(),
                    a=key(float(m.group(1)), float(m.group(2))),
                    b=key(float(m.group(3)), float(m.group(4))),
                    w=float(m.group(5)), layer=m.group(6), net=net))
  return out


def largest_component(mine):
  """Split a net's tracks into connected pieces; return (kept, orphans).

  Leftover stubs from editing are not connected to the main run and would
  otherwise be mistaken for a free end. The piece with the most length wins.
  """
  comps = []
  todo = list(mine)
  while todo:
    comp = [todo.pop()]
    pts = {comp[0]["a"], comp[0]["b"]}
    grew = True
    while grew:
      grew = False
      for g in list(todo):
        if g["a"] in pts or g["b"] in pts:
          todo.remove(g)
          comp.append(g)
          pts |= {g["a"], g["b"]}
          grew = True
    comps.append(comp)
  comps.sort(key=lambda c: -sum(math.hypot(g["b"][0] - g["a"][0],
                                           g["b"][1] - g["a"][1]) for g in c))
  return comps[0], [g for c in comps[1:] for g in c]


def end_tails(mine, max_len=0.6):
  """Pieces at a free end on a layer different from what follows them.

  Returns the segments to drop: runs shorter than `max_len` that sit at
  a free end and whose layer differs from the next piece of the net.
  """
  deg = {}
  for g in mine:
    for p in (g["a"], g["b"]):
      deg[p] = deg.get(p, 0) + 1
  tails = []
  for end in [p for p, d in deg.items() if d == 1]:
    run, cur, prev, length = [], end, None, 0.0
    while True:
      g = next((g for g in mine if cur in (g["a"], g["b"]) and g is not prev),
               None)
      if g is None:
        break
      if run and g["layer"] != run[0]["layer"]:
        if length < max_len:
          tails += run
        break
      run.append(g)
      length += math.hypot(g["b"][0] - g["a"][0], g["b"][1] - g["a"][1])
      prev, cur = g, g["b"] if g["a"] == cur else g["a"]
      if deg.get(cur, 0) != 2:
        break
  return tails


def prune_branches(mine):
  """Remove dangling side branches so the net is one path with two ends.

  Editing leaves short spurs hanging off a junction. While a net has more
  than two free ends, the shortest branch from a free end back to a
  junction is removed. Returns (kept, removed).
  """
  removed = []
  mine = list(mine)
  while True:
    deg = {}
    for g in mine:
      for p in (g["a"], g["b"]):
        deg[p] = deg.get(p, 0) + 1
    ends = [p for p, d in deg.items() if d == 1]
    if len(ends) <= 2:
      return mine, removed
    branches = []
    for end in ends:
      path, cur, prev, length = [], end, None, 0.0
      while True:
        g = next((g for g in mine if cur in (g["a"], g["b"]) and g is not prev),
                 None)
        if g is None:
          break
        path.append(g)
        length += math.hypot(g["b"][0] - g["a"][0], g["b"][1] - g["a"][1])
        cur = g["b"] if g["a"] == cur else g["a"]
        prev = g
        if deg.get(cur, 0) != 2:
          break
      branches.append((length, path))
    length, path = min(branches, key=lambda b: b[0])
    if length > MAX_SPUR:
      end = next(p for p in (path[0]["a"], path[0]["b"]) if deg[p] == 1)
      raise ValueError(f"{path[0]['net']}: a {length:.1f} mm dead-end "
                       f"branch ends at {end} on {path[0]['layer']}; "
                       "leftover route on the board?")
    removed += path
    mine = [g for g in mine if g not in path]


def branch_from(mine, end):
  """(length, pieces) walking from a free end to the first junction."""
  deg = {}
  for g in mine:
    for p in (g["a"], g["b"]):
      deg[p] = deg.get(p, 0) + 1
  path, cur, prev, length = [], end, None, 0.0
  while True:
    g = next((g for g in mine if cur in (g["a"], g["b"]) and g is not prev),
             None)
    if g is None:
      return length, path
    path.append(g)
    length += math.hypot(g["b"][0] - g["a"][0], g["b"][1] - g["a"][1])
    cur = g["b"] if g["a"] == cur else g["a"]
    prev = g
    if deg.get(cur, 0) != 2:
      return length, path


def lies_on(piece, mine):
  """True if `piece` runs entirely on top of another piece of the net.

  A duplicate drawn over an existing segment (seen: 1.4 mm of DDR_DQ9_A
  on its own meander) is redundant copper, not a second route.
  """
  for g in mine:
    if g is piece or g["layer"] != piece["layer"]:
      continue
    (ax, ay), (bx, by) = g["a"], g["b"]
    vx, vy = bx - ax, by - ay
    L2 = vx * vx + vy * vy
    if not L2:
      continue
    ok = True
    for px, py in (piece["a"], piece["b"]):
      t = ((px - ax) * vx + (py - ay) * vy) / L2
      off = math.hypot(px - ax - t * vx, py - ay - t * vy)
      if not (-0.01 <= t <= 1.01 and off < g["w"] / 2):
        ok = False
        break
    if ok:
      return True
  return False


def t_junctions(mine):
  """(point, piece) for every free end lying on the middle of a piece."""
  deg = {}
  for g in mine:
    for p in (g["a"], g["b"]):
      deg[p] = deg.get(p, 0) + 1
  out = []
  for p, d in deg.items():
    if d != 1:
      continue
    own = next(g for g in mine if p in (g["a"], g["b"]))
    for g in mine:
      if p in (g["a"], g["b"]):
        continue
      # the piece before the last one: a tiny zig-zag at a ball (seen on
      # DDR_DQ9_A, 0.04 mm past the via and 0.08 mm back) puts the end on
      # it without being a second route
      if {g["a"], g["b"]} & {own["a"], own["b"]}:
        continue
      (ax, ay), (bx, by) = g["a"], g["b"]
      vx, vy = bx - ax, by - ay
      L2 = vx * vx + vy * vy
      t = ((p[0] - ax) * vx + (p[1] - ay) * vy) / L2 if L2 else 0
      if 0.05 < t < 0.95 and math.hypot(p[0] - ax - t * vx,
                                        p[1] - ay - t * vy) < g["w"] / 2:
        out.append((p, g))
  return out


def passed_vias(mine, vias):
  """Vias of the net that the trace runs straight through on one layer.

  A via-in-pad with trace on both sides on the same layer means the route
  continues past the ball (seen: 5.5 mm of leftover trace on DDR_DQ9_B),
  and the free end found beyond it is not the ball.
  """
  out = []
  for (vx, vy) in vias:
    here = [g for g in mine if key(vx, vy) in (g["a"], g["b"])]
    if len(here) == 2 and here[0]["layer"] == here[1]["layer"]:
      out.append((vx, vy))
  return out


def track_points(pieces, step=0.1):
  """(x, y, half width, net) sampled along pieces for obstacle checks."""
  out = []
  for g in pieces:
    (ax, ay), (bx, by) = g["a"], g["b"]
    n = max(1, int(math.hypot(bx - ax, by - ay) / step))
    out += [(ax + (bx - ax) * i / n, ay + (by - ay) * i / n, g["w"] / 2,
             g["net"]) for i in range(n + 1)]
  return out


def direction_from(end, pieces, reach=0.4):
  """Vector from `end` to the point `reach` mm along the walked pieces.

  The first piece alone can point anywhere (a 0.08 mm zig-zag at a ball
  on DDR_DQ9_A pointed backwards and put the stub beside the trace), so
  the direction is taken over the first few tenths of a millimetre.
  """
  cur, walked = end, 0.0
  for g in pieces:
    far = g["b"] if g["a"] == cur else g["a"]
    length = math.hypot(far[0] - cur[0], far[1] - cur[1])
    if walked + length >= reach and length:
      t = (reach - walked) / length
      pt = (cur[0] + (far[0] - cur[0]) * t, cur[1] + (far[1] - cur[1]) * t)
      return (pt[0] - end[0], pt[1] - end[1])
    walked += length
    cur = far
  return (cur[0] - end[0], cur[1] - end[1])


def stub_for(end, into, w, obstacles):
  """(tip, direction out, length, GND vias to remove) of the stub past an end.

  The stub may point along any axis without a component along the way
  the trace leaves; of those the one farthest from everything in
  `obstacles` (x, y, radius) wins, and it is shortened (never below
  MIN_STUB) until the
  port strip keeps STUB_CLEAR from the nearest copper. GND via-in-pads
  sit on the 0.65 mm ball grid in every direction once they are placed,
  and the other leg of a pair runs 0.4 mm past the ball.
  """
  # never alongside the trace: a 45° escape has two forward axes, and a
  # stub on either runs 0.1 mm from the diagonal and drives it in parallel
  # (seen: DDR_DQ13_B, a flat -6 dB)
  axes = [(1, 0), (-1, 0), (0, 1), (0, -1)]
  dirs = [d for d in axes if d[0] * into[0] + d[1] * into[1] <= 1e-9]
  near = [(x, y, r, net) for x, y, r, net in obstacles
          if 1e-6 < math.hypot(x - end[0], y - end[1]) < 2.0]

  def too_close(d, length, items):
    tip = (end[0] + d[0] * length, end[1] + d[1] * length)
    hits, best = [], 9.0
    for x, y, r, net in items:
      # distance from the obstacle's centre to the stub's centre line
      t = max(0.0, min(length, (x - end[0]) * d[0] + (y - end[1]) * d[1]))
      px, py = end[0] + d[0] * t, end[1] + d[1] * t
      c = math.hypot(x - px, y - py) - r - w / 2
      best = min(best, c)
      if c < STUB_CLEAR:
        hits.append((x, y))
    return best, hits, tip
  # signal copper decides the direction and the length; GND stitching vias
  # in the way are removed from the slice instead (the planes under the
  # port are solid anyway), because a stub squeezed below MIN_STUB gives
  # an MSL port too short for its probes
  signal = [o for o in near if o[3] != "GND"]
  choice = None
  for d in dirs:
    length = STUB_LEN
    while True:
      c, _, tip = too_close(d, length, signal)
      if c >= STUB_CLEAR or length <= MIN_STUB:
        break
      length = max(MIN_STUB, length - 0.05)
    if choice is None or c > choice[0]:
      choice = (c, key(*tip), d, length)
  _, tip, d, length = choice
  _, gnd_hits, _ = too_close(d, length, [o for o in near if o[3] == "GND"])
  return tip, d, length, gnd_hits


def find_ports(mine, drop, obstacles=()):
  """Walk a net inward from both free ends to an axis-aligned segment.

  Non-axis-aligned pieces passed on the way (diagonals, arcs), straight
  pieces shorter than MIN_PORT_SEG and any orphan stubs are added to
  `drop`. `obstacles` are (x, y, radius, net) of copper a stub must
  avoid: vias of any net and sampled points of other nets' tracks.
  Returns a port dict per end.
  """
  mine, orphans = largest_component(mine)
  for g in orphans:
    drop.add(id(g))
  # two parallel routes on the board (seen on DDR_DQ9_B) give a cycle, or
  # a free end sitting on the middle of another piece; no port placement
  # can make sense of either
  verts = {p for g in mine for p in (g["a"], g["b"])}
  if len(mine) >= len(verts):
    raise ValueError(f"{mine[0]['net']}: the routing has a loop "
                     f"({len(mine)} pieces, {len(verts)} points); "
                     "two parallel traces on the board?")
  # a free end lying on another piece: a short one is an editing leftover
  # (seen: 0.5 mm of DDR_DQ10_A duplicated on its own diagonal) and goes,
  # a long one is a second route
  for p, g in t_junctions(mine):
    length, path = branch_from(mine, p)
    if length > MAX_SPUR and not all(lies_on(x, mine) for x in path):
      raise ValueError(f"{g['net']}: a trace ends at {p} on top of another "
                       f"piece of the same net ({g['a']}-{g['b']} on "
                       f"{g['layer']}); duplicate route on the board?")
    for piece in path:
      drop.add(id(piece))
  mine = [g for g in mine if id(g) not in drop]
  # a short tail on another layer at an end (a stray via-and-tail) is cut
  # off; a layer change in the middle of the run is kept, with its via
  for tail in end_tails(mine):
    drop.add(id(tail))
  mine = [g for g in mine if id(g) not in drop]
  deg = {}
  for g in mine:
    for p in (g["a"], g["b"]):
      deg[p] = deg.get(p, 0) + 1
  mine, pruned = prune_branches(mine)
  for g in pruned:
    drop.add(id(g))
  deg = {}
  for g in mine:
    for p in (g["a"], g["b"]):
      deg[p] = deg.get(p, 0) + 1
  ends = sorted([p for p, d in deg.items() if d == 1],
                key=lambda p: (p[1], p[0]))
  if len(ends) != 2:
    raise ValueError(f"{mine[0]['net']}: expected 2 free ends, got {ends}")
  ports = []
  for end in ends:
    cur, prev, cut = end, None, []
    while True:
      g = next(g for g in mine if cur in (g["a"], g["b"])
               and id(g) not in drop and g is not prev)
      far = g["b"] if g["a"] == cur else g["a"]
      dx, dy = far[0] - cur[0], far[1] - cur[1]
      straight = abs(dx) < 1e-6 or abs(dy) < 1e-6
      seg_len = math.hypot(dx, dy)
      if straight and seg_len >= MIN_PORT_SEG:
        d = (int(math.copysign(1, dx)) if abs(dx) > 1e-6 else 0,
             int(math.copysign(1, dy)) if abs(dy) > 1e-6 else 0)
        # the port strip must lie on a uniform straight run: never longer
        # than 80 % of the segment under it
        ports.append(dict(net=g["net"], pos=cur, rot=ROT[d],
                          layer=g["layer"], w=g["w"],
                          length=min(PORT_LEN, 0.8 * seg_len)))
        for c in cut:
          drop.add(id(c))
        break
      cut.append(g)
      if sum(math.hypot(c["b"][0] - c["a"][0], c["b"][1] - c["a"][1])
             for c in cut) > MAX_CUT:
        # the end runs diagonally for too long to cut: keep it all and add
        # a straight stub beyond the free end for the port to sit on
        first = cut[0]
        into = direction_from(end, cut)
        tip, d_out, length, gnd = stub_for(end, into, first["w"], obstacles)
        ports.append(dict(net=first["net"], pos=tip,
                          rot=ROT[(-d_out[0], -d_out[1])],
                          layer=first["layer"], w=first["w"],
                          length=min(PORT_LEN, 0.8 * length),
                          stub=(end, tip), remove_gnd=gnd))
        break
      prev, cur = g, far
  return ports


def refill_zones(pcb):
  """Refill the slice's zones with kicad-cli (needs the .kicad_pro beside it).

  pcbnew's ZONE_FILLER from Python has no DRC engine and fills straight
  through other nets' vias; kicad-cli's DRC refill uses the project's
  netclass clearances and gives each via its antipad.
  """
  report = pathlib.Path(pcb).with_suffix(".drc.rpt")
  subprocess.run(["kicad-cli", "pcb", "drc", "--refill-zones",
                  "--save-board", "--severity-error", "-o", str(report),
                  str(pcb)], check=True, capture_output=True)
  report.unlink(missing_ok=True)


def drop_surplus_ports(s, fps, order, ports):
  """Keep one Simulation_Port footprint per trace-end port, the one
  nearest to the end, numbered SP1..SPn in `order`, and cut the port
  list to n. Returns (board text, ports).

  si-wrapper puts a port on every pad of a designated net, so a net
  with more pads than trace ends (a second connector pad hanging on a
  stub, seen: UP_D on Backstop with J1's A and B row pads) brings
  surplus footprints and ports.
  """
  anchors = [p["stub"][0] if "stub" in p else p["pos"] for p in order]
  keep = {}
  for i, (ax, ay) in enumerate(anchors):
    free = [m for m in fps if m.start() not in keep]
    best = min(free, key=lambda m: math.hypot(float(m.group(1)) - ax,
                                              float(m.group(2)) - ay))
    keep[best.start()] = i + 1
  for m in sorted(fps, key=lambda m: m.start(), reverse=True):
    if m.start() in keep:
      a, b = m.span(4)
      s = s[:a] + str(keep[m.start()]) + s[b:]
    else:
      start = s.rfind("\n\t(footprint", 0, m.end())
      end = s.index("\n\t)", m.start()) + len("\n\t)")
      s = s[:start] + s[end:]
  ports = [dict(p, number=i + 1) for i, p in enumerate(ports[:len(order)])]
  return s, ports


def fix_slice(pcb, nets, refill=True):
  """Fix one slice in place; returns the ordered port list.

  Order is per net in `nets`, start (smaller KiCad y) then stop, matching
  the SP1..SPn footprints that si-wrapper wrote into simulation.json.
  With `refill` the zones are refilled by kicad-cli afterwards and clipped
  to the outline again (the refill extends them to the zone outline).
  """
  s = clip_fills(set_origin(open(pcb).read()))
  if nets:
    s = PAD_RE.sub("", s)
  if not nets:
    open(pcb, "w").write(s)
    return []
  segs = tracks(s)
  drop = set()
  ports = []
  all_vias = [(tuple(float(v) for v in re.search(
                  r'\(at ([-\d.]+) ([-\d.]+)\)', m.group(0)).groups()),
               m.group(1)) for m in VIA_RE.finditer(s)]
  for net in nets:
    mine = [g for g in segs if g["net"] == net]
    vias = [v for v, n in all_vias if n == net]
    for v in passed_vias(mine, vias):
      raise ValueError(f"{net}: the trace runs through its via at {v} "
                       "without changing layer; a leftover route past "
                       "the ball?")
    obstacles = [(x, y, 0.125, n) for (x, y), n in all_vias]
    obstacles += track_points([g for g in segs if g["net"] != net])
    ports += find_ports(mine, drop, obstacles)
  # drop the simulated nets' vias at the ports (the via-in-pad ends and
  # cut-off tails); keep their interior vias (layer changes mid-run) and
  # every other via, those are the GND stitching that ties the planes

  def near_port(m):
    x, y = (float(v) for v in re.search(r'\(at ([-\d.]+) ([-\d.]+)\)',
                                        m.group(0)).groups())
    # the via-in-pad the port end sits on, nothing further along the
    # trace. The trace end can meet the via off-centre (seen: 0.17 mm),
    # so the radius covers the via ring plus half a trace width
    anchors = [p["stub"][0] if "stub" in p else p["pos"] for p in ports]
    return any(math.hypot(x - ax, y - ay) < VIA_AT_PORT
               for ax, ay in anchors)

  def gnd_in_the_way(m):
    x, y = (float(v) for v in re.search(r'\(at ([-\d.]+) ([-\d.]+)\)',
                                        m.group(0)).groups())
    return any(math.hypot(x - gx, y - gy) < 0.01
               for p in ports for gx, gy in p.get("remove_gnd", []))
  spans = sorted([g["span"] for g in segs if id(g) in drop]
                 + [m.span() for m in VIA_RE.finditer(s)
                    if (m.group(1) in nets and near_port(m))
                    or (m.group(1) == "GND" and gnd_in_the_way(m))],
                 reverse=True)
  for a, b in spans:
    s = s[:a] + s[b:]
  if STITCH:
    s = add_stitching(s)
  if RETURN_VIA:
    extra = return_vias(s, nets, RETURN_VIA)
    i = s.rfind("\n)")
    s = s[:i] + extra + s[i:]
  order = []
  for net in nets:
    order += sorted([p for p in ports if p["net"] == net],
                    key=lambda p: p["pos"][1])
  sim_path = pathlib.Path(pcb).parent / "simulation.json"
  cfg = json.load(open(sim_path))
  fps = list(FOOTPRINT_RE.finditer(s))
  if len(fps) > len(order) and len(fps) == len(cfg["ports"]):
    s, cfg["ports"] = drop_surplus_ports(s, fps, order, cfg["ports"])
    fps = list(FOOTPRINT_RE.finditer(s))
  if not len(fps) == len(order) == len(cfg["ports"]):
    raise ValueError(f"port count mismatch: {len(fps)} footprints, "
                     f"{len(order)} trace ends, {len(cfg['ports'])} ports")
  # SPn in the file is port n-1 in simulation.json; file order is arbitrary
  for fp in sorted(fps, key=lambda m: m.start(), reverse=True):
    p = order[int(fp.group(4)) - 1]
    at_end = s.index(")", fp.span(1)[0]) + 1
    at = f'{p["pos"][0]} {p["pos"][1]} {p["rot"]})'
    s = s[:fp.span(1)[0]] + at + s[at_end:]
  stubs = "".join(
      f'\n\t(segment\n\t\t(start {p["stub"][0][0]} {p["stub"][0][1]})'
      f'\n\t\t(end {p["stub"][1][0]} {p["stub"][1][1]})'
      f'\n\t\t(width {p["w"]})\n\t\t(layer "{p["layer"]}")'
      f'\n\t\t(net "{p["net"]}")\n\t)'
      for p in order if "stub" in p)
  if stubs:
    i = s.rfind("\n)")
    s = s[:i] + stubs + s[i:]
  open(pcb, "w").write(s)
  if refill:
    if ZONE_CLEARANCE is not None:
      s = ZONE_CLR_RE.sub(rf"\g<1>{ZONE_CLEARANCE:g}\2", s)
      open(pcb, "w").write(s)
    refill_zones(pcb)
    refilled = clip_fills(open(pcb).read())
    open(pcb, "w").write(refilled)
    # KiCad's connectivity pass on load merges nets whose copper touches:
    # a leg that vanished here is shorted to its partner on the board
    for net in nets:
      if f'(net "{net}")' not in refilled:
        raise ValueError(f"{net}: no copper left after the refill; its "
                         "copper touches another net on the board "
                         "(seen: DDR_DQS1_B legs 0.02 mm apart in the "
                         "meander)")
  for pc, p in zip(cfg["ports"], order):
    layer, plane = layer_plane(p["layer"])
    pc.update(layer=layer, plane=plane,
              width=int(round(p["w"] * 1000)),
              length=int(round(p["length"] * 1000)))
  json.dump(cfg, open(sim_path, "w"), indent=2)
  return order


def main():
  """CLI entry."""
  for p in fix_slice(sys.argv[1], sys.argv[2:]):
    print(p)


if __name__ == "__main__":
  main()
