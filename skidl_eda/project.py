# -*- coding: utf-8 -*-
"""Orchestration entry -- ``skidl_eda.project.generate``.

The ``generate_kicad_project`` equivalent for the skidl stack: take a built
skidl :class:`~skidl.Circuit`, render it with the **fork's KiCad-10 renderer**,
scaffold a KiCad-openable project directory (``.kicad_pro`` + ``.kicad_sch`` +
``.net``), run the file-level gate pipeline (ERC read-only, save-crash gate,
footprint check), export BOM/PDF, and return the loop's result dict.

skidl has no project-file writer of its own (``gen_schematic`` emits only the
``.kicad_sch`` sheets), so the ``.kicad_pro`` scaffold lives here -- it is what
makes the output openable in KiCad and is the piece circuit-synth's
``generate_kicad_project`` provided that the bare skidl renderer does not.

The caller is responsible for binding KiCad-10 libraries
(:func:`skidl_eda.setup_kicad10`) and building the circuit BEFORE calling
:func:`generate`; this function only renders + gates an already-built circuit.

ERC runs by default with the net-aware **PWR_FLAG autofix** (``erc_autofix``);
it is report-only unless the caller sets ``erc_must_be_clean``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .export.bom import export_bom_csv
from .export.pdf import export_pdf
from .gates.erc import AUTOFIX_TYPES, ErcUnavailable, erc_gate, run_erc
from .gates.footprint_check import check_footprints
from .gates.save_gate import check_save_ok

logger = logging.getLogger(__name__)


# A minimal-but-complete KiCad-10 project skeleton (mirrors circuit-synth's
# known-good blank template). ``meta.filename`` is stamped per project so the
# file opens without KiCad rewriting it.
_BLANK_PRO: Dict[str, Any] = {
    "board": {
        "3dviewports": [],
        "design_settings": {
            "defaults": {},
            "diff_pair_dimensions": [],
            "drc_exclusions": [],
            "rules": {},
            "track_widths": [],
            "via_dimensions": [],
        },
        "ipc2581": {"dist": "", "distpn": "", "internal_id": "", "mfg": "", "mpn": ""},
        "layer_presets": [],
        "viewports": [],
    },
    "boards": [],
    "cvpcb": {"equivalence_files": []},
    "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
    "meta": {"filename": "blank.kicad_pro", "version": 1},
    "net_settings": {
        "classes": [
            {
                "bus_width": 12,
                "clearance": 0.2,
                "diff_pair_gap": 0.25,
                "diff_pair_via_gap": 0.25,
                "diff_pair_width": 0.2,
                "line_style": 0,
                "microvia_diameter": 0.3,
                "microvia_drill": 0.1,
                "name": "Default",
                "pcb_color": "rgba(0, 0, 0, 0.000)",
                "schematic_color": "rgba(0, 0, 0, 0.000)",
                "track_width": 0.2,
                "via_diameter": 0.6,
                "via_drill": 0.3,
                "wire_width": 6,
            }
        ],
        "meta": {"version": 3},
        "net_colors": None,
        "netclass_assignments": None,
        "netclass_patterns": [],
    },
    "pcbnew": {
        "last_paths": {
            "gencad": "",
            "idf": "",
            "netlist": "",
            "plot": "",
            "pos_files": "",
            "specctra_dsn": "",
            "step": "",
            "svg": "",
            "vrml": "",
        },
        "page_layout_descr_file": "",
    },
    "schematic": {"legacy_lib_dir": "", "legacy_lib_list": []},
    "sheets": [],
    "text_variables": {},
}


def _write_project_file(project_file: Path) -> None:
    """Write a minimal KiCad-10 ``.kicad_pro`` with the right ``meta.filename``."""
    data = json.loads(json.dumps(_BLANK_PRO))  # deep copy
    data["meta"]["filename"] = project_file.name
    project_file.write_text(json.dumps(data, indent=2), encoding="utf-8")


def generate(
    circuit,
    project_name: str,
    output_dir=".",
    *,
    top_name: Optional[str] = None,
    run_erc_gate: bool = True,
    erc_autofix: bool = True,
    run_save_gate: bool = True,
    run_footprint_check: bool = True,
    run_drawing_connectivity: bool = True,
    drawing_must_match: bool = False,
    export_bom: bool = True,
    bom_fields: Optional[str] = None,
    export_pdf_schematic: bool = True,
    erc_must_be_clean: bool = False,
    evaluate: bool = True,
    reference_netlist: Optional[str] = None,
    verify_models: bool = False,
    pcb: bool = False,
    pcb_output: Optional[str] = None,
    fp_lib_dirs: Optional[list] = None,
    renderer_options: Optional[Dict[str, Any]] = None,
    kicad_cli: Optional[str] = None,
) -> Dict[str, Any]:
    """Render + scaffold + gate a built skidl circuit into a KiCad project.

    Args:
        circuit: a built skidl :class:`~skidl.Circuit` (libraries already bound
            via :func:`skidl_eda.setup_kicad10`).
        project_name: base name for the project dir and its files.
        output_dir: parent directory; the project is written to
            ``<output_dir>/<project_name>/``.
        top_name: top-sheet base name (defaults to ``project_name`` so the
            ``.kicad_pro`` and top ``.kicad_sch`` share a base name -- KiCad
            requires this to open the project).
        run_erc_gate: run the ERC gate (report errors/warnings).
        erc_autofix: when running ERC, apply the net-aware PWR_FLAG autofix
            (iterate with revert-on-regression, editing the schematic in place)
            before reporting; needs kicad-sch-api (the ``hitl`` extra) and
            degrades to a read-only run if absent. Default True.
        run_save_gate: run the KiCad save-crash gate.
        run_footprint_check: warn on footprint ids absent from KiCad libraries.
        run_drawing_connectivity: export a netlist from the rendered schematic and
            structurally compare it to the logical ``.net`` (catches a drawing that
            doesn't connect a pin the circuit does; B3). Report-only by default --
            sets ``steps["drawing_connectivity"]["equiv"]``.
        drawing_must_match: if True, a drawing-vs-netlist mismatch (equiv=False)
            fails the project. Off by default (report-only).
        export_bom: export a BOM CSV.
        bom_fields: kicad-cli ``--fields`` string; ``None`` uses
            ``DEFAULT_BOM_FIELDS`` (adds MPN/Manufacturer/Distributor columns).
        export_pdf_schematic: export a schematic PDF.
        erc_must_be_clean: if True, remaining ERC *errors* (after any autofix)
            fail the project. Off by default: even with the PWR_FLAG autofix,
            deliverable designs legitimately carry design-level ERC errors
            (e.g. unused pins) the autofix must not touch. ERC is reported either
            way; the per-step ``autofixes_applied`` records what the autofix did.
        evaluate: run the aggregate structural quality metric over the generated
            netlist (grade + per-check breakdown); report-only, never gates.
        reference_netlist: an optional golden ``.net`` to score against (the
            regression oracle) — appears under ``result["evaluation"]["oracle"]``.
        verify_models: opt-in, report-only. Smoke-test every part whose SPICE
            model resolves in the configured KiCad-Spice-Library index (confirm
            ngspice loads it), recording results under
            ``result["model_verification"]``. Off by default; never gates.
            Degrades to a ``skipped`` step when the corpus isn't available.
        pcb: run the gated skidl-layout PCB step (plan a placement + write a
            scored ``.kicad_pcb`` into the project dir). Off by default and
            **report-only** (a poor layout score never fails the project);
            degrades to a ``skipped`` step if skidl-layout is not installed.
        pcb_output: explicit ``.kicad_pcb`` path (defaults to
            ``<project_dir>/<project_name>.kicad_pcb``).
        fp_lib_dirs: footprint-library roots for the PCB step (auto-discovered
            from a standard KiCad install when omitted).
        renderer_options: extra kwargs forwarded to ``generate_schematic`` that
            override the defaults. The default render path is
            ``{"seed_placement": True, "deconflict_stubs": True,
            "hierarchical_sheet_pins": True, "power_stubs": True}`` --
            constructive placement + a deconflicted on-grid stub + local-label
            closure for every pin, so drawing == netlist by construction and
            parts never collide. Pass ``{"deconflict_stubs": False}`` for a pure
            A* wired render (more hand-drawn-looking, but can leave a net split
            on a dense sheet; the self-heal then retries with deconflict).
        kicad_cli: explicit path to ``kicad-cli`` (else auto-discovered).

    Returns:
        The loop result dict -- see module docstring. ``ok`` is True iff every
        generation step succeeded and the save-crash gate did not hard-fail
        (the openability contract). ERC is report-only unless ``erc_must_be_clean``
        is set; footprint warnings and skipped gates never fail the project.
        ``erc_clean`` is a separate top-level bool for visibility.
    """
    from skidl import KICAD10

    top_name = top_name or project_name
    out_root = Path(output_dir)
    project_dir = out_root / project_name
    project_dir.mkdir(parents=True, exist_ok=True)

    netlist_file = project_dir / f"{project_name}.net"
    schematic_file = project_dir / f"{top_name}.kicad_sch"
    project_file = project_dir / f"{project_name}.kicad_pro"

    result: Dict[str, Any] = {
        "ok": False,
        "project_dir": str(project_dir),
        "project_file": str(project_file),
        "schematic_file": str(schematic_file),
        "netlist_file": str(netlist_file),
        "steps": {},
    }
    steps = result["steps"]

    def _skidl_log_errors() -> int:
        """SKiDL's own error-record count for the phase just run (R4).

        Reads the fork logger's per-phase counters (reset at each phase start)
        so the result dict carries one reconciled number per phase -- these are
        SKiDL *log records*, independent of the kicad-cli ERC gate.
        """
        try:
            from skidl.logger import active_logger

            return int(active_logger.error.count + active_logger.bare_error.count)
        except Exception:  # noqa: BLE001 - never let logging break the loop
            return 0

    # --- 1. netlist ---------------------------------------------------------
    try:
        circuit.generate_netlist(tool=KICAD10, file_=str(netlist_file))
        ok = netlist_file.exists() and netlist_file.stat().st_size > 0
        steps["netlist"] = {
            "ok": ok,
            "file": str(netlist_file),
            "skidl_log_errors": _skidl_log_errors(),
        }
    except Exception as e:  # noqa: BLE001
        logger.error("netlist generation failed: %s", e)
        steps["netlist"] = {"ok": False, "file": str(netlist_file), "error": str(e)}

    # --- 2. schematic (fork KiCad-10 renderer) ------------------------------
    # Default render path: constructive seed placement + deconflicted-stub wiring.
    # `seed_placement` places each part in the direction its connecting pin faces
    # (constructive geometry, central part first); `deconflict_stubs` then gives
    # EVERY remaining pin an on-grid, world-unique stub wire and closes each net
    # with per-connected-component local labels -- so connectivity is COMPLETE by
    # construction (drawing == netlist) and parts never collide, even on a dense
    # sheet. This is the proven Stage-25/25b render (0 off-grid, equivalence PASS).
    # `power_stubs` pulls power symbols one grid step off the pin; `hierarchical_
    # sheet_pins` gives the true KiCad cross-sheet interconnect. Pure A* routing
    # (renderer_options={"deconflict_stubs": False}) yields a more hand-drawn-
    # looking wired sheet but can leave a net split on a dense sheet (the
    # drawing_connectivity gate flags it, and the self-heal retries with
    # deconflict). Everything here is overridable via renderer_options.
    render_opts = {
        "seed_placement": True,
        "auto_stub": False,
        "deconflict_stubs": True,
        "hierarchical_sheet_pins": True,
        "power_stubs": True,
    }
    render_opts.update(renderer_options or {})
    try:
        circuit.generate_schematic(
            tool=KICAD10,
            filepath=str(project_dir),
            top_name=top_name,
            **render_opts,
        )
        ok = schematic_file.exists() and schematic_file.stat().st_size > 0
        steps["schematic"] = {
            "ok": ok,
            "file": str(schematic_file),
            "skidl_log_errors": _skidl_log_errors(),
        }
    except Exception as e:  # noqa: BLE001
        logger.error("schematic generation failed: %s", e)
        steps["schematic"] = {"ok": False, "file": str(schematic_file), "error": str(e)}

    # --- 3. project scaffold (.kicad_pro) -----------------------------------
    try:
        _write_project_file(project_file)
        steps["project"] = {"ok": project_file.exists(), "file": str(project_file)}
    except Exception as e:  # noqa: BLE001
        logger.error("project-file scaffold failed: %s", e)
        steps["project"] = {"ok": False, "file": str(project_file), "error": str(e)}

    gen_ok = all(
        steps.get(k, {}).get("ok") for k in ("netlist", "schematic", "project")
    )

    # --- 4. footprint check (warn-only) -------------------------------------
    if run_footprint_check:
        try:
            parts = list(getattr(circuit, "parts", []) or [])
            n = check_footprints(parts)
            steps["footprint"] = {"ok": True, "warnings": n}
        except Exception as e:  # noqa: BLE001
            logger.warning("footprint check errored: %s", e)
            steps["footprint"] = {"ok": True, "warnings": 0, "error": str(e)}

    # Gate helpers (steps 5/6/6b) -- defined as closures so the auto_stub
    # self-heal below can re-run them on a re-rendered schematic.
    def _do_erc():
        """Run the ERC gate on the current schematic -> (erc_clean, hard_fail)."""
        if not (run_erc_gate and steps.get("schematic", {}).get("ok")):
            return None, False
        try:
            if erc_autofix:
                report = erc_gate(schematic_file, kicad_cli_path=kicad_cli)
            else:
                report = run_erc(schematic_file, kicad_cli_path=kicad_cli)
            # Autofixable errors remaining vs. genuine design-level errors.
            autofixable_errs = sum(
                1
                for v in report.violations
                if v.severity == "error" and v.type in AUTOFIX_TYPES
            )
            non_autofixable_errs = report.error_count - autofixable_errs
            steps["erc"] = {
                "ok": report.error_count == 0,
                "skipped": False,
                "errors": report.error_count,
                "warnings": report.warning_count,
                "autofixable_errors": autofixable_errs,
                "non_autofixable_errors": non_autofixable_errs,
                "autofixes_applied": report.autofixes_applied,
                "summary": report.summary(),
            }
            # Only gate when the caller demands a clean ERC.
            return report.error_count == 0, (
                erc_must_be_clean and report.error_count > 0
            )
        except ErcUnavailable as e:
            steps["erc"] = {"ok": True, "skipped": True, "error": str(e)}
            return None, False
        except Exception as e:  # noqa: BLE001
            logger.error("ERC gate errored: %s", e)
            steps["erc"] = {"ok": False, "skipped": False, "error": str(e)}
            return None, erc_must_be_clean

    def _do_save():
        """Run the save-crash gate on the current schematic -> hard_fail bool."""
        if not (run_save_gate and steps.get("schematic", {}).get("ok")):
            return False
        res = check_save_ok(schematic_file, kicad_cli)
        steps["save_gate"] = res
        return not res["ok"] and not res["skipped"]

    def _do_drawing():
        """Run the drawing-connectivity gate -> (dc_dict_or_None, hard_fail)."""
        if not (
            run_drawing_connectivity
            and steps.get("schematic", {}).get("ok")
            and steps.get("netlist", {}).get("ok")
        ):
            return None, False
        try:
            from .gates.drawing_connectivity import check_drawing_connectivity

            dc = check_drawing_connectivity(
                schematic_file, netlist_file, kicad_cli=kicad_cli
            )
            steps["drawing_connectivity"] = dc
            return dc, (drawing_must_match and dc.get("equiv") is False)
        except Exception as e:  # noqa: BLE001 - never break the loop
            logger.warning("drawing connectivity gate errored: %s", e)
            steps["drawing_connectivity"] = {
                "ok": False,
                "skipped": False,
                "equiv": None,
                "error": str(e),
            }
            return None, False

    # --- 5. ERC gate --- 6. save gate --- 6b. drawing connectivity ----------
    erc_clean, erc_hard_fail = _do_erc()
    save_hard_fail = _do_save()
    dc, drawing_hard_fail = _do_drawing()

    # --- 6c. deconflict-stub self-heal (C6) ---------------------------------
    # A render whose wiring leaves drawing != netlist (drawing_connectivity
    # DIVERGES) while result["ok"] stays True would ship a visually wrong sheet.
    # The default already uses deconflict_stubs (connectivity complete by
    # construction), so this fires only when a caller turned it OFF (e.g. a pure
    # A* wired render) and the sheet then diverged: re-render once with
    # deconflict_stubs=True (stub every pin + local-label closure -- the robust
    # connectivity path, NOT auto_stub, which we measured can add part collisions
    # and still diverge). A diverged drawing is a correctness bug (drawing !=
    # netlist), so it heals even when the caller opted into pure A*.
    if (
        dc is not None
        and dc.get("equiv") is False
        and not render_opts.get("deconflict_stubs")
    ):
        logger.info(
            "drawing_connectivity diverged; retrying render with deconflict_stubs=True"
        )
        try:
            retry_opts = dict(render_opts)
            retry_opts["deconflict_stubs"] = True
            circuit.generate_schematic(
                tool=KICAD10,
                filepath=str(project_dir),
                top_name=top_name,
                **retry_opts,
            )
            ok = schematic_file.exists() and schematic_file.stat().st_size > 0
            steps["schematic"] = {
                "ok": ok,
                "file": str(schematic_file),
                "skidl_log_errors": _skidl_log_errors(),
                "deconflict_fallback": True,
            }
            # Re-run the gates that depend on the schematic.
            erc_clean, erc_hard_fail = _do_erc()
            save_hard_fail = _do_save()
            dc, drawing_hard_fail = _do_drawing()
            if isinstance(steps.get("drawing_connectivity"), dict):
                steps["drawing_connectivity"]["deconflict_fallback"] = True
                if dc is not None and dc.get("equiv") is False:
                    steps["drawing_connectivity"]["hint"] = (
                        "sheet too dense - split into @subcircuit sheets"
                    )
        except Exception as e:  # noqa: BLE001 - never break the loop
            logger.warning("deconflict-stub self-heal re-render failed: %s", e)

    # gen_ok can change if the self-heal re-render failed; recompute.
    gen_ok = all(
        steps.get(k, {}).get("ok") for k in ("netlist", "schematic", "project")
    )

    # --- 7. exports (skip-tolerant, non-gating) -----------------------------
    if export_bom and steps.get("schematic", {}).get("ok"):
        steps["bom"] = export_bom_csv(
            schematic_file, project_dir / f"{project_name}_bom.csv",
            fields=bom_fields, kicad_cli=kicad_cli
        )
    if export_pdf_schematic and steps.get("schematic", {}).get("ok"):
        steps["pdf"] = export_pdf(
            schematic_file, project_dir / f"{project_name}.pdf", kicad_cli=kicad_cli
        )

    # --- 8. aggregate quality evaluation (report-only, never gates) ---------
    if evaluate and steps.get("netlist", {}).get("ok"):
        try:
            from .evaluation import evaluate_netlist

            report = evaluate_netlist(netlist_file, reference=reference_netlist)
            result["evaluation"] = report
            steps["evaluation"] = {
                "ok": True,
                "grade": report["grade"],
                "oracle_match": (
                    report["oracle"].equivalent if report.get("oracle") else None
                ),
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("evaluation errored: %s", e)
            steps["evaluation"] = {"ok": True, "grade": None, "error": str(e)}

    # --- 8b. vendor-model verification (opt-in, report-only, never gates) ---
    if verify_models:
        try:
            from .sourcing.spice_library import verify_circuit_models

            mv = verify_circuit_models(circuit)
            result["model_verification"] = mv
            steps["model_verification"] = mv
        except Exception as e:  # noqa: BLE001
            logger.warning("model verification errored: %s", e)
            steps["model_verification"] = {"ok": True, "skipped": True, "error": str(e)}

    # --- 9. gated PCB step (opt-in, report-only, never gates) ---------------
    if pcb:
        try:
            from .layout import LayoutUnavailable, plan_pcb

            pcb_path = pcb_output or str(project_dir / f"{project_name}.kicad_pcb")
            try:
                pcb_res = plan_pcb(circuit, pcb_path, fp_lib_dirs=fp_lib_dirs)
                steps["pcb"] = pcb_res
                result["layout"] = pcb_res
            except LayoutUnavailable as e:
                steps["pcb"] = {"ok": True, "skipped": True, "error": str(e)}
        except Exception as e:  # noqa: BLE001
            logger.warning("PCB step errored: %s", e)
            steps["pcb"] = {"ok": True, "skipped": False, "error": str(e)}

    result["erc_clean"] = erc_clean
    result["ok"] = (
        gen_ok and not erc_hard_fail and not save_hard_fail and not drawing_hard_fail
    )
    return result


def summarize(result: Dict[str, Any]) -> str:
    """One-line-per-step human summary of a :func:`generate` result dict."""
    lines = [
        f"project: {result['project_dir']}  ->  "
        f"{'OK' if result['ok'] else 'FAIL'}"
    ]
    for name, step in result.get("steps", {}).items():
        if step.get("skipped"):
            state = "SKIP"
        elif step.get("ok") or step.get("success"):
            state = "PASS"
        else:
            state = "FAIL"
        extra = ""
        if name == "erc" and not step.get("skipped"):
            afx = step.get("autofixable_errors", 0)
            applied = step.get("autofixes_applied", 0)
            extra = (
                f" ({step.get('errors', 0)} err / {step.get('warnings', 0)} warn"
                + (f", {applied} PWR_FLAG fixed" if applied else "")
                + (f", {afx} autofixable left" if afx else "")
                + ")"
            )
            state = "WARN" if step.get("errors") else state  # report-only
        elif name == "drawing_connectivity" and not step.get("skipped"):
            equiv = step.get("equiv")
            ndiff = len(step.get("messages") or [])
            fb = (" deconflict fallback" if step.get("deconflict_fallback")
                  else " auto_stub fallback" if step.get("auto_stub_fallback")
                  else "")
            if equiv is True:
                extra = f" (matches netlist;{fb})" if fb else " (matches netlist)"
            elif equiv is False:
                extra = f" (DIVERGES: {ndiff} diff{fb})"
                hint = step.get("hint")
                if hint:
                    extra += f" -- {hint}"
                state = "WARN"  # report-only unless drawing_must_match
            else:
                extra = ""
        elif name == "footprint":
            extra = f" ({step.get('warnings', 0)} warn)"
        elif name == "bom" and step.get("success"):
            extra = f" ({step.get('component_count', 0)} parts)"
        elif name == "evaluation":
            g = step.get("grade")
            om = step.get("oracle_match")
            extra = (
                (f" (grade {g}/100" if g is not None else " (grade n/a")
                + (f", oracle {'MATCH' if om else 'DRIFT'}" if om is not None else "")
                + ")"
            )
        elif name == "model_verification" and not step.get("skipped"):
            nfail = len(step.get("failed") or [])
            extra = (
                f" ({step.get('vendor_models', 0)} vendor models"
                + (f", {step.get('explicit_libraries', 0)} pinned"
                   if step.get("explicit_libraries") else "")
                + (f", {nfail} FAILED-TO-LOAD" if nfail else "")
                + ")"
            )
            state = "WARN" if nfail else state  # report-only
        elif name == "pcb" and not step.get("skipped"):
            sc = step.get("score")
            extra = (
                (f" (score {sc:.0f}/100" if isinstance(sc, (int, float)) else " (")
                + f", {step.get('overlaps', 0)} overlap"
                + (", pcb written" if step.get("pcb_written") else ", no pcb")
                + ")"
            )
        lines.append(f"  {name:10} {state}{extra}")
    return "\n".join(lines)
