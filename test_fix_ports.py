#!/usr/bin/env python3
"""Unit tests for the geometry helpers in fix_ports.py."""

import json
import pathlib
import re
import tempfile
import unittest
import unittest.mock

import fix_ports


def seg_text(a, b, net, layer="In2.Cu", w=0.2):
  """KiCad text of one segment."""
  return (f'\n\t(segment\n\t\t(start {a[0]} {a[1]})\n\t\t(end {b[0]} {b[1]})'
          f'\n\t\t(width {w})\n\t\t(layer "{layer}")\n\t\t(net "{net}")\n\t)')


def seg(a, b, net="N", layer="In2.Cu"):
  """Track dict as fix_ports.tracks() builds it."""
  return dict(span=(0, 0), a=a, b=b, w=0.185, layer=layer, net=net)


class LargestComponentTest(unittest.TestCase):
  """Orphan stubs are separated from the main run."""

  def test_stub_is_orphan(self):
    main = [seg((0, 0), (5, 0)), seg((5, 0), (5, 3))]
    stub = [seg((9, 9), (9, 9.3), layer="In5.Cu")]
    kept, orphans = fix_ports.largest_component(main + stub)
    self.assertEqual(len(kept), 2)
    self.assertEqual(orphans, stub)


class LayerTailTest(unittest.TestCase):
  """A short tail on another layer is cut off; the port goes to the main run."""

  def test_tail_dropped(self):
    tail = seg((1, 0), (1, 0.3), layer="In5.Cu")
    run = [tail, seg((1, 0.3), (1, 6)), seg((1, 6), (4, 6))]
    drop = set()
    ports = fix_ports.find_ports(run, drop)
    self.assertEqual({p["pos"] for p in ports}, {(1, 0.3), (4, 6)})
    self.assertIn(id(tail), drop)
    self.assertTrue(all(p["layer"] == "In2.Cu" for p in ports))


class LayerChangeTest(unittest.TestCase):
  """A layer change in the middle of the run is kept, ports on each layer."""

  def test_mid_run_change_kept(self):
    l3 = [seg((0, 0), (0, 2))]
    l6 = [seg((0, 2), (0, 8), layer="In5.Cu"),
          seg((0, 8), (3, 8), layer="In5.Cu")]
    drop = set()
    ports = fix_ports.find_ports(l3 + l6, drop)
    self.assertEqual(drop, set())
    self.assertEqual({(p["pos"], p["layer"]) for p in ports},
                     {((0, 0), "In2.Cu"), ((3, 8), "In5.Cu")})


class ShortEndTest(unittest.TestCase):
  """A straight piece shorter than MIN_PORT_SEG is cut; port moves inward."""

  def test_short_end_skipped(self):
    short = seg((0, 0), (0.2, 0))
    run = [short, seg((0.2, 0), (0.2, 5)), seg((0.2, 5), (3, 5))]
    drop = set()
    ports = fix_ports.find_ports(run, drop)
    self.assertIn(id(short), drop)
    self.assertEqual({p["pos"] for p in ports}, {(0.2, 0), (3, 5)})


class LongDiagonalTest(unittest.TestCase):
  """A long diagonal end is kept and gets a straight stub with the port."""

  def test_stub_added(self):
    diag = seg((0, 0), (3, 3))
    run = [diag, seg((3, 3), (3, 9)), seg((3, 9), (8, 9))]
    drop = set()
    ports = fix_ports.find_ports(run, drop)
    self.assertNotIn(id(diag), drop)
    stubbed = [p for p in ports if "stub" in p]
    self.assertEqual(len(stubbed), 1)
    p = stubbed[0]
    self.assertEqual(p["stub"][0], (0, 0))
    L = fix_ports.STUB_LEN
    self.assertIn(p["stub"][1], [(-L, 0), (0, -L)])
    self.assertLessEqual(p["length"], 0.8 * L)
    # the stub plus the trace's end cap must stay clear of the next ball
    # on a 0.65 mm pitch BGA, whose via-in-pad ring reaches 0.125 inward
    self.assertLess(L + p["w"] / 2, 0.65 - 0.125)
    self.assertIn(p["rot"], [270, 180])


class StubDirectionTest(unittest.TestCase):
  """The stub avoids vias on the ball grid and never points into the trace."""

  def test_default_direction_without_obstacles(self):
    tip, d, L, gnd = fix_ports.stub_for((0, 0), (1, 1), 0.185, [])
    self.assertIn(d, [(-1, 0), (0, -1)])
    self.assertAlmostEqual(L, fix_ports.STUB_LEN)
    self.assertEqual(gnd, [])

  def test_diagonal_escape_never_alongside(self):
    # trace leaves at 45° toward +x -y: both (1,0) and (0,-1) run beside it
    for _ in range(3):
      tip, d, L, _ = fix_ports.stub_for((0, 0), (0.27, -0.27), 0.185, [])
      self.assertIn(d, [(-1, 0), (0, 1)])

  def test_avoids_via_on_axis(self):
    # trace leaves toward +x/+y; a GND via 0.6 mm at -x, nothing at -y
    tip, d, L, _ = fix_ports.stub_for((0, 0), (1, 0.2), 0.185,
                                      [(-0.6, 0, 0.125, "X")])
    self.assertIn(d, [(0, 1), (0, -1)])

  def test_shortens_when_boxed_in(self):
    obstacles = [(-0.45, 0, 0.125, "X"), (0, -0.45, 0.125, "Y"),
                 (0, 0.45, 0.125, "Z")]
    tip, d, L, _ = fix_ports.stub_for((0, 0), (1, 0), 0.185, obstacles)
    self.assertLess(L, fix_ports.STUB_LEN)
    self.assertGreaterEqual(L, fix_ports.MIN_STUB)

  def test_gnd_via_in_the_way_is_removed_not_avoided(self):
    # GND stitching 0.45 mm out on every free side: keep the full stub
    # and hand the via on the chosen side back for removal
    obstacles = [(-0.45, 0, 0.125, "GND"), (0, -0.45, 0.125, "GND"),
                 (0, 0.45, 0.125, "GND")]
    tip, d, L, gnd = fix_ports.stub_for((0, 0), (1, 0), 0.185, obstacles)
    self.assertAlmostEqual(L, fix_ports.STUB_LEN)
    self.assertEqual(len(gnd), 1)

  def test_avoids_other_leg(self):
    # the partner leg runs vertically 0.4 mm to the +x side of the ball
    other = fix_ports.track_points([seg((0.4, -0.2), (0.4, 0.6), net="O")])
    tip, d, L, _ = fix_ports.stub_for((0, 0), (0, 1), 0.185, other)
    self.assertNotEqual(d, (1, 0))


class ClipFillsTest(unittest.TestCase):
  """Fill vertices outside the outline are clamped onto it."""

  def test_clamp(self):
    text = ('\n\t(gr_rect\n\t\t(start 10 20)\n\t\t(end 30 40)'
            '\n\t\t(layer "Edge.Cuts")\n\t)'
            '\n\t(zone\n\t\t(filled_polygon\n\t\t\t(layer "In1.Cu")'
            '\n\t\t\t(pts\n\t\t\t\t(xy 9.8 19.5) (xy 30.2 19.5) (xy 15 25)'
            '\n\t\t\t)\n\t\t)\n\t)')
    out = fix_ports.clip_fills(text)
    i = fix_ports.FILL_INSET
    self.assertIn(f"(xy {10 + i:g} {20 + i:g})", out)
    self.assertIn(f"(xy {30 - i:g} {20 + i:g})", out)
    self.assertIn("(xy 15 25)", out)


class ReturnViaTest(unittest.TestCase):
  """return_vias puts a GND via beside each via of the net, on a free side."""

  def test_free_side(self):
    s = ('\n\t(segment\n\t\t(start 0 0)\n\t\t(end 5 0)\n\t\t(width 0.2)'
         '\n\t\t(layer "In2.Cu")\n\t\t(net "N")\n\t)'
         '\n\t(via\n\t\t(at 5 0)\n\t\t(size 0.3)\n\t\t(drill 0.15)'
         '\n\t\t(layers "F.Cu" "B.Cu")\n\t\t(net "N")\n\t)'
         '\n\t(via\n\t\t(at 5 -0.5)\n\t\t(size 0.3)\n\t\t(drill 0.15)'
         '\n\t\t(layers "F.Cu" "B.Cu")\n\t\t(net "X")\n\t)\n)')
    out = fix_ports.return_vias(s, ["N"], 0.5)
    self.assertEqual(out.count("(via"), 1)
    self.assertIn('(net "GND")', out)
    # -x has the track, -y the foreign via: +x or +y is free
    at = re.search(r'\(at ([-\d.]+) ([-\d.]+)\)', out).groups()
    self.assertIn(tuple(float(v) for v in at), [(5.5, 0.0), (5.0, 0.5)])


class ZoneClearanceTest(unittest.TestCase):
  """The zone clearance override rewrites only the zone's clearance."""

  def test_rewrite(self):
    s = '(zone (connect_pads yes (clearance 0.5)) (x (clearance 0.5)))'
    out = fix_ports.ZONE_CLR_RE.sub(r"\g<1>0.1\2", s)
    self.assertEqual(out, '(zone (connect_pads yes (clearance 0.1)) '
                          '(x (clearance 0.5)))')


class PadRemovalTest(unittest.TestCase):
  """The kept ball pads go, port footprint text stays."""

  def test_pads_removed(self):
    s = ('\n\t(footprint "U1"\n\t\t(pad "E4" smd circle\n\t\t\t(at 0 0)'
         '\n\t\t\t(layers "F.Cu")\n\t\t)\n\t)'
         '\n\t(footprint "Simulation_Port"\n\t\t(at 1 2 90)\n\t)')
    out = fix_ports.PAD_RE.sub("", s)
    self.assertNotIn("(pad", out)
    self.assertIn('(footprint "Simulation_Port"\n\t\t(at 1 2 90)', out)


class LongSpurTest(unittest.TestCase):
  """A dead-end branch longer than MAX_SPUR is an error, not pruned."""

  def test_long_spur_rejected(self):
    run = [seg((0, 0), (5, 0)), seg((5, 0), (9, 0)), seg((5, 0), (5, 3))]
    with self.assertRaisesRegex(ValueError, "dead-end branch"):
      fix_ports.prune_branches(run)

  def test_short_spur_pruned(self):
    run = [seg((0, 0), (5, 0)), seg((5, 0), (9, 0)), seg((5, 0), (5, 0.3))]
    kept, removed = fix_ports.prune_branches(run)
    self.assertEqual(len(removed), 1)


class PassedViaTest(unittest.TestCase):
  """A via with same-layer trace on both sides is a leftover route."""

  def test_same_layer_both_sides(self):
    run = [seg((0, 0), (5, 0)), seg((5, 0), (9, 0))]
    self.assertEqual(fix_ports.passed_vias(run, [(5, 0)]), [(5, 0)])

  def test_layer_change_ok(self):
    run = [seg((0, 0), (5, 0)), seg((5, 0), (9, 0), layer="In5.Cu")]
    self.assertEqual(fix_ports.passed_vias(run, [(5, 0)]), [])


class LoopTest(unittest.TestCase):
  """Two parallel routes between the same points are an error, not a port."""

  def test_loop_rejected(self):
    run = [seg((0, 0), (5, 0)), seg((0, 0), (0, 1)), seg((0, 1), (5, 1)),
           seg((5, 1), (5, 0)), seg((5, 0), (9, 0))]
    with self.assertRaisesRegex(ValueError, "loop"):
      fix_ports.find_ports(run, set())

  def test_end_on_other_piece_rejected(self):
    # the second route rejoins the first in the middle of a segment
    run = [seg((0, 0), (5, 0)), seg((5, 0), (9, 0)), seg((0, 0), (0, 1)),
           seg((0, 1), (3, 1)), seg((3, 1), (3, 0))]
    with self.assertRaisesRegex(ValueError, "on top of another"):
      fix_ports.find_ports(run, set())

  def test_long_duplicate_on_top_dropped(self):
    # 1.4 mm drawn exactly over the middle of a 2 mm segment
    dup = seg((0.5, 0), (1.9, 0))
    run = [seg((-3, 0), (0, 0)), seg((0, 0), (2, 0)), seg((2, 0), (6, 0)),
           dup]
    drop = set()
    ports = fix_ports.find_ports(run, drop)
    self.assertIn(id(dup), drop)
    self.assertEqual(len(ports), 2)

  def test_direction_over_zigzag(self):
    # end piece points -x for 0.08 mm, then the trace runs +x +y
    pieces = [seg((0.08, 0), (0, 0)), seg((0, 0), (0.28, 0.24)),
              seg((0.28, 0.24), (3, 3))]
    into = fix_ports.direction_from((0.08, 0), pieces)
    self.assertGreater(into[0], 0)
    self.assertGreater(into[1], 0)

  def test_zigzag_at_ball_is_not_a_junction(self):
    # the trace overshoots the ball by 0.04 and comes back 0.08 on itself
    run = [seg((5, 0), (0.28, 0.24)), seg((0.28, 0.24), (0, 0)),
           seg((0, 0), (0.08, 0))]
    self.assertEqual(fix_ports.t_junctions(run), [])

  def test_short_overlap_dropped(self):
    # 0.5 mm duplicated on top of the first segment, from its start
    dup = seg((0, 0), (0.5, 0))
    run = [seg((0, 0), (5, 0)), seg((5, 0), (9, 0)), dup,
           seg((0, 0), (0, -2)), seg((0, -2), (-3, -2))]
    drop = set()
    ports = fix_ports.find_ports(run, drop)
    self.assertIn(id(dup), drop)
    self.assertEqual(len(ports), 2)


class VanishedNetTest(unittest.TestCase):
  """A net with no copper after the refill is reported, not simulated."""

  def test_vanished(self):
    with tempfile.TemporaryDirectory() as d:
      pcb = pathlib.Path(d) / "x.kicad_pcb"
      sim = pathlib.Path(d) / "simulation.json"
      sim.write_text(json.dumps({"ports": [{}, {}]}))
      pcb.write_text(
          '(kicad_pcb\n\t(gr_rect\n\t\t(start 0 0)\n\t\t(end 20 10)'
          '\n\t\t(layer "Edge.Cuts")\n\t)'
          + "".join(seg_text((2, 5), (18, 5), "N"))
          + '\n\t(footprint "Simulation_Port"\n\t\t(at 0 0)'
          '\n\t\t(property "Reference" "SP1"\n\t)'
          '\n\t(footprint "Simulation_Port"\n\t\t(at 0 0)'
          '\n\t\t(property "Reference" "SP2"\n\t)\n)')

      def fake_refill(path):
        text = pathlib.Path(path).read_text().replace('(net "N")',
                                                      '(net "M")')
        pathlib.Path(path).write_text(text)
      with unittest.mock.patch.object(fix_ports, "refill_zones",
                                      fake_refill):
        with self.assertRaisesRegex(ValueError, "no copper left"):
          fix_ports.fix_slice(str(pcb), ["N"])


class RefillTest(unittest.TestCase):
  """refill_zones calls kicad-cli's DRC refill on the slice."""

  def test_command(self):
    calls = []

    def fake_run(cmd, **kw):
      calls.append(cmd)
      pathlib.Path(cmd[-2]).write_text("report")
    with unittest.mock.patch.object(fix_ports.subprocess, "run", fake_run):
      with tempfile.TemporaryDirectory() as d:
        pcb = pathlib.Path(d) / "x.kicad_pcb"
        fix_ports.refill_zones(pcb)
        self.assertFalse(list(pathlib.Path(d).glob("*.rpt")))
    self.assertEqual(calls[0][:4], ["kicad-cli", "pcb", "drc",
                                    "--refill-zones"])
    self.assertIn("--save-board", calls[0])
    self.assertEqual(calls[0][-1], str(pcb))


class PruneBranchesTest(unittest.TestCase):
  """A short spur off a junction is removed, the long path survives."""

  def test_spur_removed(self):
    main = [seg((0, 0), (0, 5)), seg((0, 5), (0, 10))]
    spur = [seg((0, 5), (0.4, 5))]
    kept, removed = fix_ports.prune_branches(main + spur)
    self.assertEqual(removed, spur)
    self.assertEqual(len(kept), 2)


class FindPortsTest(unittest.TestCase):
  """Ports land on the first axis-aligned segment from each free end."""

  def test_short_diagonal_end_dropped(self):
    diag = seg((0.7, 0.7), (1, 1))
    run = [diag, seg((1, 1), (1, 6)), seg((1, 6), (4, 6))]
    ports = fix_ports.find_ports(run, set())
    self.assertTrue(all(0 < p["length"] <= fix_ports.PORT_LEN for p in ports))
    drop = set()
    ports = fix_ports.find_ports(run, drop)
    self.assertEqual({p["pos"] for p in ports}, {(1, 1), (4, 6)})
    self.assertEqual(drop, {id(diag)})
    rots = {p["pos"]: p["rot"] for p in ports}
    # from (1,1) the trace goes +y -> 180; from (4,6) it goes -x -> 90
    self.assertEqual(rots[(1, 1)], 180)
    self.assertEqual(rots[(4, 6)], 90)


class SurplusPortsTest(unittest.TestCase):
  """A net with more pads than trace ends: the footprint nearest to each
  end stays, renumbered in port order, the rest and their ports go."""

  def test_drop(self):
    def fp(n, x, y):
      return (f'\n\t(footprint "Simulation_Port"\n\t\t(at {x} {y} 0)'
              f'\n\t\t(property "Reference" "SP{n}"\n\t\t)\n\t)')
    # the surplus one sits between the kept ones
    s = "(kicad_pcb" + fp(1, 0, 0) + fp(2, 10, 5) + fp(3, 10, 0) + "\n)"
    order = [dict(pos=(10.2, 0.1)), dict(pos=(0.1, 0))]
    ports = [{"number": k} for k in (1, 2, 3)]
    fps = list(fix_ports.FOOTPRINT_RE.finditer(s))
    out, kept = fix_ports.drop_surplus_ports(s, fps, order, ports)
    self.assertEqual(len(re.findall(r'"Reference" "SP(\d+)"', out)), 2)
    self.assertEqual(out.count('(footprint "Simulation_Port"'), 2)
    self.assertNotIn("(at 10 5 0)", out)
    self.assertIn("(at 10 0 0)", out)
    self.assertEqual([p["number"] for p in kept], [1, 2])
    # the footprint at (10, 0) serves order[0] and becomes SP1
    self.assertLess(out.index("(at 10 0 0)"), out.index('"SP1"'))
    self.assertLess(out.index("(at 0 0 0)"), out.index('"SP2"'))


if __name__ == "__main__":
  unittest.main()
