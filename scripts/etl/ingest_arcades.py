#!/usr/bin/env python3
"""ETL layer: normalize shops_detail.jsonl into warehouse-ready outputs with QA reports.

Responsibilities:
1. Parse and validate line-delimited JSON records.
2. Produce normalized `arcade_shops` and `arcade_titles` JSONL artifacts.
3. Capture bad rows without interrupting whole-batch ingestion.
4. Optionally sink normalized data into local SQLite for API smoke testing.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest arcade JSONL with bad-row tolerance.")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/raw/bemanicn/shops_detail.jsonl"),
        help="Input shops detail JSONL path.",
    )
    parser.add_argument(
        "--run-summary",
        type=Path,
        default=Path("data/raw/bemanicn/run_summary.json"),
        help="Run summary JSON path produced by crawler.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/processed/bemanicn"),
        help="Output directory for normalized artifacts.",
    )
    parser.add_argument(
        "--batch-id",
        type=str,
        default=None,
        help="Optional ingestion batch id. Auto-generated when omitted.",
    )
    parser.add_argument(
        "--sqlite-path",
        type=Path,
        default=None,
        help="Optional SQLite db file for local runtime queries.",
    )
    return parser.parse_args()


def _coerce_code(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


@dataclass
class ParseIssue:
    line_no: int
    reason: str
    raw_line: str

    def to_dict(self) -> dict[str, Any]:
        return {"line_no": self.line_no, "reason": self.reason, "raw_line": self.raw_line}


def normalize_shop(raw: dict[str, Any], batch_id: str, line_no: int) -> tuple[dict[str, Any] | None, list[ParseIssue]]:
    issues: list[ParseIssue] = []
    required = ("source", "source_id", "source_url", "name")
    missing = [field for field in required if raw.get(field) in (None, "")]
    if missing:
        issues.append(
            ParseIssue(
                line_no=line_no,
                reason=f"missing_required:{','.join(missing)}",
                raw_line=_json(raw),
            )
        )
        return None, issues

    source_id = _coerce_int(raw.get("source_id"))
    if source_id is None:
        issues.append(ParseIssue(line_no=line_no, reason="invalid_source_id", raw_line=_json(raw)))
        return None, issues

    arcades = raw.get("arcades")
    if not isinstance(arcades, list):
        issues.append(
            ParseIssue(
                line_no=line_no,
                reason="arcades_not_array",
                raw_line=_json({"source_id": source_id, "arcades": arcades}),
            )
        )
        arcades = []

    shop = {
        "source": str(raw.get("source")),
        "source_id": source_id,
        "source_url": str(raw.get("source_url")),
        "name": str(raw.get("name")),
        "name_pinyin": raw.get("name_pinyin"),
        "address": raw.get("address"),
        "transport": raw.get("transport"),
        "url": raw.get("url"),
        "comment": raw.get("comment"),
        "province_code": _coerce_code(raw.get("province_code")),
        "province_name": raw.get("province_name"),
        "city_code": _coerce_code(raw.get("city_code")),
        "city_name": raw.get("city_name"),
        "county_code": _coerce_code(raw.get("county_code")),
        "county_name": raw.get("county_name"),
        "longitude_gcj02": raw.get("longitude_gcj02"),
        "latitude_gcj02": raw.get("latitude_gcj02"),
        "longitude_wgs84": raw.get("longitude_wgs84"),
        "latitude_wgs84": raw.get("latitude_wgs84"),
        "geo_source": raw.get("geo_source"),
        "geo_precision": raw.get("geo_precision"),
        "status": raw.get("status"),
        "type": raw.get("type"),
        "pay_type": raw.get("pay_type"),
        "locked": raw.get("locked"),
        "ea_status": raw.get("ea_status"),
        "price": raw.get("price"),
        "start_time": raw.get("start_time"),
        "end_time": raw.get("end_time"),
        "fav_count": _coerce_int(raw.get("fav_count")),
        "created_at_src": raw.get("created_at"),
        "updated_at_src": raw.get("updated_at"),
        "option1": raw.get("option1"),
        "option2": raw.get("option2"),
        "option3": raw.get("option3"),
        "option4": raw.get("option4"),
        "option5": raw.get("option5"),
        "collab": raw.get("collab"),
        "image_thumb": raw.get("image_thumb"),
        "events": raw.get("events") if isinstance(raw.get("events"), list) else [],
        "raw": raw,
        "ingest_batch_id": batch_id,
    }

    title_rows: list[dict[str, Any]] = []
    for entry in arcades:
        if not isinstance(entry, dict):
            issues.append(
                ParseIssue(
                    line_no=line_no,
                    reason="arcade_item_not_object",
                    raw_line=_json({"source_id": source_id, "arcade_item": entry}),
                )
            )
            continue
        title_rows.append(
            {
                "source": shop["source"],
                "source_id": source_id,
                "arcade_item_id": _coerce_int(entry.get("id")),
                "title_id": entry.get("title_id"),
                "title_name": entry.get("title_name"),
                "quantity": _coerce_int(entry.get("quantity")),
                "version": entry.get("version"),
                "coin": entry.get("coin"),
                "eacoin": entry.get("eacoin"),
                "comment": entry.get("comment"),
                "raw": entry,
                "ingest_batch_id": batch_id,
            }
        )
    shop["_titles"] = title_rows
    return shop, issues


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")


def _sqlite_bootstrap(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        create table if not exists arcade_shops (
          source text not null,
          source_id integer not null,
          source_url text not null,
          name text not null,
          name_pinyin text,
          address text,
          transport text,
          url text,
          comment text,
          province_code text,
          province_name text,
          city_code text,
          city_name text,
          county_code text,
          county_name text,
          longitude_gcj02 real,
          latitude_gcj02 real,
          longitude_wgs84 real,
          latitude_wgs84 real,
          geo_source text,
          geo_precision text,
          status text,
          type text,
          pay_type text,
          locked text,
          ea_status text,
          price text,
          start_time text,
          end_time text,
          fav_count integer,
          created_at_src text,
          updated_at_src text,
          option1 text,
          option2 text,
          option3 text,
          option4 text,
          option5 text,
          collab integer,
          image_thumb text,
          events text,
          raw text not null,
          ingest_batch_id text not null,
          primary key (source, source_id)
        );

        create table if not exists arcade_titles (
          id integer primary key autoincrement,
          source text not null,
          source_id integer not null,
          arcade_item_id integer,
          title_id text,
          title_name text,
          quantity integer,
          version text,
          coin text,
          eacoin text,
          comment text,
          raw text not null,
          ingest_batch_id text not null
        );

        create table if not exists ingest_runs (
          batch_id text primary key,
          source text not null,
          started_at text,
          finished_at text,
          args text not null,
          counts text not null,
          failed_shop_ids text not null,
          outputs text not null,
          created_at text not null
        );

        create index if not exists idx_arcade_shops_region
          on arcade_shops (province_code, city_code, county_code);
        create index if not exists idx_arcade_shops_name
          on arcade_shops (name);
        create index if not exists idx_arcade_titles_shop
          on arcade_titles (source, source_id, title_id);
        """
    )


def sink_sqlite(
    sqlite_path: Path,
    shops: list[dict[str, Any]],
    titles: list[dict[str, Any]],
    ingest_run: dict[str, Any],
) -> None:
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(sqlite_path))
    try:
        _sqlite_bootstrap(conn)
        with conn:
            for shop in shops:
                conn.execute(
                    """
                    insert into arcade_shops (
                      source, source_id, source_url, name, name_pinyin, address, transport, url, comment,
                      province_code, province_name, city_code, city_name, county_code, county_name,
                      longitude_gcj02, latitude_gcj02, longitude_wgs84, latitude_wgs84, geo_source, geo_precision,
                      status, type, pay_type, locked, ea_status, price, start_time, end_time, fav_count,
                      created_at_src, updated_at_src, option1, option2, option3, option4, option5,
                      collab, image_thumb, events, raw, ingest_batch_id
                    ) values (
                      :source, :source_id, :source_url, :name, :name_pinyin, :address, :transport, :url, :comment,
                      :province_code, :province_name, :city_code, :city_name, :county_code, :county_name,
                      :longitude_gcj02, :latitude_gcj02, :longitude_wgs84, :latitude_wgs84, :geo_source, :geo_precision,
                      :status, :type, :pay_type, :locked, :ea_status, :price, :start_time, :end_time, :fav_count,
                      :created_at_src, :updated_at_src, :option1, :option2, :option3, :option4, :option5,
                      :collab, :image_thumb, :events, :raw, :ingest_batch_id
                    )
                    on conflict(source, source_id) do update set
                      source_url=excluded.source_url,
                      name=excluded.name,
                      name_pinyin=excluded.name_pinyin,
                      address=excluded.address,
                      transport=excluded.transport,
                      url=excluded.url,
                      comment=excluded.comment,
                      province_code=excluded.province_code,
                      province_name=excluded.province_name,
                      city_code=excluded.city_code,
                      city_name=excluded.city_name,
                      county_code=excluded.county_code,
                      county_name=excluded.county_name,
                      longitude_gcj02=excluded.longitude_gcj02,
                      latitude_gcj02=excluded.latitude_gcj02,
                      longitude_wgs84=excluded.longitude_wgs84,
                      latitude_wgs84=excluded.latitude_wgs84,
                      geo_source=excluded.geo_source,
                      geo_precision=excluded.geo_precision,
                      status=excluded.status,
                      type=excluded.type,
                      pay_type=excluded.pay_type,
                      locked=excluded.locked,
                      ea_status=excluded.ea_status,
                      price=excluded.price,
                      start_time=excluded.start_time,
                      end_time=excluded.end_time,
                      fav_count=excluded.fav_count,
                      created_at_src=excluded.created_at_src,
                      updated_at_src=excluded.updated_at_src,
                      option1=excluded.option1,
                      option2=excluded.option2,
                      option3=excluded.option3,
                      option4=excluded.option4,
                      option5=excluded.option5,
                      collab=excluded.collab,
                      image_thumb=excluded.image_thumb,
                      events=excluded.events,
                      raw=excluded.raw,
                      ingest_batch_id=excluded.ingest_batch_id
                    """,
                    {
                        **shop,
                        "option1": _json(shop.get("option1")),
                        "option2": _json(shop.get("option2")),
                        "option3": _json(shop.get("option3")),
                        "option4": _json(shop.get("option4")),
                        "option5": _json(shop.get("option5")),
                        "collab": 1 if shop.get("collab") else 0,
                        "image_thumb": _json(shop.get("image_thumb")),
                        "events": _json(shop.get("events")),
                        "raw": _json(shop.get("raw")),
                    },
                )

            touched_keys = {(row["source"], row["source_id"]) for row in shops}
            for source, source_id in touched_keys:
                conn.execute(
                    "delete from arcade_titles where source = ? and source_id = ?",
                    (source, source_id),
                )

            for title in titles:
                conn.execute(
                    """
                    insert into arcade_titles (
                      source, source_id, arcade_item_id, title_id, title_name, quantity,
                      version, coin, eacoin, comment, raw, ingest_batch_id
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        title.get("source"),
                        title.get("source_id"),
                        title.get("arcade_item_id"),
                        str(title.get("title_id")) if title.get("title_id") is not None else None,
                        title.get("title_name"),
                        title.get("quantity"),
                        title.get("version"),
                        _json(title.get("coin")),
                        _json(title.get("eacoin")),
                        title.get("comment"),
                        _json(title.get("raw")),
                        title.get("ingest_batch_id"),
                    ),
                )

            conn.execute(
                """
                insert into ingest_runs (
                  batch_id, source, started_at, finished_at, args, counts, failed_shop_ids, outputs, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(batch_id) do update set
                  source=excluded.source,
                  started_at=excluded.started_at,
                  finished_at=excluded.finished_at,
                  args=excluded.args,
                  counts=excluded.counts,
                  failed_shop_ids=excluded.failed_shop_ids,
                  outputs=excluded.outputs,
                  created_at=excluded.created_at
                """,
                (
                    ingest_run["batch_id"],
                    ingest_run["source"],
                    ingest_run.get("started_at"),
                    ingest_run.get("finished_at"),
                    _json(ingest_run.get("args")),
                    _json(ingest_run.get("counts")),
                    _json(ingest_run.get("failed_shop_ids")),
                    _json(ingest_run.get("outputs")),
                    ingest_run.get("created_at"),
                ),
            )
    finally:
        conn.close()


def main() -> None:
    args = parse_args()
    if not args.input.exists():
        raise FileNotFoundError(f"Input JSONL not found: {args.input}")

    run_summary: dict[str, Any] = {}
    if args.run_summary.exists():
        run_summary = json.loads(args.run_summary.read_text(encoding="utf-8"))

    batch_id = args.batch_id
    if not batch_id:
        seed = run_summary.get("started_at") or utc_now_iso()
        cleaned = str(seed).replace(":", "").replace("-", "").replace(".", "")
        batch_id = f"etl_{cleaned[:18]}"

    started_at = utc_now_iso()
    raw_total = 0
    issues: list[ParseIssue] = []
    missing_counter: Counter[str] = Counter()
    deduped: dict[tuple[str, int], dict[str, Any]] = {}
    duplicate_count = 0

    with args.input.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            raw_total += 1
            stripped = line.strip()
            if not stripped:
                issues.append(ParseIssue(line_no=line_no, reason="empty_line", raw_line=""))
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                issues.append(
                    ParseIssue(
                        line_no=line_no,
                        reason=f"json_decode_error:{exc.msg}",
                        raw_line=stripped[:1000],
                    )
                )
                continue

            shop, row_issues = normalize_shop(payload, batch_id, line_no)
            issues.extend(row_issues)
            if shop is None:
                for field in ("source", "source_id", "source_url", "name"):
                    if payload.get(field) in (None, ""):
                        missing_counter[field] += 1
                continue
            key = (shop["source"], int(shop["source_id"]))
            if key in deduped:
                duplicate_count += 1
            deduped[key] = shop

    shops: list[dict[str, Any]] = []
    titles: list[dict[str, Any]] = []
    for shop in deduped.values():
        for title in shop.pop("_titles", []):
            titles.append(title)
        shops.append(shop)

    shops.sort(key=lambda row: row["source_id"])
    titles.sort(key=lambda row: (row["source_id"], str(row.get("title_id") or "")))

    bad_rows = [item.to_dict() for item in issues]
    finished_at = utc_now_iso()

    qa_report = {
        "batch_id": batch_id,
        "source": "bemanicn",
        "generated_at": finished_at,
        "counts": {
            "input_rows": raw_total,
            "valid_rows": len(shops),
            "bad_rows": len(bad_rows),
            "bad_ratio": round((len(bad_rows) / raw_total), 6) if raw_total else 0.0,
            "duplicate_source_rows": duplicate_count,
            "titles_rows": len(titles),
        },
        "missing_required_top": dict(missing_counter.most_common(10)),
        "issue_top": dict(Counter(item.reason for item in issues).most_common(20)),
    }

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    shops_path = output_dir / "arcade_shops.jsonl"
    titles_path = output_dir / "arcade_titles.jsonl"
    bad_rows_path = output_dir / "bad_rows.jsonl"
    qa_path = output_dir / "qa_report.json"
    ingest_run_path = output_dir / "ingest_run.json"

    write_jsonl(shops_path, shops)
    write_jsonl(titles_path, titles)
    write_jsonl(bad_rows_path, bad_rows)
    write_json(qa_path, qa_report)

    ingest_run = {
        "batch_id": batch_id,
        "source": "bemanicn",
        "started_at": started_at,
        "finished_at": finished_at,
        "args": {
            "input": str(args.input),
            "run_summary": str(args.run_summary),
            "output_dir": str(args.output_dir),
            "sqlite_path": str(args.sqlite_path) if args.sqlite_path else None,
        },
        "counts": qa_report["counts"],
        "failed_shop_ids": [],
        "outputs": {
            "arcade_shops": str(shops_path),
            "arcade_titles": str(titles_path),
            "bad_rows": str(bad_rows_path),
            "qa_report": str(qa_path),
            "ingest_run": str(ingest_run_path),
        },
        "created_at": finished_at,
        "source_run_summary": run_summary,
    }
    write_json(ingest_run_path, ingest_run)

    if args.sqlite_path:
        sink_sqlite(args.sqlite_path, shops=shops, titles=titles, ingest_run=ingest_run)

    print(
        f"[ok] batch_id={batch_id} input={raw_total} valid={len(shops)} "
        f"bad={len(bad_rows)} titles={len(titles)} sqlite={args.sqlite_path}",
        flush=True,
    )
    print(f"[ok] outputs={output_dir}", flush=True)


if __name__ == "__main__":
    main()

