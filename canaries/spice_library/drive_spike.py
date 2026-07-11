# -*- coding: utf-8 -*-
"""Phase-0 canary: prove real KiCad-Spice-Library models resolve + simulate.

Before building any indexer, drive the EXISTING ``skidl.sim`` external-model
seam (``Sim_Library`` + ``Sim_Name`` + ``Sim_Pins`` + ``Sim_Compat="psa"``)
against three real files from the corpus, one per model kind:

  * TL072   -- a ``.subckt`` op-amp   (Operational Amplifier/Tl072.mod)
  * D1N914  -- a bare ``.model`` diode (Diode/diode.lib)
  * MAX402  -- a vendor ``.fam`` op-amp subckt (Manufacturer/Maxim.../MAX402.FAM)

For each we build a minimal testbench, run an operating point, and confirm the
model loaded (provenance tier == "vendor_lib") and produced physical numbers.
This validates the reference contract for THIS corpus and pins down the exact
subckt node order the Phase-1 parser must reproduce.

The op-amp subckts are attached onto a known single-unit op-amp SYMBOL
(ADA4817-1ACP, pins 1..9) purely to get connectable numbered pins; ``Sim_Pins``
maps those symbol pins onto the vendor subckt's own node names -- so this test
exercises the model seam, not KiCad symbol quirks.

Exit 0 = all pass, 1 = a check failed, 2 = backend/corpus unavailable.

Corpus root: env SKIDL_SPICE_LIB_PATH (first entry), else the sibling
``../../../KiCad-Spice-Library``.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from skidl_eda import setup_kicad10  # noqa: E402


def _corpus_root() -> str:
    env = os.environ.get("SKIDL_SPICE_LIB_PATH")
    if env:
        first = env.split(os.pathsep)[0]
        if first:
            return first
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "KiCad-Spice-Library")
    )


ROOT = _corpus_root()
MODELS = os.path.join(ROOT, "Models")

TL072_LIB = os.path.join(MODELS, "Operational Amplifier", "Tl072.mod")
DIODE_LIB = os.path.join(MODELS, "Diode", "diode.lib")
MAX402_LIB = os.path.join(MODELS, "Manufacturer", "Maxim Integrated", "MAX402.FAM")

# Single-unit op-amp symbol used only as a pin carrier for the subckt tests.
OPAMP_SYM = ("Amplifier_Operational", "ADA4817-1ACP")
OPAMP_FP = "Package_CSP:Analog_LFCSP-8-1EP_3x3mm_P0.5mm_EP1.53x1.85mm"


def _opamp_follower(name, lib_path, sim_name, sim_pins, vin=1.0):
    """Unity-gain follower around an external op-amp subckt. Returns Circuit.

    Symbol pins: 4=+in, 3=-in, 7=out, 8=+Vs, 5=-Vs (ADA4817-1ACP).
    ``sim_pins`` maps those onto the vendor subckt's node names.
    """
    from skidl import Circuit, Net, Part

    ckt = Circuit(name=name)
    with ckt:
        u = Part(
            *OPAMP_SYM, ref="U1", value=sim_name, footprint=OPAMP_FP,
            Sim_Library=lib_path, Sim_Name=sim_name, Sim_Pins=sim_pins,
            Sim_Compat="psa",
        )
        vsrc = Part("Simulation_SPICE", "VDC", ref="V1", value=str(vin))
        vp = Part("Simulation_SPICE", "VDC", ref="VP", value="15")
        vn = Part("Simulation_SPICE", "VDC", ref="VN", value="-15")

        vin_n = Net("VIN")
        vout = Net("VOUT")
        vpos = Net("VPOS")
        vneg = Net("VNEG")
        gnd = Net("0")

        u[4] += vin_n     # +in
        u[3] += vout      # -in  (feedback for unity gain)
        u[7] += vout      # out
        u[8] += vpos      # +Vs
        u[5] += vneg      # -Vs

        vsrc[1] += vin_n; vsrc[2] += gnd
        vp[1] += vpos; vp[2] += gnd
        vn[1] += vneg; vn[2] += gnd
    return ckt


def _diode_forward(name, lib_path, model_name, vsupply=5.0, r_ohms="1k"):
    """Forward-biased diode through a series R from a DC supply. Returns Circuit."""
    from skidl import Circuit, Net, Part

    ckt = Circuit(name=name)
    with ckt:
        v = Part("Simulation_SPICE", "VDC", ref="V1", value=str(vsupply))
        r = Part("Device", "R", ref="R1", value=r_ohms)
        d = Part(
            "Device", "D", ref="D1", value=model_name,
            Sim_Library=lib_path, Sim_Name=model_name,
        )
        vin = Net("VIN")
        vd = Net("VD")
        gnd = Net("0")

        v[1] += vin; v[2] += gnd
        r[1] += vin; r[2] += vd
        d["A"] += vd     # anode  -> series R
        d["K"] += gnd    # cathode -> gnd
    return ckt


def _prov(sim, ref):
    prov = sim.model_provenance.get(ref)
    return getattr(prov, "tier", "?"), getattr(prov, "name", "?"), getattr(
        prov, "source", "?"
    )


# compat modes to try, in order (many vendor PSpice macromodels only parse
# under a specific ngspice `ngbehavior`).
COMPAT_MODES = ["psa", "all", "ps", "lt"]


def run_opamp(label, lib_path, sim_name, sim_pins):
    from skidl.sim import simulate

    if not os.path.exists(lib_path):
        print(f"RESULT {label} MISSING_FILE {lib_path}")
        return None
    last_err = None
    for compat in COMPAT_MODES:
        try:
            ckt = _opamp_follower(
                f"spike_{label}", lib_path, sim_name, sim_pins, vin=1.0)
            sim = simulate(ckt, compat=compat)
            result = sim.operating_point()
            tier, name, source = _prov(sim, "U1")
            vout = result.get_voltage("VOUT")
            ok = tier == "vendor_lib" and abs(vout - 1.0) < 0.1
            print(f"RESULT {label} compat={compat} tier={tier} model={name} "
                  f"source={source} vout={vout:+.4f}V (expect ~+1.0) "
                  f"{'PASS' if ok else 'FAIL'}")
            return ok
        except Exception as e:  # try next compat mode
            last_err = f"{type(e).__name__}: {str(e)[:70]}"
            print(f"RESULT {label} compat={compat} ERROR {last_err}")
    print(f"RESULT {label} FAIL (no compat mode loaded it; last={last_err})")
    return False


def run_diode(label, lib_path, model_name):
    from skidl.sim import simulate

    if not os.path.exists(lib_path):
        print(f"RESULT {label} MISSING_FILE {lib_path}")
        return None
    for compat in COMPAT_MODES:
        try:
            ckt = _diode_forward(f"spike_{label}", lib_path, model_name)
            sim = simulate(ckt, compat=compat)
            result = sim.operating_point()
            tier, name, source = _prov(sim, "D1")
            vd = result.get_voltage("VD")  # cathode at gnd -> Vf = V(VD)
            ok = tier == "vendor_lib" and 0.4 <= vd <= 0.9
            print(f"RESULT {label} compat={compat} tier={tier} model={name} "
                  f"source={source} vf={vd:+.4f}V (expect 0.4..0.9) "
                  f"{'PASS' if ok else 'FAIL'}")
            return ok
        except Exception as e:
            print(f"RESULT {label} compat={compat} ERROR "
                  f"{type(e).__name__}: {str(e)[:70]}")
    print(f"RESULT {label} FAIL (no compat mode loaded it)")
    return False


def main() -> int:
    setup_kicad10()
    print(f"CORPUS_ROOT {ROOT}")
    print(f"CORPUS_PRESENT {os.path.isdir(MODELS)}")

    results = []
    try:
        # TL072 subckt: nodes [1 2 3 4 5] = [+in -in V+ V- out]
        results.append(run_opamp(
            "tl072_subckt", TL072_LIB, "TL072", "4=1 3=2 8=3 5=4 7=5"))
        # D1N914 bare .model diode.
        results.append(run_diode("d1n914_model", DIODE_LIB, "D1N914"))
        # MAX402 .fam subckt: nodes [1 2 99 50 97] = [+in -in V+ V- out]
        results.append(run_opamp(
            "max402_fam", MAX402_LIB, "MAX402", "4=1 3=2 8=99 5=50 7=97"))
    except Exception as e:  # backend/corpus problems -> exit 2
        import traceback
        traceback.print_exc()
        print(f"SIMULATION_UNAVAILABLE: {e}")
        return 2

    considered = [r for r in results if r is not None]
    if not considered:
        print("OVERALL: NO_MODELS (corpus missing?)")
        return 2
    ok = all(considered)
    print(f"SUMMARY {sum(1 for r in considered if r)}/{len(considered)} passed")
    print("OVERALL: PASS" if ok else "OVERALL: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
