"""Command line: finlake [scorecard|close|lineage|quality] [--period YYYY-MM]."""

from __future__ import annotations

import argparse
from datetime import date

from finlake import close, metrics, pipeline, synthetic


def build(period: str) -> pipeline.Lakehouse:
    y, m = (int(p) for p in period.split("-"))
    as_of = date(y + (m == 12), m % 12 + 1, 1)
    return pipeline.run(synthetic.generate(period=period), as_of)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="finlake")
    ap.add_argument("command", choices=["scorecard", "close", "lineage", "quality"], nargs="?", default="scorecard")
    ap.add_argument("--period", default="2026-10")
    args = ap.parse_args(argv)
    lh = build(args.period)

    if args.command == "scorecard":
        print(f"Finance scorecard {args.period} (governed metrics, Gold layer)")
        for name, value, unit, owner in metrics.scorecard(lh):
            shown = f"${value:,.0f}" if unit == "$" else f"{value}{'%' if unit == '%' else ' ' + unit}"
            print(f"  {name:<24} {shown:>14}   owner: {owner}")
        print(f"  AR aging: {lh.gold['ar_aging']}")
        print(f"  AP aging: {lh.gold['ap_aging']}")
    elif args.command == "close":
        for c in close.checks(lh, args.period):
            print(f"  [{'OK' if c.ok else 'FAIL'}] {c.name}: expected {c.expected:,.2f}, actual {c.actual:,.2f}")
        print("Close status:", close.status(lh, args.period))
    elif args.command == "lineage":
        for src, tgt, rows in lh.lineage.edges:
            print(f"  {src:<16} -> {tgt:<20} {rows:>4} rows")
        print("  gold.trial_balance upstream:", ", ".join(lh.lineage.upstream("gold.trial_balance")))
    else:
        for q in lh.quality:
            print(f"  {q.dataset:<9} {q.rule:<17} pass {q.pass_rate:6.1%}  failed {q.failed}")
        for item in lh.quarantine:
            print(f"  quarantined {item['dataset']}: {item['reason']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
