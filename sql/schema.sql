PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

DROP VIEW IF EXISTS vw_rejected_documents;
DROP VIEW IF EXISTS vw_rejection_summary;
DROP VIEW IF EXISTS vw_monthly_spend;
DROP VIEW IF EXISTS vw_spend_by_vendor;
DROP VIEW IF EXISTS vw_invoices;
DROP VIEW IF EXISTS vw_latest_run;

CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id              TEXT PRIMARY KEY,
    started_at          TEXT NOT NULL,
    finished_at         TEXT NOT NULL,
    model               TEXT NOT NULL,
    documents_seen      INTEGER NOT NULL CHECK (documents_seen >= 0),
    accepted            INTEGER NOT NULL CHECK (accepted >= 0),
    rejected            INTEGER NOT NULL CHECK (rejected >= 0),
    total_spend_cents   INTEGER NOT NULL DEFAULT 0,
    avg_ocr_confidence  REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS invoices (
    invoice_pk          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    document_id         TEXT NOT NULL,
    invoice_number      TEXT NOT NULL,
    invoice_date        TEXT NOT NULL,
    vendor              TEXT NOT NULL,
    total_cents         INTEGER NOT NULL CHECK (total_cents > 0),
    currency            TEXT NOT NULL,
    source_file         TEXT NOT NULL,
    ocr_confidence      REAL NOT NULL DEFAULT 0,
    extraction_model    TEXT NOT NULL,
    raw_extraction_json TEXT,
    created_at          TEXT NOT NULL,
    UNIQUE (run_id, invoice_number),
    UNIQUE (run_id, document_id)
);

CREATE TABLE IF NOT EXISTS rejected_documents (
    rejection_pk        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    document_id         TEXT NOT NULL,
    source_file         TEXT NOT NULL,
    stage               TEXT NOT NULL,
    reason_codes_json   TEXT NOT NULL,
    reasons_json        TEXT NOT NULL,
    raw_extraction_json TEXT,
    ocr_confidence      REAL NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    UNIQUE (run_id, document_id)
);

CREATE TABLE IF NOT EXISTS rejection_reasons (
    run_id              TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    document_id         TEXT NOT NULL,
    reason_code         TEXT NOT NULL,
    reason_text         TEXT NOT NULL,
    PRIMARY KEY (run_id, document_id, reason_code)
);

CREATE INDEX IF NOT EXISTS idx_invoices_run ON invoices(run_id);
CREATE INDEX IF NOT EXISTS idx_invoices_vendor ON invoices(vendor);
CREATE INDEX IF NOT EXISTS idx_rejected_run ON rejected_documents(run_id);

CREATE VIEW IF NOT EXISTS vw_latest_run AS
SELECT run_id
FROM ingestion_runs
ORDER BY rowid DESC
LIMIT 1;

CREATE VIEW IF NOT EXISTS vw_invoices AS
SELECT
    i.invoice_number,
    i.invoice_date,
    i.vendor,
    i.currency,
    i.total_cents / 100.0 AS total_amount,
    i.total_cents,
    i.source_file,
    i.ocr_confidence,
    i.extraction_model,
    i.run_id
FROM invoices i
WHERE i.run_id = (SELECT run_id FROM vw_latest_run);

CREATE VIEW IF NOT EXISTS vw_spend_by_vendor AS
SELECT
    vendor,
    currency,
    COUNT(*) AS invoice_count,
    ROUND(SUM(total_cents) / 100.0, 2) AS spend,
    ROUND(AVG(total_cents) / 100.0, 2) AS avg_invoice
FROM invoices
WHERE run_id = (SELECT run_id FROM vw_latest_run)
GROUP BY vendor, currency
ORDER BY spend DESC;

CREATE VIEW IF NOT EXISTS vw_monthly_spend AS
SELECT
    substr(invoice_date, 1, 7) AS month,
    COUNT(*) AS invoice_count,
    ROUND(SUM(total_cents) / 100.0, 2) AS spend
FROM invoices
WHERE run_id = (SELECT run_id FROM vw_latest_run)
GROUP BY substr(invoice_date, 1, 7)
ORDER BY month;

CREATE VIEW IF NOT EXISTS vw_rejection_summary AS
SELECT
    reason_code,
    COUNT(DISTINCT document_id) AS document_count,
    ROUND(100.0 * COUNT(DISTINCT document_id) /
        (SELECT COUNT(*) FROM rejected_documents
         WHERE run_id = (SELECT run_id FROM vw_latest_run)), 1) AS pct_of_rejections
FROM rejection_reasons
WHERE run_id = (SELECT run_id FROM vw_latest_run)
GROUP BY reason_code
ORDER BY document_count DESC, reason_code;

CREATE VIEW IF NOT EXISTS vw_rejected_documents AS
SELECT
    document_id,
    source_file,
    stage,
    reason_codes_json,
    reasons_json,
    ocr_confidence
FROM rejected_documents
WHERE run_id = (SELECT run_id FROM vw_latest_run)
ORDER BY document_id;

