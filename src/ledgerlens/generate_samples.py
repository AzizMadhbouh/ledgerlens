"""Generate a deterministic set of synthetic invoices and a ground-truth CSV.

The documents are intentionally varied: four layouts of date text, vendor-name
variants that need normalization, and four documents with business-rule
problems. They contain only fake data.
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from reportlab.lib.colors import HexColor, black, grey, white
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from .config import Settings

DOCUMENT_COUNT = 25


@dataclass(frozen=True)
class Vendor:
    name: str
    variants: tuple[str, ...]
    address: str
    city: str
    tax_id: str
    accent: str
    items: tuple[tuple[str, Decimal], ...]
    tax_rate: Decimal


VENDORS = (
    Vendor(
        "Northwind Supply Co.",
        ("NORTHWIND SUPPLY CO.", "Northwind Supply Co."),
        "1842 Foundry Road",
        "Portland, OR 97209",
        "US-93-7712045",
        "#0F766E",
        (
            ("Industrial shelving unit", Decimal("189.00")),
            ("Pallet jack", Decimal("420.00")),
            ("Safety gloves, case", Decimal("68.50")),
        ),
        Decimal("0.0825"),
    ),
    Vendor(
        "Acme Cloud Services",
        ("Acme Cloud Services, Inc.", "ACME CLOUD SERVICES"),
        "700 Market Street, Suite 900",
        "San Francisco, CA 94103",
        "US-88-4109921",
        "#2563EB",
        (
            ("Cloud workspace seat", Decimal("45.00")),
            ("Object storage, per TB", Decimal("22.00")),
            ("Priority support retainer", Decimal("300.00")),
        ),
        Decimal("0.0825"),
    ),
    Vendor(
        "Blue Harbor Logistics",
        ("Blue Harbor Logistics", "BLUE HARBOR LOGISTICS LLC"),
        "44 Harbor Way",
        "Seattle, WA 98104",
        "US-27-6653208",
        "#1D4ED8",
        (
            ("Regional freight lane", Decimal("540.00")),
            ("Fuel surcharge", Decimal("85.00")),
            ("Liftgate service", Decimal("60.00")),
        ),
        Decimal("0.0825"),
    ),
    Vendor(
        "Lumen Office Goods",
        ("Lumen Office Goods", "LUMEN OFFICE GOODS, INC."),
        "923 Willow Avenue",
        "Austin, TX 78701",
        "US-74-3319082",
        "#B45309",
        (
            ("Ergonomic task chair", Decimal("289.00")),
            ("Standing desk", Decimal("520.00")),
            ("Monitor arm", Decimal("96.00")),
        ),
        Decimal("0.0825"),
    ),
    Vendor(
        "Vertex Consulting",
        ("Vertex Consulting", "VERTEX CONSULTING GROUP"),
        "1200 Peachtree Street NW",
        "Atlanta, GA 30309",
        "US-58-1047723",
        "#6D28D9",
        (
            ("Data engineering retainer", Decimal("1850.00")),
            ("Analytics advisory", Decimal("640.00")),
            ("Executive workshop", Decimal("900.00")),
        ),
        Decimal("0.0000"),
    ),
    Vendor(
        "Greenline Energy",
        ("Greenline Energy", "GREENLINE ENERGY PLC"),
        "88 Prairie Wind Drive",
        "Des Moines, IA 50309",
        "US-19-8802134",
        "#15803D",
        (
            ("Renewable energy credits", Decimal("320.00")),
            ("Demand response credit", Decimal("150.00")),
            ("Metering fee", Decimal("42.50")),
        ),
        Decimal("0.0000"),
    ),
    Vendor(
        "Atlas Hardware",
        ("Atlas Hardware", "ATLAS HARDWARE SUPPLY"),
        "315 Ironworks Lane",
        "Denver, CO 80216",
        "US-46-5520179",
        "#92400E",
        (
            ("Stainless fastener kit", Decimal("74.00")),
            ("Torque wrench", Decimal("128.00")),
            ("Workbench", Decimal("340.00")),
        ),
        Decimal("0.0825"),
    ),
    Vendor(
        "Paper & Pixel",
        ("Paper & Pixel", "PAPER AND PIXEL"),
        "61 Studio Court",
        "Brooklyn, NY 11201",
        "US-31-4490206",
        "#BE185D",
        (
            ("Brand identity workshop", Decimal("1200.00")),
            ("Design retainer", Decimal("800.00")),
            ("Print production", Decimal("245.00")),
        ),
        Decimal("0.0825"),
    ),
)

# (ISO date, text rendered on the invoice)
VALID_SCHEDULE: tuple[tuple[str, str], ...] = (
    ("2026-01-09", "January 9, 2026"),
    ("2026-01-23", "23 January 2026"),
    ("2026-02-05", "February 5, 2026"),
    ("2026-02-18", "18 Feb 2026"),
    ("2026-03-02", "2026-03-02"),
    ("2026-03-13", "March 13, 2026"),
    ("2026-03-27", "27/03/2026"),
    ("2026-04-08", "April 8, 2026"),
    ("2026-04-21", "21 Apr 2026"),
    ("2026-05-04", "2026-05-04"),
    ("2026-05-15", "May 15, 2026"),
    ("2026-05-29", "29/05/2026"),
    ("2026-06-10", "June 10, 2026"),
    ("2026-06-24", "24 June 2026"),
    ("2026-07-07", "July 7, 2026"),
    ("2026-07-20", "2026-07-20"),
    ("2026-08-03", "August 3, 2026"),
    ("2026-08-17", "17 Aug 2026"),
    ("2026-09-01", "September 1, 2026"),
    ("2026-09-14", "14 September 2026"),
    ("2026-09-25", "2026-09-25"),
)

INVALID_SCHEDULE: tuple[tuple[str, str, str, str, str], ...] = (
    ("2026-09-08", "31/02/2026", "invalid_date", "INV-2026-0022", "Greenline Energy"),
    ("2026-09-18", "September 18, 2026", "unsupported_currency", "INV-2026-0023", "Atlas Hardware"),
    ("2026-09-22", "September 22, 2026", "non_positive_total", "INV-2026-0024", "Paper & Pixel"),
    ("2026-09-28", "September 28, 2026", "invalid_vendor", "INV-2026-0025", "N/A"),
)


def _money(value: Decimal) -> str:
    return f"${value:,.2f}"


def _with_tax(subtotal: Decimal, rate: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    tax = (subtotal * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total = (subtotal + tax).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return subtotal, tax, total


def _line_items(vendor: Vendor, index: int) -> list[tuple[str, int, Decimal, Decimal]]:
    rows: list[tuple[str, int, Decimal, Decimal]] = []
    for offset, (description, unit_price) in enumerate(vendor.items):
        qty = 1 + ((index + offset) % 3)
        if offset == 2:
            qty = 1
        rows.append((description, qty, unit_price, qty * unit_price))
    return rows


def _invoice_data(index: int) -> dict[str, object]:
    """Build all printed and expected values for one document."""

    vendor = VENDORS[index % len(VENDORS)]
    doc_no = index + 1
    invoice_number = f"INV-2026-{doc_no:04d}"
    rows = _line_items(vendor, index)
    subtotal = sum((row[3] for row in rows), Decimal("0"))
    subtotal, tax, total = _with_tax(subtotal, vendor.tax_rate)
    is_invalid = index >= len(VALID_SCHEDULE)

    if not is_invalid:
        iso_date, printed_date = VALID_SCHEDULE[index]
        expected_status = "accepted"
        expected_reason = ""
        printed_vendor = vendor.variants[index % len(vendor.variants)]
        currency = "USD"
        printed_currency = "USD"
    else:
        iso_date, printed_date, expected_reason, forced_number, forced_vendor = (
            INVALID_SCHEDULE[index - len(VALID_SCHEDULE)]
        )
        invoice_number = forced_number
        expected_status = "rejected"
        printed_vendor = forced_vendor
        currency = "USD"
        printed_currency = "USD"
        if expected_reason == "invalid_vendor":
            printed_vendor = "N/A"
        if expected_reason == "unsupported_currency":
            currency = "XYZ"
            printed_currency = "XYZ"
        if expected_reason == "non_positive_total":
            total = Decimal("-312.40")
            tax = Decimal("0.00")
            subtotal = total

    return {
        "document_id": f"doc-{doc_no:03d}",
        "source_file": f"invoice_{doc_no:03d}.pdf",
        "invoice_number": invoice_number,
        "date_iso": iso_date,
        "date_printed": printed_date,
        "vendor": vendor.name,
        "vendor_printed": printed_vendor,
        "vendor_address": vendor.address,
        "vendor_city": vendor.city,
        "vendor_tax_id": vendor.tax_id,
        "accent": vendor.accent,
        "rows": rows,
        "subtotal": subtotal,
        "tax": tax,
        "tax_rate": vendor.tax_rate,
        "total": total,
        "currency": currency,
        "currency_printed": printed_currency,
        "expected_status": expected_status,
        "expected_reason": expected_reason,
        "index": index,
    }


def _draw_table(
    c: canvas.Canvas,
    rows: list[tuple[str, int, Decimal, Decimal]],
    *,
    y: float,
    accent: HexColor,
) -> float:
    left = 0.72 * inch
    right = 7.78 * inch
    col_qty = 4.45 * inch
    col_unit = 5.65 * inch
    col_amount = 7.78 * inch

    c.setFillColor(accent)
    c.rect(left, y - 0.28 * inch, right - left, 0.28 * inch, stroke=0, fill=1)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(left + 8, y - 0.18 * inch, "DESCRIPTION")
    c.drawCentredString(col_qty + 30, y - 0.18 * inch, "QTY")
    c.drawRightString(col_unit + 55, y - 0.18 * inch, "UNIT")
    c.drawRightString(col_amount, y - 0.18 * inch, "AMOUNT")

    y -= 0.28 * inch
    c.setFillColor(black)
    for description, qty, unit_price, amount in rows:
        c.setFont("Helvetica", 9.2)
        c.drawString(left + 8, y - 0.19 * inch, description)
        c.drawCentredString(col_qty + 30, y - 0.19 * inch, str(qty))
        c.drawRightString(col_unit + 55, y - 0.19 * inch, f"{unit_price:,.2f}")
        c.drawRightString(col_amount, y - 0.19 * inch, f"{amount:,.2f}")
        c.setStrokeColor(HexColor("#D7DEE5"))
        c.setLineWidth(0.5)
        c.line(left, y - 0.28 * inch, right, y - 0.28 * inch)
        y -= 0.28 * inch
    return y


def _draw_invoice(path: Path, data: dict[str, object]) -> None:
    c = canvas.Canvas(str(path), pagesize=letter)
    width, height = letter
    accent = HexColor(str(data["accent"]))
    index = int(data["index"])

    # Background band and logo mark.
    c.setFillColor(HexColor("#F8FAFC"))
    c.rect(0, 0, width, height, stroke=0, fill=1)
    c.setFillColor(accent)
    c.rect(0, height - 0.16 * inch, width, 0.16 * inch, stroke=0, fill=1)
    c.roundRect(
        0.72 * inch,
        height - 1.30 * inch,
        0.52 * inch,
        0.52 * inch,
        7,
        stroke=0,
        fill=1,
    )
    initials = "".join(word[0] for word in str(data["vendor"]).replace("&", "").split()[:2])
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 15)
    c.drawCentredString(0.98 * inch, height - 1.12 * inch, initials.upper())

    # Seller block.
    c.setFillColor(black)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(1.38 * inch, height - 1.02 * inch, str(data["vendor_printed"]))
    c.setFont("Helvetica", 8.5)
    c.setFillColor(HexColor("#475569"))
    c.drawString(1.38 * inch, height - 1.22 * inch, str(data["vendor_address"]))
    c.drawString(1.38 * inch, height - 1.38 * inch, str(data["vendor_city"]))
    c.drawString(
        1.38 * inch,
        height - 1.54 * inch,
        f"Tax ID: {data['vendor_tax_id']}   |   billing@{str(data['vendor']).lower().replace(' ', '').replace('&', 'and')}.example",
    )

    # Document title and metadata.
    c.setFillColor(accent)
    c.setFont("Helvetica-Bold", 29)
    c.drawRightString(7.78 * inch, height - 1.08 * inch, "INVOICE")
    c.setFillColor(HexColor("#334155"))
    c.setFont("Helvetica-Bold", 8)
    c.drawRightString(7.78 * inch, height - 1.38 * inch, f"NO.  {data['invoice_number']}")
    c.drawRightString(
        7.78 * inch, height - 1.56 * inch, f"DATE  {data['date_printed']}"
    )
    c.drawRightString(
        7.78 * inch, height - 1.74 * inch, f"CURRENCY  {data['currency_printed']}"
    )

    # Bill-to block.
    c.setFillColor(HexColor("#E2E8F0"))
    c.roundRect(0.72 * inch, height - 3.00 * inch, 3.10 * inch, 1.02 * inch, 5, stroke=0, fill=1)
    c.setFillColor(HexColor("#64748B"))
    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(0.90 * inch, height - 2.20 * inch, "BILL TO")
    c.setFillColor(black)
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(0.90 * inch, height - 2.41 * inch, "Juniper Field Studio")
    c.setFont("Helvetica", 8.5)
    c.setFillColor(HexColor("#475569"))
    c.drawString(0.90 * inch, height - 2.59 * inch, "410 Alder Street, Suite 6")
    c.drawString(0.90 * inch, height - 2.75 * inch, "Portland, OR 97205")

    c.setFillColor(HexColor("#475569"))
    c.setFont("Helvetica-Bold", 7.5)
    c.drawRightString(7.78 * inch, height - 2.26 * inch, "PAYMENT TERMS")
    c.setFillColor(black)
    c.setFont("Helvetica", 9)
    c.drawRightString(7.78 * inch, height - 2.44 * inch, "Net 30")
    c.setFillColor(HexColor("#475569"))
    c.setFont("Helvetica-Bold", 7.5)
    c.drawRightString(7.78 * inch, height - 2.72 * inch, "PURCHASE ORDER")
    c.setFillColor(black)
    c.setFont("Helvetica", 9)
    c.drawRightString(7.78 * inch, height - 2.90 * inch, f"PO-2026-{2410 + index:04d}")

    rows = data["rows"]
    assert isinstance(rows, list)
    y = _draw_table(c, rows, y=height - 3.38 * inch, accent=accent)

    # Totals block.
    subtotal = data["subtotal"]
    tax = data["tax"]
    total = data["total"]
    tax_rate = data["tax_rate"]
    assert isinstance(subtotal, Decimal)
    assert isinstance(tax, Decimal)
    assert isinstance(total, Decimal)
    assert isinstance(tax_rate, Decimal)
    total_x = 7.78 * inch
    label_x = 5.75 * inch
    y_totals = y - 0.25 * inch
    c.setFont("Helvetica", 9)
    c.setFillColor(HexColor("#334155"))
    c.drawRightString(label_x, y_totals, "Subtotal")
    c.drawRightString(total_x, y_totals, _money(subtotal))
    c.drawRightString(label_x, y_totals - 0.24 * inch, f"Tax ({tax_rate * 100:.2f}%)")
    c.drawRightString(total_x, y_totals - 0.24 * inch, _money(tax))

    c.setStrokeColor(accent)
    c.setLineWidth(1.3)
    c.line(label_x, y_totals - 0.36 * inch, total_x, y_totals - 0.36 * inch)
    c.setFillColor(accent)
    c.setFont("Helvetica-Bold", 13)
    c.drawRightString(label_x, y_totals - 0.62 * inch, "TOTAL DUE")
    currency_printed = str(data["currency_printed"])
    total_text = (
        _money(total)
        if currency_printed == "USD"
        else f"{total:,.2f} {currency_printed}"
    )
    c.drawRightString(total_x, y_totals - 0.62 * inch, total_text)

    # Notes.
    c.setFillColor(HexColor("#64748B"))
    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(0.72 * inch, 1.12 * inch, "NOTES")
    c.setFont("Helvetica", 8)
    c.drawString(
        0.72 * inch,
        0.96 * inch,
        "Thank you for your business. Please reference the invoice number with payment.",
    )
    c.drawString(
        0.72 * inch,
        0.81 * inch,
        "Questions? billing@vendor.example  |  Payment by ACH or wire.",
    )
    c.setStrokeColor(HexColor("#CBD5E1"))
    c.setLineWidth(0.5)
    c.line(0.72 * inch, 0.62 * inch, 7.78 * inch, 0.62 * inch)
    c.setFillColor(HexColor("#94A3B8"))
    c.setFont("Helvetica", 6.5)
    c.drawString(0.72 * inch, 0.44 * inch, "SYNTHETIC DEMO DOCUMENT - NOT A REAL INVOICE")
    c.drawRightString(7.78 * inch, 0.44 * inch, f"Page 1 of 1  |  {data['document_id']}")

    c.setTitle(f"Invoice {data['invoice_number']}")
    c.setAuthor(str(data["vendor"]))
    c.setSubject("Synthetic invoice for LedgerLens demo")
    c.showPage()
    c.save()


def generate_samples(settings: Settings, *, force: bool = False) -> dict[str, int]:
    """Write invoices and ground truth; skip when a complete set exists."""

    settings.ensure_directories()
    expected_pdfs = {
        f"invoice_{i:03d}.pdf" for i in range(1, DOCUMENT_COUNT + 1)
    }
    existing = {path.name for path in settings.invoices_dir.glob("*.pdf")}
    if (
        not force
        and expected_pdfs <= existing
        and settings.ground_truth_path.exists()
    ):
        return {"pdfs": len(expected_pdfs), "reused": len(expected_pdfs)}

    # The sample set is fully deterministic. The seed keeps line-item variation
    # stable if the generator is extended later.
    random.seed(20261030)
    settings.invoices_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []

    for index in range(DOCUMENT_COUNT):
        data = _invoice_data(index)
        path = settings.invoices_dir / str(data["source_file"])
        _draw_invoice(path, data)
        total = data["total"]
        assert isinstance(total, Decimal)
        rows.append(
            {
                "document_id": data["document_id"],
                "source_file": data["source_file"],
                "invoice_number": data["invoice_number"],
                "date_raw": data["date_printed"],
                "date_iso": data["date_iso"] if data["expected_status"] == "accepted" else "",
                "vendor": data["vendor"] if data["expected_status"] == "accepted" else "",
                "vendor_printed": data["vendor_printed"],
                "total_cents": int(total * 100),
                "currency": data["currency"],
                "expected_status": data["expected_status"],
                "expected_reason": data["expected_reason"],
            }
        )

    fieldnames = list(rows[0].keys())
    with settings.ground_truth_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return {"pdfs": DOCUMENT_COUNT, "reused": 0}
