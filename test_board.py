"""Tests for the board configuration."""

import json
import pathlib
import shutil
import tempfile
import unittest

import board

HERE = pathlib.Path(__file__).resolve().parent
STACKUP = HERE / "testdata" / "stackup_1080b.json"
CFG = {"project": "x", "planes": ["In1.Cu", "In3.Cu", "In6.Cu"],
       "netclass_z": {"DDR_DATA": [40, None], "DDR_DQS": [40, 80]},
       "z_cal": {"se": 1.07, "diff": 1.16},
       "lanes": {"data": "DDR_DQ{bit}_{ch}",
                 "strobe": "DDR_DQS{lane}_{ch}_{leg}", "channels": ["A"],
                 "lanes": 2, "limit": 0.635}}


def make_board(d):
  """A board directory with the 1080B stackup and the config above."""
  si = pathlib.Path(d) / "hw" / "si"
  si.mkdir(parents=True)
  json.dump(CFG, open(si / "board.json", "w"))
  shutil.copy(STACKUP, si / "stackup.json")
  return board.Board(si)


class BoardTest(unittest.TestCase):
  """Paths, names and the cross-sections derived from the stackup."""

  def test_paths_and_names(self):
    with tempfile.TemporaryDirectory() as d:
      b = make_board(d)
      self.assertEqual(b.pcb.name, "x.kicad_pcb")
      self.assertEqual(b.si_wrapper_name("DDR_DATA"), "40Ohm-SE-DDR_DATA")
      self.assertEqual(b.si_wrapper_name("DDR_DQS"), "80Ohm-DIFF-DDR_DQS")
      self.assertEqual(b.lane_nets()[1][0], "DDR_DQS1_A_P")
      self.assertEqual(b.lane_nets()[1][2][0], "DDR_DQ8_A")

  def test_layer_z(self):
    with tempfile.TemporaryDirectory() as d:
      z = make_board(d).layer_z()
    self.assertAlmostEqual(z["F.Cu"], 0.0)
    self.assertAlmostEqual(z["In2.Cu"], 0.035 + 0.0764 + 0.0152 + 0.1)

  def test_cross_sections(self):
    with tempfile.TemporaryDirectory() as d:
      b = make_board(d)
      l3, z3, t3 = b.cross_section("In2.Cu")
      l6, z6, _ = b.cross_section("In5.Cu")
      l1, z1, t1 = b.cross_section("F.Cu")
      l8, z8, _ = b.cross_section("B.Cu")
    # the deeper plane (In3) is the bottom of the section
    self.assertAlmostEqual(z3, 0.2868)
    self.assertEqual(l3[0], (0.2868, 4.3))
    self.assertEqual(l3[2], (0.1, 4.4))
    # L6: core to In6 below, prepreg + empty In4 + core up to In3
    self.assertAlmostEqual(z6, 0.1)
    self.assertEqual(len(l6), 5)
    self.assertEqual(l6[3], (0.0152, 4.3))
    # outer layers: prepreg to the plane, then mask and air
    self.assertAlmostEqual(z1, 0.0764)
    self.assertEqual(t1, 0.035)
    self.assertEqual(l1[-1], (1.0, 1.0))
    self.assertAlmostEqual(z8, 0.0764)
    self.assertEqual(l8[-2], board.MASK)


if __name__ == "__main__":
  unittest.main()
