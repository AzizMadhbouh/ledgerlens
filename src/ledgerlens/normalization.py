"""Field-level cleanup and business validation.

Keeping this module pure makes the rules easy to test and easy to explain to
finance reviewers. The model never writes directly to SQL; it must pass here.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .models import RawInvoice

INVOICE_NUMBER_RE = re.compile(r"^[A-Z]{2,10}-\d{4}-\d{3,6}$")

CURRENCY_ALIASES = {
    "$": "USD",
    "US$": "USD",
    "USD": "USD",
    "US DOLLAR": "USD",
    "US DOLLARS": "USD",
    "DOLLAR": "USD",
    "DOLLARS": "USD",
    "€": "EUR",
    "EUR": "EUR",
    "EURO": "EUR",
    "EUROS": "EUR",
    "£": "GBP",
    "GBP": "GBP",
    "POUND": "GBP",
    "POUNDS": "GBP",
    "C$": "CAD",
    "CAD": "CAD",
    "A$": "AUD",
    "AUD": "AUD",
    "CHF": "CHF",
    "JPY": "JPY",
}
SUPPORTED_CURRENCIES = {"USD", "EUR", "GBP", "CAD", "AUD", "CHF", "JPY"}

VENDOR_ALIASES = {
    "northwind supply co.": "Northwind Supply Co.",
    "northwind supply co": "Northwind Supply Co.",
    "northwind supply company": "Northwind Supply Co.",
    "acme cloud services": "Acme Cloud Services",
    "acme cloud services, inc.": "Acme Cloud Services",
    "acme cloud services inc.": "Acme Cloud Services",
    "acme cloud": "Acme Cloud Services",
    "blue harbor logistics": "Blue Harbor Logistics",
    "blue harbor logistics llc": "Blue Harbor Logistics",
    "lumen office goods": "Lumen Office Goods",
    "lumen office goods, inc.": "Lumen Office Goods",
    "vertex consulting": "Vertex Consulting",
    "vertex consulting group": "Vertex Consulting",
    "greenline energy": "Greenline Energy",
    "greenline energy plc": "Greenline Energy",
    "atlas hardware": "Atlas Hardware",
    "atlas hardware supply": "Atlas Hardware",
    "paper & pixel": "Paper & Pixel",
    "paper and pixel": "Paper & Pixel",
    "paper & pixel studio": "Paper & Pixel",
}
PLACEHOLDER_VENDORS = {
    "",
    "-",
    "--",
    "—",
    "n/a",
    "na",
    "none",
    "null",
    "unknown",
    "vendor",
    "bill to",
}

DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d %B %Y",
    "%d %b %Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%B %d %Y",
    "%b %d %Y",
    "%d %B, %Y",
    "%d %b, %Y",
)
ORDINAL_RE = re.compile(r"(\d+)(st|nd|rd|th)\b", re.IGNORECASE)


def normalize_invoice_number(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip().upper()
    if not value:
        return None
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"-{2,}", "-", value)
    return value


def parse_invoice_date(raw: str | None) -> date | None:
    if raw is None:
        return None
    value = str(raw).strip()
    if not value or value.casefold() in {"n/a", "na", "none", "null", "-"}:
        return None
    value = ORDINAL_RE.sub(r"\1", value)
    value = re.sub(r"\s+", " ", value).strip(" ,.")
    for fmt in DATE_FORMATS:
        try:
            parsed = datetime.strptime(value, fmt).date()
        except ValueError:
            continue
        if 2000 <= parsed.year <= 2100:
            return parsed
    return None


def parse_total_cents(raw: float | str | None) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, float) and (math.isnan(raw) or math.isinf(raw)):
        return None
    text = str(raw).strip()
    if not text or text.casefold() in {"n/a", "na", "none", "null", "-"}:
        return None

    negative = text.startswith("(") and text.endswith(")")
    text = text.replace("(", "").replace(")", "")
    # Keep digits, separators and an explicit sign. Currency symbols/codes are
    # deliberately discarded; currency is validated as a separate field.
    text = re.sub(r"[^0-9,.\-+]", "", text)
    if not text or text in {"-", "+", ".", ",", "-.", "+."}:
        return None
    text = text.replace(",", "")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if negative:
        amount = -abs(amount)
    if amount != amount or amount.is_infinite():
        return None
    cents = int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return cents


def normalize_currency(raw: str | None) -> tuple[str | None, str | None]:
    """Return ``(iso_code, rejection_code)``."""

    if raw is None or not str(raw).strip():
        return None, "missing_currency"
    compact = re.sub(r"\s+", " ", str(raw).strip()).upper()
    iso = CURRENCY_ALIASES.get(compact)
    if iso is None and re.fullmatch(r"[A-Z]{3}", compact):
        iso = compact
    if iso is None:
        return None, "unsupported_currency"
    if iso not in SUPPORTED_CURRENCIES:
        return None, "unsupported_currency"
    return iso, None


def seller_header_is_placeholder(text: str) -> bool:
    """Return True when the invoice's seller header is explicitly N/A.

    The seller name is the first printed line of the document. If it is a
    placeholder, a model may still try to infer a vendor from an address,
    email domain or the Bill To block; those are not valid seller names.
    """

    for line in text.splitlines():
        value = line.strip()
        if not value or re.fullmatch(r"-+\s*Page\s+\d+\s*-+", value, re.IGNORECASE):
            continue
        return value.casefold().strip(" \t\r\n,:;.") in PLACEHOLDER_VENDORS
    return False


def normalize_vendor(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = re.sub(r"\s+", " ", str(raw)).strip(" \t\r\n,:;")
    if value.casefold() in PLACEHOLDER_VENDORS:
        return None
    alias = VENDOR_ALIASES.get(value.casefold())
    if alias:
        return alias
    # Normalize shouty OCR output without mangling ampersands or short legal
    # suffixes. Aliases cover the deliberate variations in the sample set.
    if value.isupper():
        value = " ".join(
            part.capitalize() if part.casefold() not in {"llc", "plc", "inc", "co"} else part.title()
            for part in value.split()
        )
    elif value.islower():
        value = value.title()
    if not re.search(r"[A-Za-z]{2,}", value):
        return None
    if len(value) < 2 or len(value) > 120:
        return None
    return value


def validate_invoice(
    raw: RawInvoice,
    *,
    document_id: str,
    source_file: str,
    ocr_confidence: float,
    extraction_model: str,
    ocr_text: str = "",
) -> tuple[dict[str, object] | None, list[str], list[str]]:
    """Validate a raw extraction.

    Returns ``(canonical_dict, reason_codes, human_readable_reasons)``.
    """

    codes: list[str] = []
    reasons: list[str] = []
    invoice_number = normalize_invoice_number(raw.invoice_number)
    invoice_date = parse_invoice_date(raw.invoice_date)
    vendor = normalize_vendor(raw.vendor)
    seller_header_placeholder = bool(ocr_text) and seller_header_is_placeholder(ocr_text)
    total_cents = parse_total_cents(raw.total)
    currency, currency_code = normalize_currency(raw.currency)

    if not invoice_number:
        codes.append("missing_invoice_number")
        reasons.append("Invoice number is missing.")
    elif not INVOICE_NUMBER_RE.fullmatch(invoice_number):
        codes.append("invalid_invoice_number")
        reasons.append(f"Invoice number '{invoice_number}' does not match the expected format.")

    if not raw.invoice_date or not str(raw.invoice_date).strip():
        codes.append("missing_date")
        reasons.append("Invoice date is missing.")
    elif invoice_date is None:
        codes.append("invalid_date")
        reasons.append(f"Invoice date '{raw.invoice_date}' is not a valid date.")

    if not raw.vendor or not str(raw.vendor).strip():
        codes.append("missing_vendor")
        reasons.append("Vendor is missing.")
    elif vendor is None or seller_header_placeholder:
        codes.append("invalid_vendor")
        if seller_header_placeholder:
            reasons.append(
                "The seller name at the top of the invoice is a placeholder; "
                "it cannot be inferred from the Bill To block or email."
            )
        else:
            reasons.append(f"Vendor '{raw.vendor}' is a placeholder or not recognizable.")

    if raw.total is None or not str(raw.total).strip():
        codes.append("missing_total")
        reasons.append("Invoice total is missing.")
    elif total_cents is None:
        codes.append("invalid_total")
        reasons.append(f"Invoice total '{raw.total}' is not a number.")
    elif total_cents <= 0:
        codes.append("non_positive_total")
        reasons.append("Invoice total must be greater than zero.")

    if currency is None:
        codes.append(currency_code or "unsupported_currency")
        reasons.append(f"Currency '{raw.currency or ''}' is missing or unsupported.")

    if codes:
        return None, codes, reasons

    assert invoice_number and invoice_date and vendor and total_cents is not None
    canonical: dict[str, object] = {
        "document_id": document_id,
        "invoice_number": invoice_number,
        "invoice_date": invoice_date,
        "vendor": vendor,
        "total_cents": total_cents,
        "currency": currency,
        "source_file": source_file,
        "ocr_confidence": round(float(ocr_confidence), 2),
        "extraction_model": extraction_model,
        "raw_extraction": raw.model_dump(),
    }
    return canonical, [], []
