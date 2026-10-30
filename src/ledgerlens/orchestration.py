"""Dagster asset graph for the complete ingestion chain.

Run the whole graph with ``ledgerlens run`` or explore it with
``dagster dev -m ledgerlens.orchestration``.
"""

import os
from dataclasses import dataclass

from dagster import (
    AssetExecutionContext,
    Config,
    Definitions,
    MaterializeResult,
    MetadataValue,
    asset,
    define_asset_job,
    materialize,
)

from .config import Settings, get_settings
from .dashboard import build_dashboard
from .evaluation import evaluate
from .extract import run_extraction
from .generate_samples import generate_samples
from .ocr import run_ocr
from .storage import build_run_summary, store_validation_run
from .validation import build_validation_report


class SampleConfig(Config):
    force: bool = False


class OcrConfig(Config):
    force: bool = False
    limit: int = 0


class ExtractConfig(Config):
    force: bool = False
    limit: int = 0


@dataclass
class PipelineOptions:
    force_samples: bool = False
    force_ocr: bool = False
    force_extraction: bool = False
    limit: int = 0


def _settings() -> Settings:
    return get_settings()


@asset(group_name="ingestion", compute_kind="python")
def source_documents(context: AssetExecutionContext, config: SampleConfig) -> MaterializeResult:
    """Create (or reuse) the synthetic invoice batch and ground truth."""

    result = generate_samples(_settings(), force=config.force)
    context.log.info("Sample documents: %s", result)
    return MaterializeResult(
        metadata={
            "pdfs": result["pdfs"],
            "reused": result["reused"],
            "data_dir": MetadataValue.path(str(_settings().invoices_dir)),
        }
    )


@asset(deps=[source_documents], group_name="ingestion", compute_kind="tesseract")
def ocr_text(context: AssetExecutionContext, config: OcrConfig) -> MaterializeResult:
    """Render PDF pages and run Tesseract, caching raw text."""

    metadata = run_ocr(
        _settings(),
        force=config.force,
        limit=config.limit,
    )
    avg_conf = (
        sum(item.mean_confidence for item in metadata) / len(metadata)
        if metadata
        else 0
    )
    context.log.info(
        "OCR complete: %s documents, average confidence %.2f",
        len(metadata),
        avg_conf,
    )
    return MaterializeResult(
        metadata={
            "documents": len(metadata),
            "avg_confidence": round(avg_conf, 2),
            "ocr_dir": MetadataValue.path(str(_settings().ocr_dir)),
        }
    )


@asset(deps=[ocr_text], group_name="extraction", compute_kind="ollama")
def extracted_invoices(context: AssetExecutionContext, config: ExtractConfig) -> MaterializeResult:
    """Use LangChain + a local Ollama model to produce structured JSON."""

    records = run_extraction(
        _settings(),
        force=config.force,
        limit=config.limit,
    )
    failures = sum(1 for record in records if record.error)
    context.log.info("Extraction complete: %s records, %s failures", len(records), failures)
    return MaterializeResult(
        metadata={
            "documents": len(records),
            "failures": failures,
            "model": _settings().model,
            "extracted_dir": MetadataValue.path(str(_settings().extracted_dir)),
        }
    )


@asset(deps=[extracted_invoices], group_name="sql", compute_kind="pydantic")
def validated_invoices(context: AssetExecutionContext) -> MaterializeResult:
    """Apply deterministic field and business validation."""

    report = build_validation_report(_settings())
    context.log.info(
        "Validation complete: %s accepted, %s rejected",
        len(report.accepted),
        len(report.rejected),
    )
    return MaterializeResult(
        metadata={
            "seen": report.seen,
            "accepted": len(report.accepted),
            "rejected": len(report.rejected),
            "report": MetadataValue.path(str(_settings().validation_report_path)),
        }
    )


@asset(deps=[validated_invoices], group_name="sql", compute_kind="sqlite")
def invoice_ledger(context: AssetExecutionContext) -> MaterializeResult:
    """Load accepted records and measure extraction quality."""

    run_id = store_validation_run(_settings())
    evaluation = evaluate(_settings(), run_id=run_id)
    build_run_summary(_settings())
    context.log.info("Stored run %s and evaluated extraction", run_id)
    return MaterializeResult(
        metadata={
            "run_id": run_id,
            "field_accuracy_pct": evaluation["extraction"]["average_field_accuracy_pct"],
            "database": MetadataValue.path(str(_settings().database_path)),
            "evaluation": MetadataValue.path(str(_settings().evaluation_path)),
        }
    )


@asset(deps=[invoice_ledger], group_name="delivery", compute_kind="html")
def operations_dashboard(context: AssetExecutionContext) -> MaterializeResult:
    """Build the demo dashboard and Power BI-ready extracts."""

    path = build_dashboard(_settings())
    context.log.info("Dashboard complete: %s", path)
    return MaterializeResult(
        metadata={
            "dashboard": MetadataValue.path(path),
            "exports": MetadataValue.path(str(_settings().exports_dir)),
        }
    )


ledgerlens_job = define_asset_job(
    name="ledgerlens_pipeline",
    selection=[
        source_documents,
        ocr_text,
        extracted_invoices,
        validated_invoices,
        invoice_ledger,
        operations_dashboard,
    ],
    description="PDF -> OCR -> local LLM -> validation -> SQLite -> dashboard.",
)


defs = Definitions(
    assets=[
        source_documents,
        ocr_text,
        extracted_invoices,
        validated_invoices,
        invoice_ledger,
        operations_dashboard,
    ],
    jobs=[ledgerlens_job],
)


def run_pipeline(settings: Settings, options: PipelineOptions) -> bool:
    """Materialize the full asset graph in-process with one call."""

    # Asset functions resolve settings through the environment. Keep the
    # caller's chosen directories/model authoritative for this run.
    os.environ["LEDGERLENS_DATA_DIR"] = str(settings.data_dir)
    os.environ["LEDGERLENS_MODEL"] = settings.model
    os.environ["OLLAMA_BASE_URL"] = settings.ollama_base_url
    if settings.tesseract_cmd:
        os.environ["TESSERACT_CMD"] = settings.tesseract_cmd

    run_config = {
        "ops": {
            "source_documents": {"config": {"force": options.force_samples}},
            "ocr_text": {
                "config": {"force": options.force_ocr, "limit": options.limit}
            },
            "extracted_invoices": {
                "config": {
                    "force": options.force_extraction,
                    "limit": options.limit,
                }
            },
        }
    }
    result = materialize(
        [
            source_documents,
            ocr_text,
            extracted_invoices,
            validated_invoices,
            invoice_ledger,
            operations_dashboard,
        ],
        run_config=run_config,
        raise_on_error=False,
    )
    return result.success

