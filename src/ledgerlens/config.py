"""Runtime configuration and filesystem layout."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = "llama3.2:3b"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"

load_dotenv(PROJECT_ROOT / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    """Every mutable path and runtime knob in one place."""

    project_root: Path = PROJECT_ROOT
    data_dir: Path = PROJECT_ROOT / "data"
    model: str = DEFAULT_MODEL
    ollama_base_url: str = DEFAULT_OLLAMA_URL
    tesseract_cmd: str | None = None
    dpi: int = 300
    ollama_timeout_seconds: int = 180

    @classmethod
    def from_env(cls) -> "Settings":
        project_root = Path(
            os.getenv("LEDGERLENS_PROJECT_ROOT", str(PROJECT_ROOT))
        ).resolve()
        data_dir = Path(
            os.getenv("LEDGERLENS_DATA_DIR", str(project_root / "data"))
        ).resolve()
        tesseract_cmd = os.getenv("TESSERACT_CMD") or None
        return cls(
            project_root=project_root,
            data_dir=data_dir,
            model=os.getenv("LEDGERLENS_MODEL", DEFAULT_MODEL),
            ollama_base_url=os.getenv(
                "OLLAMA_BASE_URL", DEFAULT_OLLAMA_URL
            ).rstrip("/"),
            tesseract_cmd=tesseract_cmd,
            dpi=int(os.getenv("LEDGERLENS_OCR_DPI", "300")),
            ollama_timeout_seconds=int(
                os.getenv("LEDGERLENS_OLLAMA_TIMEOUT", "180")
            ),
        )

    # ------------------------------------------------------------------
    # Derived paths
    # ------------------------------------------------------------------
    @property
    def invoices_dir(self) -> Path:
        return self.data_dir / "invoices"

    @property
    def ocr_dir(self) -> Path:
        return self.data_dir / "ocr_text"

    @property
    def extracted_dir(self) -> Path:
        return self.data_dir / "extracted"

    @property
    def validation_dir(self) -> Path:
        return self.data_dir / "validation"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def ground_truth_path(self) -> Path:
        return self.data_dir / "ground_truth.csv"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "ledgerlens.db"

    @property
    def dashboard_path(self) -> Path:
        return self.data_dir / "dashboard.html"

    @property
    def validation_report_path(self) -> Path:
        return self.validation_dir / "validation_report.json"

    @property
    def run_summary_path(self) -> Path:
        return self.reports_dir / "run_summary.json"

    @property
    def evaluation_path(self) -> Path:
        return self.reports_dir / "evaluation.json"

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir,
            self.invoices_dir,
            self.ocr_dir,
            self.extracted_dir,
            self.validation_dir,
            self.exports_dir,
            self.reports_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    settings = Settings.from_env()
    settings.ensure_directories()
    return settings
