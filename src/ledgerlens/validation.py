"""Run canonicalization and business rules over all extraction records."""

from __future__ import annotations

import logging
import json
from datetime import UTC, datetime

from .config import Settings
from .extract import load_extraction_records
from .models import (
    CanonicalInvoice,
    RejectedDocument,
    ValidationReport,
)
from .normalization import validate_invoice
from .ocr import document_id_for_pdf, load_ocr_metadata, load_ocr_text

logger = logging.getLogger(__name__)


def build_validation_report(settings: Settings) -> ValidationReport:
    records = load_extraction_records(settings)
    if not records:
        raise RuntimeError(
            f"No extraction records found in {settings.extracted_dir}. "
            "Run the extraction step first."
        )

    current_ids = {
        document_id_for_pdf(path) for path in settings.invoices_dir.glob("*.pdf")
    }
    if current_ids:
        records = [record for record in records if record.document_id in current_ids]

    accepted: list[CanonicalInvoice] = []
    rejected: list[RejectedDocument] = []
    seen_invoice_numbers: dict[str, str] = {}

    for record in sorted(records, key=lambda item: item.document_id):
        metadata = load_ocr_metadata(settings, record.document_id)
        confidence = metadata.mean_confidence if metadata else 0.0
        source_file = metadata.source_file if metadata else record.source_file
        raw = record.extraction.model_dump() if record.extraction else None

        if record.error or record.extraction is None:
            stage = "ocr" if record.error and "OCR" in record.error else "extraction"
            rejected.append(
                RejectedDocument(
                    document_id=record.document_id,
                    source_file=source_file,
                    stage=stage,
                    reason_codes=["extraction_failed"],
                    reasons=[f"Extraction failed: {record.error or 'unknown error'}"],
                    raw_extraction=raw,
                    ocr_confidence=confidence,
                )
            )
            continue

        canonical, codes, reasons = validate_invoice(
            record.extraction,
            document_id=record.document_id,
            source_file=source_file,
            ocr_confidence=confidence,
            extraction_model=record.model,
            ocr_text=load_ocr_text(settings, record.document_id),
        )
        if canonical is None:
            rejected.append(
                RejectedDocument(
                    document_id=record.document_id,
                    source_file=source_file,
                    stage="validation",
                    reason_codes=codes,
                    reasons=reasons,
                    raw_extraction=raw,
                    ocr_confidence=confidence,
                )
            )
            continue

        invoice = CanonicalInvoice.model_validate(canonical)
        previous = seen_invoice_numbers.get(invoice.invoice_number)
        if previous is not None:
            rejected.append(
                RejectedDocument(
                    document_id=record.document_id,
                    source_file=source_file,
                    stage="validation",
                    reason_codes=["duplicate_invoice_number"],
                    reasons=[
                        f"Invoice number {invoice.invoice_number} was already seen "
                        f"in {previous}; duplicate documents are not loaded."
                    ],
                    raw_extraction=raw,
                    ocr_confidence=confidence,
                )
            )
            continue
        seen_invoice_numbers[invoice.invoice_number] = record.document_id
        accepted.append(invoice)

    report = ValidationReport(
        generated_at=datetime.now(UTC),
        accepted=accepted,
        rejected=rejected,
        seen=len(records),
    )
    settings.validation_dir.mkdir(parents=True, exist_ok=True)
    settings.validation_report_path.write_text(
        report.model_dump_json(indent=2), encoding="utf-8"
    )
    logger.info(
        "[validate] %s seen, %s accepted, %s rejected",
        report.seen,
        len(report.accepted),
        len(report.rejected),
    )
    for item in report.rejected:
        logger.info(
            "[validate] rejected %s (%s): %s",
            item.document_id,
            item.stage,
            ", ".join(item.reason_codes),
        )
    return report


def load_validation_report(settings: Settings) -> ValidationReport:
    if not settings.validation_report_path.exists():
        raise RuntimeError(
            "No validation report found. Run the validation step first."
        )
    return ValidationReport.model_validate_json(
        settings.validation_report_path.read_text(encoding="utf-8")
    )


def validation_counts_by_reason(report: ValidationReport) -> dict[str, int]:
    counts: dict[str, int] = {}
    for rejected in report.rejected:
        for code in rejected.reason_codes:
            counts[code] = counts.get(code, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def validation_report_as_json(report: ValidationReport) -> str:
    return json.dumps(report.model_dump(mode="json"), indent=2)
