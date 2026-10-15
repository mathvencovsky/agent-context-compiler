"""USD cost of an M10 session from its billed usage (PREREG §6).

``usage_by_model`` maps each model that served the session to its billed token
counts, with the Anthropic usage field names: ``input_tokens``,
``cache_creation_input_tokens``, ``cache_read_input_tokens`` and
``output_tokens``. Prices come from ``bench/prices.yaml``. Cache tokens need
``cache_write_usd_per_1m`` and ``cache_read_usd_per_1m`` there, taken from the
provider's pricing page before the pilot.

Unlike ``bench.report.model_cost_usd`` (M8, input and output only), an unknown
model or a missing cache price raises instead of costing 0.

:func:`reconcile` applies the PREREG §6 rule: the run's total must be within
±3% of the provider's billed total; otherwise per-session costs are scaled so
they add up to the bill.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Usage field -> price field in ``bench/prices.yaml`` (USD per 1M tokens).
_PRICE_OF = {
    "input_tokens": "input_usd_per_1m",
    "cache_creation_input_tokens": "cache_write_usd_per_1m",
    "cache_read_input_tokens": "cache_read_usd_per_1m",
    "output_tokens": "output_usd_per_1m",
}
#: PREREG §6: allowed gap between the computed and the billed run total.
TOLERANCE = 0.03


def session_cost_usd(
    usage_by_model: Mapping[str, Mapping[str, int]], prices: Mapping[str, Any]
) -> float:
    """Sum, over models and usage fields, of tokens times the per-token price."""
    models = prices.get("models", {})
    total = 0.0
    for model, usage in usage_by_model.items():
        if model not in models:
            raise ValueError(f"no price for model {model!r} in bench/prices.yaml")
        for field, price_field in _PRICE_OF.items():
            tokens = int(usage.get(field, 0) or 0)
            if not tokens:
                continue
            if price_field not in models[model]:
                raise ValueError(
                    f"{model!r} used {field} but bench/prices.yaml has no "
                    f"{price_field}; add it from the provider's pricing page"
                )
            total += tokens * float(models[model][price_field]) / 1_000_000
    return total


def reconcile(computed: float, billed: float, tolerance: float = TOLERANCE) -> float:
    """Factor for per-session costs: 1.0 within tolerance, else billed/computed."""
    if computed <= 0 or billed <= 0:
        raise ValueError("both totals must be positive")
    if abs(billed - computed) <= tolerance * billed:
        return 1.0
    return billed / computed
