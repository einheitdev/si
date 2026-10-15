#!/usr/bin/env python3
"""Slice, simulate and record signal integrity of nets on a KiCad board.

Chain, per case (one single-ended net, or one differential pair):

  1. scratch copy of the KiCad project with netclasses renamed to the
     `NNOhm-SE-*` / `NNOhm-DIFF-*` convention si-wrapper requires
  2. `si-wrapper slice` cuts the net and its neighbours into a small board
  3. `fix_ports.fix_slice` puts the simulation ports on the copper and the
     aux origin at the slice corner
  4. `kicad-cli` exports Gerbers, Excellon drills and the port positions
  5. `simulation.json` gets the targets (impedance, frequency, mesh)
  6. `gerber2ems -a` meshes and runs openEMS; pairs get their leg mapping
     detected from the result and are postprocessed again
  7. S-parameters are reduced to impedance, loss and crosstalk at fixed
     frequencies, judged, and appended to the board's results ledger
     (`results/ledger.jsonl`, one record per case; the slice and CSVs go
     to `results/raw/<case>/`). `report/build_report.py` makes the PDF.

The board is a directory holding `board.json` and `stackup.json` (see
board.py), by default the current directory.

Example:
  ./run_si.py --board ../m/m_rk3576/hw/si --se DDR_DQ0_A \\
      --pair DDR_DQS0_A_P,DDR_DQS0_A_N
"""

import argparse
import cmath
import csv
import fnmatch
import json
import math
import os
import pathlib
import pwd
import re
import shutil
import subprocess

import board as board_mod
import fix_ports
import ledger

# HOME differs per Claude session on this machine; use the account home
VENV = (pathlib.Path(pwd.getpwuid(os.getuid()).pw_dir) / "opt" / "openEMS"
        / "venv" / "bin")
PCBNEW_PATH = "/usr/local/lib/python3/dist-packages"
REPORT_FREQS_MHZ = (500, 1000, 2133, 3000, 4266)
# simulated band, table rows and Nyquist marker of a case; board.json
# `report` overrides them per netclass (USB wants other rows than
# LPDDR4X), an explicit --f-stop wins over both
REPORT_DEFAULT = {"f_start": 2e8, "f_stop": 5e9,
                  "rows_mhz": list(REPORT_FREQS_MHZ), "nyquist_mhz": 2133}
# impedance calibration of this flow, taken from board.json (`z_cal`):
# measured against a 2D field solve of the same cross-sections (zcalc.py),
# openEMS with these ports and this mesh reads inner single-ended traces
# 7 % low and pairs 16 % low on the 1080B stackup. --z-cal overrides it;
# 1,1 is the raw reading; recal.py rescales recorded results.
Z_CAL = {"se": 1.0, "diff": 1.0}
TOLERANCE = 0.10
# crosstalk limits used for the verdict, dB, at the LPDDR4X Nyquist
XTALK_LIMIT_DB = -20.0
# max sum |S(i,k)|^2 accepted as a physical excitation; the FDTD port
# extraction is good to a few percent, clean cases sit at 1.00-1.10 alone
# and up to ~1.2 with terminated neighbours in the slice
ENERGY_LIMIT = 1.3


def gamma_to_z(gamma, z_ref):
  """Impedance seen at a port from its complex reflection coefficient."""
  return z_ref * (1 + gamma) / (1 - gamma)


def db(x):
  """Magnitude in dB, floored so a zero never raises."""
  return 20 * math.log10(max(abs(x), 1e-12))


def netclass_of(net, patterns):
  """Netclass name for a net from the project's (pattern, class) list."""
  for pattern, cls in patterns:
    if fnmatch.fnmatchcase(net, pattern):
      return cls
  return "Default"


def load_patterns(pro_path):
  """Read the netclass assignment patterns from a .kicad_pro file."""
  pro = json.load(open(pro_path))
  out = []
  for a in pro["net_settings"].get("netclass_patterns") or []:
    out.append((a["pattern"], a["netclass"]))
  return out


def report_spec(board, netclass, f_stop=None):
  """Band, table rows and Nyquist of a class: board.json `report` over
  REPORT_DEFAULT; an explicit stop frequency wins over both."""
  spec = dict(REPORT_DEFAULT)
  spec.update(getattr(board, "report", {}).get(netclass) or {})
  if f_stop:
    spec["f_stop"] = f_stop
  return spec


def make_work_copy(board, work):
  """Copy the project into `work` with si-wrapper style netclass names."""
  work.mkdir(parents=True, exist_ok=True)
  for src in (board.pcb, board.pro, board.dru, board.hw_dir / "fp-lib-table"):
    if src.exists():
      shutil.copy(src, work / src.name)
  pcb = work / board.pcb.name
  healed, n = heal_micro_segments(unlock(pcb.read_text()))
  pcb.write_text(healed)
  if n:
    print(f"work copy: merged away {n} track slivers under 0.06 mm")
  for lib in board.hw_dir.glob("*.pretty"):
    dst = work / lib.name
    if not dst.exists():
      shutil.copytree(lib, dst)
  pro_path = work / board.pro.name
  pro = json.load(open(pro_path))
  rename = {k: board.si_wrapper_name(k) for k in board.netclass_z}
  for c in pro["net_settings"]["classes"]:
    c["name"] = rename.get(c["name"], c["name"])
  for key in ("netclass_assignments", "netclass_patterns"):
    for a in pro["net_settings"].get(key) or []:
      a["netclass"] = rename.get(a["netclass"], a["netclass"])
  json.dump(pro, open(pro_path, "w"), indent=2)
  dru_path = work / board.dru.name
  if not dru_path.exists():
    return
  dru = open(dru_path).read()
  for k, v in rename.items():
    dru = dru.replace(f"A.NetClass == '{k}'", f"A.NetClass == '{v}'")
  open(dru_path, "w").write(dru)


LOCK_RE = re.compile(r"\n\t\t\(locked yes\)")


def unlock(text):
  """Drop the `(locked yes)` of every item in a board copy.

  KiCad 10 writes it between `(width` and `(layer` of a segment (after
  `(layers` of a via), which the track regexes here and in fix_ports do
  not expect (seen: Backstop, whose USB pairs are all locked). The
  scratch copy needs no locks.
  """
  return LOCK_RE.sub("", text)


SEG_RE = re.compile(r'\n\t\(segment\s*\(start ([-\d.]+) ([-\d.]+)\)\s*'
                    r'\(end ([-\d.]+) ([-\d.]+)\)\s*\(width [-\d.]+\)\s*'
                    r'\(layer "[^"]+"\)(?:.*?\n)*?[^\n]*\(net "([^"]+)"\)'
                    r'(?:.*?\n)*?\t\)')


def heal_micro_segments(text, min_len=0.06):
  """Remove sub-`min_len` track pieces and close the gap they leave.

  Tuning and dragging leave 0.01-0.05 mm slivers in a track. si-wrapper's
  track walker stops at them and silently truncates the net, so the scratch
  copy gets them merged away: the sliver is deleted and the segments that
  touched its far end are re-pointed to its near end.
  """
  removed = 0
  while True:
    hit = None
    for m in SEG_RE.finditer(text):
      x1, y1, x2, y2 = (float(v) for v in m.groups()[:4])
      if math.hypot(x2 - x1, y2 - y1) < min_len:
        hit = m
        break
    if hit is None:
      return text, removed
    a = f"{hit.group(1)} {hit.group(2)}"
    b = f"{hit.group(3)} {hit.group(4)}"
    text = text[:hit.start()] + text[hit.end():]
    text = text.replace(f"(start {b})", f"(start {a})")
    text = text.replace(f"(end {b})", f"(end {a})")
    removed += 1


TRACK_BLOCK_RE = re.compile(
    r'\n\t\((?:segment|arc|via)\b(?:.*?\n)*?[^\n]*\(net "([^"]+)"\)'
    r'(?:.*?\n)*?\t\)')


def restore_net_copper(slice_pcb, board_pcb, nets):
  """Replace the designated nets' tracks and vias in the slice with the
  board's own.

  si-wrapper drops pieces of a net now and then (observed: a diagonal
  before a via-in-pad, the far end of a layer-changing run) and the slice
  then ends in mid-air. The slice outline and planes already cover the
  whole net, so the board's copper is copied in verbatim.
  """
  text = slice_pcb.read_text()
  board = board_pcb.read_text()
  text = TRACK_BLOCK_RE.sub(
      lambda m: "" if m.group(1) in nets else m.group(0), text)
  blocks = [m.group(0) for m in TRACK_BLOCK_RE.finditer(board)
            if m.group(1) in nets]
  i = text.rfind("\n)")
  text = text[:i] + "".join(blocks) + text[i:]
  slice_pcb.write_text(text)
  return len(blocks)


def venv_env():
  """Environment for the openEMS venv tools plus KiCad's pcbnew module."""
  env = dict(os.environ)
  env["PATH"] = f"{VENV}:{env['PATH']}"
  env["PYTHONPATH"] = PCBNEW_PATH
  env["VIRTUAL_ENV"] = str(VENV.parent)
  return env


def run(cmd, cwd, log=None):
  """Run a command, raising on failure; optionally tee output to a file."""
  res = subprocess.run(cmd, cwd=cwd, env=venv_env(), text=True,
                       capture_output=True)
  if log:
    open(log, "a").write(f"$ {' '.join(map(str, cmd))}\n{res.stdout}"
                         f"{res.stderr}\n")
  if res.returncode != 0:
    raise RuntimeError(f"{cmd[0]} failed ({res.returncode}):\n"
                       f"{res.stdout[-2000:]}\n{res.stderr[-2000:]}")
  return res


def fs_name(nets):
  """si-wrapper's directory name of a case (its Settings.get_filesystem_name
  rule): the sheet path and spaces of a hierarchical net name become
  underscores, a pair's _P / + / _N / - suffix becomes _Diff."""
  nn = nets[0].removeprefix("/")
  for a, b in (("/", "_"), (" ", "_"), ("(", ""), (")", ""), ("{", ""),
               ("}", ""), ("~", "neg")):
    nn = nn.replace(a, b)
  if len(nets) == 2:
    nn = re.sub(r"((_P)|(_?\+)|(_N)|(_?\-))$", "_Diff", nn)
  return nn


def slice_case(work, nets, neighbour_offset, common_points, margin=3.0):
  """Cut `nets` (1 net or 1 pair) out of the work copy; returns slice dir."""
  cfg_dir = work / "net_configs"
  cfg_dir.mkdir(exist_ok=True)
  cfg = {
      "designated_nets": nets,
      "board_offset": {"top": margin, "bottom": margin, "left": margin,
                       "right": margin},
      "included_pads": [],
      "excluded_pads": [],
      "hidden_pads": {"designated_net": True, "other_nets": True},
      "neighbouring_nets": {"offset": neighbour_offset,
                            "common_points": common_points,
                            "in_use": True, "netlist": []},
  }
  cfg_path = cfg_dir / f"{fs_name(nets[:1])}.json"
  json.dump(cfg, open(cfg_path, "w"), indent=2)
  # si-wrapper names the slice after the first net, or <pair>_Diff when it
  # recognises a _P/_N pair
  candidates = [work / "slices" / fs_name(nets), work / "slices" / nets[0]]
  if len(nets) == 2:
    candidates.append(work / "slices" / f"{pair_name(nets)}_Diff")
  # a hierarchical name ("/sheet/NET") would make these absolute paths
  candidates = [c for c in candidates if not c.is_absolute()
                or str(c).startswith(str(work))]
  for c in candidates:
    if c.exists():
      shutil.rmtree(c)
  run(["si-wrapper", "slice", "-f", str(cfg_path)], cwd=work,
      log=work / "si-wrapper.log")
  for c in candidates:
    if c.exists():
      return c
  raise RuntimeError(f"si-wrapper produced no slice for {nets}")


def export_fab(board, slice_dir, name):
  """Export Gerbers, drills and port positions with the slice-corner origin."""
  fab = slice_dir / "fab"
  if fab.exists():
    shutil.rmtree(fab)
  pcb = f"{name}.kicad_pcb"
  run(["kicad-cli", "pcb", "export", "gerbers", "--no-protel-ext",
       "--use-drill-file-origin", "-o", "fab/", pcb], cwd=slice_dir)
  run(["kicad-cli", "pcb", "export", "drill", "--format", "excellon",
       "--excellon-separate-th", "--drill-origin", "plot", "-o", "fab/",
       pcb], cwd=slice_dir)
  run(["kicad-cli", "pcb", "export", "pos", "--format", "csv", "--units",
       "mm", "--side", "front", "--use-drill-file-origin", "-o",
       f"fab/{name}-top-pos.csv", pcb], cwd=slice_dir)
  shutil.copy(board.stackup_path, fab / "stackup.json")


def configure(slice_dir, nets, ports, z_se, z_diff, f_stop, mesh_um,
              f_start=2e8, max_frequency=4.266e9):
  """Write targets, interfaces and mesh into the slice's simulation.json."""
  path = slice_dir / "simulation.json"
  cfg = json.load(open(path))
  cfg["frequency"] = {"start": f_start, "stop": f_stop}
  cfg["grid"] = {"optimal": mesh_um, "diagonal": mesh_um,
                 "perpendicular": 4 * mesh_um, "max": 10 * mesh_um}
  # solid vias (patched gerber2ems): a hollow 50 um barrel wall is lost
  # between mesh lines and the layer change reads as an open circuit
  cfg["via"] = {"plating_thickness": 50, "filling_epsilon": 0}
  for port, p in zip(cfg["ports"], ports):
    port["impedance"] = z_se
    port["layer"], port["plane"] = fix_ports.layer_plane(p["layer"])
    port["width"] = int(round(p["w"] * 1000))
    port["length"] = int(round(p["length"] * 1000))
  interface = {"max_frequency": max_frequency,
               "se_impedance_nominal": z_se,
               "se_impedance_tolerance": TOLERANCE}
  if z_diff:
    interface.update(diff_impedance_nominal=z_diff,
                     diff_impedance_tolerance=TOLERANCE)
    # excite both near ends; the leg mapping is fixed after the solve
    for i, port in enumerate(cfg["ports"]):
      port["excite"] = i in (0, 2)
    cfg["differential_pairs"] = [{"start_p": 0, "stop_p": 1, "start_n": 2,
                                  "stop_n": 3, "name": pair_name(nets),
                                  "nets": nets, "interface": interface}]
    cfg.pop("traces", None)
  else:
    cfg["traces"] = [{"start": 0, "stop": 1, "name": nets[0],
                      "nets": nets, "interface": interface}]
    cfg.pop("differential_pairs", None)
  json.dump(cfg, open(path, "w"), indent=2)


def read_sparams(slice_dir):
  """Read gerber2ems Port_<k>_data.csv files.

  Returns (freqs_mhz, S) where S[(i, k)] is the complex S(i <- k) array for
  every measured port i and excited port k.
  """
  s_params = {}
  freqs = None
  for path in sorted((slice_dir / "ems" / "results").glob("Port_*_data.csv")):
    k = int(path.stem.split("_")[1])
    rows = list(csv.reader(open(path)))
    hdr, data = rows[0], [[float(x) for x in r] for r in rows[1:]]
    freqs = [r[0] for r in data]
    for col, name in enumerate(hdr):
      if name.startswith("|S"):
        i = int(name[2:].split("-")[0])
        arg_col = hdr.index(f"Arg(S{i}-{k}) [rad]")
        s_params[(i, k)] = [cmath.rect(r[col], r[arg_col]) for r in data]
  return freqs, s_params


def detect_legs(s_params, excited):
  """Find each excited port's far end: the port with the highest |S|."""
  far = {}
  for k in excited:
    cands = {i: s for (i, kk), s in s_params.items() if kk == k and i != k}
    far[k] = max(cands, key=lambda i: sum(abs(x) for x in cands[i]))
  return far


def at(freqs, values, f_mhz):
  """Value at the sample closest to f_mhz."""
  idx = min(range(len(freqs)), key=lambda i: abs(freqs[i] - f_mhz))
  return values[idx]


def mixed_mode(s_params, p_near, n_near, p_far, n_far):
  """SDD11 and SDD21 from single-ended S with the given leg ports."""
  n = len(s_params[(p_near, p_near)])
  sdd11 = [(s_params[(p_near, p_near)][i] - s_params[(p_near, n_near)][i]
            - s_params[(n_near, p_near)][i] + s_params[(n_near, n_near)][i])
           / 2 for i in range(n)]
  sdd21 = [(s_params[(p_far, p_near)][i] - s_params[(p_far, n_near)][i]
            - s_params[(n_far, p_near)][i] + s_params[(n_far, n_near)][i])
           / 2 for i in range(n)]
  return sdd11, sdd21


def se_metrics(freqs, s_params, k, far, z_ref, rows_mhz=REPORT_FREQS_MHZ):
  """Per-frequency table for one excited single-ended port."""
  rows = []
  others = sorted({i for (i, kk) in s_params if kk == k and i not in (k, far)})
  for f in rows_mhz:
    s11 = at(freqs, s_params[(k, k)], f)
    row = {"f_mhz": f, "z": abs(gamma_to_z(s11, z_ref)) * Z_CAL["se"],
           "rl_db": db(s11),
           "il_db": db(at(freqs, s_params[(far, k)], f))}
    for i in others:
      row[f"xt{i}_db"] = db(at(freqs, s_params[(i, k)], f))
    rows.append(row)
  return rows, others


def mixed_mode_symmetric(s_params, k_near, k_far, o_near, o_far):
  """SDD11/SDD21 from one physical excitation, assuming identical legs.

  With the other leg's own reflection taken equal to this one's and
  reciprocity for the cross terms: SDD11 = S(k,k) - S(o,k),
  SDD21 = S(kf,k) - S(of,k).
  """
  n = len(s_params[(k_near, k_near)])
  sdd11 = [s_params[(k_near, k_near)][i] - s_params[(o_near, k_near)][i]
           for i in range(n)]
  sdd21 = [s_params[(k_far, k_near)][i] - s_params[(o_far, k_near)][i]
           for i in range(n)]
  return sdd11, sdd21


def pair_metrics(freqs, s_params, p_near, n_near, p_far, n_far, z_diff,
                 use_only=None, rows_mhz=REPORT_FREQS_MHZ):
  """Per-frequency differential table for a pair.

  `use_only` names the one excitation (p_near or n_near) to trust when the
  other failed the energy check; the pair is then taken as symmetric.
  """
  if use_only is None:
    sdd11, sdd21 = mixed_mode(s_params, p_near, n_near, p_far, n_far)
  elif use_only == p_near:
    sdd11, sdd21 = mixed_mode_symmetric(s_params, p_near, p_far, n_near,
                                        n_far)
  else:
    sdd11, sdd21 = mixed_mode_symmetric(s_params, n_near, n_far, p_near,
                                        p_far)
  rows = []
  for f in rows_mhz:
    g = at(freqs, sdd11, f)
    zdiff = abs(gamma_to_z(g, z_diff)) * Z_CAL["diff"]
    rows.append({"f_mhz": f, "zdiff": zdiff,
                 "sdd11_db": db(g), "sdd21_db": db(at(freqs, sdd21, f)),
                 "leg_xt_near_db": db(at(freqs, s_params[(n_near, p_near)], f)),
                 "leg_xt_far_db": db(at(freqs, s_params[(n_far, p_near)], f))})
  return rows


def pair_name(nets):
  """Common name of a pair: the shared prefix without the _P/_N suffix."""
  a, b = nets
  i = 0
  while i < min(len(a), len(b)) and a[i] == b[i]:
    i += 1
  return a[:i].rstrip("_") or a


def energy_check(s_params, excited):
  """Max over frequency of sum |S(i,k)|^2 per excited port k.

  A passive slice cannot exceed 1; values well above mean a port's own
  impedance extraction failed (strip on a bend, overlapping copper) and
  every number derived from that excitation is meaningless.
  """
  worst = {}
  ports = sorted({i for i, _ in s_params})
  for k in excited:
    n = len(s_params[(k, k)])
    worst[k] = max(sum(abs(s_params[(i, k)][j]) ** 2 for i in ports)
                   for j in range(n))
  return worst


def verdict(values, nominal):
  """PASS within TOLERANCE, MARGINAL within 1.5x of it (the mesh uncertainty
  at 50 um is a few percent), FAIL beyond."""
  for label, k in (("PASS", 1.0), ("MARGINAL", 1.5)):
    lo, hi = nominal * (1 - k * TOLERANCE), nominal * (1 + k * TOLERANCE)
    if all(lo <= v <= hi for v in values):
      return label
  return "FAIL"


def solve(slice_dir, is_pair):
  """Run gerber2ems; for pairs fix the leg mapping and postprocess again."""
  log = slice_dir / "run.log"
  run(["gerber2ems", "-a"], cwd=slice_dir, log=log)
  if is_pair:
    _, s_params = read_sparams(slice_dir)
    far = detect_legs(s_params, (0, 2))
    path = slice_dir / "simulation.json"
    cfg = json.load(open(path))
    cfg["differential_pairs"][0].update(start_p=0, stop_p=far[0], start_n=2,
                                        stop_n=far[2])
    json.dump(cfg, open(path, "w"), indent=2)
    run(["gerber2ems", "-p"], cwd=slice_dir, log=log)
    return far
  return None


def git_rev(cwd):
  """Short git revision of the board's repo, or 'unknown'."""
  try:
    return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                   cwd=cwd, text=True).strip()
  except (OSError, subprocess.CalledProcessError):
    return "unknown"


def run_config(args):
  """The settings a record carries so a report can state them."""
  return {"f_stop": args.f_stop or REPORT_DEFAULT["f_stop"],
          "mesh": args.mesh,
          "neighbour_offset": args.neighbour_offset,
          "common_points": args.common_points, "margin": args.margin,
          "stitch": not args.no_stitch, "return_via": args.return_via,
          "zone_clearance": args.zone_clearance, "tolerance": TOLERANCE,
          "xtalk_limit_db": XTALK_LIMIT_DB, "energy_limit": ENERGY_LIMIT,
          "z_cal": dict(Z_CAL)}


def keep_raw(board, slice_dir, prefix):
  """Copy the slice board, port configuration and S-parameter CSVs to
  results/raw/<case>/, replacing the previous run of the case."""
  raw = ledger.raw_dir(board, prefix)
  if raw.exists():
    shutil.rmtree(raw)
  raw.mkdir(parents=True)
  keep = (list((slice_dir / "ems" / "results").glob("Port_*_data.csv"))
          + list(slice_dir.glob("*.kicad_pcb"))
          + [slice_dir / "simulation.json"])
  for src in keep:
    shutil.copy(src, raw / src.name)
  return raw


def geometry_text(ports, nets, spec=None):
  """Short description of where the slice's ports ended up."""
  layers = sorted({p["layer"] for p in ports})
  band = (f"; {spec['f_start'] / 1e9:g}–{spec['f_stop'] / 1e9:g} GHz"
          if spec else "")
  return (f"{len(nets)} net(s), ports on {', '.join(layers)}, trace width "
          f"{ports[0]['w']:.3f} mm{band}")


def run_case(board, nets, args, work, patterns):
  """Full chain for one case; returns the case entry."""
  cls = netclass_of(nets[0], patterns)
  if cls not in board.netclass_z:
    raise SystemExit(f"{nets[0]}: netclass {cls} has no impedance target")
  z_se, z_diff = board.netclass_z[cls]
  spec = report_spec(board, cls, args.f_stop)
  is_pair = len(nets) == 2
  if is_pair and not z_diff:
    raise SystemExit(f"{nets}: class {cls} is not a differential class")
  slice_dir = slice_case(work, nets, args.neighbour_offset,
                         args.common_points, args.margin)
  name = slice_dir.name
  restore_net_copper(slice_dir / f"{name}.kicad_pcb",
                     work / board.pcb.name, nets)
  ports = fix_ports.fix_slice(str(slice_dir / f"{name}.kicad_pcb"), nets)
  export_fab(board, slice_dir, name)
  configure(slice_dir, nets, ports, z_se, z_diff, spec["f_stop"], args.mesh,
            f_start=spec["f_start"],
            max_frequency=2e6 * spec["nyquist_mhz"])
  far = solve(slice_dir, is_pair)
  freqs, s_params = read_sparams(slice_dir)
  energy = energy_check(s_params, (0, 2) if is_pair else (0,))
  entry = {"nets": nets, "netclass": cls, "pair": is_pair,
           "energy": energy,
           "neighbours": sorted({n for (i, _) in s_params for n in []}),
           "geometry": geometry_text(ports, nets, spec), "report": spec,
           "raw": str(keep_raw(board, slice_dir, name).relative_to(
               board.results))}
  if is_pair:
    good = [k for k in (0, 2) if energy[k] <= ENERGY_LIMIT]
    use_only = good[0] if len(good) == 1 else None
    rows = pair_metrics(freqs, s_params, 0, 2, far[0], far[2], z_diff,
                        use_only, rows_mhz=spec["rows_mhz"])
    zs = [r["zdiff"] for r in rows]
    sym = ("" if use_only is None else
           f" [one port failed the energy check, pair taken as symmetric "
           f"from port {use_only}]")
    entry.update(title=f"Pair {pair_name(nets)}", rows=rows,
                 target=f"{z_diff} Ω differential",
                 verdict=verdict(zs, z_diff),
                 note=f"Z_diff {min(zs):.1f}–{max(zs):.1f} Ω against "
                      f"{z_diff} ±{z_diff * TOLERANCE:.0f}; leg coupling at "
                      f"{spec['rows_mhz'][-1]} MHz "
                      f"{rows[-1]['leg_xt_near_db']:.0f} dB NEXT "
                      "(about -20 dB at a 0.10 mm gap, -25 to -35 dB at "
                      "the 0.217 mm netclass gap)." + sym)
  else:
    far = detect_legs(s_params, (0,))[0]
    rows, others = se_metrics(freqs, s_params, 0, far, z_se,
                              rows_mhz=spec["rows_mhz"])
    zs = [r["z"] for r in rows]
    xt_worst = max([r[f"xt{i}_db"] for r in rows for i in others] or [-99])
    v = verdict(zs, z_se)
    if xt_worst > XTALK_LIMIT_DB:
      v = "FAIL"
    entry.update(title=f"Trace {nets[0]}", rows=rows, others=others,
                 target=f"{z_se} Ω single-ended", verdict=v,
                 note=f"Z {min(zs):.1f}–{max(zs):.1f} Ω against {z_se} "
                      f"±{z_se * TOLERANCE:.0f}; IL {rows[-1]['il_db']:.1f} dB "
                      f"at {spec['rows_mhz'][-1]} MHz; worst crosstalk "
                      f"{xt_worst:.0f} dB" + (" (no neighbour in slice)"
                                              if not others else "") + ".")
  bad = [k for k, e in energy.items() if e > ENERGY_LIMIT]
  if bad and not (is_pair and len(bad) == 1):
    entry["verdict"] = "INVALID"
    entry["note"] = ("port extraction failed, sum|S|^2 = "
                     + ", ".join(f"{energy[k]:.2f} (port {k})" for k in bad)
                     + "; " + entry["note"])
  return entry


def parse_args(argv):
  """Command line."""
  ap = argparse.ArgumentParser(
      description=__doc__,
      formatter_class=argparse.RawDescriptionHelpFormatter)
  ap.add_argument("--se", action="append", default=[], metavar="NET",
                  help="single-ended net to simulate (repeatable)")
  ap.add_argument("--pair", action="append", default=[],
                  metavar="NET_P,NET_N",
                  help="differential pair, two nets (repeatable)")
  ap.add_argument("--board", type=pathlib.Path, default=pathlib.Path("."),
                  help="directory with board.json and stackup.json "
                       "(default: the current directory)")
  ap.add_argument("--work", type=pathlib.Path, default=None,
                  help="scratch directory (default <board>/work)")
  ap.add_argument("--mesh", type=float, default=50,
                  help="cell size on traces, µm")
  ap.add_argument("--f-stop", type=float, default=None,
                  help="stop frequency, Hz (default: the class's band from "
                       "board.json `report`, else 5 GHz)")
  ap.add_argument("--neighbour-offset", type=float, default=0.3,
                  help="include other nets closer than this, mm")
  ap.add_argument("--common-points", type=int, default=10)
  ap.add_argument("--margin", type=float, default=3.0,
                  help="slice margin around the nets, mm (keeps the ports "
                       "away from the walls and stitching vias)")
  ap.add_argument("--return-via", type=float, default=None, metavar="MM",
                  help="experiment: add a GND via this far from each "
                       "layer-change via of the net")
  ap.add_argument("--zone-clearance", type=float, default=None,
                  metavar="MM", help="override the zones' clearance in the "
                  "slice before refilling (the board's GND zone has 0.5)")
  ap.add_argument("--no-stitch", action="store_true",
                  help="do not add GND stitching vias to the slice (debug)")
  ap.add_argument("--z-cal", default=None, metavar="SE,DIFF",
                  help="scale reported impedances (default: the board's "
                       "z_cal, the flow's offset against the 2D field "
                       "solve; 1,1 for raw)")
  return ap.parse_args(argv)


def main(argv=None):
  """Entry point."""
  args = parse_args(argv)
  board = board_mod.Board(args.board)
  args.work = (args.work or board.work).resolve()
  Z_CAL.update(board.z_cal)
  if args.z_cal:
    Z_CAL["se"], Z_CAL["diff"] = (float(x) for x in args.z_cal.split(","))
  fix_ports.STITCH = not args.no_stitch
  fix_ports.COPPER_LAYERS = board.copper_layers()
  fix_ports.PORT_PLANE = dict(board.port_plane)
  fix_ports.RETURN_VIA = args.return_via
  fix_ports.ZONE_CLEARANCE = args.zone_clearance
  if not args.se and not args.pair:
    raise SystemExit("nothing to do: give --se and/or --pair")
  make_work_copy(board, args.work)
  patterns = load_patterns(board.pro)
  git = git_rev(board.hw_dir)
  config = run_config(args)
  saved = board.pcb.stat().st_mtime
  cases = []
  jobs = [[n] for n in args.se] + [p.split(",") for p in args.pair]
  for nets in jobs:
    label = ",".join(nets)
    try:
      cases.append(run_case(board, nets, args, args.work, patterns))
    except Exception as exc:  # one broken case must not stop a sweep
      print(f"{label}: ERROR — {exc}")
      cases.append({"nets": nets, "netclass": "?", "pair": len(nets) == 2,
                    "title": ("Pair " if len(nets) == 2 else "Trace ") + label,
                    "target": "", "geometry": "", "raw": "", "rows": [],
                    "others": [], "energy": {}, "verdict": "ERROR",
                    "note": str(exc)})
    else:
      print(f"{label}: {cases[-1]['verdict']} — {cases[-1]['note']}")
    ledger.append(board, ledger.make_record(saved, git, config, cases[-1]))
  print(f"recorded {len(cases)} case(s) in {ledger.path(board)}")


if __name__ == "__main__":
  main()
