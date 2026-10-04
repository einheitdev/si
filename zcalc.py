#!/usr/bin/env python3
"""2D electrostatic impedance of a trace in a board's stackup.

An independent check on the calculator numbers in the netclasses and on
the openEMS flow's level: the cross-section (reference planes, dielectric
layers with their Dk, one or two rectangular traces) is solved with a
finite-difference Laplace solve, once with the dielectrics and once in
air, and Z = 1 / (c * sqrt(C * C_air)). A pair is solved in its odd mode
(+V, -V) and even mode (+V, +V); Z_diff = 2 * Z_odd.

The solver is static, so it gives the low-frequency (lossless, no
dispersion) impedance, which is what the calculators give too.

CLI: zcalc.py --board DIR [--layer In2.Cu --width 0.185 [--gap 0.217]]
     zcalc.py --board DIR --sweep    the width that lands each netclass
With no --width it solves every netclass of the board on each inner layer
listed in `planes`' neighbours (the signal layers) and on the outer ones.
"""

import argparse
import json
import math
import pathlib

import numpy as np
import scipy.sparse
import scipy.sparse.linalg

import board as board_mod

C_LIGHT = 299792458.0
EPS0 = 8.8541878128e-12


def solve(layers, trace_z, width, thickness, gap=None, mode="odd", dx=0.005,
          half_width=1.5, air=True):
  """Capacitance per metre of trace 1 for the given cross-section.

  `layers` is a list of (thickness_mm, eps) from the bottom plane (z = 0,
  PEC) upward; the top of the last layer is a PEC plane too, so put an air
  layer last for a microstrip. The trace bottom sits at `trace_z` (mm).
  With `gap` a second trace sits `gap` to the right, driven +V (even) or
  -V (odd). With `air` False the dielectrics are replaced by vacuum.
  Returns C in F/m.
  """
  height = sum(t for t, _ in layers)
  nx = int(round(2 * half_width / dx)) + 1
  nz = int(round(height / dx)) + 1
  xs = -half_width + dx * np.arange(nx)
  zs = dx * np.arange(nz)
  # cell permittivity (between nodes), by z
  eps_cell = np.ones(nz - 1)
  if air:
    z = 0.0
    for t, e in layers:
      lo, hi = int(round(z / dx)), int(round((z + t) / dx))
      eps_cell[lo:hi] = e
      z += t
  # node permittivity for the x-faces (average of the cells above/below)
  eps_node = np.ones(nz)
  eps_node[1:-1] = 0.5 * (eps_cell[:-1] + eps_cell[1:])
  eps_node[0], eps_node[-1] = eps_cell[0], eps_cell[-1]
  fixed = np.zeros((nx, nz), dtype=bool)
  value = np.zeros((nx, nz))
  fixed[:, 0] = fixed[:, -1] = True
  fixed[0, :] = fixed[-1, :] = True

  def trace(x_centre, volt):
    ix = (np.abs(xs - x_centre) <= width / 2 + 1e-9)
    iz = (zs >= trace_z - 1e-9) & (zs <= trace_z + thickness + 1e-9)
    region = np.outer(ix, iz)
    fixed[region] = True
    value[region] = volt
    return region
  if gap is None:
    t1 = trace(0.0, 1.0)
  else:
    pitch = width + gap
    t1 = trace(-pitch / 2, 1.0)
    trace(pitch / 2, -1.0 if mode == "odd" else 1.0)
  # assemble: for each free node, sum over 4 faces eps_face * (phi_n - phi)
  idx = -np.ones((nx, nz), dtype=int)
  free = ~fixed
  idx[free] = np.arange(free.sum())
  n = free.sum()
  rows, cols, vals = [], [], []
  rhs = np.zeros(n)
  ii, kk = np.nonzero(free)
  for di, dk in ((1, 0), (-1, 0), (0, 1), (0, -1)):
    ni, nk = ii + di, kk + dk
    if dk == 0:
      ef = eps_node[kk]
    else:
      ef = eps_cell[np.minimum(kk, nk)]
    me = idx[ii, kk]
    rows.append(me)
    cols.append(me)
    vals.append(-ef)
    nb_free = free[ni, nk]
    rows.append(me[nb_free])
    cols.append(idx[ni[nb_free], nk[nb_free]])
    vals.append(ef[nb_free])
    nb_fixed = ~nb_free
    np.add.at(rhs, me[nb_fixed], -ef[nb_fixed] * value[ni[nb_fixed],
                                                       nk[nb_fixed]])
  a = scipy.sparse.csr_matrix((np.concatenate(vals),
                               (np.concatenate(rows), np.concatenate(cols))),
                              shape=(n, n))
  phi = value.copy()
  phi[free] = scipy.sparse.linalg.spsolve(a, rhs)
  # charge on trace 1: flux through the faces leaving it
  q = 0.0
  ti, tk = np.nonzero(t1)
  for di, dk in ((1, 0), (-1, 0), (0, 1), (0, -1)):
    ni, nk = ti + di, tk + dk
    outside = ~t1[ni, nk]
    ef = eps_node[tk] if dk == 0 else eps_cell[np.minimum(tk, nk)]
    q += np.sum(ef[outside] * (phi[ti[outside], tk[outside]]
                               - phi[ni[outside], nk[outside]]))
  return EPS0 * q


def impedance(layers, trace_z, width, thickness, gap=None, dx=0.005):
  """(Z_single or Z_diff, details) for the geometry, in ohm."""
  if gap is None:
    c = solve(layers, trace_z, width, thickness, dx=dx)
    c0 = solve(layers, trace_z, width, thickness, dx=dx, air=False)
    return 1 / (C_LIGHT * math.sqrt(c * c0)), {}
  out = {}
  for mode in ("odd", "even"):
    c = solve(layers, trace_z, width, thickness, gap, mode, dx=dx)
    c0 = solve(layers, trace_z, width, thickness, gap, mode, dx=dx,
               air=False)
    out[mode] = 1 / (C_LIGHT * math.sqrt(c * c0))
  return 2 * out["odd"], out


def signal_layers(board):
  """Copper layers that are not reference planes."""
  return [lay for lay in board.copper_layers() if lay not in board.planes]


def main(argv=None):
  """CLI."""
  ap = argparse.ArgumentParser(description=__doc__)
  ap.add_argument("--board", type=pathlib.Path, default=pathlib.Path("."))
  ap.add_argument("--dx", type=float, default=0.005, help="grid (mm)")
  ap.add_argument("--layer", help="one copper layer, e.g. In2.Cu")
  ap.add_argument("--width", type=float, help="trace width (mm)")
  ap.add_argument("--gap", type=float, help="pair gap (mm) for a pair")
  ap.add_argument("--sweep", action="store_true",
                  help="find the width that lands each netclass target")
  args = ap.parse_args(argv)
  board = board_mod.Board(args.board)
  layers = [args.layer] if args.layer else signal_layers(board)
  if args.width:
    for lay in layers:
      sec, z, t = board.cross_section(lay)
      zval, modes = impedance(sec, z, args.width, t, args.gap, dx=args.dx)
      extra = (f"  odd {modes['odd']:.1f} even {modes['even']:.1f}"
               if modes else "")
      print(f"{lay:8s} w {args.width:g} gap {args.gap or '-'}: "
            f"{zval:6.1f} Ω{extra}")
    return
  pro = json.load(open(board.pro))["net_settings"]["classes"]
  classes = {c["name"]: c for c in pro}
  print(f"{'layer':8s} {'netclass':10s} {'w':>6s} {'gap':>6s} {'target':>7s} "
        f"{'solve':>7s}")
  for lay in layers:
    sec, z, t = board.cross_section(lay)
    for name, (z_se, z_diff) in board.netclass_z.items():
      c = classes.get(name)
      if not c:
        continue
      w = c.get("track_width", 0)
      zval, _ = impedance(sec, z, w, t, dx=args.dx)
      print(f"{lay:8s} {name:10s} {w:6.3f} {'-':>6s} {z_se:7g} {zval:7.1f}")
      if z_diff:
        w, g = c.get("diff_pair_width", w), c.get("diff_pair_gap", 0)
        zval, _ = impedance(sec, z, w, t, g, dx=args.dx)
        print(f"{lay:8s} {name:10s} {w:6.3f} {g:6.3f} {z_diff:7g} "
              f"{zval:7.1f}")
      if args.sweep:
        for target, gap in ((z_se, None), (z_diff, c.get("diff_pair_gap"))):
          if not target:
            continue
          lo, hi = 0.06, 0.40
          for _ in range(14):
            mid = (lo + hi) / 2
            zv, _ = impedance(sec, z, mid, t, gap, dx=args.dx)
            lo, hi = (mid, hi) if zv > target else (lo, mid)
          print(f"{lay:8s} {name:10s} {target:g} Ω"
                f"{' diff' if gap else ''} -> width {mid:.3f} mm")


if __name__ == "__main__":
  main()
