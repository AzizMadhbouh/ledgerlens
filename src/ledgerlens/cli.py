"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import webbrowser
from dataclasses import replace
from pathlib import Path

import pandas as pd

from . import __version__
from .config import Settings, get_settings
from .dashboard import build_dashboard
from .evaluation import evaluate
from .extract import check_ollama, run_extraction
from .generate_samples import generate_samples
from .ocr import configure_tesseract, run_ocr
from .storage import (
    build_run_summary,
    get_monthly_spend,
    get_rejected,
    get_rejection_summary,
    get_run_history,
    get_run_summary,
    get_vendor_spend,
    store_validation_run,
)
from .validation import build_validation_report, load_validation_report

logger = logging.getLogger("ledgerlens")


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def _settings_from_args(args: argparse.Namespace) -> Settings:
    settings = Settings.from_env()
    model = getattr(args, "model", None)
    if model:
        settings = replace(settings, model=model)
    settings.ensure_directories()
    return settings


def _print_table(rows: list[dict]) -> None:
    if not rows:
        print("No rows.")
        return
    frame = pd.DataFrame(rows)
    print(frame.to_string(index=False))


def _print_run_summary(settings: Settings) -> None:
    summary = get_run_summary(settings)
    if not summary:
        return
    print()
    print("Run summary")
    print("-----------")
    print(f"Run:               {summary['run_id']}")
    print(f"Model:             {summary['model']}")
    print(f"Documents:         {summary['documents_seen']}")
    print(f"Accepted:          {summary['accepted']}")
    print(f"Rejected:          {summary['rejected']}")
    print(f"Total spend:       ${float(summary['total_spend']):,.2f}")
    print(f"Straight-through:  {float(summary['straight_through_pct']):.1f}%")
    print(f"OCR confidence:    {float(summary['avg_ocr_confidence']):.1f}%")
    print()
    print(f"Database:          {settings.database_path}")
    print(f"Dashboard:         {settings.dashboard_path}")
    print(f"Power BI exports:  {settings.exports_dir}")


def cmd_doctor(args: argparse.Namespace) -> int:
    settings = _settings_from_args(args)
    ok = True
    print(f"LedgerLens {__version__}")
    print(f"Project root: {settings.project_root}")
    print(f"Data dir:     {settings.data_dir}")
    try:
        executable = configure_tesseract(settings)
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        version = (result.stdout or result.stderr).splitlines()[0]
        print(f"Tesseract:    OK - {version}")
    except Exception as exc:  # noqa: BLE001 - diagnostic command
        ok = False
        print(f"Tesseract:    MISSING - {exc}")
    try:
        check_ollama(settings)
        print(f"Ollama:       OK - {settings.ollama_base_url} / {settings.model}")
    except Exception as exc:  # noqa: BLE001 - diagnostic command
        ok = False
        print(f"Ollama:       NOT READY - {exc}")
    pdfs = len(list(settings.invoices_dir.glob("*.pdf")))
    ocr = len(list(settings.ocr_dir.glob("*.txt")))
    extracted = len(list(settings.extracted_dir.glob("*.json")))
    print(f"Artifacts:    {pdfs} PDFs, {ocr} OCR texts, {extracted} extractions")
    print(f"SQLite:       {'present' if settings.database_path.exists() else 'not built'}")
    if settings.dashboard_path.exists():
        print(f"Dashboard:    {settings.dashboard_path}")
    return 0 if ok else 1


def cmd_generate(args: argparse.Namespace) -> int:
    settings = _settings_from_args(args)
    result = generate_samples(settings, force=args.force)
    print(f"Generated/reused {result['pdfs']} PDFs in {settings.invoices_dir}")
    print(f"Ground truth: {settings.ground_truth_path}")
    return 0


def cmd_ocr(args: argparse.Namespace) -> int:
    settings = _settings_from_args(args)
    metadata = run_ocr(settings, force=args.force, limit=args.limit)
    print(f"OCR complete: {len(metadata)} documents")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    settings = _settings_from_args(args)
    records = run_extraction(settings, force=args.force, limit=args.limit)
    failures = sum(1 for record in records if record.error)
    print(f"Extraction complete: {len(records)} documents, {failures} failures")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    settings = _settings_from_args(args)
    report = build_validation_report(settings)
    print(
        f"Validation complete: {report.seen} seen, "
        f"{len(report.accepted)} accepted, {len(report.rejected)} rejected"
    )
    print(f"Report: {settings.validation_report_path}")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    settings = _settings_from_args(args)
    report = load_validation_report(settings)
    run_id = store_validation_run(settings, report)
    evaluation = evaluate(settings, report, run_id=run_id)
    build_dashboard(settings, evaluation=evaluation)
    _print_run_summary(settings)
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    settings = _settings_from_args(args)
    path = build_dashboard(settings)
    print(f"Dashboard: {path}")
    print(f"CSV exports: {settings.exports_dir}")
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    settings = _settings_from_args(args)
    datasets = {
        "summary": get_run_summary,
        "vendors": get_vendor_spend,
        "monthly": get_monthly_spend,
        "rejected": get_rejected,
        "reasons": get_rejection_summary,
    }
    if args.dataset == "all":
        for name, function in datasets.items():
            print(f"\n{name.upper()}")
            _print_table([function(settings)] if name == "summary" else function(settings))
    else:
        function = datasets[args.dataset]
        rows = function(settings)
        _print_table([rows] if args.dataset == "summary" else rows)
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    settings = _settings_from_args(args)
    _print_table(get_run_history(settings))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    settings = _settings_from_args(args)
    from .orchestration import PipelineOptions, run_pipeline

    options = PipelineOptions(
        force_samples=args.force_samples or args.full_refresh,
        force_ocr=args.force_ocr or args.full_refresh,
        force_extraction=args.force_extraction or args.full_refresh,
        limit=args.limit,
    )
    success = run_pipeline(settings, options)
    if not success:
        logger.error("Pipeline failed. Inspect the Dagster output above.")
        return 1
    _print_run_summary(settings)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    settings = _settings_from_args(args)
    if not settings.dashboard_path.exists():
        print("Dashboard not found; building it now.")
        build_dashboard(settings)
    import functools
    import http.server
    import socketserver

    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(settings.data_dir)
    )
    url = f"http://127.0.0.1:{args.port}/dashboard.html"
    print(f"Serving {url} (Ctrl+C to stop)")
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - browser is a convenience
        pass
    with socketserver.TCPServer(("127.0.0.1", args.port), handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
    return 0


def cmd_sql(args: argparse.Namespace) -> int:
    settings = _settings_from_args(args)
    from .storage import query

    rows = query(settings, args.query)
    _print_table(rows)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ledgerlens",
        description="Local-first invoice pipeline: OCR -> Ollama -> SQL -> dashboard.",
    )
    parser.add_argument("--version", action="version", version=f"ledgerlens {__version__}")
    parser.add_argument(
        "--model",
        help="Ollama model name (default: llama3.2:3b or LEDGERLENS_MODEL).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run the full Dagster asset graph.")
    run.add_argument("--full-refresh", action="store_true", help="Ignore all caches.")
    run.add_argument("--force-samples", action="store_true")
    run.add_argument("--force-ocr", action="store_true")
    run.add_argument("--force-extraction", action="store_true")
    run.add_argument(
        "--limit", type=int, default=0, help="Smoke-test with only the first N PDFs."
    )
    run.set_defaults(func=cmd_run)

    doctor = subparsers.add_parser("doctor", help="Check local dependencies and artifacts.")
    doctor.set_defaults(func=cmd_doctor)

    generate = subparsers.add_parser("generate", help="Create the synthetic invoice batch.")
    generate.add_argument("--force", action="store_true")
    generate.set_defaults(func=cmd_generate)

    ocr = subparsers.add_parser("ocr", help="OCR PDFs only.")
    ocr.add_argument("--force", action="store_true")
    ocr.add_argument("--limit", type=int, default=0)
    ocr.set_defaults(func=cmd_ocr)

    extract = subparsers.add_parser("extract", help="Run only local-LLM extraction.")
    extract.add_argument("--force", action="store_true")
    extract.add_argument("--limit", type=int, default=0)
    extract.set_defaults(func=cmd_extract)

    validate = subparsers.add_parser("validate", help="Validate and normalize extractions.")
    validate.set_defaults(func=cmd_validate)

    ingest = subparsers.add_parser("ingest", help="Load SQLite, evaluate and build dashboard.")
    ingest.set_defaults(func=cmd_ingest)

    dashboard = subparsers.add_parser("dashboard", help="Rebuild dashboard and CSV exports.")
    dashboard.set_defaults(func=cmd_dashboard)

    query_parser = subparsers.add_parser("query", help="Print analytical SQL results.")
    query_parser.add_argument(
        "dataset",
        choices=["summary", "vendors", "monthly", "rejected", "reasons", "all"],
    )
    query_parser.set_defaults(func=cmd_query)

    history = subparsers.add_parser("history", help="List previous ingestion runs.")
    history.set_defaults(func=cmd_history)

    serve = subparsers.add_parser("serve", help="Serve the HTML dashboard locally.")
    serve.add_argument("--port", type=int, default=8765)
    serve.set_defaults(func=cmd_serve)

    sql = subparsers.add_parser("sql", help="Run an ad-hoc read-only SQL query.")
    sql.add_argument("query", help="SELECT statement")
    sql.set_defaults(func=cmd_sql)
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - user-facing CLI
        logger.error("%s", exc)
        if logging.getLogger().isEnabledFor(logging.DEBUG):
            raise
        return 1


if __name__ == "__main__":
    sys.exit(main())
