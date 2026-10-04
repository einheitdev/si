"""Tests for the routed-length lane check."""

import unittest

import lanes

Z = {"F.Cu": 0.0, "In2.Cu": 0.2266, "In5.Cu": 0.9458}
ITEMS = [
    ("segment", '\t\t(start 0 0)\n\t\t(end 10 0)\n\t\t(layer "In5.Cu")'
                '\n\t\t(net "DDR_DQ0_A")\n'),
    ("via", '\t\t(at 0 0)\n\t\t(net "DDR_DQ0_A")\n'),
    ("segment", '\t\t(start 0 0)\n\t\t(end 10 0)\n\t\t(layer "In2.Cu")'
                '\n\t\t(net "DDR_DQS0_A_P")\n'),
    ("via", '\t\t(at 0 0)\n\t\t(net "DDR_DQS0_A_P")\n'),
    ("segment", '\t\t(start 0 0)\n\t\t(end 10 0)\n\t\t(layer "In2.Cu")'
                '\n\t\t(net "DDR_DQS0_A_N")\n'),
    ("via", '\t\t(at 0 0)\n\t\t(net "DDR_DQS0_A_N")\n'),
]


class RoutedTest(unittest.TestCase):
  """Via-in-pad barrels count between the ball pad and the trace layer."""

  def test_via_length_by_layer(self):
    t, v, n = lanes.routed(ITEMS, "DDR_DQ0_A", Z)
    self.assertAlmostEqual(t, 10.0)
    self.assertAlmostEqual(v, Z["In5.Cu"])
    t, v, n = lanes.routed(ITEMS, "DDR_DQS0_A_P", Z)
    self.assertAlmostEqual(v, Z["In2.Cu"])

  def test_lane_delta(self):
    ref, strobe, rows = lanes.lane_table(ITEMS, Z, "DDR_DQS0_A_P",
                                         "DDR_DQS0_A_N", ["DDR_DQ0_A"], 0.635)
    self.assertAlmostEqual(ref, 10 + Z["In2.Cu"])
    net, t, v, r, d, ok = rows[0]
    self.assertAlmostEqual(d, Z["In5.Cu"] - Z["In2.Cu"])
    self.assertFalse(ok)


if __name__ == "__main__":
  unittest.main()
