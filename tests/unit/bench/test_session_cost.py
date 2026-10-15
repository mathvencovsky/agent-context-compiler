"""Tests for the M10 session cost (bench/session_cost.py)."""

from __future__ import annotations

import pytest

from bench.session_cost import reconcile, session_cost_usd

_PRICES = {
    "models": {
        "big": {
            "input_usd_per_1m": 2.0,
            "output_usd_per_1m": 10.0,
            "cache_write_usd_per_1m": 2.5,
            "cache_read_usd_per_1m": 0.2,
        },
        "small": {"input_usd_per_1m": 0.1, "output_usd_per_1m": 0.5},
    }
}


def test_cost_sums_every_usage_field_over_models() -> None:
    usage = {
        "big": {
            "input_tokens": 1_000,
            "cache_creation_input_tokens": 10_000,
            "cache_read_input_tokens": 100_000,
            "output_tokens": 2_000,
        },
        # No cache tokens, so its missing cache prices are not needed.
        "small": {"input_tokens": 50_000, "output_tokens": 1_000},
    }
    # big: 2000 + 25000 + 20000 + 20000 = 67000; small: 5000 + 500 = 5500 (per 1M)
    assert session_cost_usd(usage, _PRICES) == pytest.approx(0.0725)


def test_cost_fails_loudly_on_unknown_model_or_missing_cache_price() -> None:
    with pytest.raises(ValueError, match="no price for model 'other'"):
        session_cost_usd({"other": {"input_tokens": 1}}, _PRICES)
    with pytest.raises(ValueError, match="cache_read_usd_per_1m"):
        session_cost_usd({"small": {"cache_read_input_tokens": 5}}, _PRICES)


def test_reconcile_keeps_costs_within_3_percent_else_scales_to_the_bill() -> None:
    assert reconcile(100.0, 102.0) == 1.0
    assert reconcile(100.0, 110.0) == pytest.approx(1.1)
    with pytest.raises(ValueError):
        reconcile(0.0, 10.0)
