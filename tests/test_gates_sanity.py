# -*- coding: utf-8 -*-
"""Tests for the design-sanity gate (shorted 2-pin passive, unconnected part,
merged power rails, placeholder value).

Motivating regression: the HV precision supply's R8 gate stopper had both pins on
net ``GATE_P`` (a series element bypassed by a direct connection). ERC was clean
and the drawing matched the netlist, so nothing caught it. See
``kicadprojects/hv_precision_supply/design_log.md`` Iteration 2. The first fixture
below IS that guard.
"""

from skidl_eda.gates.sanity import check_shorted_components, describe_finding


def _write(tmp_path, text, name="n.net"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# --- R8-bug shape: op-amp out, R8.1, R8.2, Q3.1 all on GATE_P ---------------
# (both R8 pins share GATE_P => the stopper is shorted out)
_R8_BUG = """
(export (version "E")
  (components
    (comp (ref "U1") (value "OPAMP") (footprint "F:SO8"))
    (comp (ref "R8") (value "100") (footprint "R:0603"))
    (comp (ref "Q3") (value "IRF740") (footprint "F:TO220")))
  (nets
    (net (code "1") (name "GATE_P")
      (node (ref "U1") (pin "1"))
      (node (ref "R8") (pin "1"))
      (node (ref "R8") (pin "2"))
      (node (ref "Q3") (pin "1")))
    (net (code "2") (name "VOUT")
      (node (ref "Q3") (pin "2"))
      (node (ref "U1") (pin "3")))))
"""

# The fixed shape: R8 in series via AMP_OUT.
_R8_FIXED = """
(export (version "E")
  (components
    (comp (ref "U1") (value "OPAMP") (footprint "F:SO8"))
    (comp (ref "R8") (value "100") (footprint "R:0603"))
    (comp (ref "Q3") (value "IRF740") (footprint "F:TO220")))
  (nets
    (net (code "1") (name "AMP_OUT")
      (node (ref "U1") (pin "1"))
      (node (ref "R8") (pin "1")))
    (net (code "2") (name "GATE_P")
      (node (ref "R8") (pin "2"))
      (node (ref "Q3") (pin "1")))
    (net (code "3") (name "VOUT")
      (node (ref "Q3") (pin "2"))
      (node (ref "U1") (pin "3")))))
"""


def test_shorted_passive_warns(tmp_path):
    res = check_shorted_components(_write(tmp_path, _R8_BUG))
    assert res["ok"] is False
    shorted = [w for w in res["warnings"] if w["check"] == "shorted_component"]
    assert len(shorted) == 1
    f = shorted[0]
    assert f["ref"] == "R8"
    assert f["net"] == "GATE_P"
    assert f["value"] == "100"
    assert "shorted" in describe_finding(f)


def test_fixed_topology_no_short(tmp_path):
    res = check_shorted_components(_write(tmp_path, _R8_FIXED))
    shorted = [w for w in res["warnings"] if w["check"] == "shorted_component"]
    assert shorted == []


# --- 2-pin connector both pins same net -> info, not warning ----------------
_CONNECTOR_SHORT = """
(export (version "E")
  (components
    (comp (ref "J1") (value "CONN") (footprint "F:HDR2"))
    (comp (ref "R1") (value "1k") (footprint "R:0603")))
  (nets
    (net (code "1") (name "PWR")
      (node (ref "J1") (pin "1"))
      (node (ref "J1") (pin "2"))
      (node (ref "R1") (pin "1")))
    (net (code "2") (name "GND")
      (node (ref "R1") (pin "2")))))
"""


def test_connector_short_is_info_not_warning(tmp_path):
    res = check_shorted_components(_write(tmp_path, _CONNECTOR_SHORT))
    j1_info = [i for i in res["info"] if i.get("ref") == "J1"]
    assert len(j1_info) == 1
    assert j1_info[0]["check"] == "shorted_component"
    # R1 is NOT unconnected here (it shares PWR with J1); no shorted warning for it.
    assert all(w.get("ref") != "J1" for w in res["warnings"])


# --- 3-pin part (pot) with 2 pins on one net -> not exactly-2-pin, no finding
_POT_TWO_ON_ONE = """
(export (version "E")
  (components
    (comp (ref "RV1") (value "10k") (footprint "F:POT"))
    (comp (ref "R1") (value "1k") (footprint "R:0603")))
  (nets
    (net (code "1") (name "TOP")
      (node (ref "RV1") (pin "1"))
      (node (ref "RV1") (pin "2"))
      (node (ref "R1") (pin "1")))
    (net (code "2") (name "WIPE")
      (node (ref "RV1") (pin "3"))
      (node (ref "R1") (pin "2")))))
"""


def test_three_pin_part_not_flagged_as_short(tmp_path):
    res = check_shorted_components(_write(tmp_path, _POT_TWO_ON_ONE))
    assert all(f.get("ref") != "RV1" for f in res["warnings"] + res["info"]
               if f.get("check") == "shorted_component")


# --- fully-unconnected component -------------------------------------------
_UNCONNECTED = """
(export (version "E")
  (components
    (comp (ref "R1") (value "1k") (footprint "R:0603"))
    (comp (ref "R2") (value "2k") (footprint "R:0603"))
    (comp (ref "C9") (value "100n") (footprint "C:0603")))
  (nets
    (net (code "1") (name "A")
      (node (ref "R1") (pin "1"))
      (node (ref "R2") (pin "1")))
    (net (code "2") (name "B")
      (node (ref "R1") (pin "2"))
      (node (ref "R2") (pin "2")))
    (net (code "3") (name "unconnected-(C9-Pad1)")
      (node (ref "C9") (pin "1")))
    (net (code "4") (name "unconnected-(C9-Pad2)")
      (node (ref "C9") (pin "2")))))
"""


def test_unconnected_component_warns(tmp_path):
    res = check_shorted_components(_write(tmp_path, _UNCONNECTED))
    unc = [w for w in res["warnings"] if w["check"] == "unconnected_component"]
    # Only C9 (both pins in single-pin nets) is unconnected; R1/R2 share nets.
    assert len(unc) == 1
    assert unc[0]["ref"] == "C9"
    assert "never connected" in describe_finding(unc[0])


# --- merged power rails ------------------------------------------------------
_MERGED_RAILS = """
(export (version "E")
  (components
    (comp (ref "#PWR01") (value "GND") (footprint ""))
    (comp (ref "#PWR02") (value "VIN") (footprint ""))
    (comp (ref "R1") (value "1k") (footprint "R:0603")))
  (nets
    (net (code "1") (name "MERGED")
      (node (ref "#PWR01") (pin "1"))
      (node (ref "#PWR02") (pin "1"))
      (node (ref "R1") (pin "1")))
    (net (code "2") (name "OUT")
      (node (ref "R1") (pin "2")))))
"""


def test_merged_power_rails_warns(tmp_path):
    res = check_shorted_components(_write(tmp_path, _MERGED_RAILS))
    merged = [w for w in res["warnings"] if w["check"] == "merged_power_rails"]
    assert len(merged) == 1
    assert merged[0]["rails"] == ["GND", "VIN"]
    assert "merged" in describe_finding(merged[0])


# --- placeholder value (info only) ------------------------------------------
_PLACEHOLDER = """
(export (version "E")
  (components
    (comp (ref "R1") (value "") (footprint "R:0603"))
    (comp (ref "C1") (value "100n") (footprint "C:0603")))
  (nets
    (net (code "1") (name "A")
      (node (ref "R1") (pin "1"))
      (node (ref "C1") (pin "1")))
    (net (code "2") (name "B")
      (node (ref "R1") (pin "2"))
      (node (ref "C1") (pin "2")))))
"""


def test_placeholder_value_is_info(tmp_path):
    res = check_shorted_components(_write(tmp_path, _PLACEHOLDER))
    ph = [i for i in res["info"] if i.get("check") == "placeholder_value"]
    assert len(ph) == 1
    assert ph[0]["ref"] == "R1"
    # placeholder is info, never a warning.
    assert all(w.get("check") != "placeholder_value" for w in res["warnings"])
