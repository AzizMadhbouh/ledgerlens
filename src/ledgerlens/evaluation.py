"""Measure extraction quality and validation behaviour against ground truth."""

from __future__ import annotations

import csv
import json
import logging
from datetime import UTC, datetime, date

from .config import Settings
from .extract import load_extraction_records
from .models import ValidationReport
from .normalization import (
    normalize_currency,
    normalize_invoice_number,
    normalize_vendor,
    parse_invoice_date,
    parse_total_cents,
)
from .ocr import load_ocr_metadata
from .validation import load_validation_report

logger = logging.getLogger(__name__)

FIELDS = ("invoice_number", "invoice_date", "vendor", "total", "currency")


def _truth_rows(settings: Settings) -> list[dict[str, str]]:
    with settings.ground_truth_path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _compare_field(field: str, raw, expected_row: dict[str, str]) -> bool:
    if field == "invoice_number":
        return normalize_invoice_number(raw) == expected_row["invoice_number"]
    if field == "invoice_date":
        expected = (
            date.fromisoformat(expected_row["date_iso"])
            if expected_row["date_iso"]
            else None
        )
        return parse_invoice_date(raw) == expected
    if field == "vendor":
        expected = expected_row["vendor"] or None
        return normalize_vendor(raw) == expected
    if field == "total":
        return parse_total_cents(raw) == int(expected_row["total_cents"])
    if field == "currency":
        normalized, _ = normalize_currency(raw)
        return normalized == expected_row["currency"]
    raise ValueError(f"Unknown field: {field}")


def _matches(field: str, value, expected_row: dict[str, str]) -> bool:
    if value is None:
        return False
    try:
        return _compare_field(field, value, expected_row)
    except (TypeError, ValueError):
        return False


def evaluate(
    settings: Settings,
    report: ValidationReport | None = None,
    *,
    run_id: str | None = None,
) -> dict:
    report = report or load_validation_report(settings)
    truth = {row["document_id"]: row for row in _truth_rows(settings)}
    extractions = {
        record.document_id: record for record in load_extraction_records(settings)
    }

    valid_truth = {key: row for key, row in truth.items() if row["expected_status"] == "accepted"}
    rejected_truth = {key: row for key, row in truth.items() if row["expected_status"] == "rejected"}

    field_hits = {field: 0 for field in FIELDS}
    raw_field_hits = {field: 0 for field in FIELDS}
    field_total = 0
    exact_documents = 0
    raw_exact_documents = 0
    mismatches: list[dict[str, object]] = []
    raw_mismatches: list[dict[str, object]] = []

    expected_display = {
        "invoice_number": "invoice_number",
        "invoice_date": "date_iso",
        "vendor": "vendor",
        "total": "total_cents",
        "currency": "currency",
    }
    for document_id, row in sorted(valid_truth.items()):
        record = extractions.get(document_id)
        final = record.extraction if record and record.extraction else None
        raw = (
            record.model_extraction
            if record and record.model_extraction
            else final
        )
        document_ok = True
        raw_document_ok = True
        for field in FIELDS:
            field_total += 1
            final_value = getattr(final, field, None) if final is not None else None
            raw_value = getattr(raw, field, None) if raw is not None else None
            final_ok = _matches(field, final_value, row)
            raw_ok = _matches(field, raw_value, row)
            if final_ok:
                field_hits[field] += 1
            else:
                document_ok = False
                mismatches.append(
                    {
                        "document_id": document_id,
                        "field": field,
                        "expected": row[expected_display[field]],
                        "extracted": final_value,
                    }
                )
            if raw_ok:
                raw_field_hits[field] += 1
            else:
                raw_document_ok = False
                raw_mismatches.append(
                    {
                        "document_id": document_id,
                        "field": field,
                        "expected": row[expected_display[field]],
                        "extracted": raw_value,
                    }
                )
        if document_ok:
            exact_documents += 1
        if raw_document_ok:
            raw_exact_documents += 1

    field_accuracy = {
        field: round(100.0 * field_hits[field] / max(len(valid_truth), 1), 1)
        for field in FIELDS
    }
    raw_field_accuracy = {
        field: round(100.0 * raw_field_hits[field] / max(len(valid_truth), 1), 1)
        for field in FIELDS
    }
    average_field_accuracy = round(
        100.0 * sum(field_hits.values()) / max(field_total, 1), 1
    )
    raw_average_field_accuracy = round(
        100.0 * sum(raw_field_hits.values()) / max(field_total, 1), 1
    )

    rejected_ids = {item.document_id for item in report.rejected}
    rejected_codes = {item.document_id: set(item.reason_codes) for item in report.rejected}
    detected = [doc_id for doc_id in rejected_truth if doc_id in rejected_ids]
    reason_matches = [
        doc_id
        for doc_id, row in rejected_truth.items()
        if row["expected_reason"] in rejected_codes.get(doc_id, set())
    ]
    valid_ids = set(valid_truth)
    false_rejections = [
        item.document_id for item in report.rejected if item.document_id in valid_ids
    ]
    invalid_accepted = [
        doc_id for doc_id in rejected_truth if doc_id not in rejected_ids
    ]

    ocr_confidences: list[float] = []
    ocr_ms = 0
    for document_id in truth:
        metadata = load_ocr_metadata(settings, document_id)
        if metadata:
            ocr_confidences.append(metadata.mean_confidence)
            ocr_ms += metadata.duration_ms
    extraction_ms = sum(record.duration_ms for record in extractions.values())
    model = next(
        (record.model for record in extractions.values() if record.model),
        settings.model,
    )
    reconciliations = [
        {"document_id": record.document_id, "notes": record.reconciliations}
        for record in sorted(extractions.values(), key=lambda item: item.document_id)
        if record.reconciliations
    ]

    evaluation = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "run_id": run_id,
        "model": model,
        "methodology": (
            "Field accuracy uses the 21 documents expected to pass. Values are "
            "compared after the same canonicalization used by validation. "
            "Final metrics include the printed-TOTAL DUE reconciliation step; "
            "raw_model_* metrics show the LLM output before that step. OCR mean "
            "confidence is Tesseract's word-confidence average."
        ),
        "documents": {
            "total": len(truth),
            "expected_valid": len(valid_truth),
            "expected_rejected": len(rejected_truth),
            "accepted": len(report.accepted),
            "rejected": len(report.rejected),
        },
        "extraction": {
            "field_accuracy_pct": field_accuracy,
            "average_field_accuracy_pct": average_field_accuracy,
            "fields_correct": sum(field_hits.values()),
            "fields_compared": field_total,
            "document_exact_match_pct": round(
                100.0 * exact_documents / max(len(valid_truth), 1), 1
            ),
            "documents_exact_match": exact_documents,
            "raw_model_field_accuracy_pct": raw_field_accuracy,
            "raw_model_average_field_accuracy_pct": raw_average_field_accuracy,
            "raw_model_fields_correct": sum(raw_field_hits.values()),
            "raw_model_document_exact_match_pct": round(
                100.0 * raw_exact_documents / max(len(valid_truth), 1), 1
            ),
            "raw_model_documents_exact_match": raw_exact_documents,
            "reconciled_documents": len(reconciliations),
            "reconciliations": reconciliations,
            "mismatches": mismatches,
            "raw_model_mismatches": raw_mismatches,
        },
        "validation": {
            "expected_rejected_detected": len(detected),
            "expected_rejected_total": len(rejected_truth),
            "catch_rate_pct": round(
                100.0 * len(detected) / max(len(rejected_truth), 1), 1
            ),
            "expected_reason_matched": len(reason_matches),
            "reason_match_rate_pct": round(
                100.0 * len(reason_matches) / max(len(rejected_truth), 1), 1
            ),
            "false_rejections": false_rejections,
            "invalid_documents_wrongly_accepted": invalid_accepted,
        },
        "ocr": {
            "average_confidence": round(
                sum(ocr_confidences) / len(ocr_confidences), 2
            )
            if ocr_confidences
            else 0.0,
            "documents": len(ocr_confidences),
            "total_seconds": round(ocr_ms / 1000, 1),
        },
        "timing": {
            "ocr_total_seconds": round(ocr_ms / 1000, 1),
            "llm_total_seconds": round(extraction_ms / 1000, 1),
        },
        "straight_through_pct": round(
            100.0 * len(report.accepted) / max(report.seen, 1), 1
        ),
    }
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    settings.evaluation_path.write_text(
        json.dumps(evaluation, indent=2), encoding="utf-8"
    )
    logger.info(
        "[evaluate] final field accuracy %.1f%%, raw LLM %.1f%%, "
        "final exact docs %.1f%%, raw exact docs %.1f%%, rejection catch %s/%s",
        evaluation["extraction"]["average_field_accuracy_pct"],
        evaluation["extraction"]["raw_model_average_field_accuracy_pct"],
        evaluation["extraction"]["document_exact_match_pct"],
        evaluation["extraction"]["raw_model_document_exact_match_pct"],
        len(detected),
        len(rejected_truth),
    )
    return evaluation
