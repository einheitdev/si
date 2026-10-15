#!/usr/bin/env python3
"""Unit tests for the pure parts of run_si.py (no KiCad or openEMS needed)."""

import unittest

import run_si


class GammaToZTest(unittest.TestCase):
  """Reflection coefficient to impedance."""

  def test_matched(self):
    self.assertAlmostEqual(abs(run_si.gamma_to_z(0, 40)), 40)

  def test_open_and_short(self):
    self.assertGreater(abs(run_si.gamma_to_z(0.999, 40)), 1e4)
    self.assertLess(abs(run_si.gamma_to_z(-0.999, 40)), 0.1)

  def test_known_value(self):
    # Z = 50 at 40 ohm reference: gamma = (50-40)/(50+40)
    self.assertAlmostEqual(abs(run_si.gamma_to_z(10 / 90, 40)), 50)


class NetclassTest(unittest.TestCase):
  """Pattern based netclass lookup."""

  PATTERNS = [("DDR_DQS*", "DDR_DQS"), ("DDR_DQ*", "DDR_DATA"),
              ("EMMC_*", "EMMC")]

  def test_first_match_wins(self):
    self.assertEqual(run_si.netclass_of("DDR_DQS0_t_A", self.PATTERNS),
                     "DDR_DQS")
    self.assertEqual(run_si.netclass_of("DDR_DQ3_B", self.PATTERNS),
                     "DDR_DATA")

  def test_default(self):
    self.assertEqual(run_si.netclass_of("GND", self.PATTERNS), "Default")


class DetectLegsTest(unittest.TestCase):
  """Far end of an excited port is the port with the strongest transfer."""

  def test_crossed_mapping(self):
    n = 3
    s = {}
    for i in range(4):
      for k in (0, 2):
        s[(i, k)] = [0.01] * n
    s[(3, 0)] = [0.99] * n
    s[(1, 2)] = [0.98] * n
    self.assertEqual(run_si.detect_legs(s, (0, 2)), {0: 3, 2: 1})


class MixedModeTest(unittest.TestCase):
  """Differential S-parameters from single-ended ones."""

  def test_ideal_pair(self):
    # two identical uncoupled 40 ohm legs measured at 40 ohm: no reflection,
    # full transmission -> SDD11 = 0, SDD21 = -1 * ... = unity magnitude
    n = 2
    s = {(i, k): [0j] * n for i in range(4) for k in (0, 2)}
    s[(3, 0)] = [1 + 0j] * n
    s[(1, 2)] = [1 + 0j] * n
    sdd11, sdd21 = run_si.mixed_mode(s, 0, 2, 3, 1)
    self.assertAlmostEqual(abs(sdd11[0]), 0)
    self.assertAlmostEqual(abs(sdd21[0]), 1)

  def test_reflection_maps_to_zdiff(self):
    # both legs reflect like 50 ohm at 40 ohm reference -> Zdiff = 100 at 80
    n = 1
    g = 10 / 90
    s = {(i, k): [0j] * n for i in range(4) for k in (0, 2)}
    s[(0, 0)] = [g + 0j]
    s[(2, 2)] = [g + 0j]
    sdd11, _ = run_si.mixed_mode(s, 0, 2, 3, 1)
    self.assertAlmostEqual(abs(run_si.gamma_to_z(sdd11[0], 80)), 100)


class PairNameTest(unittest.TestCase):
  """Pair name from the two leg names."""

  def test_suffix_stripped(self):
    self.assertEqual(run_si.pair_name(["DDR_DQS0_A_P", "DDR_DQS0_A_N"]),
                     "DDR_DQS0_A")
    self.assertEqual(run_si.pair_name(["USB_DP", "USB_DN"]), "USB_D")


class SymmetricMixedModeTest(unittest.TestCase):
  """One-excitation estimate equals the full one for a symmetric pair."""

  def test_matches_full(self):
    n = 1
    g = 10 / 90
    c = 0.05 + 0j
    s = {(i, k): [0j] * n for i in range(4) for k in (0, 2)}
    s[(0, 0)] = [g + 0j]
    s[(2, 2)] = [g + 0j]
    s[(2, 0)] = [c]
    s[(0, 2)] = [c]
    s[(3, 0)] = [0.9 + 0j]
    s[(1, 2)] = [0.9 + 0j]
    full11, full21 = run_si.mixed_mode(s, 0, 2, 3, 1)
    sym11, sym21 = run_si.mixed_mode_symmetric(s, 0, 3, 2, 1)
    self.assertAlmostEqual(full11[0], sym11[0])
    self.assertAlmostEqual(full21[0], sym21[0])


class HealTest(unittest.TestCase):
  """Slivers are removed and the neighbour re-pointed to close the gap."""

  def test_sliver_removed(self):
    seg = ('\n\t(segment\n\t\t(start {a})\n\t\t(end {b})\n\t\t(width 0.1849)'
           '\n\t\t(layer "In2.Cu")\n\t\t(net "N")\n\t)')
    text = (seg.format(a="0 0", b="0 5") + seg.format(a="0 5", b="0 5.02")
            + seg.format(a="0 5.02", b="3 5.02"))
    out, n = run_si.heal_micro_segments(text)
    self.assertEqual(n, 1)
    self.assertNotIn("(end 0 5.02)", out)
    self.assertIn("(start 0 5)\n\t\t(end 3 5.02)", out)


class RestoreCopperTest(unittest.TestCase):
  """The slice gets the board's tracks for the designated net, verbatim."""

  def test_restore(self):
    import pathlib
    import tempfile
    blk = ('\n\t(segment\n\t\t(start {a})\n\t\t(end {b})\n\t\t(width 0.1849)'
           '\n\t\t(layer "In2.Cu")\n\t\t(net "{n}")\n\t)')
    board = "(kicad_pcb" + blk.format(a="0 0", b="0 5", n="X") + blk.format(
        a="0 5", b="3 5", n="X") + blk.format(a="9 9", b="9 1", n="Y") + "\n)"
    sl = "(kicad_pcb" + blk.format(a="0 0", b="0 5", n="X") + blk.format(
        a="9 9", b="9 1", n="Y") + "\n)"
    with tempfile.TemporaryDirectory() as d:
      bp, sp = pathlib.Path(d, "b.kicad_pcb"), pathlib.Path(d, "s.kicad_pcb")
      bp.write_text(board)
      sp.write_text(sl)
      n = run_si.restore_net_copper(sp, bp, ["X"])
      out = sp.read_text()
    self.assertEqual(n, 2)
    self.assertEqual(out.count('(net "X")'), 2)
    self.assertEqual(out.count('(net "Y")'), 1)
    self.assertIn("(end 3 5)", out)


class VerdictTest(unittest.TestCase):
  """Tolerance check."""

  def test_pass_marginal_fail(self):
    self.assertEqual(run_si.verdict([36.5, 43.9], 40), "PASS")
    self.assertEqual(run_si.verdict([35.9, 40], 40), "MARGINAL")
    self.assertEqual(run_si.verdict([33.9, 40], 40), "FAIL")


class AtTest(unittest.TestCase):
  """Nearest frequency sample."""

  def test_nearest(self):
    self.assertEqual(run_si.at([100, 200, 300], ["a", "b", "c"], 240), "b")


class ReportSpecTest(unittest.TestCase):
  """Band and table rows per netclass from board.json `report`."""

  class Board:
    report = {"USB2": {"f_start": 5e7, "f_stop": 3e9,
                       "rows_mhz": [240, 500, 1000, 2000],
                       "nyquist_mhz": 240}}

  def test_default(self):
    spec = run_si.report_spec(self.Board(), "DDR_DATA")
    self.assertEqual(spec["rows_mhz"], list(run_si.REPORT_FREQS_MHZ))
    self.assertEqual(spec["f_stop"], 5e9)
    self.assertEqual(spec["nyquist_mhz"], 2133)

  def test_class_override_and_cli(self):
    spec = run_si.report_spec(self.Board(), "USB2")
    self.assertEqual(spec["f_start"], 5e7)
    self.assertEqual(spec["rows_mhz"], [240, 500, 1000, 2000])
    self.assertEqual(run_si.report_spec(self.Board(), "USB2", 4e9)["f_stop"],
                     4e9)


if __name__ == "__main__":
  unittest.main()
