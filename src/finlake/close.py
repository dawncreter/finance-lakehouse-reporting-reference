"""Record to Report month-end close checks: subledger-to-GL reconciliations and close blockers."""

from __future__ import annotations

from dataclasses import dataclass

from finlake.pipeline import Lakehouse


@dataclass
class Check:
    name: str
    expected: float
    actual: float
    tolerance: float = 0.01

    @property
    def ok(self) -> bool:
        return abs(self.expected - self.actual) <= self.tolerance


def checks(lh: Lakehouse, period: str) -> list[Check]:
    tb = lh.gold["trial_balance"]
    ar_sub = sum(r["amount"] for r in lh.silver["ar"] if r["invoice_date"].startswith(period))
    ap_sub = sum(r["amount"] for r in lh.silver["ap"] if r["bill_date"].startswith(period))
    blocked = sum(1 for q in lh.quarantine if q["reason"] == "journal_balanced")
    return [
        Check("Trial balance nets to zero", 0.0, round(sum(tb.values()), 2) + 0.0),
        Check("AR subledger = GL 1200 (Lead to Cash)", round(ar_sub, 2), tb.get("1200", 0.0)),
        Check("AP subledger = GL 2000 (Procure to Pay)", round(ap_sub, 2), -tb.get("2000", 0.0)),
        Check("Unbalanced journals held out of GL", 0.0, float(blocked), tolerance=0.0),
    ]


def status(lh: Lakehouse, period: str) -> str:
    failed = [c.name for c in checks(lh, period) if not c.ok]
    return "READY TO CLOSE" if not failed else "BLOCKED: " + "; ".join(failed)
