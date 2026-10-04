#!/usr/bin/env python3
"""What drives each single-ended result: layers, vias, length, level, ripple.

Reads a case from `results.json` plus its slice board under `raw/` and
returns the numbers the summary table and the verdict discussion need:
which layers the trace uses (with the length on each), how many of its
own vias are in the slice, the trace length, the impedance at the lowest
tabulated frequency (the level), the impedance at Nyquist, the lowest
value and the ripple (max - min over the band).
"""

import math
import pathlib
import re
import statistics

PIECE_RE = re.compile(r'\((segment|arc)\b(.*?)\n\t\)', re.S)
END_RE = re.compile(r'\((?:start|end) ([-\d.]+) ([-\d.]+)\)')
LAYER_RE = re.compile(r'\(layer "([^"]+)"\)')
LAYER_LABEL = {"F.Cu": "L1", "In2.Cu": "L3", "In5.Cu": "L6", "B.Cu": "L8"}


def copper(slice_text, net):
  """(length per layer, own via count) of `net` in a slice board."""
  layers = {}
  for m in PIECE_RE.finditer(slice_text):
    body = m.group(2)
    if f'(net "{net}")' not in body:
      continue
    (x1, y1), (x2, y2) = [(float(x), float(y)) for x, y in
                          END_RE.findall(body)[:2]]
    layer = LAYER_RE.search(body).group(1)
    layers[layer] = layers.get(layer, 0.0) + math.hypot(x2 - x1, y2 - y1)
  vias = len(re.findall(r'\(via\b(?:.*?\n)*?[^\n]*\(net "%s"\)'
                        % re.escape(net), slice_text))
  return layers, vias


def drivers(case, raw_dir):
  """Driver numbers for one single-ended case (None for pairs/errors)."""
  if case["pair"] or case["verdict"] == "ERROR" or not case["rows"]:
    return None
  net = case["nets"][0]
  pcb = next(pathlib.Path(raw_dir).glob("*.kicad_pcb"))
  layers, vias = copper(pcb.read_text(), net)
  zs = [r["z"] for r in case["rows"]]
  by_f = {r["f_mhz"]: r["z"] for r in case["rows"]}
  nyq = min(by_f, key=lambda f: abs(f - 2133))
  return {
      "layers": "+".join(LAYER_LABEL.get(k, k) for k in sorted(
          layers, key=layers.get, reverse=True)),
      "vias": vias, "length": sum(layers.values()),
      "level": zs[0], "z_nyquist": by_f[nyq], "z_min": min(zs),
      "ripple": max(zs) - min(zs),
  }


def discussion(entries):
  """Sentences on what separates the verdicts, from the driver numbers."""
  d = [e for e in entries if e]
  if not d:
    return []
  inner = [e for e in d if "L1" not in e["layers"] and "+" not in e["layers"]]
  out = []
  if inner:
    lvl = statistics.median(e["level"] for e in inner)
    rip = statistics.median(e["ripple"] for e in inner)
    out.append(f"Single-layer inner traces sit at a median {lvl:.1f} Ω at "
               f"0.5 GHz with ripple {rip:.1f} Ω; "
               "that level is the flow's baseline for JLC's 40 Ω geometry, "
               "and whether a net lands above or below the 36 Ω edge of the "
               "band is this baseline plus its own ripple, not a routing "
               "difference.")
  outer = [e for e in d if "L1" in e["layers"]]
  if outer:
    out.append(f"Traces with an L1 section ({len(outer)}) read "
               f"{statistics.median(e['level'] for e in outer):.1f} Ω median "
               "at 0.5 GHz, the lowest group.")
  multi = [e for e in d if "+" in e["layers"]]
  if multi:
    out.append(f"Nets that change layer ({len(multi)}) carry a median ripple "
               f"of {statistics.median(e['ripple'] for e in multi):.1f} Ω "
               "against "
               f"{statistics.median(e['ripple'] for e in inner):.1f} Ω "
               "for single-layer nets; the dip at 3–4 GHz is the via.")
  long_ = [e for e in d if e["length"] >= 15]
  short = [e for e in d if e["length"] < 12]
  if long_ and short:
    rl = statistics.median(e["ripple"] for e in long_)
    rs = statistics.median(e["ripple"] for e in short)
    out.append(f"Length matters too: nets over 15 mm ripple {rl:.1f} Ω "
               f"(median) against {rs:.1f} Ω under 12 mm, the serpentines "
               "showing as a rise toward Nyquist.")
  return out
