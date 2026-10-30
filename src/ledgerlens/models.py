"""Shared Pydantic contracts for extraction, OCR and validation."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RawInvoice(BaseModel):
    """What the local model is allowed to return.

    Values are intentionally permissive. The validation layer, not the LLM,
    decides whether a value is fit for the warehouse.
    """

    model_config = ConfigDict(extra="ignore")

    @field_validator("total", mode="before")
    @classmethod
    def _coerce_numeric_total(cls, value: object) -> object:
        if value is None or isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            cleaned = re.sub(r"[^0-9,.\-+]", "", value).replace(",", "")
            try:
                return float(cleaned)
            except ValueError:
                return None
        return value

    invoice_number: str | None = Field(
        default=None, description="Seller's invoice number, exactly as printed."
    )
    invoice_date: str | None = Field(
        default=None, description="Invoice date, exactly as printed."
    )
    vendor: str | None = Field(
        default=None, description="Seller/vendor name, not the bill-to customer."
    )
    total: float | None = Field(
        default=None, description="Grand total / amount due as a number."
    )
    currency: str | None = Field(
        default=None, description="Currency code or symbol exactly as printed."
    )


class OcrMetadata(BaseModel):
    document_id: str
    source_file: str
    page_count: int
    mean_confidence: float = Field(ge=0, le=100)
    char_count: int
    dpi: int
    duration_ms: int
    engine: str = "tesseract"


class ExtractionRecord(BaseModel):
    document_id: str
    source_file: str
    model: str
    prompt_version: str
    text_sha256: str
    extraction: RawInvoice | None = None
    model_extraction: RawInvoice | None = None
    reconciliations: list[str] = Field(default_factory=list)
    raw_response: str | None = None
    duration_ms: int = 0
    error: str | None = None


class CanonicalInvoice(BaseModel):
    """Validated, normalized invoice ready for SQL."""

    document_id: str
    invoice_number: str
    invoice_date: date
    vendor: str
    total_cents: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    source_file: str
    ocr_confidence: float = 0.0
    extraction_model: str
    raw_extraction: dict[str, object] = Field(default_factory=dict)


class RejectedDocument(BaseModel):
    document_id: str
    source_file: str
    stage: Literal["ocr", "extraction", "validation"]
    reason_codes: list[str]
    reasons: list[str]
    raw_extraction: dict[str, object] | None = None
    ocr_confidence: float = 0.0


class ValidationReport(BaseModel):
    generated_at: datetime
    accepted: list[CanonicalInvoice]
    rejected: list[RejectedDocument]
    seen: int
