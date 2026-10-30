-- Rejected documents with their automated reasons.
SELECT
    document_id,
    source_file,
    stage,
    reason_codes_json,
    reasons_json,
    ROUND(ocr_confidence, 1) AS ocr_confidence
FROM vw_rejected_documents
ORDER BY document_id;
