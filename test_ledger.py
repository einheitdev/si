"""Tests for the results ledger."""

import pathlib
import tempfile
import unittest

import ledger


class FakeBoard:
  """Just the results directory a ledger needs."""

  def __init__(self, d):
    self.results = pathlib.Path(d) / "results"


class LedgerTest(unittest.TestCase):
  """Records append, the latest per net set wins, names are stable."""

  def test_append_and_latest(self):
    with tempfile.TemporaryDirectory() as d:
      b = FakeBoard(d)
      ledger.append(b, {"nets": ["A"], "verdict": "FAIL"})
      ledger.append(b, {"nets": ["B_P", "B_N"], "verdict": "PASS"})
      ledger.append(b, {"nets": ["A"], "verdict": "PASS"})
      records = ledger.load(b)
      self.assertEqual(len(records), 3)
      self.assertTrue(all("time" in r for r in records))
      latest = ledger.latest(records)
      self.assertEqual([r["nets"] for r in latest], [["A"], ["B_P", "B_N"]])
      self.assertEqual(latest[0]["verdict"], "PASS")
      ledger.rewrite(b, latest)
      self.assertEqual(len(ledger.load(b)), 2)

  def test_case_name(self):
    self.assertEqual(ledger.case_name(["DDR_DQ3_B"]), "DDR_DQ3_B")
    self.assertEqual(ledger.case_name(["DDR_DQS0_A_P", "DDR_DQS0_A_N"]),
                     "DDR_DQS0_A_Diff")

  def test_record(self):
    r = ledger.make_record(0, "abc", {"mesh": 50},
                           {"nets": ["A"], "images": ["x"], "raw": "raw/A"})
    self.assertEqual(r["git"], "abc")
    self.assertNotIn("images", r)
    self.assertEqual(r["raw"], "raw/A")


if __name__ == "__main__":
  unittest.main()
