# -*- coding: utf-8 -*-
"""Tests for the honest-skip availability facade (no network required)."""

from skidl_eda.sourcing import check_availability
from skidl_eda.sourcing.availability import _coerce_price


def test_unknown_source_is_skipped_not_error():
    rep = check_availability("2N7000", sources=("nope",))
    assert rep.results == []
    assert rep.skipped.get("nope") == "unknown source"


def test_digikey_without_creds_is_honest_skip():
    # No DigiKey client is vendored, so its import fails -> honest skip, never a
    # raise and never fabricated data.
    rep = check_availability("2N7000", sources=("digikey",))
    assert rep.results == []
    assert "digikey" in rep.skipped


def test_coerce_price_parses_and_rejects():
    assert _coerce_price(1.2) == 1.2
    assert _coerce_price("$1.20@100pcs") == 1.2
    assert _coerce_price("n/a") is None
    assert _coerce_price(None) is None
