"""Offline JSONL enrichment for arcade shop GCJ-02 coordinates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.protocol.messages import ArcadeGeoDto
from app.services.arcade_geo_resolver import ArcadeGeoResolver


ProgressCallback = Callable[["ArcadeGeoJsonlEnrichStats"], None]


@dataclass
class ArcadeGeoJsonlEnrichStats:
    """Counters emitted by the offline coordinate enrichment job."""

    total_lines: int = 0
    json_rows: int = 0
    bad_lines: int = 0
    already_geocoded: int = 0
    attempted: int = 0
    enriched: int = 0
    failed: int = 0
    skipped_by_limit: int = 0


class ArcadeGeoJsonlEnricher:
    """Fill missing JSONL GCJ-02 coordinates through the shared geo resolver."""

    def __init__(self, *, resolver: ArcadeGeoResolver) -> None:
        self._resolver = resolver

    def enrich(
        self,
        *,
        input_path: Path,
        output_path: Path | None = None,
        limit: int | None = None,
        dry_run: bool = False,
        progress_every: int = 100,
        progress_callback: ProgressCallback | None = None,
    ) -> ArcadeGeoJsonlEnrichStats:
        """Read JSONL rows, geocode missing GCJ-02 values, and optionally write output."""
        if limit is not None and limit < 0:
            raise ValueError("limit must be >= 0")
        if not dry_run and output_path is None:
            raise ValueError("output_path is required unless dry_run is true")
        if not input_path.exists():
            raise FileNotFoundError(f"Arcade data file not found: {input_path}")

        stats = ArcadeGeoJsonlEnrichStats()
        progress_every = max(1, progress_every)
        temp_output = self._temp_output_path(output_path) if output_path and not dry_run else None
        output_handle = None
        try:
            if temp_output is not None:
                temp_output.parent.mkdir(parents=True, exist_ok=True)
                output_handle = temp_output.open("w", encoding="utf-8")

            with input_path.open("r", encoding="utf-8") as input_handle:
                for raw_line in input_handle:
                    stats.total_lines += 1
                    line = raw_line.strip()
                    if not line:
                        stats.bad_lines += 1
                        self._write_line(output_handle, raw_line)
                        self._maybe_report(stats, progress_every, progress_callback)
                        continue

                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        stats.bad_lines += 1
                        self._write_line(output_handle, raw_line)
                        self._maybe_report(stats, progress_every, progress_callback)
                        continue

                    if not isinstance(row, dict):
                        stats.bad_lines += 1
                        self._write_line(output_handle, raw_line)
                        self._maybe_report(stats, progress_every, progress_callback)
                        continue

                    stats.json_rows += 1
                    updated = self._enrich_row(row, stats=stats, limit=limit, dry_run=dry_run)
                    if output_handle is not None:
                        output_handle.write(json.dumps(updated, ensure_ascii=False, separators=(",", ":")))
                        output_handle.write("\n")
                    self._maybe_report(stats, progress_every, progress_callback)
        except Exception:
            if output_handle is not None:
                output_handle.close()
            if temp_output is not None:
                temp_output.unlink(missing_ok=True)
            raise
        finally:
            if output_handle is not None and not output_handle.closed:
                output_handle.close()

        if temp_output is not None and output_path is not None:
            temp_output.replace(output_path)
        return stats

    def _enrich_row(
        self,
        row: dict[str, Any],
        *,
        stats: ArcadeGeoJsonlEnrichStats,
        limit: int | None,
        dry_run: bool,
    ) -> dict[str, Any]:
        if _has_gcj02_fields(row):
            stats.already_geocoded += 1
            return row

        if limit is not None and stats.attempted >= limit:
            stats.skipped_by_limit += 1
            return row

        stats.attempted += 1
        if dry_run:
            return row

        geo = self._resolver.resolve_one(row)
        if geo is None or geo.gcj02 is None:
            geo = self._resolver.geocode_one(row)
        if geo is None or not _apply_gcj02(row, geo):
            stats.failed += 1
            return row

        stats.enriched += 1
        return row

    @staticmethod
    def _temp_output_path(output_path: Path | None) -> Path | None:
        if output_path is None:
            return None
        return output_path.with_name(f".{output_path.name}.tmp")

    @staticmethod
    def _write_line(output_handle: Any, raw_line: str) -> None:
        if output_handle is not None:
            output_handle.write(raw_line)

    @staticmethod
    def _maybe_report(
        stats: ArcadeGeoJsonlEnrichStats,
        progress_every: int,
        progress_callback: ProgressCallback | None,
    ) -> None:
        if progress_callback is not None and stats.total_lines % progress_every == 0:
            progress_callback(stats)


def _has_gcj02_fields(row: dict[str, Any]) -> bool:
    lng = _coerce_float(row.get("longitude_gcj02"))
    lat = _coerce_float(row.get("latitude_gcj02"))
    return lng is not None and lat is not None and _valid_lng(lng) and _valid_lat(lat)


def _apply_gcj02(row: dict[str, Any], geo: ArcadeGeoDto) -> bool:
    point = geo.gcj02
    if point is None or not _valid_lng(point.lng) or not _valid_lat(point.lat):
        return False
    row["longitude_gcj02"] = point.lng
    row["latitude_gcj02"] = point.lat
    return True


def _coerce_float(raw: object) -> float | None:
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _valid_lng(value: float) -> bool:
    return -180 <= value <= 180


def _valid_lat(value: float) -> bool:
    return -90 <= value <= 90
