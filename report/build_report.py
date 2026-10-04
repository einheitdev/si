#!/usr/bin/env python3
"""Build a board's Typst PDF report from its results ledger.

The latest record of every net set in `results/ledger.jsonl` is plotted
in the print style (siplots, sliceplot) from the slice board and CSVs
under `results/raw/<case>/`, `report.typ` is written from `template.typ`
and compiled with `typst` to `results/report.pdf`.

Usage:
  ./build_report.py --board DIR [--out X.pdf] [--title "..."] \\
      [--subtitle "..."] [--note "..."]... [--only NET ...]
"""

import argparse
import json
import pathlib
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import board as board_mod  # noqa: E402
import drivers  # noqa: E402
import ledger  # noqa: E402
import siplots  # noqa: E402
import sliceplot  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
FONT_DIRS = [pathlib.Path.home() / ".local/share/fonts",
             pathlib.Path("/home/karl/claude-work/.local/share/fonts")]


def typ_str(s):
  """Quote a string for Typst source."""
  return json.dumps(str(s), ensure_ascii=False)


def typ_value(v):
  """Serialise a Python value as a Typst literal."""
  if isinstance(v, str):
    return typ_str(v)
  if isinstance(v, bool):
    return "true" if v else "false"
  if isinstance(v, (int, float)):
    return repr(v)
  if isinstance(v, dict):
    return "(" + ", ".join(f"{k}: {typ_value(x)}" for k, x in v.items()) + ")"
  if isinstance(v, (list, tuple)):
    inner = ", ".join(typ_value(x) for x in v)
    return f"({inner},)" if len(v) == 1 else f"({inner})"
  return typ_str(v)


def fmt(x, nd=1):
  """Number for a table cell."""
  return f"{x:.{nd}f}"


def case_entry(case, results_dir, plot_dir, cfg):
  """Plots and table for one case; returns the dict the template expects."""
  if case["verdict"] == "ERROR" or not case.get("raw"):
    return {"title": case["title"], "verdict": case["verdict"],
            "nets": case["nets"], "netclass": case["netclass"],
            "target": case["target"], "geometry": case["geometry"],
            "note": case["note"], "slice": "", "plots": [], "header": [],
            "rows": []}
  raw = results_dir / case["raw"]
  name = raw.name
  tol = cfg["tolerance"]
  pcb = next(raw.glob("*.kicad_pcb"))
  sim = json.load(open(raw / "simulation.json"))
  port_len = max(p["length"] for p in sim["ports"]) / 1000
  slice_png = plot_dir / f"{name}_slice.png"
  sliceplot.draw(pcb, case["nets"], slice_png, port_len=port_len)
  nets = case["nets"]
  if case["pair"]:
    z_diff = int(case["target"].split()[0])
    pair = sim["differential_pairs"][0]
    legs = (pair["start_p"], pair["start_n"], pair["stop_p"], pair["stop_n"])
    use_only = None
    energy = {int(k): v for k, v in case["energy"].items()}
    good = [k for k in (0, 2) if energy.get(k, 9) <= cfg["energy_limit"]]
    if len(good) == 1:
      use_only = good[0]
    plots = siplots.pair_figures(raw, plot_dir, name, z_diff, tol, legs,
                                 use_only)
    header = ["MHz", "Z_diff Ω", "SDD11 dB", "SDD21 dB", "leg NEXT dB",
              "leg FEXT dB"]
    rows = [[str(r["f_mhz"]), fmt(r["zdiff"]), fmt(r["sdd11_db"]),
             fmt(r["sdd21_db"]), fmt(r["leg_xt_near_db"]),
             fmt(r["leg_xt_far_db"])] for r in case["rows"]]
  else:
    z_se = float(case["target"].split()[0])
    others = case.get("others", [])
    far = 1
    plots = siplots.se_figures(raw, plot_dir, name, z_se, tol, far, others)
    header = (["MHz", "Z Ω", "RL dB", "IL dB"]
              + [f"to port {i} dB" for i in others])
    rows = [[str(r["f_mhz"]), fmt(r["z"]), fmt(r["rl_db"]), fmt(r["il_db"])]
            + [fmt(r[f"xt{i}_db"]) for i in others] for r in case["rows"]]
  return {
      "title": case["title"], "verdict": case["verdict"], "nets": nets,
      "netclass": case["netclass"], "target": case["target"],
      "geometry": case["geometry"], "note": case["note"],
      "slice": str(slice_png.relative_to(plot_dir.parent)),
      "plots": [str((plot_dir / p).relative_to(plot_dir.parent))
                for p in plots],
      "header": header, "rows": rows,
  }


def main(argv=None):
  """CLI."""
  ap = argparse.ArgumentParser(description=__doc__)
  ap.add_argument("--board", type=pathlib.Path, default=pathlib.Path("."))
  ap.add_argument("--out", type=pathlib.Path, default=None,
                  help="PDF path (default <board>/results/report.pdf)")
  ap.add_argument("--title", default=None)
  ap.add_argument("--subtitle", default="")
  ap.add_argument("--note", action="append", default=[],
                  help="summary paragraph (repeatable)")
  ap.add_argument("--only", action="append", default=[], metavar="NET",
                  help="restrict to cases containing this net (repeatable)")
  args = ap.parse_args(argv)
  board = board_mod.Board(args.board)
  out = (args.out or ledger.report_path(board)).resolve()
  build = out.parent / (out.stem + "_build")
  if build.exists():
    shutil.rmtree(build)
  plot_dir = build / "plots"
  plot_dir.mkdir(parents=True)
  siplots.setup_style()
  records = ledger.latest(ledger.load(board))
  if args.only:
    records = [r for r in records if set(r["nets"]) & set(args.only)]
  if not records:
    raise SystemExit("no records to report")
  cases, summary_rows, driver_entries = [], [], []
  for c in records:
    e = case_entry(c, board.results, plot_dir, c["config"])
    cases.append(e)
    key = "zdiff" if c["pair"] else "z"
    zs = [r[key] for r in c["rows"]]
    dr = (drivers.drivers(c, board.results / c["raw"]) if c.get("raw")
          else None)
    driver_entries.append(dr)
    summary_rows.append([
        c["title"].replace("Trace ", "").replace("Pair ", ""),
        c["target"].split()[0] + " Ω" if c["target"] else "—",
        dr["layers"] if dr else ("pair" if c["pair"] else "—"),
        f"{dr['length']:.0f}" if dr else "—",
        f"{dr['level']:.1f}" if dr else (f"{zs[0]:.1f}" if zs else "—"),
        f"{dr['ripple']:.1f}" if dr else (f"{max(zs) - min(zs):.1f}"
                                          if zs else "—"),
        f"{min(zs):.0f}–{max(zs):.0f}" if zs else "—", c["verdict"]])
  cfg = records[-1]["config"]
  saves = sorted({r.get("board_saved", "") for r in records})
  config_rows = [
      ["Board", f"{board.pcb.name}, saves {saves[0]} to {saves[-1]}"
                if len(saves) > 1 else f"{board.pcb.name}, save {saves[0]}"],
      ["Solver", "openEMS FDTD via gerber2ems, slices via si-wrapper; "
                 "planes stitched with a GND via ring"],
      ["Stackup", board.stackup_path.name],
      ["Frequency", f"0.2 to {cfg['f_stop'] / 1e9:g} GHz, 1001 points"],
      ["Mesh", f"{cfg['mesh']:g} µm on traces, {4 * cfg['mesh']:g} µm "
               f"perpendicular, {10 * cfg['mesh']:g} µm max"],
      ["Slice", f"net plus neighbours within {cfg['neighbour_offset']} mm, "
                f"{cfg['margin']} mm margin, other pads hidden"],
      ["Ports", "MSL ports on a straight run at each trace end, trace "
                "width, ≤ 0.3 mm long, referenced to the nearest plane; "
                "ends that enter a BGA ball diagonally get a 0.35 mm stub "
                "for the port"],
      ["Zones", "refilled with kicad-cli after the port fix"
                + (f", zone clearance set to {cfg['zone_clearance']:g} mm"
                   if cfg.get("zone_clearance") is not None
                   else ", clearance as on the board")],
      ["Calibration", (f"impedances ×{cfg['z_cal']['se']:g} single-ended, "
                       f"×{cfg['z_cal']['diff']:g} differential, the flow's "
                       "offset against a 2D field solve of the same "
                       "cross-sections (zcalc.py)")
       if cfg.get("z_cal", {}).get("se", 1) != 1 else
       "none, raw openEMS readings"],
      ["Tolerance", f"±{cfg['tolerance'] * 100:.0f} % on impedance; "
                    f"crosstalk limit {cfg['xtalk_limit_db']:g} dB; "
                    f"excitation valid while Σ|S|² ≤ {cfg['energy_limit']}"],
      ["Verdicts", "PASS within tolerance, MARGINAL within 1.5× of it, "
                   "FAIL beyond, INVALID when the port extraction failed, "
                   "ERROR when the slice could not be built (the note says "
                   "what is wrong on the board)"],
  ]
  title = args.title or f"{board.project} signal-integrity report"
  subtitle = args.subtitle or (
      f"Latest result per net, board saves {saves[0]} to {saves[-1]}"
      if len(saves) > 1 else f"Board save {saves[0]}")
  meta = {"title": title, "subtitle": subtitle,
          "facts": [["Board", board.pcb.name],
                    ["Git", ", ".join(sorted({r["git"] for r in records}))],
                    ["Ledger", str(ledger.path(board))],
                    ["Cases", str(len(cases))]]}
  summary = {"header": ["Case", "Target", "Layers", "mm", "Z 0.5 GHz",
                        "Ripple Ω", "Measured Ω", "Verdict"],
             "rows": summary_rows,
             "notes": args.note + drivers.discussion(driver_entries)}
  src = (f'#import "template.typ": report\n'
         f"#report({typ_value(meta)}, {typ_value(summary)}, "
         f"{typ_value(config_rows)}, {typ_value(cases)})\n")
  shutil.copy(HERE / "template.typ", build / "template.typ")
  (build / "report.typ").write_text(src)
  cmd = ["typst", "compile", "--root", str(build)]
  for fd in FONT_DIRS:
    if fd.exists():
      cmd += ["--font-path", str(fd)]
  cmd += [str(build / "report.typ"), str(out)]
  subprocess.run(cmd, check=True)
  shutil.rmtree(build)
  print(f"wrote {out}")


if __name__ == "__main__":
  main()
