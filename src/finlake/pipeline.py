"""Medallion pipeline: Bronze (raw + ingest metadata) -> Silver (validated, conformed) -> Gold (reporting facts).

Every step records data-quality results and lineage so a CFO-facing number can be traced back to source rows.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from finlake.synthetic import Row

KEYS = {"ar": "invoice_id", "ap": "bill_id", "gl": "je_id", "accounts": "account"}
DATES = {"ar": ["invoice_date", "due_date"], "ap": ["bill_date", "due_date"], "gl": ["posted_date"], "accounts": []}


@dataclass
class QualityResult:
    dataset: str
    rule: str
    passed: int
    failed: int

    @property
    def pass_rate(self) -> float:
        total = self.passed + self.failed
        return 1.0 if total == 0 else self.passed / total


@dataclass
class Lineage:
    edges: list[tuple[str, str, int]] = field(default_factory=list)

    def add(self, source: str, target: str, rows: int) -> None:
        self.edges.append((source, target, rows))

    def upstream(self, target: str) -> list[str]:
        """All datasets that feed `target`, nearest first."""
        found: list[str] = []
        frontier = [target]
        while frontier:
            node = frontier.pop(0)
            for src, tgt, _ in self.edges:
                if tgt == node and src not in found:
                    found.append(src)
                    frontier.append(src)
        return found


@dataclass
class Lakehouse:
    bronze: dict[str, list[Row]] = field(default_factory=dict)
    silver: dict[str, list[Row]] = field(default_factory=dict)
    quarantine: list[Row] = field(default_factory=list)
    gold: dict[str, Any] = field(default_factory=dict)
    quality: list[QualityResult] = field(default_factory=list)
    lineage: Lineage = field(default_factory=Lineage)


def _valid_date(value: Any) -> bool:
    try:
        date.fromisoformat(str(value))
        return True
    except ValueError:
        return False


def ingest(raw: dict[str, list[Row]], batch_id: str = "batch-001") -> Lakehouse:
    lh = Lakehouse()
    for name, rows in raw.items():
        lh.bronze[name] = [{**r, "_source": f"erp.{name}", "_batch": batch_id} for r in rows]
        lh.lineage.add(f"erp.{name}", f"bronze.{name}", len(rows))
    return lh


def refine(lh: Lakehouse) -> Lakehouse:
    """Bronze -> Silver: key, date and duplicate checks; failing rows go to quarantine with a reason."""
    for name, rows in lh.bronze.items():
        seen: set[tuple[Any, ...]] = set()
        good: list[Row] = []
        fails = {"key_present": 0, "valid_dates": 0, "no_duplicates": 0}
        for r in rows:
            body = tuple(sorted((k, v) for k, v in r.items() if not k.startswith("_")))
            if r.get(KEYS[name]) in (None, ""):
                reason = "key_present"
            elif not all(_valid_date(r[c]) for c in DATES[name]):
                reason = "valid_dates"
            elif body in seen:
                reason = "no_duplicates"
            else:
                seen.add(body)
                good.append({k: v for k, v in r.items() if not k.startswith("_")})
                continue
            fails[reason] += 1
            lh.quarantine.append({"dataset": name, "reason": reason, "row": r})
        for rule, failed in fails.items():
            lh.quality.append(QualityResult(name, rule, len(rows) - failed, failed))
        lh.silver[name] = good
    _check_journals(lh)
    for name, rows in lh.silver.items():
        lh.lineage.add(f"bronze.{name}", f"silver.{name}", len(rows))
    return lh


def _check_journals(lh: Lakehouse) -> None:
    """Record to Report control: every journal entry must balance (debits = credits)."""
    totals: dict[str, float] = defaultdict(float)
    for line in lh.silver.get("gl", []):
        totals[line["je_id"]] += line["debit"] - line["credit"]
    bad = {je for je, diff in totals.items() if abs(diff) > 0.005}
    lh.quality.append(QualityResult("gl", "journal_balanced", len(totals) - len(bad), len(bad)))
    for je in sorted(bad):
        lh.quarantine.append({"dataset": "gl", "reason": "journal_balanced", "row": {"je_id": je, "diff": round(totals[je], 2)}})
    lh.silver["gl"] = [ln for ln in lh.silver.get("gl", []) if ln["je_id"] not in bad]


def publish(lh: Lakehouse, as_of: date) -> Lakehouse:
    """Silver -> Gold: trial balance (R2R), AR aging (L2C), AP aging (P2P)."""
    acct_type = {a["account"]: a["type"] for a in lh.silver["accounts"]}
    tb: dict[str, float] = defaultdict(float)
    for ln in lh.silver["gl"]:
        tb[ln["account"]] += ln["debit"] - ln["credit"]
    lh.gold["trial_balance"] = {a: round(v, 2) for a, v in sorted(tb.items())}
    lh.gold["account_type"] = acct_type
    lh.gold["ar_aging"] = _aging(lh.silver["ar"], as_of)
    lh.gold["ap_aging"] = _aging(lh.silver["ap"], as_of)
    lh.gold["ar_open"] = round(sum(r["amount"] - r["paid_amount"] for r in lh.silver["ar"]), 2)
    lh.gold["ap_open"] = round(sum(r["amount"] - r["paid_amount"] for r in lh.silver["ap"]), 2)
    for src, tgt in [("gl", "trial_balance"), ("accounts", "trial_balance"), ("ar", "ar_aging"), ("ap", "ap_aging")]:
        lh.lineage.add(f"silver.{src}", f"gold.{tgt}", len(lh.silver[src]))
    return lh


BUCKETS = [(0, "current"), (30, "1-30"), (60, "31-60"), (90, "61-90")]


def _aging(rows: list[Row], as_of: date) -> dict[str, float]:
    out = {label: 0.0 for _, label in BUCKETS} | {"90+": 0.0}
    for r in rows:
        open_amt = r["amount"] - r["paid_amount"]
        if open_amt <= 0:
            continue
        late = (as_of - date.fromisoformat(r["due_date"])).days
        label = "90+"
        for limit, name in BUCKETS:
            if late <= limit:
                label = name
                break
        out[label] += open_amt
    return {k: round(v, 2) for k, v in out.items()}


def run(raw: dict[str, list[Row]], as_of: date) -> Lakehouse:
    return publish(refine(ingest(raw)), as_of)
