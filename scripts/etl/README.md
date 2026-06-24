# ETL Scripts

`ingest_arcades.py` 用于将 `data/raw/bemanicn/shops_detail.jsonl` 规范化为入仓产物，并输出质量报告：

```bash
python scripts/etl/ingest_arcades.py \
  --input data/raw/bemanicn/shops_detail.jsonl \
  --run-summary data/raw/bemanicn/run_summary.json \
  --output-dir data/processed/bemanicn \
  --sqlite-path data/processed/arcadegent.db
```

产物：

- `arcade_shops.jsonl`
- `arcade_titles.jsonl`
- `bad_rows.jsonl`
- `qa_report.json`
- `ingest_run.json`

