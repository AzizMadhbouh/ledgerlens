from ledgerlens.extract import parse_json_object, total_due_from_ocr
from ledgerlens.models import RawInvoice


def test_parse_fenced_json():
    raw = """```json
    {"invoice_number": "INV-2026-0001", "invoice_date": "2026-01-09",
     "vendor": "Acme", "total": 12.5, "currency": "USD"}
    ```"""
    payload = parse_json_object(raw)
    invoice = RawInvoice.model_validate(payload)
    assert invoice.invoice_number == "INV-2026-0001"
    assert invoice.total == 12.5


def test_coerce_formatted_total():
    invoice = RawInvoice.model_validate({"total": "$1,234.56"})
    assert invoice.total == 1234.56


def test_extra_keys_are_ignored():
    invoice = RawInvoice.model_validate({"currency": "USD", "comment": "ok"})
    assert invoice.currency == "USD"


def test_total_due_same_line():
    text = "Subtotal $1,055.50\nTax $87.08\nTOTAL DUE $1,142.58\n"
    assert total_due_from_ocr(text) == 1142.58


def test_total_due_stacked_layout():
    text = (
        "Subtotal\nTax (8.25%)\nTOTAL DUE\n"
        "UNIT AMOUNT\n189.00 567.00\n$1,055.50\n$87.08\n$1,142.58\n"
    )
    assert total_due_from_ocr(text) == 1142.58


def test_total_due_preserves_negative_amount():
    text = "Subtotal $-312.40\nTax $0.00\nTOTAL DUE $-312.40\n"
    assert total_due_from_ocr(text) == -312.40


def test_no_total_label_returns_none():
    assert total_due_from_ocr("Subtotal $10.00\nTax $0.82\n") is None
