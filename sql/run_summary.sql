-- Headline metrics for the most recent pipeline run.
SELECT
    r.run_id,
    r.started_at,
    r.finished_at,
    r.model,
    r.documents_seen,
    r.accepted,
    r.rejected,
    ROUND(r.total_spend_cents / 100.0, 2) AS total_spend,
    ROUND(r.avg_ocr_confidence, 1) AS avg_ocr_confidence,
    ROUND(100.0 * r.accepted / NULLIF(r.documents_seen, 0), 1) AS straight_through_pct,
    ROUND(100.0 * r.rejected / NULLIF(r.documents_seen, 0), 1) AS rejection_pct
FROM ingestion_runs r
WHERE r.run_id = (SELECT run_id FROM vw_latest_run);
