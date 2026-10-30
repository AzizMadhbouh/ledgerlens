-- Spend by vendor for the most recent pipeline run.
SELECT
    vendor,
    currency,
    invoice_count,
    spend,
    avg_invoice
FROM vw_spend_by_vendor
ORDER BY spend DESC;
