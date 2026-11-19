"""Deterministic synthetic ERP extracts: chart of accounts, GL journals, AR (Lead to Cash) and AP (Procure to Pay).

The generator deliberately injects the defects real ERP feeds carry (duplicate rows, missing keys,
unbalanced journals, bad dates) so the Silver layer has something to catch.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any

Row = dict[str, Any]

ACCOUNTS: list[Row] = [
    {"account": "1000", "name": "Cash", "type": "asset"},
    {"account": "1200", "name": "Accounts Receivable", "type": "asset"},
    {"account": "2000", "name": "Accounts Payable", "type": "liability"},
    {"account": "4000", "name": "Subscription Revenue", "type": "revenue"},
    {"account": "4100", "name": "Services Revenue", "type": "revenue"},
    {"account": "5000", "name": "Cost of Revenue", "type": "expense"},
    {"account": "6000", "name": "Operating Expenses", "type": "expense"},
]

CUSTOMERS = ["Apex Health", "Bluewater Retail", "Cobalt Logistics", "Delta Foods", "Evergreen Labs", "Fulton Energy"]
VENDORS = ["CloudCo", "DataPipe", "OfficeHub", "TalentWorks", "SecureNet"]


def _d(start: date, rng: random.Random, span: int) -> date:
    return start + timedelta(days=rng.randrange(span))


def generate(seed: int = 7, period: str = "2026-10") -> dict[str, list[Row]]:
    """Return raw ERP tables for one fiscal period (plus prior-period open items)."""
    rng = random.Random(seed)
    year, month = (int(p) for p in period.split("-"))
    start = date(year, month, 1)

    ar: list[Row] = []
    for i in range(1, 41):
        inv_date = _d(start - timedelta(days=60), rng, 90)
        amount = round(rng.uniform(5_000, 60_000), 2)
        paid = rng.random() < 0.55
        ar.append({
            "invoice_id": f"INV-{i:04d}",
            "customer": rng.choice(CUSTOMERS),
            "invoice_date": inv_date.isoformat(),
            "due_date": (inv_date + timedelta(days=30)).isoformat(),
            "amount": amount,
            "paid_amount": amount if paid else 0.0,
            "revenue_account": rng.choice(["4000", "4000", "4100"]),
        })

    ap: list[Row] = []
    for i in range(1, 31):
        bill_date = _d(start - timedelta(days=45), rng, 75)
        amount = round(rng.uniform(1_000, 25_000), 2)
        paid = rng.random() < 0.6
        ap.append({
            "bill_id": f"BILL-{i:04d}",
            "vendor": rng.choice(VENDORS),
            "bill_date": bill_date.isoformat(),
            "due_date": (bill_date + timedelta(days=45)).isoformat(),
            "amount": amount,
            "paid_amount": amount if paid else 0.0,
            "expense_account": rng.choice(["5000", "5000", "6000"]),
        })

    gl: list[Row] = []
    for je, inv in enumerate(r for r in ar if r["invoice_date"].startswith(period)):
        gl += _entry(f"JE-AR-{je:03d}", inv["invoice_date"], [("1200", inv["amount"]), (inv["revenue_account"], -inv["amount"])])
    for je, bill in enumerate(r for r in ap if r["bill_date"].startswith(period)):
        gl += _entry(f"JE-AP-{je:03d}", bill["bill_date"], [(bill["expense_account"], bill["amount"]), ("2000", -bill["amount"])])

    # Injected defects
    ar.append(dict(ar[3]))                                              # exact duplicate
    ar.append({**ar[5], "invoice_id": None})                           # missing business key
    ap.append({**ap[2], "bill_id": "BILL-9999", "bill_date": "2026-13-40"})  # invalid date
    gl += _entry("JE-MAN-001", f"{period}-28", [("6000", 1_250.00), ("1000", -1_200.00)])  # unbalanced manual JE

    return {"accounts": [dict(a) for a in ACCOUNTS], "gl": gl, "ar": ar, "ap": ap}


def _entry(je_id: str, posted: str, lines: list[tuple[str, float]]) -> list[Row]:
    return [
        {"je_id": je_id, "line": n, "posted_date": posted, "account": acct,
         "debit": round(amt, 2) if amt > 0 else 0.0, "credit": round(-amt, 2) if amt < 0 else 0.0}
        for n, (acct, amt) in enumerate(lines, start=1)
    ]
