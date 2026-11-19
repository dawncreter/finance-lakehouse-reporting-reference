"""Governed metric layer: one registered definition per KPI, computed only from Gold tables."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from finlake.pipeline import Lakehouse


@dataclass(frozen=True)
class Metric:
    name: str
    definition: str
    owner: str
    unit: str
    compute: Callable[[Lakehouse], float]


def _sum_type(lh: Lakehouse, kind: str) -> float:
    types = lh.gold["account_type"]
    return sum(v for a, v in lh.gold["trial_balance"].items() if types.get(a) == kind)


def revenue(lh: Lakehouse) -> float:
    return round(-_sum_type(lh, "revenue"), 2)


def cost_of_revenue(lh: Lakehouse) -> float:
    return round(lh.gold["trial_balance"].get("5000", 0.0), 2)


def gross_margin_pct(lh: Lakehouse) -> float:
    rev = revenue(lh)
    return 0.0 if rev == 0 else round(100 * (rev - cost_of_revenue(lh)) / rev, 1)


def dso(lh: Lakehouse, days: int = 30) -> float:
    rev = revenue(lh)
    return 0.0 if rev == 0 else round(lh.gold["ar_open"] / rev * days, 1)


def dpo(lh: Lakehouse, days: int = 30) -> float:
    spend = _sum_type(lh, "expense")
    return 0.0 if spend == 0 else round(lh.gold["ap_open"] / spend * days, 1)


def dq_pass_rate(lh: Lakehouse) -> float:
    passed = sum(q.passed for q in lh.quality)
    total = passed + sum(q.failed for q in lh.quality)
    return round(100 * passed / total, 1) if total else 100.0


REGISTRY: list[Metric] = [
    Metric("Revenue", "Credits less debits on revenue accounts (4xxx) for the period", "Controller", "$", revenue),
    Metric("Gross margin", "(Revenue - Cost of revenue 5000) / Revenue", "FP&A", "%", gross_margin_pct),
    Metric("DSO", "Open AR / period revenue x 30 days", "Credit & Collections", "days", dso),
    Metric("DPO", "Open AP / period expenses x 30 days", "Accounts Payable", "days", dpo),
    Metric("Data quality pass rate", "Rows passing all Silver rules / rows checked", "Data Governance", "%", dq_pass_rate),
]


def scorecard(lh: Lakehouse) -> list[tuple[str, float, str, str]]:
    return [(m.name, m.compute(lh), m.unit, m.owner) for m in REGISTRY]
