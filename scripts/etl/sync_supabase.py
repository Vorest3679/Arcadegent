#!/usr/bin/env python3
"""Sync normalized ETL artifacts into Supabase through PostgREST."""

from __future__ import annotations

import argparse
import json
import math
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SHOPS_PATH = PROJECT_ROOT / "data/processed/bemanicn/arcade_shops.jsonl"
DEFAULT_TITLES_PATH = PROJECT_ROOT / "data/processed/bemanicn/arcade_titles.jsonl"
DEFAULT_INGEST_RUN_PATH = PROJECT_ROOT / "data/processed/bemanicn/ingest_run.json"
DEFAULT_GEO_CACHE_PATH = PROJECT_ROOT / "data/runtime/arcade_geo_cache.json"

SHOP_TEXT_COLUMNS = {
    "status",
    "type",
    "pay_type",
    "locked",
    "ea_status",
    "price",
    "start_time",
    "end_time",
}
TITLE_TEXT_COLUMNS = {"title_id", "coin", "eacoin"}
REQUIRED_TABLES = ("arcade_shops", "arcade_titles", "ingest_runs")
GEO_COLUMNS = {
    "longitude_gcj02",
    "latitude_gcj02",
    "longitude_wgs84",
    "latitude_wgs84",
    "geo_source",
    "geo_precision",
    "geo_wgs84",
}
EARTH_A = 6378245.0
EARTH_EE = 0.006693421622965943


@dataclass(frozen=True)
class SyncConfig:
    supabase_url: str
    service_role_key: str
    shops_path: Path = DEFAULT_SHOPS_PATH
    titles_path: Path = DEFAULT_TITLES_PATH
    ingest_run_path: Path = DEFAULT_INGEST_RUN_PATH
    batch_size: int = 500
    timeout_seconds: float = 30.0
    dry_run: bool = False
    sync_geo_cache: bool = True
    geo_cache_path: Path | None = DEFAULT_GEO_CACHE_PATH
    geo_only: bool = False


@dataclass(frozen=True)
class SyncResult:
    shops: int
    titles: int
    touched_shops: int
    geo_updates: int
    ingest_run_batch_id: str | None
    dry_run: bool
    geo_only: bool = False


class SupabaseSchemaMissingError(RuntimeError):
    """Raised when the target Supabase project has not applied required migrations."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync normalized arcade ETL artifacts to Supabase.")
    parser.add_argument("--shops-path", type=Path, default=DEFAULT_SHOPS_PATH)
    parser.add_argument("--titles-path", type=Path, default=DEFAULT_TITLES_PATH)
    parser.add_argument("--ingest-run-path", type=Path, default=DEFAULT_INGEST_RUN_PATH)
    parser.add_argument("--supabase-url", default=None)
    parser.add_argument("--service-role-key", default=None)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--geo-cache-path",
        type=Path,
        default=DEFAULT_GEO_CACHE_PATH,
        help="Runtime arcade geo cache JSON path used to backfill stable coordinates.",
    )
    parser.add_argument(
        "--skip-geo-cache",
        action="store_true",
        help="Do not patch Supabase coordinates from the runtime geo cache.",
    )
    parser.add_argument(
        "--geo-only",
        action="store_true",
        help="Only patch coordinate columns on arcade_shops; do not upsert shops, titles, or ingest runs.",
    )
    return parser.parse_args()


def _load_dotenv_if_exists() -> None:
    dotenv_path = PROJECT_ROOT / ".env"
    if not dotenv_path.exists():
        return
    try:
        lines = dotenv_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        if key:
            os.environ.setdefault(key, value.strip().strip("'").strip('"'))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            raw = line.strip()
            if not raw:
                continue
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError(f"jsonl_row_must_be_object:{path}:{line_no}")
            rows.append(payload)
    return rows


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"json_must_be_object:{path}")
    return payload


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text != "" else None


def build_shop_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    payload.pop("_titles", None)
    payload.pop("id", None)
    for key in GEO_COLUMNS:
        payload.pop(key, None)
    for key in SHOP_TEXT_COLUMNS:
        payload[key] = _coerce_text(payload.get(key))
    return payload


def build_title_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    payload.pop("id", None)
    for key in TITLE_TEXT_COLUMNS:
        payload[key] = _coerce_text(payload.get(key))
    return payload


def build_ingest_run_payload(row: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "batch_id",
        "source",
        "started_at",
        "finished_at",
        "args",
        "counts",
        "failed_shop_ids",
        "outputs",
        "created_at",
    }
    return {key: row.get(key) for key in allowed}


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not (-180 <= parsed <= 180):
        return None
    return parsed


def _valid_lng_lat(lng: float | None, lat: float | None) -> bool:
    return lng is not None and lat is not None and -180 <= lng <= 180 and -90 <= lat <= 90


def _is_in_mainland_china(lng: float, lat: float) -> bool:
    return 72.004 <= lng <= 137.8347 and 0.8293 <= lat <= 55.8271


def _transform_lat(lng: float, lat: float) -> float:
    result = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat + 0.1 * lng * lat + 0.2 * abs(lng) ** 0.5
    result += ((20.0 * math.sin(6.0 * lng * math.pi) + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0) / 3.0
    result += ((20.0 * math.sin(lat * math.pi) + 40.0 * math.sin((lat / 3.0) * math.pi)) * 2.0) / 3.0
    result += ((160.0 * math.sin((lat / 12.0) * math.pi) + 320.0 * math.sin((lat * math.pi) / 30.0)) * 2.0) / 3.0
    return result


def _transform_lng(lng: float, lat: float) -> float:
    result = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng + 0.1 * lng * lat + 0.1 * abs(lng) ** 0.5
    result += ((20.0 * math.sin(6.0 * lng * math.pi) + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0) / 3.0
    result += ((20.0 * math.sin(lng * math.pi) + 40.0 * math.sin((lng / 3.0) * math.pi)) * 2.0) / 3.0
    result += ((150.0 * math.sin((lng / 12.0) * math.pi) + 300.0 * math.sin((lng / 30.0) * math.pi)) * 2.0) / 3.0
    return result


def approximate_wgs84_to_gcj02(lng: float, lat: float) -> tuple[float, float]:
    if not _is_in_mainland_china(lng, lat):
        return lng, lat
    d_lat = _transform_lat(lng - 105.0, lat - 35.0)
    d_lng = _transform_lng(lng - 105.0, lat - 35.0)
    rad_lat = (lat / 180.0) * math.pi
    magic = math.sin(rad_lat)
    magic = 1 - EARTH_EE * magic * magic
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / (((EARTH_A * (1 - EARTH_EE)) / (magic * sqrt_magic)) * math.pi)
    d_lng = (d_lng * 180.0) / ((EARTH_A / sqrt_magic) * math.cos(rad_lat) * math.pi)
    return lng + d_lng, lat + d_lat


def approximate_gcj02_to_wgs84(lng: float, lat: float) -> tuple[float, float]:
    if not _is_in_mainland_china(lng, lat):
        return lng, lat
    guess_lng, guess_lat = lng, lat
    for _ in range(3):
        converted_lng, converted_lat = approximate_wgs84_to_gcj02(guess_lng, guess_lat)
        guess_lng -= converted_lng - lng
        guess_lat -= converted_lat - lat
    return guess_lng, guess_lat


def build_geo_update_from_shop(row: dict[str, Any]) -> dict[str, Any] | None:
    source = row.get("source")
    source_id = row.get("source_id")
    if not isinstance(source, str) or not isinstance(source_id, int):
        return None
    gcj_lng = _coerce_float(row.get("longitude_gcj02"))
    gcj_lat = _coerce_float(row.get("latitude_gcj02"))
    wgs_lng = _coerce_float(row.get("longitude_wgs84"))
    wgs_lat = _coerce_float(row.get("latitude_wgs84"))
    payload: dict[str, Any] = {
        "source": source,
        "source_id": source_id,
    }
    if _valid_lng_lat(gcj_lng, gcj_lat):
        payload["longitude_gcj02"] = gcj_lng
        payload["latitude_gcj02"] = gcj_lat
    if _valid_lng_lat(wgs_lng, wgs_lat):
        payload["longitude_wgs84"] = wgs_lng
        payload["latitude_wgs84"] = wgs_lat
    if len(payload) <= 2:
        return None
    payload["geo_source"] = _coerce_text(row.get("geo_source")) or "catalog"
    payload["geo_precision"] = _coerce_text(row.get("geo_precision")) or "exact"
    return payload


def _geo_point_from_cache(value: object) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        return None
    lng = _coerce_float(value.get("lng"))
    lat = _coerce_float(value.get("lat"))
    if not _valid_lng_lat(lng, lat):
        return None
    return lng, lat


def build_geo_update_from_cache_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    source_id = entry.get("source_id")
    if not isinstance(source_id, int):
        return None
    geo = entry.get("geo")
    if not isinstance(geo, dict):
        return None
    gcj = _geo_point_from_cache(geo.get("gcj02"))
    wgs = _geo_point_from_cache(geo.get("wgs84"))
    if gcj is None and wgs is None:
        return None

    payload: dict[str, Any] = {
        "source": "bemanicn",
        "source_id": source_id,
        "geo_source": _coerce_text(geo.get("source")) or "geocode",
        "geo_precision": _coerce_text(geo.get("precision")) or "approx",
    }
    if gcj is not None:
        payload["longitude_gcj02"] = gcj[0]
        payload["latitude_gcj02"] = gcj[1]
    if wgs is None and gcj is not None:
        wgs = approximate_gcj02_to_wgs84(gcj[0], gcj[1])
    if wgs is not None:
        payload["longitude_wgs84"] = wgs[0]
        payload["latitude_wgs84"] = wgs[1]
    return payload


def load_geo_cache_updates(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    payload = load_json(path)
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return []
    by_source_id: dict[int, dict[str, Any]] = {}
    for value in entries.values():
        if not isinstance(value, dict):
            continue
        update = build_geo_update_from_cache_entry(value)
        if update is not None:
            by_source_id[int(update["source_id"])] = update
    return list(by_source_id.values())


def merge_geo_updates(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, int], dict[str, Any]] = {}
    for group in groups:
        for update in group:
            source = update.get("source")
            source_id = update.get("source_id")
            if not isinstance(source, str) or not isinstance(source_id, int):
                continue
            merged[(source, source_id)] = update
    return list(merged.values())


def chunked(rows: list[dict[str, Any]], batch_size: int) -> Iterable[list[dict[str, Any]]]:
    safe_size = max(1, batch_size)
    for index in range(0, len(rows), safe_size):
        yield rows[index : index + safe_size]


def _headers(key: str, *, prefer: str = "return=minimal") -> dict[str, str]:
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Prefer": prefer,
    }


def _schema_missing_message(*, table: str, raw_error: str) -> str:
    return (
        f"supabase_schema_missing: public.{table} is not available through PostgREST.\n"
        "Apply migrations to the same Supabase project configured by SUPABASE_URL, in order:\n"
        "  1. supabase/migrations/20260220_000001_init_arcade_schema.sql\n"
        "  2. supabase/migrations/20260416_000001_arcade_runtime_rpc.sql\n"
        "Then wait a few seconds for Supabase/PostgREST to refresh its schema cache and rerun sync.\n"
        f"Original Supabase error: {raw_error}"
    )


def _raise_for_supabase_error(
    *,
    method: str,
    url: str,
    status_code: int,
    text: str,
) -> None:
    table = url.rstrip("/").rsplit("/", 1)[-1]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = {}
    code = payload.get("code") if isinstance(payload, dict) else None
    message = payload.get("message") if isinstance(payload, dict) else None
    if code == "PGRST205" or (
        isinstance(message, str)
        and "Could not find the table" in message
    ):
        raise SupabaseSchemaMissingError(_schema_missing_message(table=table, raw_error=text[:500]))
    raise RuntimeError(f"supabase_sync_failed:{method}:{url}:{status_code}:{text[:500]}")


def _request(
    client: httpx.Client,
    *,
    method: str,
    url: str,
    key: str,
    json_payload: Any | None = None,
    params: dict[str, str] | None = None,
    prefer: str = "return=minimal",
) -> None:
    response = client.request(
        method,
        url,
        headers=_headers(key, prefer=prefer),
        params=params,
        json=json_payload,
    )
    if response.status_code >= 400:
        _raise_for_supabase_error(
            method=method,
            url=url,
            status_code=response.status_code,
            text=response.text,
        )


def preflight_supabase_schema(
    client: httpx.Client,
    *,
    base_url: str,
    key: str,
    tables: Iterable[str] = REQUIRED_TABLES,
) -> None:
    """Check required tables before sending large write batches."""
    for table in tables:
        _request(
            client,
            method="GET",
            url=f"{base_url}/rest/v1/{table}",
            params={"select": "*", "limit": "1"},
            key=key,
        )


def patch_geo_updates(
    client: httpx.Client,
    *,
    base_url: str,
    api_key: str,
    geo_updates: list[dict[str, Any]],
) -> None:
    """Patch stable coordinate columns without touching other shop fields."""
    for update in geo_updates:
        source = update.get("source")
        source_id = update.get("source_id")
        if not isinstance(source, str) or not isinstance(source_id, int):
            continue
        payload = {
            key: value
            for key, value in update.items()
            if key not in {"source", "source_id"} and value is not None
        }
        if not payload:
            continue
        _request(
            client,
            method="PATCH",
            url=f"{base_url}/rest/v1/arcade_shops",
            params={
                "source": f"eq.{source}",
                "source_id": f"eq.{source_id}",
            },
            key=api_key,
            json_payload=payload,
        )


def sync_supabase(config: SyncConfig, *, client: httpx.Client | None = None) -> SyncResult:
    if not config.shops_path.exists():
        raise FileNotFoundError(f"shops artifact not found: {config.shops_path}")
    if not config.geo_only and not config.titles_path.exists():
        raise FileNotFoundError(f"titles artifact not found: {config.titles_path}")
    if not config.geo_only and not config.ingest_run_path.exists():
        raise FileNotFoundError(f"ingest run artifact not found: {config.ingest_run_path}")

    shop_rows = load_jsonl(config.shops_path)
    shops = [build_shop_payload(row) for row in shop_rows]
    titles = [] if config.geo_only else [build_title_payload(row) for row in load_jsonl(config.titles_path)]
    ingest_run = {} if config.geo_only else build_ingest_run_payload(load_json(config.ingest_run_path))
    geo_updates = merge_geo_updates(
        [update for row in shop_rows if (update := build_geo_update_from_shop(row)) is not None],
        load_geo_cache_updates(config.geo_cache_path) if config.sync_geo_cache else [],
    )
    touched_by_source: dict[str, set[int]] = defaultdict(set)
    for shop in shops:
        source = shop.get("source")
        source_id = shop.get("source_id")
        if isinstance(source, str) and isinstance(source_id, int):
            touched_by_source[source].add(source_id)

    if config.dry_run:
        return SyncResult(
            shops=len(shops),
            titles=len(titles),
            touched_shops=sum(len(values) for values in touched_by_source.values()),
            geo_updates=len(geo_updates),
            ingest_run_batch_id=ingest_run.get("batch_id") if isinstance(ingest_run.get("batch_id"), str) else None,
            dry_run=True,
            geo_only=config.geo_only,
        )

    base_url = config.supabase_url.rstrip("/")
    if not base_url:
        raise ValueError("supabase_url_required")
    if not config.service_role_key:
        raise ValueError("supabase_service_role_key_required")

    owns_client = client is None
    active_client = client or httpx.Client(timeout=config.timeout_seconds)
    try:
        preflight_supabase_schema(
            active_client,
            base_url=base_url,
            key=config.service_role_key,
            tables=("arcade_shops",) if config.geo_only else REQUIRED_TABLES,
        )

        if config.geo_only:
            patch_geo_updates(
                active_client,
                base_url=base_url,
                api_key=config.service_role_key,
                geo_updates=geo_updates,
            )
            return SyncResult(
                shops=len(shops),
                titles=0,
                touched_shops=len(geo_updates),
                geo_updates=len(geo_updates),
                ingest_run_batch_id=None,
                dry_run=False,
                geo_only=True,
            )

        for batch in chunked(shops, config.batch_size):
            _request(
                active_client,
                method="POST",
                url=f"{base_url}/rest/v1/arcade_shops",
                params={"on_conflict": "source,source_id"},
                key=config.service_role_key,
                json_payload=batch,
                prefer="resolution=merge-duplicates,return=minimal",
            )

        patch_geo_updates(
            active_client,
            base_url=base_url,
            api_key=config.service_role_key,
            geo_updates=geo_updates,
        )

        for source, source_ids in touched_by_source.items():
            sorted_ids = sorted(source_ids)
            for index in range(0, len(sorted_ids), config.batch_size):
                batch_ids = sorted_ids[index : index + config.batch_size]
                if not batch_ids:
                    continue
                _request(
                    active_client,
                    method="DELETE",
                    url=f"{base_url}/rest/v1/arcade_titles",
                    params={
                        "source": f"eq.{source}",
                        "source_id": f"in.({','.join(str(item) for item in batch_ids)})",
                    },
                    key=config.service_role_key,
                )

        for batch in chunked(titles, config.batch_size):
            _request(
                active_client,
                method="POST",
                url=f"{base_url}/rest/v1/arcade_titles",
                key=config.service_role_key,
                json_payload=batch,
            )

        _request(
            active_client,
            method="POST",
            url=f"{base_url}/rest/v1/ingest_runs",
            params={"on_conflict": "batch_id"},
            key=config.service_role_key,
            json_payload=[ingest_run],
            prefer="resolution=merge-duplicates,return=minimal",
        )
    finally:
        if owns_client:
            active_client.close()

    return SyncResult(
        shops=len(shops),
        titles=len(titles),
        touched_shops=sum(len(values) for values in touched_by_source.values()),
        geo_updates=len(geo_updates),
        ingest_run_batch_id=ingest_run.get("batch_id") if isinstance(ingest_run.get("batch_id"), str) else None,
        dry_run=False,
        geo_only=False,
    )


def config_from_args(args: argparse.Namespace) -> SyncConfig:
    _load_dotenv_if_exists()
    return SyncConfig(
        supabase_url=args.supabase_url or os.getenv("SUPABASE_URL", ""),
        service_role_key=args.service_role_key or os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
        shops_path=args.shops_path,
        titles_path=args.titles_path,
        ingest_run_path=args.ingest_run_path,
        batch_size=args.batch_size,
        timeout_seconds=args.timeout_seconds,
        dry_run=args.dry_run,
        sync_geo_cache=not args.skip_geo_cache,
        geo_cache_path=args.geo_cache_path,
        geo_only=args.geo_only,
    )


def main() -> None:
    result = sync_supabase(config_from_args(parse_args()))
    print(
        "[ok] "
        f"dry_run={result.dry_run} "
        f"shops={result.shops} "
        f"titles={result.titles} "
        f"touched_shops={result.touched_shops} "
        f"geo_updates={result.geo_updates} "
        f"batch_id={result.ingest_run_batch_id}",
        f"geo_only={result.geo_only}",
        flush=True,
    )


if __name__ == "__main__":
    main()
