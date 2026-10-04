"""Tests for the 2D electrostatic impedance solver."""

import math
import unittest

import zcalc


class StriplineTest(unittest.TestCase):
  """A symmetric stripline matches the IPC-2141 closed form within 5 %.

  The closed form is itself an approximation good to a few percent; the
  solve lands 3 % above it at 5 µm.
  """

  def test_symmetric_stripline(self):
    b, w, t, er = 0.4, 0.1, 0.0152, 4.3
    layers = [((b - t) / 2, er), (t, er), ((b - t) / 2, er)]
    z, _ = zcalc.impedance(layers, (b - t) / 2, w, t, dx=0.005)
    expect = 60 / math.sqrt(er) * math.log(1.9 * b / (0.8 * w + t))
    self.assertLess(abs(z - expect) / expect, 0.05, (z, expect))

  def test_air_is_higher(self):
    b, w, t = 0.4, 0.1, 0.0152
    layers = [((b - t) / 2, 4.3), (t, 4.3), ((b - t) / 2, 4.3)]
    z, _ = zcalc.impedance(layers, (b - t) / 2, w, t)
    c = zcalc.solve(layers, (b - t) / 2, w, t)
    c0 = zcalc.solve(layers, (b - t) / 2, w, t, air=False)
    self.assertGreater(c, c0)
    self.assertAlmostEqual(z, 1 / (zcalc.C_LIGHT * math.sqrt(c * c0)))


class PairTest(unittest.TestCase):
  """Odd mode is below even mode and far-apart legs approach single-ended."""

  def test_modes(self):
    b, w, t = 0.4, 0.1, 0.0152
    layers = [((b - t) / 2, 4.3), (t, 4.3), ((b - t) / 2, 4.3)]
    zdiff, modes = zcalc.impedance(layers, (b - t) / 2, w, t, gap=0.1)
    self.assertLess(modes["odd"], modes["even"])
    self.assertAlmostEqual(zdiff, 2 * modes["odd"])
    single, _ = zcalc.impedance(layers, (b - t) / 2, w, t)
    _, far = zcalc.impedance(layers, (b - t) / 2, w, t, gap=1.0)
    self.assertLess(abs(far["odd"] - single) / single, 0.03)


if __name__ == "__main__":
  unittest.main()
