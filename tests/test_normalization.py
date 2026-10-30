from datetime import date

import pytest

from ledgerlens.models import RawInvoice
from ledgerlens.normalization import (
    normalize_currency,
    normalize_invoice_number,
    normalize_vendor,
    parse_invoice_date,
    parse_total_cents,
    seller_header_is_placeholder,
    validate_invoice,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-03-02", date(2026, 3, 2)),
        ("March 13, 2026", date(2026, 3, 13)),
        ("13 Mar 2026", date(2026, 3, 13)),
        ("27/03/2026", date(2026, 3, 27)),
        ("March 3rd, 2026", date(2026, 3, 3)),
    ],
)
def test_parse_dates(raw, expected):
    assert parse_invoice_date(raw) == expected


def test_invalid_calendar_date_is_rejected():
    assert parse_invoice_date("31/02/2026") is None


@pytest.mark.parametrize(
    ("raw", "cents"),
    [
        (1234.56, 123456),
        ("$1,234.56", 123456),
        ("USD 1,234.56", 123456),
        ("-$312.40", -31240),
        ("($312.40)", -31240),
    ],
)
def test_parse_totals(raw, cents):
    assert parse_total_cents(raw) == cents


def test_currency_aliases_and_rejection():
    assert normalize_currency("$") == ("USD", None)
    assert normalize_currency("US Dollars") == ("USD", None)
    assert normalize_currency("XYZ") == (None, "unsupported_currency")
    assert normalize_currency(None) == (None, "missing_currency")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ACME CLOUD SERVICES", "Acme Cloud Services"),
        ("Acme Cloud Services, Inc.", "Acme Cloud Services"),
        ("PAPER AND PIXEL", "Paper & Pixel"),
        ("LUMEN OFFICE GOODS, INC.", "Lumen Office Goods"),
        ("  Northwind Supply Co.  ", "Northwind Supply Co."),
    ],
)
def test_vendor_normalization(raw, expected):
    assert normalize_vendor(raw) == expected


def test_placeholder_vendor_is_rejected():
    assert normalize_vendor("N/A") is None
    assert normalize_vendor("12345") is None


def test_invoice_number_normalization():
    assert normalize_invoice_number("inv 2026 0001") == "INV-2026-0001"


def test_valid_invoice_passes_business_rules():
    raw = RawInvoice(
        invoice_number="INV-2026-0001",
        invoice_date="January 9, 2026",
        vendor="NORTHWIND SUPPLY CO.",
        total=1234.56,
        currency="$",
    )
    canonical, codes, reasons = validate_invoice(
        raw,
        document_id="doc-001",
        source_file="invoice_001.pdf",
        ocr_confidence=97.4,
        extraction_model="llama3.2:3b",
    )
    assert codes == [] and reasons == []
    assert canonical is not None
    assert canonical["invoice_number"] == "INV-2026-0001"
    assert canonical["vendor"] == "Northwind Supply Co."
    assert canonical["total_cents"] == 123456
    assert canonical["currency"] == "USD"


def test_seller_header_placeholder_detection():
    assert seller_header_is_placeholder("--- Page 1 ---\nN/A\n1842 Foundry Road\n")
    assert not seller_header_is_placeholder(
        "--- Page 1 ---\nNORTHWIND SUPPLY CO.\n1842 Foundry Road\n"
    )


def test_placeholder_seller_header_cannot_be_inferred_from_email():
    raw = RawInvoice(
        invoice_number="INV-2026-0025",
        invoice_date="September 28, 2026",
        vendor="Northwind Supply Co.",
        total=1188.04,
        currency="USD",
    )
    canonical, codes, reasons = validate_invoice(
        raw,
        document_id="doc-025",
        source_file="invoice_025.pdf",
        ocr_confidence=95,
        extraction_model="test",
        ocr_text="--- Page 1 ---\nN/A\nbilling@northwindsupplyco.example\n",
    )
    assert canonical is None
    assert "invalid_vendor" in codes
    assert any("placeholder" in reason for reason in reasons)


def test_invalid_document_aggregates_reasons():
    raw = RawInvoice(
        invoice_number="not-an-invoice",
        invoice_date="31/02/2026",
        vendor="N/A",
        total=-12.0,
        currency="XYZ",
    )
    canonical, codes, reasons = validate_invoice(
        raw,
        document_id="doc-999",
        source_file="bad.pdf",
        ocr_confidence=50,
        extraction_model="test",
    )
    assert canonical is None
    assert "invalid_invoice_number" in codes
    assert "invalid_date" in codes
    assert "invalid_vendor" in codes
    assert "non_positive_total" in codes
    assert "unsupported_currency" in codes
    assert len(codes) == len(reasons)
