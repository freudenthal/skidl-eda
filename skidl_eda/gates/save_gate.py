# -*- coding: utf-8 -*-
"""KiCad save-crash gate.

KiCad can *load* a ``.kicad_sch`` fine yet **segfault on save**, truncating the
file, on defects ``kicad-cli`` ERC/netlist/pdf all tolerate -- a GUI-only failure
mode. This gate reproduces it headlessly: copy the file, run
``kicad-cli sch upgrade --force`` (KiCad's own writer) on the copy, and assert the
write round-trips.

Three traps make the naive "exit != 139" check a false gate:
  1. **Pipe rc** -- reading rc through a pipe reports the pipe tail's rc; we read
     ``returncode`` directly (list argv, no shell).
  2. **0-byte truncation with rc=0** -- assert ``size > 0`` too.
  3. **MSYS path** -- handing the Windows ``kicad-cli.exe`` an MSYS ``/tmp/...``
     path silently writes 0 bytes rc=0; pass native paths (size assertion catches
     it regardless).

Gate = **rc == 0 AND filesize > 0 AND the upgraded file reloads**
(``kicad-cli sch erc`` rc in {0, 5}). Ported from circuit-synth
``tests/e2e/kicad_gate_utils`` / the skidl fork's ``tests/utils/kicad_gate``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from .kicad_cli import KicadCliUnavailable, find_kicad_cli  # noqa: F401


def assert_kicad_save_ok(sch_path, kicad_cli: Optional[str] = None) -> None:
    """Assert KiCad can re-save ``sch_path`` without crashing.

    Raises ``AssertionError`` on a save-crash class, or ``KicadCliUnavailable``
    if kicad-cli is not installed (callers decide whether to skip).
    """
    sch_path = Path(sch_path)
    cli = find_kicad_cli(kicad_cli)
    if not cli:
        raise KicadCliUnavailable("kicad-cli (KiCad 10) not available")

    copy = sch_path.with_name(sch_path.stem + "_savecopy.kicad_sch")
    shutil.copyfile(sch_path, copy)

    up = subprocess.run(
        [cli, "sch", "upgrade", "--force", str(copy)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert up.returncode == 0, (
        f"kicad-cli sch upgrade returned {up.returncode} on {copy.name} "
        f"(139 = segfault -> KiCad save crash). stderr: {up.stderr.strip()}"
    )
    size = copy.stat().st_size
    assert size > 0, (
        f"{copy.name} is 0 bytes after upgrade (a crash truncated it, or an MSYS "
        f"path trap); rc was {up.returncode}"
    )
    erc = subprocess.run(
        [cli, "sch", "erc", "-o", str(copy.with_suffix(".rpt")), str(copy)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert erc.returncode in (0, 5), (
        f"upgraded {copy.name} failed to reload in kicad-cli sch erc "
        f"(rc={erc.returncode}). stderr: {erc.stderr.strip()}"
    )


def check_save_ok(sch_path, kicad_cli: Optional[str] = None) -> Dict[str, Any]:
    """Non-raising variant returning a result dict for the loop.

    Keys: ``ok`` (bool), ``skipped`` (bool -- kicad-cli unavailable),
    ``error`` (str|None).
    """
    try:
        assert_kicad_save_ok(sch_path, kicad_cli)
        return {"ok": True, "skipped": False, "error": None}
    except KicadCliUnavailable as e:
        return {"ok": False, "skipped": True, "error": str(e)}
    except AssertionError as e:
        return {"ok": False, "skipped": False, "error": str(e)}
