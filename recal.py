#!/usr/bin/env python3
"""Rescale a board's recorded impedances and redo their verdicts.

The openEMS flow reads inner single-ended traces about 7 % low and pairs
about 16 % low against a 2D field solve of the same cross-sections
(`zcalc.py`, see the README). run_si.py applies the board's `z_cal` at
run time; this script brings every record in the ledger to the factors
given (default: the board's), from whatever factor each record carries,
so nothing needs simulating again. The ledger is backed up once as
`ledger.before-recal.jsonl`.

Usage: recal.py --board DIR [--se 1.07] [--diff 1.16]
"""

import argparse
import pathlib
import re
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import board as board_mod  # noqa: E402
import ledger  # noqa: E402
import run_si  # noqa: E402

RANGE_RE = re.compile(r"^(Z(?:_diff)?) [\d.]+–[\d.]+ Ω")


def recalibrate(records, se, diff):
  """Return the records with rows scaled from each one's z_cal to (se, diff)."""
  for c in records:
    old = c.get("config", {}).get("z_cal", {"se": 1.0, "diff": 1.0})
    if not c["rows"]:
      continue
    key, factor, nominal = (("zdiff", diff / old["diff"],
                             float(c["target"].split()[0]))
                            if c["pair"] else
                            ("z", se / old["se"],
                             float(c["target"].split()[0])))
    for r in c["rows"]:
      r[key] *= factor
    zs = [r[key] for r in c["rows"]]
    if c["verdict"] in ("PASS", "MARGINAL", "FAIL"):
      c["verdict"] = run_si.verdict(zs, nominal)
    c["note"] = RANGE_RE.sub(
        lambda m: f"{m.group(1)} {min(zs):.1f}–{max(zs):.1f} Ω", c["note"])
    c.setdefault("config", {})["z_cal"] = {"se": se, "diff": diff}
  return records


def main(argv=None):
  """CLI."""
  ap = argparse.ArgumentParser(description=__doc__)
  ap.add_argument("--board", type=pathlib.Path, default=pathlib.Path("."))
  ap.add_argument("--se", type=float, default=None)
  ap.add_argument("--diff", type=float, default=None)
  args = ap.parse_args(argv)
  board = board_mod.Board(args.board)
  se = args.se if args.se is not None else board.z_cal["se"]
  diff = args.diff if args.diff is not None else board.z_cal["diff"]
  path = ledger.path(board)
  backup = path.with_name("ledger.before-recal.jsonl")
  if not backup.exists():
    shutil.copy(path, backup)
  records = recalibrate(ledger.load(board), se, diff)
  ledger.rewrite(board, records)
  for c in ledger.latest(records):
    print(f"{c['title']}: {c['verdict']} — {c['note'][:60]}")


if __name__ == "__main__":
  main()
