"""Tests for the SI plot helpers (mixed-mode maths and CSV reading)."""

import cmath
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import siplots  # noqa: E402


class MixedModeTest(unittest.TestCase):
  """Mixed-mode terms from a synthetic symmetric 4-port."""

  def test_pure_differential_line(self):
    # ideal matched pair: no reflection, legs pass straight through
    s = {}
    for i in range(4):
      for k in range(4):
        s[(i, k)] = [0j]
    s[(2, 0)] = [1 + 0j]
    s[(3, 1)] = [1 + 0j]
    mm = siplots.mixed_mode(s, (0, 1, 2, 3))
    self.assertAlmostEqual(abs(mm["sdd21"][0]), 1)
    self.assertAlmostEqual(abs(mm["sdd11"][0]), 0)
    self.assertAlmostEqual(abs(mm["scd21"][0]), 0)

  def test_single_excitation_fallback(self):
    s = {(0, 0): [0.1 + 0j], (1, 0): [0.02 + 0j], (2, 0): [0.9 + 0j],
         (3, 0): [0.05 + 0j]}
    mm = siplots.mixed_mode(s, (0, 1, 2, 3), use_only=0)
    self.assertNotIn("scd21", mm)
    self.assertAlmostEqual(mm["sdd21"][0], 0.85)


class ReadAllTest(unittest.TestCase):
  """Delays come back in ps, S as complex."""

  def test_csv(self):
    with tempfile.TemporaryDirectory() as d:
      pathlib.Path(d, "Port_0_data.csv").write_text(
          "Frequency [MHz],|S0-0| [-],|S1-0| [-],Arg(S0-0) [rad],"
          "Arg(S1-0) [rad],Delay 0>0 [s],Delay 0>1 [s]\n"
          "1000,0.1,0.9,0,3.14159,1e-12,5e-11\n")
      freqs, s, delays = siplots.read_all(d)
    self.assertEqual(freqs, [1.0])
    self.assertAlmostEqual(abs(s[(1, 0)][0] - cmath.rect(0.9, 3.14159)), 0)
    self.assertAlmostEqual(delays[(1, 0)][0], 50)


if __name__ == "__main__":
  unittest.main()
