"""Tests for the driver numbers behind each verdict."""

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import drivers  # noqa: E402

SLICE = ('(kicad_pcb\n\t(segment\n\t\t(start 0 0)\n\t\t(end 10 0)'
         '\n\t\t(width 0.185)\n\t\t(layer "In2.Cu")\n\t\t(net "N")\n\t)'
         '\n\t(segment\n\t\t(start 10 0)\n\t\t(end 10 4)'
         '\n\t\t(width 0.185)\n\t\t(layer "In5.Cu")\n\t\t(net "N")\n\t)'
         '\n\t(via\n\t\t(at 10 0)\n\t\t(size 0.25)\n\t\t(drill 0.15)'
         '\n\t\t(layers "F.Cu" "B.Cu")\n\t\t(net "N")\n\t)\n)')


class DriversTest(unittest.TestCase):
  """Layers, vias, length and the impedance numbers come out right."""

  def test_case(self):
    case = {"pair": False, "verdict": "FAIL", "nets": ["N"],
            "rows": [{"f_mhz": 500, "z": 36.0}, {"f_mhz": 2133, "z": 38.0},
                     {"f_mhz": 4266, "z": 33.0}]}
    with tempfile.TemporaryDirectory() as d:
      pathlib.Path(d, "x.kicad_pcb").write_text(SLICE)
      e = drivers.drivers(case, d)
    self.assertEqual(e["layers"], "L3+L6")
    self.assertEqual(e["vias"], 1)
    self.assertAlmostEqual(e["length"], 14.0)
    self.assertEqual(e["level"], 36.0)
    self.assertEqual(e["z_nyquist"], 38.0)
    self.assertAlmostEqual(e["ripple"], 5.0)

  def test_pair_skipped(self):
    self.assertIsNone(drivers.drivers({"pair": True, "verdict": "PASS",
                                       "rows": [1]}, "."))

  def test_discussion_mentions_baseline(self):
    entries = [{"layers": "L3", "vias": 1, "length": 10, "level": 36.5,
                "z_nyquist": 37, "z_min": 36, "ripple": 1.0},
               {"layers": "L3+L6", "vias": 1, "length": 16, "level": 36.8,
                "z_nyquist": 39, "z_min": 33.6, "ripple": 6.4}]
    text = " ".join(drivers.discussion(entries))
    self.assertIn("36.5", text)
    self.assertIn("change layer", text)


if __name__ == "__main__":
  unittest.main()
