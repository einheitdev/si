"""Tests for the result recalibration."""

import unittest

import recal


class RecalTest(unittest.TestCase):
  """Rows scale from the stored factor, verdicts and notes follow."""

  def test_scale_and_verdict(self):
    cal = {"z_cal": {"se": 1.0, "diff": 1.0}}
    results = [
        {"pair": False, "target": "40 Ω single-ended", "verdict": "FAIL",
         "config": dict(cal),
         "rows": [{"f_mhz": 500, "z": 35.0}, {"f_mhz": 4266, "z": 36.0}],
         "note": "Z 35.0–36.0 Ω against 40 ±4; IL 0 dB"},
        {"pair": True, "target": "80 Ω differential", "verdict": "FAIL",
         "config": dict(cal),
         "rows": [{"f_mhz": 500, "zdiff": 68.0},
                  {"f_mhz": 4266, "zdiff": 72.0}],
         "note": "Z_diff 68.0–72.0 Ω against 80 ±8; leg coupling"},
        {"pair": False, "target": "", "verdict": "ERROR", "rows": [],
         "note": "broken"}]
    out = recal.recalibrate(results, 1.07, 1.16)
    se, pair, err = out
    self.assertAlmostEqual(se["rows"][0]["z"], 37.45)
    self.assertEqual(se["verdict"], "PASS")
    self.assertTrue(se["note"].startswith("Z 37.5–38.5 Ω against 40"))
    self.assertAlmostEqual(pair["rows"][1]["zdiff"], 83.52)
    self.assertEqual(pair["verdict"], "PASS")
    self.assertTrue(pair["note"].startswith("Z_diff 78.9–83.5 Ω"))
    self.assertEqual(err["verdict"], "ERROR")
    self.assertEqual(se["config"]["z_cal"], {"se": 1.07, "diff": 1.16})

  def test_idempotent_from_stored_factor(self):
    results = [
        {"pair": False, "target": "40 Ω single-ended", "verdict": "PASS",
         "config": {"z_cal": {"se": 1.07, "diff": 1.16}},
         "rows": [{"f_mhz": 500, "z": 37.45}], "note": "Z 37.5–37.5 Ω x"}]
    out = recal.recalibrate(results, 1.07, 1.16)
    self.assertAlmostEqual(out[0]["rows"][0]["z"], 37.45)


if __name__ == "__main__":
  unittest.main()
