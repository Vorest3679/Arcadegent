#!/usr/bin/env python
"""CLI for filling missing arcade GCJ-02 coordinates with AMap HTTP geocoding."""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import Settings  # noqa: E402
from app.services.arcade_geo_jsonl_enricher import (  # noqa: E402
    ArcadeGeoJsonlEnricher,
    ArcadeGeoJsonlEnrichStats,
)
from app.services.arcade_geo_resolver import ArcadeGeoResolver, ArcadeGeoResolverConfig  # noqa: E402


MAX_QPS = 3.0
DEFAULT_QPS = 2.5


def main() -> int:
    settings = Settings.from_env()
    parser = _build_parser(settings)
    args = parser.parse_args()

    qps = float(args.qps)
    if qps <= 0 or qps > MAX_QPS:
        parser.error(f"--qps must be > 0 and <= {MAX_QPS:g}")

    input_path = Path(args.input).expanduser()
    output_path, final_replace_path = _resolve_output_paths(input_path=input_path, args=args)
    api_key = args.api_key.strip()
    if not api_key and not args.dry_run:
        parser.error("AMAP_API_KEY is required unless --dry-run is used")
    if output_path.exists() and not args.overwrite and not args.dry_run:
        parser.error(f"output already exists, pass --overwrite to replace it: {output_path}")

    resolver = ArcadeGeoResolver(
        config=ArcadeGeoResolverConfig(
            api_key=api_key,
            base_url=args.base_url,
            cache_path=Path(args.cache_path).expanduser(),
            request_timeout_seconds=float(args.timeout),
            sync_limit=1,
            max_workers=1,
            request_interval_seconds=1.0 / qps,
        )
    )
    enricher = ArcadeGeoJsonlEnricher(resolver=resolver)

    print(f"input={input_path}")
    if args.dry_run:
        print("dry_run=true")
    else:
        print(f"output={final_replace_path or output_path}")
        print(f"cache={Path(args.cache_path).expanduser()}")
        print(f"qps={qps:g}")
        if final_replace_path is not None:
            print("write_mode=in_place_with_backup")

    stats = enricher.enrich(
        input_path=input_path,
        output_path=None if args.dry_run else output_path,
        limit=args.limit,
        dry_run=bool(args.dry_run),
        progress_every=max(1, int(args.progress_every)),
        progress_callback=_print_progress,
    )

    if final_replace_path is not None and not args.dry_run:
        backup_path = _backup_path(final_replace_path)
        shutil.copy2(final_replace_path, backup_path)
        output_path.replace(final_replace_path)
        print(f"backup={backup_path}")

    _print_summary(stats)
    return 0 if stats.failed == 0 else 2


def _build_parser(settings: Settings) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fill missing longitude_gcj02/latitude_gcj02 fields in arcade JSONL files via AMap HTTP."
    )
    parser.add_argument("--input", default=str(settings.data_jsonl_path), help="source arcade JSONL path")
    parser.add_argument("--output", default="", help="output JSONL path; defaults to <input>.geocoded.jsonl")
    parser.add_argument("--in-place", action="store_true", help="replace input atomically after writing a backup")
    parser.add_argument("--overwrite", action="store_true", help="allow replacing an existing output file")
    parser.add_argument("--dry-run", action="store_true", help="count rows that would be geocoded without HTTP calls")
    parser.add_argument("--limit", type=int, default=None, help="maximum number of missing rows to geocode")
    parser.add_argument(
        "--qps",
        type=float,
        default=DEFAULT_QPS,
        help=f"AMap request rate, capped at {MAX_QPS:g} QPS",
    )
    parser.add_argument("--api-key", default=settings.amap_api_key, help="AMap Web service API key")
    parser.add_argument("--base-url", default=settings.amap_base_url, help="AMap REST base URL")
    parser.add_argument("--timeout", type=float, default=settings.amap_timeout_seconds, help="HTTP timeout seconds")
    parser.add_argument(
        "--cache-path",
        default=str(settings.arcade_geo_cache_path),
        help="cache path shared with the backend geo resolver",
    )
    parser.add_argument("--progress-every", type=int, default=100, help="print progress every N JSON rows")
    return parser


def _resolve_output_paths(*, input_path: Path, args: argparse.Namespace) -> tuple[Path, Path | None]:
    if args.in_place and args.output:
        raise SystemExit("--in-place and --output cannot be used together")

    if args.in_place:
        return _temp_in_place_path(input_path), input_path

    if args.output:
        output_path = Path(args.output).expanduser()
        if _same_path(input_path, output_path):
            return _temp_in_place_path(input_path), input_path
        return output_path, None

    if input_path.suffix == ".jsonl":
        return input_path.with_name(f"{input_path.stem}.geocoded{input_path.suffix}"), None
    return input_path.with_name(f"{input_path.name}.geocoded.jsonl"), None


def _temp_in_place_path(input_path: Path) -> Path:
    return input_path.with_name(f".{input_path.name}.geo-enriched.tmp")


def _same_path(left: Path, right: Path) -> bool:
    return left.resolve() == right.resolve()


def _backup_path(input_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return input_path.with_name(f"{input_path.name}.{stamp}.bak")


def _print_progress(stats: ArcadeGeoJsonlEnrichStats) -> None:
    print(
        "progress "
        f"rows={stats.json_rows} attempted={stats.attempted} "
        f"enriched={stats.enriched} failed={stats.failed} "
        f"already={stats.already_geocoded} skipped_limit={stats.skipped_by_limit}"
    )


def _print_summary(stats: ArcadeGeoJsonlEnrichStats) -> None:
    print(
        "summary "
        f"total_lines={stats.total_lines} json_rows={stats.json_rows} bad_lines={stats.bad_lines} "
        f"already_geocoded={stats.already_geocoded} attempted={stats.attempted} "
        f"enriched={stats.enriched} failed={stats.failed} skipped_by_limit={stats.skipped_by_limit}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
