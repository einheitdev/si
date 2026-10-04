# si — signal-integrity simulation of KiCad boards with openEMS

Slices one net or pair out of a KiCad board (si-wrapper), fixes the simulation ports onto the copper, refills the zones, meshes and solves it with openEMS (gerber2ems), and judges the impedance, loss and crosstalk against the netclass targets. Results accumulate in one ledger per board and a Typst PDF is built from the latest result per net. Companions: a 2D electrostatic field solve of the stackup cross-sections (`zcalc.py`) to check calculator widths and calibrate the flow, a lane length check that counts via barrels the way KiCad does (`lanes.py`), and a recalibration of recorded results (`recal.py`).

## Using it on a board

A board is a directory holding `board.json` and `stackup.json`, by convention `<project>/hw/si/`; see `board.py` for the keys (project name, reference planes, netclass targets, calibration, lane patterns). Everything takes `--board DIR` and defaults to the current directory, so from the board's `si/` directory:

```
SI=/home/karl/dev/einheit/si
$SI/run_si.py --se DDR_DQ0_A --pair DDR_DQS0_A_P,DDR_DQS0_A_N   # simulate, record
$SI/report/build_report.py                                        # results/report.pdf
$SI/lanes.py                                                      # DQ vs strobe lengths
$SI/zcalc.py                                                      # netclass widths, 2D solve
$SI/recal.py --se 1.07 --diff 1.16                                # rescale recorded results
```

Results: `results/ledger.jsonl` (one record per simulated case, appended), `results/raw/<case>/` (latest slice board, port configuration and S-parameter CSVs per case) and `results/report.pdf`. Rerunning one net replaces its entry in the report and keeps the old record in the ledger. The ledger and `board.json` belong in the board's repository; `raw/` and the PDF are reproducible and ignored.

Tests: `python3 -m unittest discover -p 'test_*.py'` here and in `report/`; lint with `flake8` (config in `.flake8`).

`zcalc.py` discretises the cross-section at 5 µm; where a layer boundary does not land on the grid the result moves by up to about 1 Ω single-ended and 2 Ω differential (the 1080B L3 geometry reads 39.1 or 39.9 Ω depending on which plane is at the bottom). Take its numbers as ±1 Ω.

## Toolchain (installed 2026-10-03, see the build notes at the end)

| Tool | Where | What it does |
|---|---|---|
| openEMS v0.37 + CSXCAD | `~/opt/openEMS` (built from source, Python bindings in `~/opt/openEMS/venv`) | FDTD field solver |
| gerber2ems (Antmicro) | `~/opt/openEMS/venv/bin/gerber2ems` | Gerbers + drill + `stackup.json` → mesh → openEMS → S-params, Z(f), plots |
| kicad-si-simulation-wrapper (Antmicro) | `~/opt/openEMS/venv/bin/si-wrapper` | Cuts one net (plus neighbours) out of the `.kicad_pcb` into a small slice board with simulation ports |
| gerbv | `/usr/bin/gerbv` | Gerber rendering used by both |
| KiCad 10 `pcbnew` Python module | `/usr/local/lib/python3/dist-packages` | needed by `si-wrapper`; **not on sys.path by default** |

Sources are in `~/opt/src/{openEMS-Project,gerber2ems,kicad-si-simulation-wrapper}`.

Activate for a session:

```sh
source ~/opt/openEMS/venv/bin/activate
export PYTHONPATH=/usr/local/lib/python3/dist-packages   # pcbnew for si-wrapper
```

## `stackup.json`

`stackup.json` in this folder is JLC08121H-1080B in gerber2ems format (17 entries, 1.19 mm). Dielectric constants: 1080 prepreg 3.91, cores 4.4, 7628+1080 combined 4.3 (JLC's published values, Tg 150 era; the Tg 170 laminate is a few percent higher, inside the tolerance of everything simulated here). Copy it into each slice's `fab/` folder.

## Running a simulation and getting a report

`run_si.py` does the whole chain and writes a Markdown report. Nothing in the real project is touched; it works on a scratch copy in `work/`.

```sh
cd m_rk3576/hw/si
./run_si.py --se DDR_DQ0_A --se DDR_DQ3_A \
            --pair DDR_DQS0_A_P,DDR_DQS0_A_N \
            --out results/2026-10-03
```

Output: `results/<date>/report.md` with a configuration block, one table per case at 0.5 / 1 / 2.133 / 3 / 4.266 GHz, a PASS/FAIL verdict, and the plots (slice render, Z(f), S-parameters, Smith chart; Z_diff and SDD for pairs). `--se` and `--pair` repeat. Each case solves in about one to two minutes.

Unit tests for the S-parameter maths: `python3 -m unittest test_run_si test_fix_ports`.

PDF of one or more reports, with an optional intro page: `./report_pdf.py OUT.pdf --intro intro.md results/<run> [results/<run> ...]` (weasyprint from the openEMS venv; one page per case).

### What is simulated

| Item | Setting | Where |
|---|---|---|
| Solver | openEMS FDTD, MUR boundaries, run until the energy has decayed 60 dB | gerber2ems default |
| Geometry | Gerbers of the slice, every copper layer, vias from the Excellon file as plated cylinders (50 µm plating) | `fab/` of the slice |
| Stackup | JLC08121H-1080B thicknesses and Dk per layer | `si/stackup.json` |
| Slice | the net(s) plus every other net that comes within `--neighbour-offset` (0.3 mm) of it over at least `--common-points` (10) points; L2 GND pour included; other pads hidden; neighbours terminated | si-wrapper settings written by `slice_case` |
| Ports | MSL ports on the trace ends, trace width, ≤ 300 µm long, on the trace's own layer, referenced to L2 for L1–L4 traces and L7 for L5–L8; port impedance = the class single-ended target. Ends that enter a BGA ball diagonally get a 0.35 mm stub beyond the ball for the port; the ball's via-in-pad and pad are removed | `fix_ports.fix_slice` + `configure` |
| Zones | refilled with `kicad-cli pcb drc --refill-zones` after the port fix, optionally with `--zone-clearance` overriding the zone's own clearance (the board's GND zone has KiCad's 0.5 mm default) | `fix_ports.refill_zones` |
| Targets | per netclass: DDR 40 Ω SE / 80 Ω diff, eMMC 50, MDI 50/100, USB 45/90, PCIe 42.5/85; tolerance ±10 % | `NETCLASS_Z`, `TOLERANCE` in `run_si.py` |
| Frequency | 0.2 to 5 GHz (`--f-stop`), 1001 points; LPDDR4X-4266 Nyquist is 2.133 GHz | `configure` |
| Mesh | `--mesh` µm on the traces (default 50), 4× that perpendicular, 10× max, graded 1.2 in-plane / 1.5 vertical | `grid` block in `simulation.json` |
| Excitation | single-ended: port 0; pairs: both near ends (ports 0 and 2), leg mapping detected from the result before the mixed-mode step | `configure`, `solve` |

### What the report numbers mean

- **Z** (single-ended): impedance seen at the excited port from S11, so the trace as the driver sees it including the escape. Target ±10 %.
- **RL / IL**: return loss (S11) and insertion loss to the far end. IL should be a few tenths of a dB at most on a module-length trace.
- **to port N**: coupling into a neighbour's port that the slicer pulled in. The verdict fails above −20 dB.
- **Z_diff, SDD11, SDD21**: differential impedance and reflection/transmission from the mixed-mode conversion. **leg NEXT/FEXT**: how much of one leg's signal shows up on the other, −20 dB or so means the legs are coupled as a pair, −40 dB means they are routed as two separate traces.

### Accuracy, and what the slice must contain

Everything below was found the hard way on 2026-10-03/04 and is handled by `fix_ports.py` / `run_si.py`; it is listed so nobody removes a step without knowing why it exists.

- **Planes must be stitched.** With more than one plane layer and no vias joining them, the planes float, the slice rings and the solver runs to its step limit. `fix_ports` keeps every via not on the simulated nets and adds a GND via ring along the slice edge (`--no-stitch` to disable, debugging only).
- **Plane fills must stop inside the outline.** gerbv sizes each layer's raster to its own content and gerber2ems assumes one frame for all layers. The GND fill copied from the board overshoots the slice outline, so the three plane layers rendered 70 px larger than the signal layers and sat 0.35 mm off. Every via then pierced plane copper next to its antipad: a short, read as "open" at the ports. `clip_fills` clamps the fill polygons to 0.15 mm inside the outline so the outline stroke is fully visible on every layer and all rasters come out the same size. Check: all `ems/geometry/*_Cu.png` of a slice have one size.
- **The whole net is simulated, ports on stubs.** si-wrapper drops pieces of a net (a diagonal before a via-in-pad, the far part of a layer-changing run); `restore_net_copper` copies the net's tracks and vias from the board verbatim. Trace ends that run diagonally into a BGA ball get a 0.35 mm straight stub beyond the ball with the port on it; the via-in-pad under the ball is removed and the zones are then refilled with `kicad-cli pcb drc --refill-zones --save-board`, so the plane is solid under the port and every remaining via has its antipad. The refill is not optional: the board is saved with stale fills (no antipads at the DDR vias, KiCad does not refill on save) and si-wrapper copies them as they are, which put every layer-change via through solid GND copper and shorted the net (|S11| = 1, IL nan, Z of 1–10 Ω). pcbnew's `ZONE_FILLER` from Python has no DRC engine and does the same, kicad-cli's does it right. The stub must be shorter than the 0.65 mm ball pitch minus the via ring: a 0.8 mm stub ran straight through the next ball's via-in-pad, which is often GND, and shorted the port. Cutting instead (`MAX_CUT` large) loses the BGA escape, 2–4 mm of a 10–20 mm trace and the part where crosstalk and the neck discontinuity live.
- **Vias are solid** (`via.filling_epsilon = 0`, patched gerber2ems): a hollow 50 µm barrel wall can fall between mesh lines. Barrels also protrude 10 µm past the outer copper sheets (patched). With the plane fix in place, 0.15 mm drill vias conduct fine at a 50 µm mesh.
- **Single-ended levels agree with JLC** without calibration: 0.185 mm on L3/L6 reads 35–43 Ω against 40. `--z-cal` stays at 1,1.
- **Pairs read low**, 62–75 Ω for JLC's 80 Ω geometry, and one of the two near-end ports often fails energy conservation. The report then takes the pair as symmetric from the physical excitation and says so; judge pairs on flatness and leg coupling (−20 to −25 dB NEXT at 4.3 GHz means coupled), not on the absolute level.
- **Energy check.** sum |S|² per excitation above 1.15 marks the case INVALID; clean cases sit at 1.00–1.10.

Not simulated: the RK3576 PHY and DRAM drivers (no public models), solder-mask tolerance, copper roughness, and anything outside the slice. Absolute impedance is good to a few percent at 50 µm mesh; use `--mesh 30` to tighten it at ~3× the run time.

### Manual flow

The steps `run_si.py` automates, for when one of them needs a hand:

1. `make_work_copy`: project copy with netclasses renamed `NNOhm-SE-*` / `NNOhm-DIFF-*` (si-wrapper only accepts that pattern and reads the impedance from it).
2. `si-wrapper slice -f net_configs/<net>.json` inside `work/` (`PYTHONPATH=/usr/local/lib/python3/dist-packages` for pcbnew).
3. `python3 ../../../fix_ports.py <slice>.kicad_pcb <net> [<net>]` inside the slice dir.
4. Export with the drill origin: `kicad-cli pcb export gerbers --no-protel-ext --use-drill-file-origin -o fab/`, `... drill --format excellon --excellon-separate-th --drill-origin plot -o fab/`, `... pos --format csv --units mm --side front --use-drill-file-origin -o fab/<slice>-top-pos.csv`; copy `stackup.json` into `fab/`.
5. Edit `simulation.json`: `frequency`, `grid`, port `impedance`, and a `traces` or `differential_pairs` entry carrying an `interface` block (gerber2ems refuses to plot without one).
6. `gerber2ems -a`; for pairs check from the S-parameters which ports are the same leg (through ≈ 0 dB), fix `start_p/stop_p/start_n/stop_n`, `gerber2ems -p`.

## Verified install

`~/opt/src/gerber2ems/examples/differential` ran end to end on 2026-10-03 (4-port diff pair, 200 MHz – 4 GHz, ~1.3 M cells); results in that folder under `ems/results/`.

## Build notes

- Debian trixie has no openEMS package; built with `./update_openEMS.sh ~/opt/openEMS --python --disable-GUI` from `~/opt/src/openEMS-Project` (deps: libhdf5-dev libvtk9-dev libboost-all-dev libcgal-dev libtinyxml-dev qtbase5-dev libvtk9-qt-dev cython3 python3-setuptools-scm).
- `si-wrapper` says it wants KiCad 9.0.x; it imports `pcbnew` from KiCad 10.0.4 here. If `slice` breaks on an API change, that is where to look.
- si-wrapper `terminate_track` uses `pcbnew.VIATYPE_BLIND_BURIED`, which KiCad 10 split into `VIATYPE_BLIND` / `VIATYPE_BURIED`; it is only reached when a neighbouring track has to be terminated on an inner layer, so it surfaced late (DDR_DQ2_B, DDR_DQ3_B on 2026-10-04). Patched in `~/opt/src/kicad-si-simulation-wrapper/src/si_wrapper/pcbslicer.py` with a `getattr` fallback, next to the `_dup()` fix.
- gerber2ems `main` (2026-10) fails after the FDTD run with `a probe named "port_ut_0A" already exists` against openEMS ≥ 0.0.36: it re-creates its "virtual" result-reading ports on the CSX that already holds the loaded probes. Patched locally in `~/opt/src/gerber2ems/src/gerber2ems/simulation.py` (`add_virtual_port` builds them on a separate empty `ContinuousStructure`); `git diff` in that checkout shows the change. Re-apply if gerber2ems is updated.
- `ems2paraview` needs `paraview python3-paraview` (not installed; optional, for field animations).

Net names: strobe and clock nets were renamed to `*_P` / `*_N` on 2026-10-03 so KiCad treats them as pairs; result logs before that use the old `_t_` / `_c_` names.

