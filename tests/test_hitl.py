# -*- coding: utf-8 -*-
"""Phase-6 HITL regeneration tests: ``skidl_eda.hitl.regenerate``.

The result-object shape + the codegen-absent degrade path run without KiCad.
The full loop (generate a canary project -> edit its schematic via kicad-sch-api
-> regenerate skidl source -> round-trip equivalence PASS) runs only when the
KiCad-10 libraries, ``kicad-cli``, and the ``skidl-codegen`` peer package are all
present.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CANARY = os.path.join(ROOT, "canaries", "sipm_tia")
if CANARY not in sys.path:
    sys.path.insert(0, CANARY)

from skidl_eda import hitl  # noqa: E402


def _codegen_or_skip():
    try:
        import skidl_codegen  # noqa: F401
    except ImportError:
        pytest.skip("skidl-codegen peer package not installed")


def _kicad10_or_skip():
    from skidl_eda import setup_kicad10

    try:
        setup_kicad10()
    except RuntimeError:
        pytest.skip("no real KiCad-10 symbol library on this host")
    from skidl import Part

    try:
        Part("Amplifier_Operational", "ADA4817-1ACP")
    except Exception:  # noqa: BLE001
        pytest.skip("ADA4817-1ACP not in installed KiCad-10 libraries")
    setup_kicad10()


def _kicad_cli_or_skip():
    from skidl_eda.gates import find_kicad_cli

    if not find_kicad_cli():
        pytest.skip("kicad-cli not installed")


# --------------------------------------------------------------------------- #
# Unit: degrade path + result object (no KiCad needed)
# --------------------------------------------------------------------------- #


def test_codegen_unavailable_is_raised(monkeypatch):
    """When skidl-codegen can't be imported, regenerate() raises the typed error
    (callers degrade the same way as ErcUnavailable / LayoutUnavailable)."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "skidl_codegen" or name.startswith("skidl_codegen."):
            raise ImportError("blocked for test")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(hitl.CodegenUnavailable):
        hitl.regenerate("nonexistent.kicad_sch", verify=False)


def test_regenresult_bool_and_summary():
    r = hitl.RegenResult(
        ok=True,
        equivalent=True,
        modules={"main.py": "print('x')"},
        entry="main.py",
        top_func="top",
        flat=True,
    )
    assert bool(r) is True
    assert r.main_source == "print('x')"
    assert "EQUIV" in r.summary() and "flat" in r.summary()

    drift = hitl.RegenResult(
        ok=False, equivalent=False, modules={"main.py": ""}, entry="main.py",
        top_func="top", flat=False,
    )
    assert bool(drift) is False
    assert "DRIFT" in drift.summary() and "hier" in drift.summary()


def test_resolve_source_rejects_unknown(tmp_path):
    with pytest.raises(TypeError):
        hitl._resolve_source(object(), tmp_path)


# --------------------------------------------------------------------------- #
# Integration: full HITL round-trip on the canary
# --------------------------------------------------------------------------- #


def _generate_canary(tmp_path):
    from skidl_eda import project as P
    import sipm_tia_skidl as T

    c = T.sipm_tia()
    P.generate(
        c,
        "SiPM_TIA_HITL",
        output_dir=str(tmp_path),
        run_erc_gate=False,
        run_save_gate=False,
        export_bom=False,
        export_pdf_schematic=False,
        evaluate=False,
    )
    return tmp_path / "SiPM_TIA_HITL" / "SiPM_TIA_HITL.kicad_sch"


def test_regenerate_from_generated_schematic_is_equivalent(tmp_path):
    """generate -> regenerate the generated .kicad_sch -> round-trip EQUIV."""
    _codegen_or_skip()
    _kicad10_or_skip()
    _kicad_cli_or_skip()

    sch = _generate_canary(tmp_path)
    assert sch.exists()

    res = hitl.regenerate(
        str(sch), output_dir=str(tmp_path / "regen"), verify=True
    )
    assert res.ok, res.messages
    assert res.equivalent is True, res.messages
    assert res.modules  # produced at least the entry module
    assert (tmp_path / "regen").is_dir()


def test_edit_via_ksa_then_regenerate_is_equivalent(tmp_path):
    """The Phase-6 gate: edit the canary in KiCad (via kicad-sch-api) ->
    regenerate -> equivalence PASS against the EDITED schematic."""
    _codegen_or_skip()
    _kicad10_or_skip()
    _kicad_cli_or_skip()
    try:
        import kicad_sch_api as ksa
    except ImportError:
        pytest.skip("kicad-sch-api (hitl extra) not installed")

    sch = _generate_canary(tmp_path)

    # --- human edit: bump the feedback resistor value in KiCad ---------------
    schematic = ksa.load_schematic(str(sch))
    edited = False
    for comp in schematic.components:
        if comp.reference == "RF1":
            comp.value = "220k"
            edited = True
    assert edited, "canary RF1 not found to edit"
    schematic.save(str(sch))

    # --- regenerate from the edited schematic + verify equivalence -----------
    res = hitl.regenerate(str(sch), verify=True)
    assert res.ok, res.messages
    assert res.equivalent is True, res.messages
    # the edit survived the round-trip into the regenerated source
    assert "220k" in res.main_source
