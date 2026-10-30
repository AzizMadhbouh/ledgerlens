from datetime import UTC, date, datetime

from ledgerlens.config import Settings
from ledgerlens.models import (
    CanonicalInvoice,
    RejectedDocument,
    ValidationReport,
)
from ledgerlens.storage import (
    get_rejection_summary,
    get_run_summary,
    get_vendor_spend,
    query,
    store_validation_run,
)


def _settings(tmp_path) -> Settings:
    settings = Settings(project_root=tmp_path, data_dir=tmp_path / "data", model="test")
    settings.ensure_directories()
    return settings


def _report() -> ValidationReport:
    accepted = [
        CanonicalInvoice(
            document_id="doc-001",
            invoice_number="INV-2026-0001",
            invoice_date=date(2026, 1, 9),
            vendor="Northwind Supply Co.",
            total_cents=123456,
            currency="USD",
            source_file="invoice_001.pdf",
            ocr_confidence=97.0,
            extraction_model="test",
            raw_extraction={},
        ),
        CanonicalInvoice(
            document_id="doc-002",
            invoice_number="INV-2026-0002",
            invoice_date=date(2026, 2, 9),
            vendor="Acme Cloud Services",
            total_cents=50000,
            currency="USD",
            source_file="invoice_002.pdf",
            ocr_confidence=95.0,
            extraction_model="test",
            raw_extraction={},
        ),
    ]
    rejected = [
        RejectedDocument(
            document_id="doc-003",
            source_file="invoice_003.pdf",
            stage="validation",
            reason_codes=["invalid_total"],
            reasons=["Invoice total must be greater than zero."],
            raw_extraction={"total": -1},
            ocr_confidence=90.0,
        )
    ]
    return ValidationReport(
        generated_at=datetime.now(UTC),
        accepted=accepted,
        rejected=rejected,
        seen=3,
    )


def test_store_and_query_views(tmp_path):
    settings = _settings(tmp_path)
    run_id = store_validation_run(settings, _report(), run_id="run-test-1")
    assert run_id == "run-test-1"

    summary = get_run_summary(settings)
    assert summary["accepted"] == 2
    assert summary["rejected"] == 1
    assert summary["total_spend"] == 1734.56
    assert summary["straight_through_pct"] == 66.7

    vendors = get_vendor_spend(settings)
    assert {row["vendor"] for row in vendors} == {
        "Northwind Supply Co.",
        "Acme Cloud Services",
    }
    reasons = get_rejection_summary(settings)
    assert reasons[0]["reason_code"] == "invalid_total"

    invoices = query(settings, "SELECT * FROM vw_invoices")
    assert len(invoices) == 2


def test_latest_run_is_exposed_by_views(tmp_path):
    settings = _settings(tmp_path)
    store_validation_run(settings, _report(), run_id="run-old")
    second = _report()
    second.accepted = second.accepted[:1]
    second.seen = 2
    store_validation_run(settings, second, run_id="run-new")

    summary = get_run_summary(settings)
    assert summary["run_id"] == "run-new"
    assert summary["accepted"] == 1
    vendors = get_vendor_spend(settings)
    assert len(vendors) == 1
