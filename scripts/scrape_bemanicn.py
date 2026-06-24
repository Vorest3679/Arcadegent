#!/usr/bin/env python3
"""Scrape arcade data from https://map.bemanicn.com using Inertia JSON endpoints.

Example:
  python scripts/scrape_bemanicn.py --max-shops 30
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib import error, parse, request

BASE_URL = "https://map.bemanicn.com"
DEFAULT_HEADERS = {
    "accept": "application/json",
    "x-inertia": "true",
    "x-requested-with": "XMLHttpRequest",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
    ),
}


@dataclass
class CrawlConfig:
    timeout: float
    retries: int
    retry_backoff: float
    delay_min: float
    delay_max: float


class BemanicnClient:
    def __init__(self, config: CrawlConfig) -> None:
        self.config = config

    def get_json(self, path: str, referer: Optional[str] = None) -> Dict[str, Any]:
        url = path if path.startswith("http") else parse.urljoin(BASE_URL, path)
        headers = dict(DEFAULT_HEADERS)
        if referer:
            headers["referer"] = referer

        last_error: Optional[Exception] = None
        for attempt in range(1, self.config.retries + 1):
            if self.config.delay_max > 0:
                time.sleep(random.uniform(self.config.delay_min, self.config.delay_max))

            req = request.Request(url=url, headers=headers, method="GET")
            try:
                with request.urlopen(req, timeout=self.config.timeout) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                    content_type = (resp.headers.get("Content-Type") or "").lower()
                    if "json" not in content_type and raw.lstrip().startswith("<"):
                        raise RuntimeError(
                            f"Non-JSON response for {url}. "
                            "Make sure x-inertia header is present."
                        )
                    payload = json.loads(raw)
                    if not isinstance(payload, dict):
                        raise RuntimeError(f"Unexpected payload type for {url}: {type(payload)}")
                    return payload
            except json.JSONDecodeError as exc:
                last_error = exc
            except error.HTTPError as exc:
                last_error = exc
                if exc.code not in (408, 429) and exc.code < 500:
                    break
            except Exception as exc:  # noqa: BLE001
                last_error = exc

            if attempt < self.config.retries:
                sleep_s = self.config.retry_backoff * (2 ** (attempt - 1))
                time.sleep(sleep_s)

        raise RuntimeError(f"GET {url} failed after {self.config.retries} attempts: {last_error}")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape arcades from bemanicn map.")
    parser.add_argument(
        "--seed-shop-id",
        type=int,
        default=2287,
        help="Seed shop ID used to fetch province dictionary. Default: 2287",
    )
    parser.add_argument(
        "--province-code",
        action="append",
        default=[],
        help="Target province code(s). Can be repeated or comma-separated.",
    )
    parser.add_argument(
        "--include-non-mainland",
        action="store_true",
        help="Include TW/HK/MO province codes (71/81/82).",
    )
    parser.add_argument(
        "--skip-details",
        action="store_true",
        help="Only fetch province shop seeds, skip /s/{id} detail requests.",
    )
    parser.add_argument(
        "--max-shops",
        type=int,
        default=0,
        help="Limit detail fetch count for testing. 0 means no limit.",
    )
    parser.add_argument("--workers", type=int, default=6, help="Concurrent detail workers.")
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout seconds.")
    parser.add_argument("--retries", type=int, default=3, help="HTTP retry attempts.")
    parser.add_argument(
        "--retry-backoff",
        type=float,
        default=1.0,
        help="Retry backoff seconds, exponential base.",
    )
    parser.add_argument("--delay-min", type=float, default=0.05, help="Min delay before each call.")
    parser.add_argument("--delay-max", type=float, default=0.20, help="Max delay before each call.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/raw/bemanicn"),
        help="Output directory.",
    )
    return parser.parse_args()


def normalize_code_args(raw_values: Sequence[str]) -> List[str]:
    codes: List[str] = []
    seen = set()
    for raw in raw_values:
        for part in raw.split(","):
            code = part.strip()
            if not code or code in seen:
                continue
            seen.add(code)
            codes.append(code)
    return codes


def is_mainland_province(code: str) -> bool:
    return code.isdigit() and len(code) == 12 and code[:2] not in {"71", "81", "82"}


def get_province_dict(client: BemanicnClient, seed_shop_id: int) -> Dict[str, str]:
    payload = client.get_json(f"/s/{seed_shop_id}")
    props = payload.get("props") or {}
    provinces = props.get("provinces") or {}
    if not isinstance(provinces, dict):
        raise RuntimeError("Could not find props.provinces from seed shop response.")
    return {str(k): str(v) for k, v in provinces.items()}


def select_provinces(
    province_dict: Dict[str, str],
    target_codes: Sequence[str],
    include_non_mainland: bool,
) -> List[Tuple[str, str]]:
    items: List[Tuple[str, str]] = []
    if target_codes:
        for code in target_codes:
            name = province_dict.get(code, "")
            items.append((code, name))
        return sorted(items, key=lambda x: x[0])

    for code, name in province_dict.items():
        if code == "0":
            continue
        if include_non_mainland or is_mainland_province(code):
            items.append((code, name))
    return sorted(items, key=lambda x: x[0])


def crawl_province_shops(
    client: BemanicnClient,
    provinces: Sequence[Tuple[str, str]],
) -> Tuple[Dict[int, Dict[str, Any]], Dict[str, str], Dict[str, str], Dict[str, str], int]:
    seeds: Dict[int, Dict[str, Any]] = {}
    province_names: Dict[str, str] = {code: name for code, name in provinces if name}
    city_names: Dict[str, str] = {}
    county_names: Dict[str, str] = {}
    city_request_count = 0

    for idx, (province_code, province_name) in enumerate(provinces, start=1):
        referer = f"{BASE_URL}/region/province/{province_code}"
        payload = client.get_json(f"/region/province/{province_code}", referer=referer)
        props = payload.get("props") or {}

        province_obj = props.get("province") or {}
        if province_obj.get("name"):
            province_names[province_code] = str(province_obj.get("name"))
        elif province_name:
            province_names[province_code] = province_name

        cities = props.get("cities") or {}
        counties = props.get("counties") or {}
        city_names.update({str(k): str(v) for k, v in cities.items()})
        county_names.update({str(k): str(v) for k, v in counties.items()})

        # Province page only exposes partial shops; use city pages for full shop coverage.
        province_city_list = province_obj.get("cities") or []
        if not isinstance(province_city_list, list):
            province_city_list = []
        province_city_codes: List[str] = []
        for city in province_city_list:
            if not isinstance(city, dict):
                continue
            city_code = city.get("city_code")
            if city_code is None:
                continue
            city_code_str = str(city_code)
            province_city_codes.append(city_code_str)
            if city.get("name"):
                city_names[city_code_str] = str(city.get("name"))

        province_shop_rows = 0
        for city_code in province_city_codes:
            city_payload = client.get_json(
                f"/region/city/{city_code}",
                referer=f"{BASE_URL}/region/city/{city_code}",
            )
            city_request_count += 1
            city_props = city_payload.get("props") or {}
            city_obj = city_props.get("city") or {}
            if not isinstance(city_obj, dict):
                city_obj = {}

            city_code_in_payload = str(city_obj.get("city_code") or city_code)
            if city_obj.get("name"):
                city_names[city_code_in_payload] = str(city_obj.get("name"))

            city_counties = city_obj.get("counties") or []
            if isinstance(city_counties, list):
                for county in city_counties:
                    if not isinstance(county, dict):
                        continue
                    county_code = county.get("county_code")
                    if county_code is None:
                        continue
                    county_code_str = str(county_code)
                    if county.get("name"):
                        county_names[county_code_str] = str(county.get("name"))

            city_shops = city_obj.get("shops") or []
            if not isinstance(city_shops, list):
                city_shops = []

            province_shop_rows += len(city_shops)
            for shop in city_shops:
                if not isinstance(shop, dict):
                    continue
                raw_id = shop.get("id")
                if raw_id is None:
                    continue
                shop_id = int(raw_id)
                seeds[shop_id] = {
                    "id": shop_id,
                    "name": shop.get("name"),
                    "address": shop.get("address"),
                    "province_code": shop.get("province_code") or province_code,
                    "city_code": shop.get("city_code") or city_code_in_payload,
                    "county_code": shop.get("county_code"),
                    "option3": shop.get("option3"),
                }

        print(
            f"[province {idx}/{len(provinces)}] {province_code} {province_names.get(province_code, '')}: "
            f"{len(province_city_codes)} cities, {province_shop_rows} shop rows, unique total {len(seeds)}",
            flush=True,
        )

    return seeds, province_names, city_names, county_names, city_request_count


def fetch_one_shop_detail(
    client: BemanicnClient,
    shop_id: int,
) -> Tuple[int, Dict[str, Any], Dict[str, str], Dict[str, Any]]:
    payload = client.get_json(f"/s/{shop_id}", referer=f"{BASE_URL}/s/{shop_id}")
    props = payload.get("props") or {}
    shop = props.get("shop") or {}
    titles_name = ((props.get("titles") or {}).get("name") or {})
    titles_name = {str(k): str(v) for k, v in titles_name.items()}
    if not isinstance(shop, dict):
        shop = {}
    raw_props = {
        "component": payload.get("component"),
        "url": payload.get("url"),
        "version": payload.get("version"),
        "shop": shop,
        "images_count": props.get("images_count"),
        "titles": props.get("titles"),
        "provinces": props.get("provinces"),
        "cities": props.get("cities"),
        "counties": props.get("counties"),
    }
    return shop_id, shop, titles_name, raw_props


def crawl_shop_details(
    client: BemanicnClient,
    shop_ids: Sequence[int],
    workers: int,
) -> Tuple[Dict[int, Dict[str, Any]], Dict[int, Dict[str, Any]], Dict[str, str], List[int]]:
    details: Dict[int, Dict[str, Any]] = {}
    detail_props: Dict[int, Dict[str, Any]] = {}
    title_name_map: Dict[str, str] = {}
    failures: List[int] = []

    if not shop_ids:
        return details, detail_props, title_name_map, failures

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        future_map = {executor.submit(fetch_one_shop_detail, client, sid): sid for sid in shop_ids}
        done = 0
        total = len(shop_ids)

        for future in concurrent.futures.as_completed(future_map):
            shop_id = future_map[future]
            done += 1
            try:
                sid, shop, titles, raw_props = future.result()
                details[sid] = shop
                detail_props[sid] = raw_props
                if titles:
                    title_name_map.update(titles)
            except Exception as exc:  # noqa: BLE001
                failures.append(shop_id)
                print(f"[detail {done}/{total}] {shop_id} failed: {exc}", flush=True)
                continue

            if done % 50 == 0 or done == total:
                print(f"[detail] progress {done}/{total}", flush=True)

    return details, detail_props, title_name_map, failures


def normalize_record(
    seed: Dict[str, Any],
    detail: Optional[Dict[str, Any]],
    province_names: Dict[str, str],
    city_names: Dict[str, str],
    county_names: Dict[str, str],
    title_names: Dict[str, str],
) -> Dict[str, Any]:
    base = detail or seed
    province_code = str(base.get("province_code") or seed.get("province_code") or "")
    city_code = str(base.get("city_code") or seed.get("city_code") or "")
    county_code = str(base.get("county_code") or seed.get("county_code") or "")
    shop_id = int(base.get("id") or seed.get("id"))

    arcades_raw = detail.get("arcades", []) if detail else []
    arcades: List[Dict[str, Any]] = []
    if isinstance(arcades_raw, list):
        for arcade in arcades_raw:
            if not isinstance(arcade, dict):
                continue
            title_id = arcade.get("title_id")
            title_id_str = str(title_id) if title_id is not None else ""
            title_name = title_names.get(title_id_str)
            arcades.append(
                {
                    "id": arcade.get("id"),
                    "title_id": title_id,
                    "title_name": title_name,
                    "title_icon_url": f"{BASE_URL}/imgs/titles/{title_name}.png" if title_name else None,
                    "quantity": arcade.get("quantity"),
                    "version": arcade.get("version"),
                    "coin": arcade.get("coin"),
                    "eacoin": arcade.get("eacoin"),
                    "comment": arcade.get("comment"),
                }
            )

    return {
        "source": "bemanicn",
        "source_id": shop_id,
        "source_url": f"{BASE_URL}/s/{shop_id}",
        "name": base.get("name") or seed.get("name"),
        "name_pinyin": base.get("name_pinyin"),
        "address": base.get("address"),
        "transport": base.get("transport"),
        "url": base.get("url"),
        "comment": base.get("comment"),
        "province_code": province_code or None,
        "province_name": province_names.get(province_code),
        "city_code": city_code or None,
        "city_name": city_names.get(city_code),
        "county_code": county_code or None,
        "county_name": county_names.get(county_code),
        "status": base.get("status"),
        "type": base.get("type"),
        "pay_type": base.get("pay_type"),
        "locked": base.get("locked"),
        "ea_status": base.get("ea_status"),
        "price": base.get("price"),
        "start_time": base.get("start_time"),
        "end_time": base.get("end_time"),
        "fav_count": base.get("fav_count"),
        "created_at": base.get("created_at"),
        "updated_at": base.get("updated_at"),
        "option1": base.get("option1"),
        "option2": base.get("option2"),
        "option3": base.get("option3"),
        "option4": base.get("option4"),
        "option5": base.get("option5"),
        "collab": base.get("collab"),
        "image_thumb": base.get("image_thumb"),
        "events": base.get("events"),
        "arcades": arcades,
    }


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")


def main() -> None:
    args = parse_args()
    if args.delay_max < args.delay_min:
        raise ValueError("--delay-max must be >= --delay-min")

    target_codes = normalize_code_args(args.province_code)
    cfg = CrawlConfig(
        timeout=args.timeout,
        retries=args.retries,
        retry_backoff=args.retry_backoff,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
    )
    client = BemanicnClient(config=cfg)

    started_at = utc_now_iso()
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    province_dict = get_province_dict(client, args.seed_shop_id)
    provinces = select_provinces(
        province_dict=province_dict,
        target_codes=target_codes,
        include_non_mainland=args.include_non_mainland,
    )
    if not provinces:
        raise RuntimeError("No provinces selected. Check --province-code arguments.")

    print(f"[start] provinces to crawl: {len(provinces)}", flush=True)
    seeds, province_names, city_names, county_names, city_request_count = crawl_province_shops(
        client,
        provinces,
    )

    shop_ids = sorted(seeds.keys())
    if args.max_shops and args.max_shops > 0:
        shop_ids = shop_ids[: args.max_shops]
        print(f"[limit] max shops enabled, detail targets: {len(shop_ids)}", flush=True)
    selected_shop_ids = set(shop_ids)

    details: Dict[int, Dict[str, Any]] = {}
    detail_props: Dict[int, Dict[str, Any]] = {}
    title_names: Dict[str, str] = {}
    failures: List[int] = []
    if not args.skip_details:
        details, detail_props, title_names, failures = crawl_shop_details(client, shop_ids, args.workers)
    else:
        print("[skip] detail crawling disabled by --skip-details", flush=True)

    records = []
    for shop_id in sorted(seeds.keys()):
        if args.max_shops and args.max_shops > 0 and shop_id not in selected_shop_ids:
            continue
        records.append(
            normalize_record(
                seed=seeds[shop_id],
                detail=details.get(shop_id),
                province_names=province_names,
                city_names=city_names,
                county_names=county_names,
                title_names=title_names,
            )
        )

    province_index = [
        {"province_code": code, "province_name": province_names.get(code) or name}
        for code, name in provinces
    ]

    seed_rows = [seeds[sid] for sid in sorted(seeds.keys())]
    detail_rows = [details[sid] for sid in sorted(details.keys())]
    detail_props_rows = [detail_props[sid] for sid in sorted(detail_props.keys())]

    province_path = output_dir / "province_index.json"
    seed_path = output_dir / "shops_seed.jsonl"
    detail_path = output_dir / "shops_detail_raw.jsonl"
    detail_props_path = output_dir / "shops_detail_props.jsonl"
    normalized_path = output_dir / "shops_detail.jsonl"
    summary_path = output_dir / "run_summary.json"

    write_json(province_path, province_index)
    write_jsonl(seed_path, seed_rows)
    write_jsonl(detail_path, detail_rows)
    write_jsonl(detail_props_path, detail_props_rows)
    write_jsonl(normalized_path, records)

    finished_at = utc_now_iso()
    summary = {
        "source": "bemanicn",
        "base_url": BASE_URL,
        "started_at": started_at,
        "finished_at": finished_at,
        "args": {
            "seed_shop_id": args.seed_shop_id,
            "province_code": target_codes,
            "include_non_mainland": bool(args.include_non_mainland),
            "skip_details": bool(args.skip_details),
            "max_shops": args.max_shops,
            "workers": args.workers,
            "timeout": args.timeout,
            "retries": args.retries,
            "retry_backoff": args.retry_backoff,
            "delay_min": args.delay_min,
            "delay_max": args.delay_max,
        },
        "counts": {
            "province_count": len(province_index),
            "city_request_count": city_request_count,
            "seed_shop_count": len(seed_rows),
            "detail_requested_count": len(shop_ids) if not args.skip_details else 0,
            "detail_success_count": len(detail_rows),
            "detail_failure_count": len(failures),
            "normalized_count": len(records),
        },
        "failed_shop_ids": sorted(failures),
        "outputs": {
            "province_index": str(province_path),
            "shops_seed": str(seed_path),
            "shops_detail_raw": str(detail_path),
            "shops_detail_props": str(detail_props_path),
            "shops_detail": str(normalized_path),
            "run_summary": str(summary_path),
        },
    }
    write_json(summary_path, summary)

    print(f"[done] normalized shops: {len(records)}", flush=True)
    print(f"[done] summary file: {summary_path}", flush=True)


if __name__ == "__main__":
    main()
