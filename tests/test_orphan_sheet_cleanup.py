# -*- coding: utf-8 -*-
"""Orphan child-sheet cleanup after a topology rename (DPSG WS4 / E2E B6).

``generate`` deletes (or, opted out, lists) any child ``.kicad_sch`` the freshly
rendered top no longer references -- keyed on ``Sheetfile`` references, so it is
part-/name-agnostic.
"""

from pathlib import Path

from skidl_eda.project import _prune_orphan_sheets, _referenced_sheet_files


def _write_top(directory: Path, name: str, children):
    refs = "\n".join(
        f'  (sheet (property "Sheetname" "{Path(c).stem}")\n'
        f'    (property "Sheetfile" "{c}"))'
        for c in children
    )
    (directory / name).write_text(
        f'(kicad_sch\n{refs}\n)\n', encoding="utf-8"
    )


def test_referenced_sheet_files_bfs(tmp_path):
    _write_top(tmp_path, "Top.kicad_sch", ["Top_a.kicad_sch", "Top_b.kicad_sch"])
    # a nested child that references a grandchild
    _write_top(tmp_path, "Top_a.kicad_sch", ["Top_c.kicad_sch"])
    (tmp_path / "Top_b.kicad_sch").write_text("(kicad_sch)", encoding="utf-8")
    (tmp_path / "Top_c.kicad_sch").write_text("(kicad_sch)", encoding="utf-8")
    refs = _referenced_sheet_files(tmp_path / "Top.kicad_sch")
    assert refs == {"Top_a.kicad_sch", "Top_b.kicad_sch", "Top_c.kicad_sch"}


def test_prune_removes_only_orphans(tmp_path):
    _write_top(tmp_path, "Top.kicad_sch", ["Top_keep.kicad_sch"])
    (tmp_path / "Top_keep.kicad_sch").write_text("(kicad_sch)", encoding="utf-8")
    # a stale child from a prior run, no longer referenced
    (tmp_path / "Top_orphan.kicad_sch").write_text("(kicad_sch)", encoding="utf-8")

    res = _prune_orphan_sheets(tmp_path, tmp_path / "Top.kicad_sch", clean=True)
    assert res["found"] == ["Top_orphan.kicad_sch"]
    assert res["removed"] == ["Top_orphan.kicad_sch"]
    assert not (tmp_path / "Top_orphan.kicad_sch").exists()
    assert (tmp_path / "Top_keep.kicad_sch").exists()  # referenced child kept
    assert (tmp_path / "Top.kicad_sch").exists()  # top never touched


def test_prune_opt_out_keeps_but_lists(tmp_path):
    _write_top(tmp_path, "Top.kicad_sch", ["Top_keep.kicad_sch"])
    (tmp_path / "Top_keep.kicad_sch").write_text("(kicad_sch)", encoding="utf-8")
    (tmp_path / "Top_orphan.kicad_sch").write_text("(kicad_sch)", encoding="utf-8")

    res = _prune_orphan_sheets(tmp_path, tmp_path / "Top.kicad_sch", clean=False)
    assert res["found"] == ["Top_orphan.kicad_sch"]
    assert res["removed"] == []
    assert (tmp_path / "Top_orphan.kicad_sch").exists()  # kept when opted out
