#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Find SPICE models in the KiCad-Spice-Library corpus, by name substring.

Prints, for each match, a paste-ready block the LLM (or a human) can drop into a
skidl ``Part``: the model name, defining file, license tier, and the exact
``Sim_*`` kwargs -- including the recovered subckt node order so subckt pins are
never misordered.

    uv run python -m skidl_eda.sourcing.find_spice_model TL072 --type opamp
    uv run python -m skidl_eda.sourcing.find_spice_model 1N4148 --verify
    uv run python -m skidl_eda.sourcing.find_spice_model BC546B --into-store BC546B

Exit 0 = matches found, 2 = none, 3 = corpus not available.
"""

from __future__ import annotations

import argparse
import os
import sys

# --type alias -> (kind filter, device-type filter list). None kind = any.
_TYPE_ALIASES = {
    "diode": (None, ["D"]),
    "led": (None, ["D"]),
    "zener": (None, ["D"]),
    "bjt": (None, ["NPN", "PNP"]),
    "npn": (None, ["NPN"]),
    "pnp": (None, ["PNP"]),
    "mosfet": (None, ["NMOS", "PMOS", "VDMOS"]),
    "nmos": (None, ["NMOS", "VDMOS"]),
    "pmos": (None, ["PMOS"]),
    "jfet": (None, ["NJF", "PJF"]),
    "opamp": ("subckt", None),
    "subckt": ("subckt", None),
    "model": ("model", None),
}

# 5-node subckt: near-universal PSpice op-amp node order.
_OPAMP5_ROLES = ["+in", "-in", "V+", "V-", "out"]


def _role_line(hit) -> str:
    if hit.kind != "subckt" or not hit.nodes:
        return ""
    if len(hit.nodes) == 5:
        pairs = ", ".join(f"{n}={r}" for n, r in zip(hit.nodes, _OPAMP5_ROLES))
        return f"  # subckt nodes (assumed op-amp order): {pairs}"
    return f"  # subckt nodes (order matters): {' '.join(hit.nodes)}"


def _subckt_terminal_lines(hit) -> list:
    """Honest labeling of a subckt's terminals (E2E finding A3).

    The tool knows the node *order* but not the node *identity* (which node is
    Drain/Gate/Source). Echo the raw ``.subckt`` line, warn that identity is
    unknown, and -- only as a clearly-marked heuristic -- surface the near-
    universal IR/Intusoft ``10 20 30`` = D/G/S convention. Never auto-fills
    Sim_Pins from the heuristic.
    """
    if hit.kind != "subckt" or not hit.nodes:
        return []
    lines = [f"  # .subckt {hit.name} {' '.join(hit.nodes)}"]
    if len(hit.nodes) != 5:  # op-amp 5-node order is already surfaced by _role_line
        lines.append(
            "  # node identity (D/G/S ...) is NOT known to the tool -- verify "
            "against the vendor header above")
    if hit.nodes == ["10", "20", "30"]:
        lines.append(
            "  # heuristic: common IR/Intusoft convention is "
            "10=Drain 20=Gate 30=Source (verify)")
    return lines


def _sim_pins_template(hit) -> str:
    """A Sim_Pins template mapping SYMBOL pins -> subckt nodes (user fills the
    symbol pin numbers). Only meaningful for subckts."""
    if hit.kind != "subckt" or not hit.nodes:
        return ""
    if len(hit.nodes) == 5:
        parts = " ".join(f"<pin_{r}>={n}" for n, r in zip(hit.nodes, _OPAMP5_ROLES))
    else:
        parts = " ".join(f"<pin{i+1}>={n}" for i, n in enumerate(hit.nodes))
    return f'  Sim_Pins="{parts}"'


def _print_hit(hit, models_dir, license_tier, verify=None, type_unverified=False):
    rel = hit.path
    try:
        rel = os.path.relpath(hit.path, models_dir)
    except ValueError:
        pass
    kinddt = hit.kind + (f", {hit.device_type}" if hit.device_type else "")
    # A device-type --type filter cannot classify a subckt; say so instead of
    # silently presenting it as a verified match (E2E finding A4).
    if type_unverified and hit.kind == "subckt":
        kinddt = "subckt -- type unverified; --type cannot classify subckts"
    print(f"{hit.name}  ({kinddt})  {rel}   license: {license_tier}")
    # Primary path: name-in-value auto-resolves via the index (needs
    # SKIDL_SPICE_LIB_PATH set). For subckts, auto-resolve also needs Sim_Pins.
    if hit.kind == "model":
        print(f'  value="{hit.name}"   Sim_Compat="psa"'
              "   # auto-resolves via the library index")
    else:
        print(f'  value="{hit.name}"   Sim_Compat="psa"')
        print(_sim_pins_template(hit))
        # The Sim_Pins block is NOT paste-ready: the left-hand <pinN> tokens are
        # placeholders for YOUR symbol's pins (E2E finding M2).
        print("  # ^ Sim_Pins maps YOUR symbol's pins to these subckt nodes: "
              "replace each")
        print("  #   <pinN> with your KiCad symbol's pin NUMBER; keep the node")
        print("  #   values (right of '=') verbatim.")
    # Explicit alternative (always works, no env var needed):
    print(f'  # explicit: Sim_Library="{os.path.abspath(hit.path)}" '
          f'Sim_Name="{hit.name}"')
    rl = _role_line(hit)
    if rl:
        print(rl)
    for ln in _subckt_terminal_lines(hit):
        print(ln)
    if hit.header:
        first = [ln for ln in hit.header.splitlines() if ln.strip("* ").strip()][:5]
        for ln in first:
            print(f"  # {ln.strip()}")
    # Curated reliability note (A2/A3/A6): license tier doesn't predict whether a
    # model loads or is numerically well-behaved -- surface what real runs found.
    from .known_models import reliability_note

    note = reliability_note(hit.name)
    if note:
        print(f"  reliability: {note}")
    if verify is not None:
        v = verify
        if getattr(v, "timed_out", False):
            print(f"  verify: TIMEOUT  [{v.error}]")
        else:
            status = "LOADS" if v.loaded else "FAILS-TO-LOAD"
            conv = " + converges" if v.converged else (" (no .op convergence)" if v.loaded else "")
            extra = f"  [{v.error}]" if v.error and not v.loaded else ""
            print(f"  verify: {status}{conv}{extra}")
    print()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Find SPICE models in the KiCad-Spice-Library corpus.")
    ap.add_argument("query", help="case-insensitive name substring, e.g. TL072")
    ap.add_argument("--type", dest="type_", choices=sorted(_TYPE_ALIASES),
                    help="restrict by device type / kind")
    ap.add_argument("--limit", type=int, default=20, help="max results (default 20)")
    ap.add_argument("--verify", action="store_true",
                    help="smoke-test each shown model against ngspice")
    ap.add_argument("--into-store", metavar="MPN",
                    help="copy the top permissive hit into the model store as MPN "
                         "(so value=MPN resolves with no env var)")
    ap.add_argument("--allow-restricted", action="store_true",
                    help="allow --into-store to copy a vendor-restricted file")
    ap.add_argument("--path", help="explicit corpus path (repo root or Models dir)")
    ap.add_argument("--rebuild", action="store_true",
                    help="force a full re-index of the corpus")
    args = ap.parse_args(argv)

    from skidl_eda.sourcing import spice_library as SL

    # Capture whether the user has the env var set *before* build_catalog aligns
    # it in-process (that setdefault only affects this CLI, not the user's next
    # simulation) -- so the M5 hint reflects the user's persistent state.
    lib_env_was_set = bool(os.environ.get("SKIDL_SPICE_LIB_PATH"))
    models_dir = SL.ensure_library(args.path)
    if models_dir is None:
        return 3
    index = SL.build_catalog(models_dir, rebuild=args.rebuild)
    if index is None:
        return 3

    kind = dts = None
    if args.type_:
        kind, dts = _TYPE_ALIASES[args.type_]
    hits = index.search(args.query, kind=kind, device_types=dts, limit=args.limit)
    if not hits:
        print(f"# no models matching {args.query!r}"
              + (f" (type={args.type_})" if args.type_ else ""), file=sys.stderr)
        return 2

    # If a --type filter drops an exact-name definition entirely (no hit with
    # that exact name survives), say so instead of silently ranking a fuzzy
    # prefix hit as the answer (A4). A same-name duplicate that is merely
    # precedence-shadowed (best-per-name) is not a filter exclusion -- skip it.
    q = args.query.strip().lower()
    exact_alts = index.alternates(args.query)
    if args.type_ and exact_alts and not any(h.name.lower() == q for h in hits):
        alt = exact_alts[0]
        print(f"# note: exact match {alt.name!r} ({alt.kind}) was excluded by "
              f"--type {args.type_} -- drop --type or use --type "
              f"{'subckt' if alt.kind == 'subckt' else 'model'} to see it",
              file=sys.stderr)

    # dts set = a device-type filter that cannot classify subckts -> tag them.
    type_unverified = bool(dts)
    for hit in hits:
        lic = SL.classify_license(hit.path, models_dir)
        # Bounded verify (subprocess + timeout) so a non-converging op-point on a
        # subckt MOSFET can't eat the whole shell timeout (A4).
        verify = SL.smoke_test_bounded(hit.name, models_dir) if args.verify else None
        _print_hit(hit, models_dir, lic, verify, type_unverified=type_unverified)

    # One caveat per run (A3): "LOADS + converges" is a single-device op-point
    # check -- it does not promise transient robustness in a feedback loop.
    if args.verify:
        print(
            "# verify note: 'LOADS + converges' is a single-device op-point check "
            "only; it does NOT guarantee transient robustness with several "
            "instances in a feedback loop (see reliability: notes above).",
            file=sys.stderr,
        )

    if args.into_store:
        top = hits[0]
        lic = SL.classify_license(top.path, models_dir)
        if lic != SL.LICENSE_PERMISSIVE and not args.allow_restricted:
            print(f"# refusing to copy {top.name} into the store: license "
                  f"'{lic}' (re-run with --allow-restricted to override; local "
                  f"simulation via Sim_Library works regardless)", file=sys.stderr)
            return 0
        try:
            from skidl.sim.model_store import get_model_store

            dest = get_model_store().add_model(
                args.into_store, top.path, source="KiCad-Spice-Library",
                model_name=top.name, license=lic)
            print(f"# copied {top.name} -> {dest} (store key '{args.into_store}')",
                  file=sys.stderr)
        except Exception as exc:
            print(f"# could not copy into store: {exc}", file=sys.stderr)

    print(f"# {len(hits)} match(es) in {models_dir}", file=sys.stderr)
    # If SKIDL_SPICE_LIB_PATH is unset in this shell, note how auto-resolve is
    # wired -- WITHOUT the old scare that it "will NOT fire in simulations"
    # (false: skidl_eda.setup_kicad10() auto-defaults the var at build time, so
    # every generate()/skill flow that calls it resolves value="<NAME>" already).
    if not lib_env_was_set:
        print(
            f"note: SKIDL_SPICE_LIB_PATH is unset in this shell. "
            f"skidl_eda.setup_kicad10() auto-defaults it to the corpus at build "
            f"time, so value=\"<NAME>\" auto-resolves in any generate()/skill flow. "
            f"Only a bare skidl script that never calls setup_kicad10() needs to "
            f"set it to {models_dir} (or add the explicit Sim_Library= line above).",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
