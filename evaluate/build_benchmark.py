"""Build the public development benchmark from curated, locally verified targets.

Run with evaluate/.venv/bin/python -m evaluate.build_benchmark.
No network, invented coordinates, or generated shop records are involved.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/local/arcades.geocoded.sample.jsonl"
SHOPS = ROOT / "data/processed/bemanicn/arcade_shops.jsonl"
TITLES = ROOT / "data/processed/bemanicn/arcade_titles.jsonl"
OUT = ROOT / "evaluate/datasets/public"

# Hand-authored language and targets; titles/addresses/IDs are audited below.
TARGETS = [
    (33, "maimai", "广州天河城逛累了，环游嘉年华有舞萌能打不？正佳那边这次先不看。"),
    (34, "SOUND VOLTEX", "广州百脑汇那个梦游仙境，想搓两把 sdvx，帮我看看有没有这个机种。"),
    (35, "CHUNITHM", "正佳里面那个哇哇哇能打中二吗？我说的是广州这家，不用给我列全市。"),
    (45, "maimai", "深圳梅林卓悦汇里的星际传奇有舞萌吗？别把宝安壹方城那家混进来。"),
    (48, "CHUNITHM", "深圳核客想去一趟，主要打中二，帮我确认店里有没有。"),
    (552, "maimai", "深圳宝安壹方城星际传奇能打舞萌不？是宝安这家，不是龙华壹方天地。"),
    (315, "maimai", "南京新街口正洪大厦那个原来的大风，现在叫趣玩汇对吧，舞萌还有记录吗？"),
    (318, "maimai", "南京常发广场的风云再起能打舞萌吗？其他分店先不看。"),
    (447, "CHUNITHM", "南京鸽屋咕咕咕有中二没？想去金銮大厦那家打，别只给我舞萌的信息。"),
    (231, "CHUNITHM", "济南和谐广场汤姆熊里面能打中二吗？帮我查机种记录，别靠店名猜。"),
    (543, "maimai", "济南高新万达那个大玩家，现在是不是叫 Play1？我想打舞萌，查一下这家。"),
    (584, "maimai", "济南世茂广场的威龙传奇有舞萌没？普通版和 DX 都算。"),
    (165, "maimai", "武汉江汉路地铁站那个风云再起有舞萌能玩吗？不是问全武汉的分店。"),
    (160, "maimai", "武汉银泰创意城真快活里面舞萌有记录吗？其他真快活先别推荐。"),
    (580, "maimai", "武汉光谷步行街嗨森汇有舞萌吗？旧版也行，想看看这家店。"),
    (225, "CHUNITHM", "合肥淮河路步行街的风云再起，中二能打不？有机种记录再推荐。"),
    (226, "maimai", "合肥之心城那个明日世界有舞萌没？就看这家商场里的。"),
    (546, "maimai", "合肥包河万达大玩家能打舞萌不？别给我换成天鹅湖万达。"),
    (1342, "maimai", "杭州奥体印象城的星际传奇有没有舞萌？别把金沙和西溪印象城也算进来。"),
    (1389, "maimai", "杭州金沙印象城第一回合能打舞萌吗？我说金沙那家，不是奥体。"),
    (4077, "maimai", "杭州西溪印象城木马王国里面有舞萌没？别找去别的印象城。"),
    (4799, "CHUNITHM", "天水桥南五道街那个一起玩能打中二吗？不是中华西路步行街分店。"),
    (5028, "maimai", "天水秦州万达里的宝贝王，有舞萌可以打吗？只查这家就行。"),
    (6013, "maimai", "天水中华西路步行街那个一起玩有舞萌吗？桥南那家先不要。"),
]


def read(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def build():
    rows = {r["source_id"]: r for r in read(DATA)}
    originals = {r["source_id"]: r for r in read(SHOPS)}
    titles = read(TITLES)
    cases, provenance = [], []
    for sid, title, message in TARGETS:
        row, original = rows[sid], originals[sid]
        for key in ("source", "source_id", "source_url", "name", "city_name", "address"):
            assert row[key] == original[key], (sid, key)
        actual = Counter((a["title_name"], a.get("quantity")) for a in row["arcades"])
        expected = Counter((a["title_name"], a.get("quantity")) for a in titles if a["source_id"] == sid)
        assert actual == expected, (sid, "machine records differ")
        assert any(title.casefold() in a["title_name"].casefold() and a.get("quantity", 0) > 0
                   for a in row["arcades"]), (sid, title)
        case_id = f"local-shop-{sid}"
        cases.append(dict(id=case_id, group="retrieval", description=f"本地快照：{row['city_name']} / {row['name']}；不假设实时营业状态或距离。",
                          capabilities=["colloquial-title-alias", "named-place", "entity-disambiguation"],
                          turns=[dict(message=message, shop_ids=[sid], required_tools=["db_query_tool"])]))
        provenance.append(dict(case_id=case_id, source_id=sid, name=row["name"], city=row["city_name"],
                               address=row["address"], title=title, source_url=row["source_url"]))

    def turn(message, ids):
        return dict(message=message, shop_ids=ids, required_tools=["db_query_tool"])

    compound = [
        ("tianshui-casual-arcades", [turn("天水有没有机厅玩的？有音游机器记录的都给我看看，别把陇南的算进来。", [4799, 5028, 6013])]),
        ("tianshui-chunithm-only", [turn("天水哪能打中二？只要有中二记录的，只有舞萌的先别推。", [4799])]),
        ("hangzhou-impressions-exclusion", [turn("杭州奥体印象城和金沙印象城里的星际传奇、第一回合都帮我看看能不能打舞萌，西溪木马王国这次不去。", [1342, 1389])]),
        ("guangzhou-two-malls", [turn("广州天河城环游嘉年华和正佳哇哇哇，这两家哪些有中二？都查查，其他店先不要。", [33, 35])]),
        ("cross-city-context-reset", [turn("杭州金沙印象城的第一回合有舞萌吗？", [1389]),
                                      turn("杭州先不看了，改查合肥之心城明日世界的舞萌，别留着刚才那家。", [226])]),
        ("jinan-no-chunithm-record", [turn("济南高新万达的 Play1 大玩家有中二吗？只找这家有中二的记录，没有就说没查到，不用推荐其他店。", [])]),
    ]
    # City-wide oracles also need to be exhaustive against the full local snapshot.
    tianshui = [r for r in rows.values() if r["city_name"] == "天水市"]
    assert {r["source_id"] for r in tianshui if r["arcades"]} == {4799, 5028, 6013}
    assert {r["source_id"] for r in tianshui if any(a["title_name"] == "CHUNITHM" for a in r["arcades"])} == {4799}
    assert not any(a["title_name"] == "CHUNITHM" for a in rows[543]["arcades"])
    cases.extend(dict(id=name, group="retrieval", capabilities=["compound-constraint", "evidence-discipline"], turns=turns)
                 for name, turns in compound)
    # The local enrichment contains only 15 geocoded shops. Restrict distance
    # comparisons to explicitly named candidates, not an unknown city-wide nearest.
    for case_id, origin, ids, message in [
        ("shanghai-here-maimai", {"lng": 121.47, "lat": 31.23, "city": "上海市"}, [6, 1],
         "这里附近舞萌在哪？先只比较上海人民广场风云再起和街机烈火这两家，按离我近的排。"),
        ("beijing-here-maimai", {"lng": 116.37, "lat": 39.90, "city": "北京市"}, [2, 4],
         "这里想打舞萌，西单明珠风云再起和朝阳大悦城环游嘉年华这两家帮我按近到远排一下。"),
    ]:
        for sid in ids:
            assert rows[sid]["longitude_gcj02"] is not None
            assert any("maimai" in a["title_name"] for a in rows[sid]["arcades"])
        cases.append(dict(id=case_id, group="retrieval", capabilities=["client-location", "distance-sort", "candidate-comparison"],
                          turns=[dict(message=message, location=origin, shop_ids=ids, ordered=True,
                                      required_tools=["db_query_tool"])]))

    def route_turn(start, end, mode):
        a, b = rows[start], rows[end]
        origin = [a["longitude_gcj02"], a["latitude_gcj02"]]
        destination = [b["longitude_gcj02"], b["latitude_gcj02"]]
        assert all(v is not None for v in origin + destination) and origin != destination
        return dict(message=f"从{a['city_name']}{a['name']}去{b['name']}，帮我规划{'步行' if mode == 'walking' else '开车'}路线。",
                    route_mode=mode, route_origin=origin, route_destination=destination,
                    required_tools=["route_plan_tool"], forbid_route=False)

    for case_id, start, end, mode in [("shanghai-walking", 1, 6, "walking"),
                                     ("shanghai-mall-walking", 6, 9, "walking"),
                                     ("beijing-driving", 2, 4, "driving")]:
        cases.append(dict(id=case_id, group="navigation", capabilities=["named-origin", "named-destination", "live-route"],
                          turns=[route_turn(start, end, mode)]))
    switched = route_turn(1, 6, "driving")
    switched["message"] = "临时改开车了，起点终点都不变，帮我换一下路线。"
    cases.append(dict(id="shanghai-route-mode-switch", group="navigation", capabilities=["multi-turn", "route-mode-switch"],
                      turns=[route_turn(1, 6, "walking"), switched]))
    cases.extend([
        dict(id="missing-location-clarification", group="robustness", capabilities=["missing-context"],
             turns=[dict(message="这里附近哪里能打舞萌？我还没给你位置。", shop_ids=[], forbidden_tools=["route_plan_tool"])]),
        dict(id="missing-city-clarification", group="robustness", capabilities=["ambiguous-landmark"],
             turns=[dict(message="万达里的大玩家能打舞萌不？我还没说哪个城市哪家万达，先问清楚再推荐。", shop_ids=[], forbidden_tools=["route_plan_tool"])]),
    ])
    manifest = dict(version=4, data_path=str(DATA.relative_to(ROOT)), targets=provenance,
                    source_sha256={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                   for p in (DATA, SHOPS, TITLES)},
                    geocoded_targets=[{k: rows[sid][k] for k in ("source_id", "name", "city_name", "longitude_gcj02", "latitude_gcj02")}
                                      for sid in (1, 2, 4, 6, 9)],
                    notes="Local snapshot evidence, not current opening/availability. Route endpoints use local GCJ02; client location uses WGS84. Nearby comparison only covers the named candidates.")
    return cases, manifest


if __name__ == "__main__":
    cases, manifest = build()
    (OUT / "benchmark.yaml").write_text("# v4：真实本地数据开发集，32 retrieval / 4 navigation / 2 robustness；配套数据见 benchmark.sources.json。\n" +
                                         yaml.safe_dump(cases, allow_unicode=True, sort_keys=False))
    (OUT / "benchmark.sources.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"Verified {len(manifest['targets'])} targets; wrote {len(cases)} cases.")
