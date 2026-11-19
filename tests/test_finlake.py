from datetime import date

import pytest

from finlake import cli, close, metrics, pipeline, synthetic

PERIOD = "2026-10"


@pytest.fixture(scope="module")
def lh() -> pipeline.Lakehouse:
    return pipeline.run(synthetic.generate(period=PERIOD), date(2026, 11, 1))


def test_generator_is_deterministic() -> None:
    assert synthetic.generate(seed=3) == synthetic.generate(seed=3)


def test_bronze_keeps_every_row_with_metadata(lh: pipeline.Lakehouse) -> None:
    raw = synthetic.generate(period=PERIOD)
    assert len(lh.bronze["ar"]) == len(raw["ar"])
    assert all(r["_source"] == "erp.ar" for r in lh.bronze["ar"])


def test_silver_quarantines_each_injected_defect(lh: pipeline.Lakehouse) -> None:
    reasons = sorted((q["dataset"], q["reason"]) for q in lh.quarantine)
    assert reasons == [("ap", "valid_dates"), ("ar", "key_present"), ("ar", "no_duplicates"), ("gl", "journal_balanced")]
    assert all("_batch" not in r for r in lh.silver["ar"])


def test_unbalanced_journal_is_removed_from_silver(lh: pipeline.Lakehouse) -> None:
    assert not [ln for ln in lh.silver["gl"] if ln["je_id"] == "JE-MAN-001"]


def test_trial_balance_nets_to_zero(lh: pipeline.Lakehouse) -> None:
    assert abs(sum(lh.gold["trial_balance"].values())) < 0.01


def test_aging_buckets_sum_to_open_balance(lh: pipeline.Lakehouse) -> None:
    assert sum(lh.gold["ar_aging"].values()) == pytest.approx(lh.gold["ar_open"], abs=0.05)
    assert sum(lh.gold["ap_aging"].values()) == pytest.approx(lh.gold["ap_open"], abs=0.05)


def test_aging_puts_very_late_items_in_90_plus() -> None:
    rows = [{"amount": 100.0, "paid_amount": 0.0, "due_date": "2026-01-01"},
            {"amount": 50.0, "paid_amount": 50.0, "due_date": "2026-01-01"}]
    assert pipeline._aging(rows, date(2026, 11, 1))["90+"] == 100.0


def test_lineage_traces_gold_back_to_erp(lh: pipeline.Lakehouse) -> None:
    up = lh.lineage.upstream("gold.trial_balance")
    assert "erp.gl" in up and "silver.gl" in up
    assert ("bronze.gl", "silver.gl", len(lh.silver["gl"])) in lh.lineage.edges


def test_quality_pass_rate_handles_empty() -> None:
    assert pipeline.QualityResult("x", "r", 0, 0).pass_rate == 1.0


def test_governed_metrics(lh: pipeline.Lakehouse) -> None:
    card = {name: value for name, value, _, _ in metrics.scorecard(lh)}
    assert card["Revenue"] > 0
    assert 0 < card["Gross margin"] < 100
    assert card["DSO"] > 0 and card["DPO"] > 0
    assert 95 < card["Data quality pass rate"] < 100


def test_metrics_guard_against_zero_denominators() -> None:
    empty = pipeline.Lakehouse(gold={"trial_balance": {}, "account_type": {}, "ar_open": 0.0, "ap_open": 0.0})
    assert metrics.gross_margin_pct(empty) == 0.0
    assert metrics.dso(empty) == 0.0
    assert metrics.dpo(empty) == 0.0
    assert metrics.dq_pass_rate(empty) == 100.0


def test_close_reconciles_subledgers_but_blocks_on_bad_journal(lh: pipeline.Lakehouse) -> None:
    results = {c.name: c.ok for c in close.checks(lh, PERIOD)}
    assert results["AR subledger = GL 1200 (Lead to Cash)"]
    assert results["AP subledger = GL 2000 (Procure to Pay)"]
    assert close.status(lh, PERIOD).startswith("BLOCKED")


def test_close_ready_when_clean() -> None:
    raw = synthetic.generate(period=PERIOD)
    raw["gl"] = [ln for ln in raw["gl"] if ln["je_id"] != "JE-MAN-001"]
    clean = pipeline.run(raw, date(2026, 11, 1))
    assert close.status(clean, PERIOD) == "READY TO CLOSE"


@pytest.mark.parametrize("cmd", ["scorecard", "close", "lineage", "quality"])
def test_cli(cmd: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([cmd]) == 0
    assert capsys.readouterr().out.strip()


def test_cli_december_rolls_year() -> None:
    assert cli.main(["scorecard", "--period", "2026-12"]) == 0
