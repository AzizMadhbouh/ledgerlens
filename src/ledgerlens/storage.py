"""SQLite persistence and analytical access."""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .config import Settings
from .models import ValidationReport
from .validation import load_validation_report

logger = logging.getLogger(__name__)

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def connect(settings: Settings) -> sqlite3.Connection:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(settings.database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(settings: Settings) -> None:
    schema = (SQL_DIR / "schema.sql").read_text(encoding="utf-8")
    with connect(settings) as connection:
        connection.executescript(schema)


def store_validation_run(
    settings: Settings,
    report: ValidationReport | None = None,
    *,
    run_id: str | None = None,
) -> str:
    """Persist one immutable run; views always expose the latest run."""

    initialize_database(settings)
    report = report or load_validation_report(settings)
    run_id = run_id or (
        "run-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    )
    finished_at = datetime.now(UTC)
    total_spend = sum(item.total_cents for item in report.accepted)
    confidences = [item.ocr_confidence for item in report.accepted]
    confidences.extend(item.ocr_confidence for item in report.rejected)
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    model = report.accepted[0].extraction_model if report.accepted else settings.model
    created_at = finished_at.isoformat(timespec="seconds")

    with connect(settings) as connection:
        connection.execute(
            """
            INSERT INTO ingestion_runs (
                run_id, started_at, finished_at, model, documents_seen,
                accepted, rejected, total_spend_cents, avg_ocr_confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                report.generated_at.isoformat(timespec="seconds"),
                finished_at.isoformat(timespec="seconds"),
                model,
                report.seen,
                len(report.accepted),
                len(report.rejected),
                total_spend,
                round(avg_confidence, 2),
            ),
        )

        for invoice in report.accepted:
            connection.execute(
                """
                INSERT INTO invoices (
                    run_id, document_id, invoice_number, invoice_date, vendor,
                    total_cents, currency, source_file, ocr_confidence,
                    extraction_model, raw_extraction_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    invoice.document_id,
                    invoice.invoice_number,
                    invoice.invoice_date.isoformat(),
                    invoice.vendor,
                    invoice.total_cents,
                    invoice.currency,
                    invoice.source_file,
                    invoice.ocr_confidence,
                    invoice.extraction_model,
                    json.dumps(invoice.raw_extraction, default=str),
                    created_at,
                ),
            )

        for rejected in report.rejected:
            connection.execute(
                """
                INSERT INTO rejected_documents (
                    run_id, document_id, source_file, stage, reason_codes_json,
                    reasons_json, raw_extraction_json, ocr_confidence, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    rejected.document_id,
                    rejected.source_file,
                    rejected.stage,
                    json.dumps(rejected.reason_codes),
                    json.dumps(rejected.reasons),
                    json.dumps(rejected.raw_extraction, default=str)
                    if rejected.raw_extraction is not None
                    else None,
                    rejected.ocr_confidence,
                    created_at,
                ),
            )
            for code, reason in zip(
                rejected.reason_codes, rejected.reasons, strict=False
            ):
                connection.execute(
                    """
                    INSERT INTO rejection_reasons (
                        run_id, document_id, reason_code, reason_text
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (run_id, rejected.document_id, code, reason),
                )

    logger.info(
        "[store] run %s: %s invoices, %s rejections -> %s",
        run_id,
        len(report.accepted),
        len(report.rejected),
        settings.database_path,
    )
    return run_id


def query(settings: Settings, sql: str, params: tuple[object, ...] = ()) -> list[dict]:
    if not settings.database_path.exists():
        raise RuntimeError(
            f"Database not found at {settings.database_path}. Run the pipeline first."
        )
    with connect(settings) as connection:
        rows = connection.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def query_file(settings: Settings, filename: str) -> list[dict]:
    path = SQL_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"SQL query not found: {path}")
    return query(settings, path.read_text(encoding="utf-8"))


def get_run_summary(settings: Settings) -> dict | None:
    rows = query_file(settings, "run_summary.sql")
    return rows[0] if rows else None


def get_vendor_spend(settings: Settings) -> list[dict]:
    return query_file(settings, "spend_by_vendor.sql")


def get_monthly_spend(settings: Settings) -> list[dict]:
    return query_file(settings, "monthly_totals.sql")


def get_rejected(settings: Settings) -> list[dict]:
    return query_file(settings, "rejected_documents.sql")


def get_rejection_summary(settings: Settings) -> list[dict]:
    return query(settings, "SELECT * FROM vw_rejection_summary")


def get_invoices(settings: Settings) -> list[dict]:
    return query(settings, "SELECT * FROM vw_invoices ORDER BY invoice_date, invoice_number")


def get_latest_run(settings: Settings) -> dict | None:
    rows = query(
        settings,
        "SELECT * FROM ingestion_runs ORDER BY rowid DESC LIMIT 1",
    )
    return rows[0] if rows else None


def get_run_history(settings: Settings) -> list[dict]:
    return query(
        settings,
        """
        SELECT run_id, finished_at, model, documents_seen, accepted, rejected,
               ROUND(total_spend_cents / 100.0, 2) AS total_spend
        FROM ingestion_runs
        ORDER BY rowid DESC
        """,
    )


def build_run_summary(settings: Settings) -> dict:
    summary = get_run_summary(settings)
    if summary is None:
        raise RuntimeError("The database has no ingestion runs.")
    payload = {
        "summary": summary,
        "spend_by_vendor": get_vendor_spend(settings),
        "monthly_spend": get_monthly_spend(settings),
        "rejection_summary": get_rejection_summary(settings),
        "rejected_documents": get_rejected(settings),
        "invoices": get_invoices(settings),
    }
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    (settings.reports_dir / "run_summary.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    return payload

