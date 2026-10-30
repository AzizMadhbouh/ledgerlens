"""LangChain + Ollama field extraction with on-disk caching."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

from .config import Settings
from .models import ExtractionRecord, OcrMetadata, RawInvoice
from .normalization import parse_total_cents
from .ocr import document_id_for_pdf, load_ocr_metadata, load_ocr_text

logger = logging.getLogger(__name__)

PROMPT_VERSION = "invoice-extract-v3"
SYSTEM_PROMPT = """You are a meticulous accounts-payable data entry clerk.
Extract fields from OCR text of exactly one invoice. OCR reading order may be
jumbled, so use the printed labels to identify values.

Return one JSON object with exactly these five keys:
- "invoice_number": string or null
- "invoice_date": string or null
- "vendor": string or null
- "total": number or null
- "currency": string or null

Rules:
1. Copy values exactly as printed. Never guess, repair, or invent missing data.
2. "vendor" is the seller/supplier: the company name at the very top of the
   invoice. A name below the "BILL TO" heading is the customer, never the
   vendor. Ignore header fragments such as "INVOICE", "NVOIC E", addresses,
   emails, and tax IDs.
3. "total" must be the amount associated with the printed "TOTAL DUE" label
   (also "Amount Due" or "Grand Total"). It is the final amount including tax.
   NEVER return Subtotal, Tax, a unit price, or a line-item amount. When both
   Subtotal and TOTAL DUE appear, choose TOTAL DUE. The total is normally the
   largest of those three amounts.
4. Emit "total" as a JSON number with no currency symbol or thousands
   separator, for example 1234.56. Preserve a leading minus sign if printed.
5. "currency" is the ISO code if printed, otherwise the symbol.
6. If a field is absent, blank, illegible, or explicitly "N/A", return null.
7. Output only the JSON object. No markdown fences, no commentary."""

USER_PROMPT = """OCR TEXT
<<<BEGIN INVOICE>>>
{text}
<<<END INVOICE>>>"""


class OllamaUnavailable(RuntimeError):
    """Raised when the local model cannot be reached or is not installed."""


def _list_models(base_url: str) -> list[str]:
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise OllamaUnavailable(
            f"Cannot reach Ollama at {base_url}. Start it with `ollama serve`. "
            f"Underlying error: {exc}"
        ) from exc
    return [model.get("name", "") for model in payload.get("models", [])]


def check_ollama(settings: Settings) -> None:
    models = _list_models(settings.ollama_base_url)
    # Ollama reports `model:latest` for implicitly tagged models.
    wanted = {settings.model}
    if ":" not in settings.model:
        wanted.add(f"{settings.model}:latest")
    if not wanted.intersection(models):
        available = ", ".join(models[:8]) or "none"
        raise OllamaUnavailable(
            f"Model '{settings.model}' is not installed in Ollama. "
            f"Run `ollama pull {settings.model}`. Installed: {available}"
        )


def build_llm(settings: Settings) -> ChatOllama:
    kwargs: dict[str, object] = {
        "model": settings.model,
        "base_url": settings.ollama_base_url,
        "temperature": 0,
        "format": "json",
        "num_ctx": 4096,
        "num_predict": 400,
        "keep_alive": "10m",
    }
    try:
        return ChatOllama(
            **kwargs,
            client_kwargs={"timeout": settings.ollama_timeout_seconds},
        )
    except TypeError:
        # Older langchain-ollama releases do not expose client_kwargs.
        kwargs.pop("format", None)
        kwargs["format"] = "json"
        return ChatOllama(**kwargs)  # type: ignore[arg-type]


def _content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and "text" in item:
                parts.append(str(item["text"]))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content)


def parse_json_object(raw: str) -> dict[str, object]:
    value = raw.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    start = value.find("{")
    end = value.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("The model response did not contain a JSON object.")
    parsed = json.loads(value[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("The model response was not a JSON object.")
    return parsed


TOTAL_LABEL_RE = re.compile(
    r"\b(?:TOTAL\s+DUE|AMOUNT\s+DUE|GRAND\s+TOTAL|BALANCE\s+DUE)\b",
    re.IGNORECASE,
)
MONEY_RE = re.compile(r"(?<![\w.])(-?\$?\s?\d[\d,]*(?:\.\d{2}))")


def total_due_from_ocr(text: str) -> float | None:
    """Read the printed TOTAL DUE amount from OCR text.

    The model is good at locating labels but a 3B model can hallucinate the
    amount. The printed total is still in the OCR text, so use it as the
    authoritative value when the label is present. The last money token is the
    total in the sample templates; a same-line amount takes precedence.
    """

    labels = list(TOTAL_LABEL_RE.finditer(text))
    if not labels:
        return None
    amounts = list(MONEY_RE.finditer(text))
    if not amounts:
        return None

    candidate: str | None = None
    for label in labels:
        line_end = text.find("\n", label.end())
        segment = text[label.end() : line_end if line_end != -1 else len(text)]
        same_line = MONEY_RE.search(segment)
        if same_line:
            candidate = same_line.group(1)
            break
    if candidate is None:
        candidate = amounts[-1].group(1)

    cents = parse_total_cents(candidate)
    return cents / 100.0 if cents is not None else None


def _reconcile_total(
    text: str, extraction: RawInvoice
) -> tuple[RawInvoice, list[str]]:
    anchored = total_due_from_ocr(text)
    if anchored is None:
        return extraction, []
    anchored_cents = parse_total_cents(anchored)
    current_cents = parse_total_cents(extraction.total)
    if anchored_cents is None or current_cents == anchored_cents:
        return extraction, []
    reconciled = extraction.model_copy(update={"total": anchored_cents / 100.0})
    note = (
        f"total {extraction.total!r} -> {anchored_cents / 100:.2f} "
        "from printed TOTAL DUE"
    )
    return reconciled, [note]


def _extract_one(
    text: str,
    *,
    settings: Settings,
    llm: ChatOllama,
) -> tuple[
    RawInvoice | None,
    RawInvoice | None,
    list[str],
    str,
    str | None,
    int,
]:
    started = time.perf_counter()
    raw_response = ""
    error: str | None = None
    invocation = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=USER_PROMPT.format(text=text[:16000])),
    ]
    for attempt in range(1, 3):
        try:
            response = llm.invoke(invocation)
            raw_response = _content_to_text(response.content)
            parsed = parse_json_object(raw_response)
            model_extraction = RawInvoice.model_validate(parsed)
            extraction, notes = _reconcile_total(text, model_extraction)
            return (
                extraction,
                model_extraction,
                notes,
                raw_response,
                None,
                int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:  # noqa: BLE001 - model output is untrusted
            error = f"{type(exc).__name__}: {exc}"
            logger.warning("Extraction attempt %s failed: %s", attempt, error)
            if attempt == 1:
                invocation = invocation + [
                    HumanMessage(
                        content=(
                            "Your previous response was not valid JSON matching "
                            "the schema. Return only the five-key JSON object."
                        )
                    )
                ]
    return (
        None,
        None,
        [],
        raw_response,
        error,
        int((time.perf_counter() - started) * 1000),
    )


def _cache_path(settings: Settings, document_id: str) -> Path:
    return settings.extracted_dir / f"{document_id}.json"


def _load_cached(
    settings: Settings, document_id: str, text_hash: str
) -> ExtractionRecord | None:
    path = _cache_path(settings, document_id)
    if not path.exists():
        return None
    try:
        record = ExtractionRecord.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        record.text_sha256 != text_hash
        or record.model != settings.model
        or record.prompt_version != PROMPT_VERSION
        or record.extraction is None
        or record.error
    ):
        return None
    return record


def run_extraction(
    settings: Settings,
    *,
    force: bool = False,
    limit: int = 0,
    progress: bool = True,
) -> list[ExtractionRecord]:
    settings.ensure_directories()
    check_ollama(settings)
    llm = build_llm(settings)

    # Only OCR files whose source PDF is still present.
    pdf_by_id = {
        document_id_for_pdf(path): path for path in settings.invoices_dir.glob("*.pdf")
    }
    ocr_files = sorted(settings.ocr_dir.glob("*.txt"))
    if limit > 0:
        ocr_files = ocr_files[:limit]
    if not ocr_files:
        raise RuntimeError(
            f"No OCR text found in {settings.ocr_dir}. Run the OCR step first."
        )

    records: list[ExtractionRecord] = []
    for index, ocr_path in enumerate(ocr_files, start=1):
        document_id = ocr_path.stem
        source_path = pdf_by_id.get(document_id)
        source_file = source_path.name if source_path else f"{document_id}.pdf"
        text = load_ocr_text(settings, document_id)
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

        if not force:
            cached = _load_cached(settings, document_id, text_hash)
            if cached:
                records.append(cached)
                if progress:
                    logger.info(
                        "[extract] %02d/%02d %s cached",
                        index,
                        len(ocr_files),
                        document_id,
                    )
                continue

        metadata: OcrMetadata | None = load_ocr_metadata(settings, document_id)
        confidence = metadata.mean_confidence if metadata else 0.0
        if progress:
            logger.info(
                "[extract] %02d/%02d %s (%s)",
                index,
                len(ocr_files),
                document_id,
                settings.model,
            )

        if not text.strip():
            record = ExtractionRecord(
                document_id=document_id,
                source_file=source_file,
                model=settings.model,
                prompt_version=PROMPT_VERSION,
                text_sha256=text_hash,
                error="OCR text is empty.",
            )
        else:
            (
                extraction,
                model_extraction,
                reconciliations,
                raw_response,
                error,
                duration_ms,
            ) = _extract_one(text, settings=settings, llm=llm)
            record = ExtractionRecord(
                document_id=document_id,
                source_file=source_file,
                model=settings.model,
                prompt_version=PROMPT_VERSION,
                text_sha256=text_hash,
                extraction=extraction,
                model_extraction=model_extraction,
                reconciliations=reconciliations,
                raw_response=raw_response,
                duration_ms=duration_ms,
                error=error,
            )
        _cache_path(settings, document_id).write_text(
            record.model_dump_json(indent=2), encoding="utf-8"
        )
        records.append(record)
        if progress:
            status = "ok" if record.error is None else "error"
            logger.info(
                "[extract] %s %s in %.1fs",
                document_id,
                status,
                record.duration_ms / 1000,
            )
            for note in record.reconciliations:
                logger.info("[extract] %s reconciled: %s", document_id, note)
    return records


def load_extraction_records(settings: Settings) -> list[ExtractionRecord]:
    records: list[ExtractionRecord] = []
    for path in sorted(settings.extracted_dir.glob("*.json")):
        try:
            records.append(
                ExtractionRecord.model_validate_json(path.read_text(encoding="utf-8"))
            )
        except (OSError, ValueError):
            continue
    return records
