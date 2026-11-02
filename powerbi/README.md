# Power BI connection

The SQLite database is the source of truth. Power BI Desktop can reach it in
three ways; the ODBC route is the recommended live connection.

## Option A - SQLite ODBC (recommended)

1. Install the 64-bit **SQLite ODBC Driver** by Christian Werner:
   <http://www.ch-werner.de/sqliteodbc/>. Power BI Desktop is 64-bit.
2. Open **ODBC Data Sources (64-bit)** from Windows Search and create a
   **User DSN**:
   - Name: `LedgerLens`
   - Database: `C:\Users\<you>\Documents\Default Project\ledgerlens\data\ledgerlens.db`
3. In Power BI Desktop: **Get Data -> ODBC**, select `LedgerLens`.
4. Load these views:
   - `vw_invoices` - one row per accepted invoice; use `total_amount`,
     `invoice_date`, `vendor`, `currency`, `ocr_confidence`.
   - `vw_rejected_documents` - documents that failed OCR/LLM/business rules.
   - Optional pre-aggregated views: `vw_spend_by_vendor`, `vw_monthly_spend`,
     `vw_rejection_summary`.
5. Set `invoice_date` to **Date** and `total_amount` to **Fixed decimal number**.
6. Add the measures from [`measures.dax`](measures.dax).
7. Load the theme in [`ledgerlens-theme.json`](ledgerlens-theme.json) via
   **View -> Themes -> Browse for themes**.

## Option B - CSV (fastest, no driver)

Every pipeline run writes:

```
data/exports/fact_invoices.csv
data/exports/spend_by_vendor.csv
data/exports/monthly_spend.csv
data/exports/rejection_summary.csv
data/exports/rejected_documents.csv
```

Use **Get Data -> Text/CSV**. The dashboard page is then the same as below.

## Option C - Python data source

In Power BI Desktop, enable **Python scripting**, then use
**Get Data -> Python script**:

```python
import sqlite3
import pandas as pd

con = sqlite3.connect(
    r"C:\Users\azizm\Documents\Default Project\ledgerlens\data\ledgerlens.db"
)
invoices = pd.read_sql_query("SELECT * FROM vw_invoices", con)
rejected = pd.read_sql_query("SELECT * FROM vw_rejected_documents", con)
```

This requires the same Python environment or a separate Python installation
with pandas. ODBC is more convenient for refresh.

## Option D - included report and template

`ledgerlens.pbix` is a saved one-page report built from the CSV exports. It is a
snapshot of the 25-document run in the repository README.

`ledgerlens.pbit` is the same page with no embedded data and a `DataFolder`
parameter:

1. Run `ledgerlens run` so `data/exports/*.csv` exists.
2. Open `ledgerlens.pbit` in Power BI Desktop.
3. When prompted, set `DataFolder` to the absolute path of `data/exports`
   (no trailing backslash).
4. Click **Load**, then **Refresh**.

The template binds by column name. A later run with the same schema refreshes
in place; renaming a column requires updating the visual binding.

## Page 1 layout

**Header:** `LedgerLens — invoice operations` on the page background.

**KPI cards (top row):**
- Total Spend
- Invoice Count
- Rejected Documents
- Straight-Through Rate
- Avg OCR Confidence

**Charts:**
- Clustered bar chart: `vendor` on Y, `Total Spend` on X, sorted descending.
- Line chart: `invoice_date` (or `month`) on X, `Total Spend` on Y.
- Donut: accepted vs rejected (the current run is 21 / 4).
- Rejected documents table: `document_id`, `source_file`, `reason_codes_json`,
  `ocr_confidence`.

**Slicers:** `currency` and `invoice_date`.

**DAX measures**

```dax
Total Spend = SUM(vw_invoices[total_amount])
Invoice Count = COUNTROWS(vw_invoices)
Rejected Documents = COUNTROWS(vw_rejected_documents)
Processed Documents = [Invoice Count] + [Rejected Documents]
Straight-Through Rate = DIVIDE([Invoice Count], [Processed Documents])
Rejection Rate = DIVIDE([Rejected Documents], [Processed Documents])
```

The same numbers are rendered in `data/dashboard.html` for environments where
Power BI Desktop is not installed. Power BI Desktop is required to open the
`.pbix` and `.pbit`; the pipeline cannot generate the report layout headlessly.
