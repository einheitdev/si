#!/usr/bin/env python3
"""The board a simulation runs on: paths, stackup, targets, calibration.

Everything board-specific lives in one `board.json` inside the board's
`si/` directory, next to its `stackup.json`; the tool itself holds no
board names. Example (`m_rk3576/hw/si/board.json`):

  {
    "project": "m_rk3576",
    "planes": ["In1.Cu", "In3.Cu", "In6.Cu"],
    "netclass_z": {"DDR_DATA": [40, null], "DDR_DQS": [40, 80]},
    "z_cal": {"se": 1.07, "diff": 1.16},
    "lanes": {"data": "DDR_DQ{bit}_{ch}", "strobe": "DDR_DQS{lane}_{ch}_{leg}",
              "channels": ["A", "B"], "lanes": 2, "limit": 0.635}
  }

`project` names the KiCad files one directory up (`<project>.kicad_pcb`,
`.kicad_pro`, `.kicad_dru`). Results go to `<si dir>/results/`.
"""

import json
import pathlib

MASK = (0.02, 3.5)


class Board:
  """A board's configuration as the scripts need it."""

  def __init__(self, si_dir):
    self.dir = pathlib.Path(si_dir).resolve()
    cfg = json.load(open(self.dir / "board.json"))
    self.project = cfg["project"]
    self.hw_dir = self.dir.parent
    self.pcb = self.hw_dir / f"{self.project}.kicad_pcb"
    self.pro = self.hw_dir / f"{self.project}.kicad_pro"
    self.dru = self.hw_dir / f"{self.project}.kicad_dru"
    self.stackup_path = self.dir / cfg.get("stackup", "stackup.json")
    self.stackup = json.load(open(self.stackup_path))["layers"]
    self.planes = cfg.get("planes", [])
    # layer -> plane the simulation ports on that layer are referenced to
    # (optional; without it fix_ports uses its 8-layer L2/L7 rule)
    self.port_plane = cfg.get("port_plane", {})
    # netclass -> {"f_start", "f_stop", "rows_mhz", "nyquist_mhz"}: the
    # simulated band and report table per class (optional; see
    # run_si.REPORT_DEFAULT for the LPDDR4X defaults)
    self.report = cfg.get("report", {})
    # netclass -> (single-ended Z, differential Z or None)
    self.netclass_z = {k: (v[0], v[1]) for k, v in cfg["netclass_z"].items()}
    self.z_cal = dict(cfg.get("z_cal", {"se": 1.0, "diff": 1.0}))
    self.lanes = cfg.get("lanes")
    self.results = self.dir / "results"
    self.work = self.dir / "work"

  def si_wrapper_name(self, netclass):
    """si-wrapper's `NNOhm-SE-*` / `NNOhm-DIFF-*` name for a class."""
    z_se, z_diff = self.netclass_z[netclass]
    if z_diff:
      return f"{z_diff:g}Ohm-DIFF-{netclass}"
    return f"{z_se:g}Ohm-SE-{netclass}"

  def copper_layers(self):
    """Copper layer names top to bottom."""
    return [lay["name"] for lay in self.stackup if lay["type"] == "copper"]

  def layer_z(self):
    """Depth of the top of each copper layer below the top surface, mm."""
    z, out = 0.0, {}
    for lay in self.stackup:
      if lay["type"] == "copper":
        out[lay["name"]] = z
      if lay["type"] in ("copper", "core", "prepreg"):
        z += lay["thickness"]
    return out

  def cross_section(self, layer):
    """(layers, trace_z, thickness) of `layer` for zcalc.

    `layers` runs from the reference plane on one side of the trace to
    the plane on the other, as (thickness, eps) tuples, with the trace's
    own copper in between at height `trace_z`; an empty copper layer in
    between is dielectric of its own thickness. An outer layer has no
    plane on the outside and gets solder mask and air there instead; a
    bottom-layer trace is mirrored so its plane is at the bottom.
    """
    seq = self.stackup
    i = next(k for k, lay in enumerate(seq) if lay["name"] == layer)
    thickness = seq[i]["thickness"]

    def walk(direction):
      out, j = [], i + direction
      while 0 <= j < len(seq):
        lay = seq[j]
        if lay["type"] == "copper":
          if lay["name"] in self.planes:
            return out, True
          out.append((lay["thickness"], None))
        elif lay["type"] in ("core", "prepreg"):
          out.append((lay["thickness"], lay["epsilon"]))
        j += direction
      return out, False
    down, plane_down = walk(+1)
    up, plane_up = walk(-1)
    if not plane_down and not plane_up:
      raise ValueError(f"{layer}: no reference plane on either side")
    if not plane_down:
      down, up, plane_up = up, down, False

    def filled(stack):
      eps = next((e for _, e in stack if e), 4.3)
      out = []
      for t, e in stack:
        eps = e or eps
        out.append((t, eps))
      return out
    down, up = filled(down), filled(up)
    layers = list(reversed(down)) + [(thickness, down[0][1])] + up
    if not plane_up:
      layers += [MASK, (1.0, 1.0)]
    return layers, sum(t for t, _ in down), thickness

  def lane_nets(self):
    """[(strobe_p, strobe_n, [data nets])] per channel and byte lane."""
    if not self.lanes:
      return []
    out = []
    for ch in self.lanes["channels"]:
      for lane in range(self.lanes["lanes"]):
        strobe = [self.lanes["strobe"].format(lane=lane, ch=ch, leg=leg)
                  for leg in ("P", "N")]
        data = [self.lanes["data"].format(bit=8 * lane + b, ch=ch)
                for b in range(8)]
        out.append((strobe[0], strobe[1], data))
    return out
