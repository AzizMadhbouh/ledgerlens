import csv

from ledgerlens.config import Settings
from ledgerlens.generate_samples import DOCUMENT_COUNT, generate_samples


def test_generated_batch_has_expected_shape(tmp_path):
    settings = Settings(
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        model="test-model",
    )
    settings.ensure_directories()
    result = generate_samples(settings)
    assert result["pdfs"] == DOCUMENT_COUNT
    assert len(list(settings.invoices_dir.glob("*.pdf"))) == DOCUMENT_COUNT
    with settings.ground_truth_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == DOCUMENT_COUNT
    accepted = [row for row in rows if row["expected_status"] == "accepted"]
    rejected = [row for row in rows if row["expected_status"] == "rejected"]
    assert len(accepted) == 21
    assert len(rejected) == 4
    assert {row["expected_reason"] for row in rejected} == {
        "invalid_date",
        "unsupported_currency",
        "non_positive_total",
        "invalid_vendor",
    }


def test_generation_is_idempotent(tmp_path):
    settings = Settings(project_root=tmp_path, data_dir=tmp_path / "data")
    settings.ensure_directories()
    first = generate_samples(settings)
    second = generate_samples(settings)
    assert first["reused"] == 0
    assert second["reused"] == DOCUMENT_COUNT
