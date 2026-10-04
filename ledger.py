#!/usr/bin/env python3
"""One file of results per board instead of a folder per run.

`results/ledger.jsonl` holds one JSON record per simulated case, appended
as the runs happen; `results/raw/<case>/` keeps the latest slice board,
port configuration and S-parameter CSVs of each case, overwritten by the
next run of the same case. A report is built from the latest record of
every net set, so a rerun of one net replaces it without touching the
rest, and older records stay in the file for the history.
"""

import datetime
import json
import pathlib


def key(nets):
  """Hashable identity of a case: its sorted net names."""
  return tuple(sorted(nets))


def path(board):
  """The ledger file of a board."""
  return board.results / "ledger.jsonl"


def load(board):
  """All records, oldest first."""
  p = path(board)
  if not p.exists():
    return []
  return [json.loads(line) for line in p.read_text().splitlines() if line]


def append(board, record):
  """Add one record with a timestamp; returns it."""
  record = dict(record)
  record.setdefault("time", datetime.datetime.now().isoformat(
      timespec="seconds"))
  p = path(board)
  p.parent.mkdir(parents=True, exist_ok=True)
  with open(p, "a") as f:
    f.write(json.dumps(record, default=str) + "\n")
  return record


def rewrite(board, records):
  """Replace the whole ledger (used by recalibration)."""
  p = path(board)
  p.write_text("".join(json.dumps(r, default=str) + "\n" for r in records))


def latest(records):
  """The newest record per net set, in first-seen order."""
  out = {}
  for r in records:
    out[key(r["nets"])] = r
  return list(out.values())


def raw_dir(board, case_name):
  """Where a case's latest slice and CSVs live."""
  return board.results / "raw" / case_name


def report_path(board):
  """The latest report PDF of a board."""
  return board.results / "report.pdf"


def make_record(board_pcb_mtime, git, config, case):
  """A ledger record from a run_si case entry."""
  return {"board_saved": datetime.datetime.fromtimestamp(
              board_pcb_mtime).isoformat(timespec="minutes"),
          "git": git, "config": config,
          **{k: v for k, v in case.items() if k != "images"},
          "raw": case.get("raw", "")}


def case_name(nets):
  """Directory name of a case under raw/ (si-wrapper's slice name)."""
  if len(nets) == 2:
    stem = pathlib.PurePath(nets[0]).name
    for suffix in ("_P", "_N"):
      if stem.endswith(suffix):
        return stem[:-2] + "_Diff"
  return nets[0]
