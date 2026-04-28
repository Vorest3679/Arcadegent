"""Unit tests for offline arcade JSONL coordinate enrichment."""

from __future__ import annotations

import json
from pathlib import Path

from app.services.arcade_geo_jsonl_enricher import ArcadeGeoJsonlEnricher
from app.services.arcade_geo_resolver import ArcadeGeoResolver, ArcadeGeoResolverConfig


def _resolver(tmp_path: Path) -> ArcadeGeoResolver:
    return ArcadeGeoResolver(
        config=ArcadeGeoResolverConfig(
            api_key="test-key",
            base_url="https://restapi.amap.com",
            cache_path=tmp_path / "arcade_geo_cache.json",
            request_timeout_seconds=0.1,
            sync_limit=1,
            max_workers=1,
        )
    )


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_enriches_missing_gcj02_fields_and_skips_existing_rows(tmp_path: Path) -> None:
    data_path = tmp_path / "shops.jsonl"
    output_path = tmp_path / "shops.geocoded.jsonl"
    _write_jsonl(
        data_path,
        [
            {
                "source_id": 1,
                "name": "Existing Arcade",
                "address": "Addr A",
                "city_name": "上海市",
                "longitude_gcj02": 121.1,
                "latitude_gcj02": 31.2,
            },
            {
                "source_id": 2,
                "name": "Missing Arcade",
                "address": "Addr B",
                "city_name": "上海市",
                "updated_at": "2026-04-17T00:00:00Z",
            },
        ],
    )
    resolver = _resolver(tmp_path)
    calls: list[tuple[str, str | None]] = []

    def fake_request_geocode(*, query: str, city: str | None) -> dict[str, object]:
        calls.append((query, city))
        return {"status": "1", "geocodes": [{"location": "121.470100,31.230400"}]}

    resolver._request_geocode = fake_request_geocode  # type: ignore[method-assign]
    stats = ArcadeGeoJsonlEnricher(resolver=resolver).enrich(
        input_path=data_path,
        output_path=output_path,
    )

    rows = _read_jsonl(output_path)
    assert stats.already_geocoded == 1
    assert stats.attempted == 1
    assert stats.enriched == 1
    assert len(calls) == 1
    assert rows[0]["longitude_gcj02"] == 121.1
    assert rows[1]["longitude_gcj02"] == 121.4701
    assert rows[1]["latitude_gcj02"] == 31.2304


def test_dry_run_counts_missing_rows_without_geocoding_or_writing(tmp_path: Path) -> None:
    data_path = tmp_path / "shops.jsonl"
    output_path = tmp_path / "shops.geocoded.jsonl"
    _write_jsonl(
        data_path,
        [
            {
                "source_id": 3,
                "name": "Dry Run Arcade",
                "address": "Addr C",
                "city_name": "北京市",
            }
        ],
    )
    resolver = _resolver(tmp_path)
    resolver._request_geocode = lambda **_: (_ for _ in ()).throw(AssertionError("no HTTP"))  # type: ignore[method-assign]

    stats = ArcadeGeoJsonlEnricher(resolver=resolver).enrich(
        input_path=data_path,
        output_path=output_path,
        dry_run=True,
    )

    assert stats.attempted == 1
    assert stats.enriched == 0
    assert not output_path.exists()


def test_limit_leaves_remaining_missing_rows_unchanged(tmp_path: Path) -> None:
    data_path = tmp_path / "shops.jsonl"
    output_path = tmp_path / "shops.geocoded.jsonl"
    _write_jsonl(
        data_path,
        [
            {"source_id": 4, "name": "One", "address": "Addr D", "city_name": "广州市"},
            {"source_id": 5, "name": "Two", "address": "Addr E", "city_name": "广州市"},
        ],
    )
    resolver = _resolver(tmp_path)
    resolver._request_geocode = lambda **_: {"status": "1", "geocodes": [{"location": "113.1,23.2"}]}  # type: ignore[method-assign]

    stats = ArcadeGeoJsonlEnricher(resolver=resolver).enrich(
        input_path=data_path,
        output_path=output_path,
        limit=1,
    )

    rows = _read_jsonl(output_path)
    assert stats.attempted == 1
    assert stats.enriched == 1
    assert stats.skipped_by_limit == 1
    assert rows[0]["longitude_gcj02"] == 113.1
    assert "longitude_gcj02" not in rows[1]
