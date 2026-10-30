"""PDF rasterization and Tesseract OCR.

PyMuPDF does the rendering so there is no Poppler install on Windows. The OCR
engine itself is still Tesseract, as in the original plan.
"""

from __future__ import annotations

import io
import json
import logging
import re
import shutil
import time
from pathlib import Path

import pymupdf
import pytesseract
from PIL import Image
from pytesseract import Output

from .config import Settings
from .models import OcrMetadata

logger = logging.getLogger(__name__)

_WINDOWS_TESSERACT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
_DOC_ID_RE = re.compile(r"^(?:doc-)?(\d+)$", re.IGNORECASE)


def document_id_for_pdf(path: Path) -> str:
    """Map ``invoice_001.pdf`` -> ``doc-001`` without needing extra state."""

    match = re.search(r"(\d{3,})$", path.stem)
    if not match:
        match = _DOC_ID_RE.search(path.stem)
    if not match:
        return path.stem
    return f"doc-{int(match.group(1)):03d}"


def resolve_tesseract(settings: Settings) -> str:
    candidates = [
        settings.tesseract_cmd,
        shutil.which("tesseract"),
        str(_WINDOWS_TESSERACT),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate))
    raise RuntimeError(
        "Tesseract was not found. Install it (scripts/setup.ps1) or set "
        "TESSERACT_CMD to the full path of tesseract.exe."
    )


def configure_tesseract(settings: Settings) -> str:
    executable = resolve_tesseract(settings)
    pytesseract.pytesseract.tesseract_cmd = executable
    return executable


def _ocr_image(image: Image.Image) -> tuple[str, float]:
    text = pytesseract.image_to_string(
        image, lang="eng", config="--oem 3 --psm 3"
    )
    data = pytesseract.image_to_data(
        image, lang="eng", config="--oem 3 --psm 3", output_type=Output.DICT
    )
    confidences: list[float] = []
    for raw_conf, word in zip(data.get("conf", []), data.get("text", [])):
        try:
            conf = float(raw_conf)
        except (TypeError, ValueError):
            continue
        if conf >= 0 and str(word).strip():
            confidences.append(conf)
    mean_conf = sum(confidences) / len(confidences) if confidences else 0.0
    return text, round(mean_conf, 2)


def ocr_pdf(path: Path, settings: Settings) -> OcrMetadata:
    configure_tesseract(settings)
    started = time.perf_counter()
    document_id = document_id_for_pdf(path)
    doc = pymupdf.open(path)
    try:
        page_texts: list[str] = []
        confidences: list[float] = []
        zoom = settings.dpi / 72.0
        matrix = pymupdf.Matrix(zoom, zoom)
        for page_number, page in enumerate(doc, start=1):
            pixmap = page.get_pixmap(
                matrix=matrix, colorspace=pymupdf.csGRAY, alpha=False
            )
            image = Image.open(io.BytesIO(pixmap.tobytes("png")))
            text, confidence = _ocr_image(image)
            page_texts.append(f"--- Page {page_number} ---\n{text.strip()}")
            confidences.append(confidence)
        page_count = max(len(page_texts), 1)
        full_text = "\n\n".join(page_texts).strip() + "\n"
    finally:
        doc.close()

    duration_ms = int((time.perf_counter() - started) * 1000)
    mean_confidence = round(sum(confidences) / len(confidences), 2) if confidences else 0.0
    metadata = OcrMetadata(
        document_id=document_id,
        source_file=path.name,
        page_count=page_count,
        mean_confidence=mean_confidence,
        char_count=len(full_text),
        dpi=settings.dpi,
        duration_ms=duration_ms,
    )

    settings.ocr_dir.mkdir(parents=True, exist_ok=True)
    (settings.ocr_dir / f"{document_id}.txt").write_text(full_text, encoding="utf-8")
    (settings.ocr_dir / f"{document_id}.json").write_text(
        metadata.model_dump_json(indent=2), encoding="utf-8"
    )
    return metadata


def _cache_is_valid(path: Path, settings: Settings) -> bool:
    document_id = document_id_for_pdf(path)
    text_path = settings.ocr_dir / f"{document_id}.txt"
    meta_path = settings.ocr_dir / f"{document_id}.json"
    if not text_path.exists() or not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return meta.get("dpi") == settings.dpi and meta.get("source_file") == path.name


def run_ocr(
    settings: Settings,
    *,
    force: bool = False,
    limit: int = 0,
    progress: bool = True,
) -> list[OcrMetadata]:
    """OCR every PDF, reusing cached text unless ``force`` is set."""

    settings.ensure_directories()
    configure_tesseract(settings)
    pdfs = sorted(settings.invoices_dir.glob("*.pdf"))
    if limit > 0:
        pdfs = pdfs[:limit]
    if not pdfs:
        raise RuntimeError(
            f"No PDFs found in {settings.invoices_dir}. Run the sample generator first."
        )

    results: list[OcrMetadata] = []
    for index, pdf in enumerate(pdfs, start=1):
        document_id = document_id_for_pdf(pdf)
        if not force and _cache_is_valid(pdf, settings):
            metadata = OcrMetadata.model_validate_json(
                (settings.ocr_dir / f"{document_id}.json").read_text(encoding="utf-8")
            )
            results.append(metadata)
            if progress:
                logger.info("[ocr] %02d/%02d %s cached", index, len(pdfs), document_id)
            continue
        if progress:
            logger.info("[ocr] %02d/%02d %s", index, len(pdfs), pdf.name)
        metadata = ocr_pdf(pdf, settings)
        results.append(metadata)
        if progress:
            logger.info(
                "[ocr] %s done: %s chars, confidence %.1f",
                metadata.document_id,
                metadata.char_count,
                metadata.mean_confidence,
            )
    return results


def load_ocr_metadata(settings: Settings, document_id: str) -> OcrMetadata | None:
    path = settings.ocr_dir / f"{document_id}.json"
    if not path.exists():
        return None
    return OcrMetadata.model_validate_json(path.read_text(encoding="utf-8"))


def load_ocr_text(settings: Settings, document_id: str) -> str:
    path = settings.ocr_dir / f"{document_id}.txt"
    return path.read_text(encoding="utf-8") if path.exists() else ""
