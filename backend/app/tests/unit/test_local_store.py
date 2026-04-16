"""Unit tests for local JSONL-backed store filtering and region indexes."""

from __future__ import annotations

import json
from pathlib import Path

from app.infra.db.local_store import LocalArcadeStore


def _write_rows(path: Path) -> None:
    rows = [
        {
            "source": "bemanicn",
            "source_id": 1,
            "source_url": "https://map.bemanicn.com/s/1",
            "name": "Alpha Arcade",
            "name_pinyin": "alpha",
            "address": "Addr A",
            "province_code": "110000000000",
            "province_name": "Beijing",
            "city_code": "110100000000",
            "city_name": "Beijing",
            "county_code": "110101000000",
            "county_name": "Dongcheng",
            "updated_at": "2026-02-20T00:00:00Z",
            "arcades": [{"title_name": "maimai", "quantity": 2}],
        },
        {
            "source": "bemanicn",
            "source_id": 2,
            "source_url": "https://map.bemanicn.com/s/2",
            "name": "Beta",
            "address": "Addr B",
            "province_code": "310000000000",
            "province_name": "Shanghai",
            "city_code": "310100000000",
            "city_name": "Shanghai",
            "county_code": "310101000000",
            "county_name": "Huangpu",
            "updated_at": "2026-02-19T00:00:00Z",
            "arcades": [],
        },
    ]
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")


def test_filter_by_keyword_and_region(tmp_path: Path) -> None:
    data_path = tmp_path / "shops.jsonl"
    _write_rows(data_path)
    store = LocalArcadeStore.from_jsonl(data_path)

    page, total = store.list_shops(
        keyword="maimai",
        province_code="110000000000",
        city_code=None,
        county_code=None,
        has_arcades=True,
        page=1,
        page_size=10,
    )
    assert total == 1
    assert page[0]["source_id"] == 1


def test_regions_index(tmp_path: Path) -> None:
    data_path = tmp_path / "shops.jsonl"
    _write_rows(data_path)
    store = LocalArcadeStore.from_jsonl(data_path)

    provinces = store.list_provinces()
    assert {row["code"] for row in provinces} == {"110000000000", "310000000000"}

    cities = store.list_cities("110000000000")
    assert len(cities) == 1
    assert cities[0]["name"] == "Beijing"


def test_keyword_can_match_city_and_title_terms(tmp_path: Path) -> None:
    data_path = tmp_path / "shops_city_title.jsonl"
    row = {
        "source": "bemanicn",
        "source_id": 9,
        "source_url": "https://map.bemanicn.com/s/9",
        "name": "GZ Arcade",
        "address": "Tianhe",
        "province_code": "440000000000",
        "province_name": "\u5e7f\u4e1c",
        "city_code": "440100000000",
        "city_name": "\u5e7f\u5dde",
        "county_code": "440106000000",
        "county_name": "\u5929\u6cb3\u533a",
        "updated_at": "2026-02-21T00:00:00Z",
        "arcades": [{"title_name": "maimai", "quantity": 3}],
    }
    with data_path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False))
        handle.write("\n")

    store = LocalArcadeStore.from_jsonl(data_path)
    page, total = store.list_shops(
        keyword="\u5e7f\u5dde maimai",
        province_code=None,
        city_code=None,
        county_code=None,
        has_arcades=True,
        page=1,
        page_size=10,
    )
    assert total == 1
    assert page[0]["source_id"] == 9


def test_filter_by_city_name_accepts_suffix_variants(tmp_path: Path) -> None:
    data_path = tmp_path / "shops_city_filter.jsonl"
    row = {
        "source": "bemanicn",
        "source_id": 11,
        "source_url": "https://map.bemanicn.com/s/11",
        "name": "City Filter Arcade",
        "address": "Tianhe",
        "province_code": "440000000000",
        "province_name": "\u5e7f\u4e1c\u7701",
        "city_code": "440100000000",
        "city_name": "\u5e7f\u5dde\u5e02",
        "county_code": "440106000000",
        "county_name": "\u5929\u6cb3\u533a",
        "updated_at": "2026-02-21T00:00:00Z",
        "arcades": [{"title_name": "maimai", "quantity": 1}],
    }
    with data_path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False))
        handle.write("\n")

    store = LocalArcadeStore.from_jsonl(data_path)
    page_one, total_one = store.list_shops(
        keyword="maimai",
        province_code=None,
        city_code=None,
        county_code=None,
        has_arcades=True,
        page=1,
        page_size=10,
        city_name="\u5e7f\u5dde",
    )
    page_two, total_two = store.list_shops(
        keyword="maimai",
        province_code=None,
        city_code=None,
        county_code=None,
        has_arcades=True,
        page=1,
        page_size=10,
        city_name="\u5e7f\u5dde\u5e02",
    )

    assert total_one == 1
    assert total_two == 1
    assert page_one[0]["source_id"] == 11
    assert page_two[0]["source_id"] == 11


def test_sort_by_specific_title_quantity_supports_asc_and_desc(tmp_path: Path) -> None:
    data_path = tmp_path / "shops_sort_title.jsonl"
    rows = [
        {
            "source": "bemanicn",
            "source_id": 1,
            "source_url": "https://map.bemanicn.com/s/1",
            "name": "Alpha",
            "province_code": "110000000000",
            "province_name": "Beijing",
            "city_code": "110100000000",
            "city_name": "Beijing",
            "county_code": "110101000000",
            "county_name": "Dongcheng",
            "updated_at": "2026-02-20T00:00:00Z",
            "arcades": [
                {"title_name": "maimai", "quantity": 2},
                {"title_name": "sdvx", "quantity": 1},
            ],
        },
        {
            "source": "bemanicn",
            "source_id": 2,
            "source_url": "https://map.bemanicn.com/s/2",
            "name": "Beta",
            "province_code": "110000000000",
            "province_name": "Beijing",
            "city_code": "110100000000",
            "city_name": "Beijing",
            "county_code": "110101000000",
            "county_name": "Dongcheng",
            "updated_at": "2026-02-21T00:00:00Z",
            "arcades": [{"title_name": "maimai", "quantity": 5}],
        },
        {
            "source": "bemanicn",
            "source_id": 3,
            "source_url": "https://map.bemanicn.com/s/3",
            "name": "Gamma",
            "province_code": "110000000000",
            "province_name": "Beijing",
            "city_code": "110100000000",
            "city_name": "Beijing",
            "county_code": "110101000000",
            "county_name": "Dongcheng",
            "updated_at": "2026-02-19T00:00:00Z",
            "arcades": [{"title_name": "sdvx", "quantity": 4}],
        },
    ]
    with data_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")

    store = LocalArcadeStore.from_jsonl(data_path)

    maimai_desc, total_desc = store.list_shops(
        keyword=None,
        province_code=None,
        city_code=None,
        county_code=None,
        has_arcades=True,
        page=1,
        page_size=10,
        sort_by="title_quantity",
        sort_order="desc",
        sort_title_name="maimai",
    )
    assert total_desc == 3
    assert [row["source_id"] for row in maimai_desc] == [2, 1, 3]

    sdvx_asc, total_asc = store.list_shops(
        keyword=None,
        province_code=None,
        city_code=None,
        county_code=None,
        has_arcades=True,
        page=1,
        page_size=10,
        sort_by="title_quantity",
        sort_order="asc",
        sort_title_name="sdvx",
    )
    assert total_asc == 3
    assert [row["source_id"] for row in sdvx_asc] == [2, 1, 3]


def test_title_quantity_sort_matches_common_title_aliases(tmp_path: Path) -> None:
    data_path = tmp_path / "shops_sort_title_alias.jsonl"
    rows = [
        {
            "source": "bemanicn",
            "source_id": 1,
            "source_url": "https://map.bemanicn.com/s/1",
            "name": "Alpha",
            "arcades": [{"title_name": "maimai DX", "quantity": 4}],
        },
        {
            "source": "bemanicn",
            "source_id": 2,
            "source_url": "https://map.bemanicn.com/s/2",
            "name": "Beta",
            "arcades": [{"title_name": "舞萌DX", "quantity": 2}],
        },
        {
            "source": "bemanicn",
            "source_id": 3,
            "source_url": "https://map.bemanicn.com/s/3",
            "name": "Gamma",
            "arcades": [{"title_name": "SOUND VOLTEX EXCEED GEAR", "quantity": 5}],
        },
    ]
    with data_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")

    store = LocalArcadeStore.from_jsonl(data_path)

    maimai_desc, _ = store.list_shops(
        keyword=None,
        province_code=None,
        city_code=None,
        county_code=None,
        has_arcades=True,
        page=1,
        page_size=10,
        sort_by="title_quantity",
        sort_order="desc",
        sort_title_name="maimai",
    )
    assert [row["source_id"] for row in maimai_desc] == [1, 2, 3]

    sdvx_desc, _ = store.list_shops(
        keyword=None,
        province_code=None,
        city_code=None,
        county_code=None,
        has_arcades=True,
        page=1,
        page_size=10,
        sort_by="title_quantity",
        sort_order="desc",
        sort_title_name="sdvx",
    )
    assert [row["source_id"] for row in sdvx_desc] == [3, 2, 1]


def test_sort_by_distance_adds_distance_and_keeps_unmapped_rows_last(tmp_path: Path) -> None:
    data_path = tmp_path / "shops_sort_distance.jsonl"
    rows = [
        {
            "source": "bemanicn",
            "source_id": 1,
            "source_url": "https://map.bemanicn.com/s/1",
            "name": "Near",
            "longitude_wgs84": 116.397428,
            "latitude_wgs84": 39.90923,
            "arcades": [{"title_name": "maimai", "quantity": 1}],
        },
        {
            "source": "bemanicn",
            "source_id": 2,
            "source_url": "https://map.bemanicn.com/s/2",
            "name": "Far",
            "longitude_wgs84": 116.407428,
            "latitude_wgs84": 39.91923,
            "arcades": [{"title_name": "maimai", "quantity": 1}],
        },
        {
            "source": "bemanicn",
            "source_id": 3,
            "source_url": "https://map.bemanicn.com/s/3",
            "name": "Unmapped",
            "arcades": [{"title_name": "maimai", "quantity": 1}],
        },
    ]
    with data_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")

    store = LocalArcadeStore.from_jsonl(data_path)
    nearest, total = store.list_shops(
        keyword=None,
        province_code=None,
        city_code=None,
        county_code=None,
        has_arcades=True,
        page=1,
        page_size=10,
        sort_by="distance",
        sort_order="asc",
        origin_lng=116.397428,
        origin_lat=39.90923,
        origin_coord_system="wgs84",
    )
    assert total == 3
    assert [row["source_id"] for row in nearest] == [1, 2, 3]
    assert nearest[0]["distance_m"] == 0
    assert nearest[1]["distance_m"] > nearest[0]["distance_m"]
    assert "distance_m" not in nearest[2]

    farthest, _ = store.list_shops(
        keyword=None,
        province_code=None,
        city_code=None,
        county_code=None,
        has_arcades=True,
        page=1,
        page_size=10,
        sort_by="distance",
        sort_order="desc",
        origin_lng=116.397428,
        origin_lat=39.90923,
        origin_coord_system="wgs84",
    )
    assert [row["source_id"] for row in farthest] == [2, 1, 3]
