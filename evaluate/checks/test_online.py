"""No-network end-to-end online-runner tests using the production runtime and HTTP codec."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from evaluate.config import Case, load, Turn
from evaluate.environment import MemorySessions, Budget, RecordedProvider
from evaluate.online import Evidence, run, compatibility, judge, plan, report
from evaluate.scoring import usage, cost, grade_turn
from evaluate.review import export_review, import_review, compare
from evaluate.scoring import aggregate


def config(tmp_path, monkeypatch, extra=""):
    for key in list(__import__("os").environ):
        if key.startswith("EVAL_"):
            monkeypatch.delenv(key)
    env = tmp_path / "eval.env"
    env.write_text("EVAL_LLM_API_KEY=fixture-secret\nEVAL_LLM_BASE_URL=https://fixture.invalid/v1\n"
                   "EVAL_LLM_MODEL=fixture-model\nEVAL_REQUEST_INTERVAL_S=0\n" + extra)
    return load(env)


def transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


def test_live_runner_uses_real_tools_and_separate_sessions_and_redacts_secrets(tmp_path, monkeypatch):
    cfg = config(tmp_path, monkeypatch, "EVAL_REPEAT=2\n")
    cfg.cases = [Case(id="runner-isolation", group="retrieval", turns=[Turn(
        message="查找上海市所有有 maimai 的机厅。", shop_ids=[900001, 900003], required_tools=["db_query_tool"]
    )])]
    legacy = Path(__file__).resolve().parents[1] / "fixtures/arcades/legacy-contracts.jsonl"
    cfg.data = legacy
    requests = []
    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        has_result = any(m.get("role") == "tool" for m in payload["messages"])
        message = ({"role": "assistant", "content": "合成近点机厅和合成远点机厅。fixture-secret"} if has_result else
                   {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {
                    "name": "db_query_tool", "arguments": json.dumps({"page": 1, "page_size": 5, "city_name": "上海", "title_name": "maimai"})}}]})
        return httpx.Response(200, json={"model": "fixture-model", "choices": [{"message": message,
            "finish_reason": "stop" if has_result else "tool_calls"}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}})
    transport(monkeypatch, handle)
    evidence = Evidence(tmp_path, cfg.values)
    assert asyncio.run(run(cfg, evidence)) == 0
    rows = [json.loads(line) for line in (tmp_path / "attempts.jsonl").read_text().splitlines()]
    assert len(rows) == 2 and all(row["hard_pass"] for row in rows)
    assert len({row["session_id"] for row in rows}) == 2
    assert all(row["quality_pass"] is None for row in rows)
    assert len(requests) == 4
    assert all(len([m for m in request["messages"] if m["role"] == "user"]) == 1 for request in requests)
    assert all("fixture-secret" not in f.read_text() for f in tmp_path.rglob("*.json*"))
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["agent_usage"]["total_tokens"] == 480
    assert summary["models"]["default"]["fully_passed"] == 0


def test_missing_key_is_not_run_and_does_not_inherit_production_env(tmp_path, monkeypatch):
    cfg = config(tmp_path, monkeypatch, "EVAL_LLM_API_KEY=\n")
    monkeypatch.setenv("LLM_API_KEY", "production-key")
    assert not cfg.ready(cfg.models["default"])
    assert asyncio.run(run(cfg, Evidence(tmp_path, cfg.values))) == 1
    rows = [json.loads(line) for line in (tmp_path / "attempts.jsonl").read_text().splitlines()]
    assert all(row["status"] == "not_run" for row in rows)
    assert not (tmp_path / "requests.jsonl").exists()


def test_call_budget_stops_further_http_requests_and_keeps_planned_rows(tmp_path, monkeypatch):
    cfg = config(tmp_path, monkeypatch, "EVAL_MAX_REQUESTS=1\n")
    count = 0
    def handle(request):
        nonlocal count
        count += 1
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": None,
            "tool_calls": [{"id": "c", "type": "function", "function": {"name": "db_query_tool", "arguments": '{"page":1,"page_size":5}'}}]}, "finish_reason": "tool_calls"}]})
    transport(monkeypatch, handle)
    assert asyncio.run(run(cfg, Evidence(tmp_path, cfg.values))) == 1
    rows = [json.loads(line) for line in (tmp_path / "attempts.jsonl").read_text().splitlines()]
    assert count == 1 and len(rows) == 3
    assert rows[0]["status"] == "budget_exhausted"
    assert all(row["status"] == "not_run" for row in rows[1:])


def test_timeout_cancels_model_and_saves_partial_evidence(tmp_path, monkeypatch):
    cfg = config(tmp_path, monkeypatch, "EVAL_ATTEMPT_TIMEOUT_S=0.1\n")
    cfg.cases = cfg.cases[:1]
    stopped = []
    async def handle(request):
        try:
            await asyncio.sleep(30)
        finally:
            stopped.append(True)
    transport(monkeypatch, handle)
    assert asyncio.run(run(cfg, Evidence(tmp_path, cfg.values))) == 1
    attempt = json.loads((tmp_path / "attempts.jsonl").read_text())
    assert attempt["status"] == "timeout" and stopped == [True]
    calls = json.loads((tmp_path / "calls.jsonl").read_text())
    assert calls["response"]["error"]["type"] == "CancelledError"
    assert attempt["usage"]["input_tokens"] is None


@pytest.mark.parametrize("score", [{"score": 101, "reason": "bad", "evidence_refs": ["e1"]},
                                   {"score": 95, "reason": "bad", "evidence_refs": ["invented"]}])
def test_judge_rejects_invalid_scores_or_evidence(tmp_path, monkeypatch, score):
    cfg = config(tmp_path, monkeypatch, "EVAL_JUDGE_ENABLED=true\nEVAL_JUDGE_API_KEY=judge-secret\n"
                 "EVAL_JUDGE_BASE_URL=https://fixture.invalid/v1\nEVAL_JUDGE_MODEL=judge\n")
    transport(monkeypatch, lambda req: httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(score)}, "finish_reason": "stop"}]}))
    result = asyncio.run(judge(cfg, {"attempt_id": "a", "case": {}, "snapshots": [{"evidence_id": "e1"}]}, Budget(1), Evidence(tmp_path, cfg.values)))
    assert result["judge_status"] == "error" and result["quality_pass"] is None


def test_incomplete_usage_and_cached_subsets_do_not_fabricate_cost():
    record = {"response": {"usage": {"input_tokens": 100, "output_tokens": 30, "cached_input_tokens": 60, "reasoning_tokens": 20}}}
    records = [record, {"response": {"usage": {}}}]
    assert usage(records)["total_tokens"] is None
    assert usage(records)["known_total_tokens"] == 130
    prices = {"P_INPUT_PRICE": "10", "P_OUTPUT_PRICE": "20", "P_CACHED_INPUT_PRICE": "1"}
    assert cost([record], prices, "P_") == pytest.approx(0.00106)
    assert cost(records, prices, "P_") is None


def test_wrong_or_stale_shops_cannot_pass_nonempty_reply():
    oracle = Turn(message="test", shop_ids=[1])
    snapshot = {"turn_start": 0, "state": {"status": "completed", "turn_index": 2, "turns": [],
                "working_memory": {"artifacts": {}, "artifact_meta": {"shops": {"turn_index": 1}}}},
                "response": {"reply": "done", "shops": [{"source_id": 2}]}}
    result = grade_turn(oracle, snapshot)
    assert not result["hard_pass"]
    assert set(result["failures"]) == {"shop_oracle_mismatch", "shops_stale_or_unproven"}


def test_conversational_oracle_checks_tool_arguments_and_inclusion_without_exact_trace():
    oracle = Turn(
        message="这里附近舞萌在哪？",
        required_shop_ids=[1], forbidden_shop_ids=[9], ordered_prefix=[1],
        tool_argument_assertions=[{"tool": "db_query_tool", "stage": "prepared", "contains": {
            "title_name": "maimai", "sort_by": "distance", "sort_order": "asc"
        }}],
    )
    tool = {"role": "tool", "name": "db_query_tool", "payload": {"status": "completed", "argument_evidence": {
        "parsed_arguments": {"title_name": "舞萌"},
        "prepared_arguments": {"title_name": "maimai", "sort_by": "distance", "sort_order": "asc"},
    }}}
    snapshot = {"turn_start": 0, "state": {"status": "completed", "turn_index": 1,
                "turns": [tool], "working_memory": {"artifacts": {}, "artifact_meta": {}}},
                "response": {"reply": "第一家在附近", "shops": [{"source_id": 1}, {"source_id": 2}]}}
    assert grade_turn(oracle, snapshot)["hard_pass"]
    tool["payload"]["argument_evidence"]["prepared_arguments"]["sort_by"] = "default"
    assert "tool_argument_constraint_missing" in grade_turn(oracle, snapshot)["failures"]


def test_compatibility_checks_second_response_uses_unpredictable_tool_result(tmp_path, monkeypatch):
    cfg = config(tmp_path, monkeypatch)
    def handle(request):
        payload = json.loads(request.content)
        last = payload["messages"][-1]
        if last["role"] == "tool":
            message = {"role": "assistant", "content": json.loads(last["content"])["marker"]}
        else:
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": "echo", "type": "function", "function": {
                "name": "eval_echo", "arguments": '{"marker":"arcade-eval"}'}}]}
        return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": "stop"}]})
    transport(monkeypatch, handle)
    assert asyncio.run(compatibility(cfg, Evidence(tmp_path, cfg.values))) == 0


def test_human_review_is_atomic_append_only_and_cannot_override_hard_failure(tmp_path):
    attempt = {"attempt_id": "a", "case_id": "case", "snapshots": [{"evidence_id": "e1"}], "hard_pass": False}
    evidence = Evidence(tmp_path, {})
    evidence.write("attempts", attempt)
    original = (tmp_path / "attempts.jsonl").read_text()
    exported = export_review(tmp_path)
    row = json.loads(exported.read_text())
    row.update(quality_pass=True, reviewer="reviewer", reason="clear answer")
    source = tmp_path / "reviews.jsonl"
    source.write_text(json.dumps(row) + "\n" + json.dumps({**row, "attempt_id": "unknown"}))
    with pytest.raises(ValueError):
        import_review(tmp_path, source)
    assert not (tmp_path / "human_reviews.jsonl").exists()
    source.write_text(json.dumps(row))
    assert import_review(tmp_path, source) == 1
    row.update(quality_pass=False, reason="corrected decision")
    source.write_text(json.dumps(row))
    assert import_review(tmp_path, source) == 1
    assert len((tmp_path / "human_reviews.jsonl").read_text().splitlines()) == 2
    assert (tmp_path / "attempts.jsonl").read_text() == original
    assert json.loads((tmp_path / "human_summary.json").read_text())["fully_passed"] == 0


def test_comparison_retains_unpaired_and_rejects_different_data(tmp_path):
    before, after = tmp_path / "before", tmp_path / "after"
    before.mkdir(); after.mkdir()
    manifest = {"data_sha256": "d", "case_sha256": "c", "map_mode": "disabled"}
    for path in [before, after]:
        (path / "manifest.json").write_text(json.dumps(manifest))
    attempt = {"model_profile": "m", "case_id": "c", "repeat_index": 0, "status": "completed", "hard_pass": True}
    (before / "attempts.jsonl").write_text(json.dumps(attempt))
    (after / "attempts.jsonl").write_text(json.dumps({**attempt, "hard_pass": False}))
    result = compare(before, after)
    assert result["regressions"] == [["m", "c", 0]]
    (after / "manifest.json").write_text(json.dumps({**manifest, "data_sha256": "other"}))
    with pytest.raises(ValueError):
        compare(before, after)


def test_judge_high_score_does_not_override_hard_failure(tmp_path, monkeypatch):
    cfg = config(tmp_path, monkeypatch, "EVAL_JUDGE_ENABLED=true\nEVAL_JUDGE_API_KEY=judge-secret\n"
                 "EVAL_JUDGE_BASE_URL=https://fixture.invalid/v1\nEVAL_JUDGE_MODEL=judge\n")
    score = {"score": 99, "reason": "clear", "evidence_refs": ["e1"]}
    transport(monkeypatch, lambda req: httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": json.dumps(score)}, "finish_reason": "stop"}]}))
    attempt = {"attempt_id": "a", "model_profile": "m", "group": "retrieval", "status": "completed", "duration_ms": 10,
               "hard_pass": False, "case": {}, "snapshots": [{"evidence_id": "e1"}]}
    result = asyncio.run(judge(cfg, attempt, Budget(1), Evidence(tmp_path, cfg.values)))
    assert result["quality_pass"] is True
    attempt.update(result)
    assert aggregate([attempt])["m"]["fully_passed"] == 0


def test_detailed_report_explains_percent_score_weights_and_model_failures(tmp_path):
    attempts = [
        {"attempt_id": "pass", "model_profile": "m", "case_id": "search", "group": "retrieval",
         "status": "completed", "hard_pass": True, "quality_pass": True, "score": 88,
         "duration_ms": 1, "turn_scores": [{"failures": []}], "cost": None},
        {"attempt_id": "fail", "model_profile": "m", "case_id": "route", "group": "navigation",
         "status": "completed", "hard_pass": False, "quality_pass": False, "score": 40,
         "reason": "没有生成路线", "duration_ms": 1,
         "turn_scores": [{"failures": ["route_missing", "required_tool_evidence_missing"]}], "cost": None},
        {"attempt_id": "robust", "model_profile": "m", "case_id": "clarify", "group": "robustness",
         "status": "completed", "hard_pass": True, "quality_pass": True, "score": 90,
         "duration_ms": 1, "turn_scores": [{"failures": []}], "cost": None},
    ]
    report(Evidence(tmp_path, {}), attempts)
    text = (tmp_path / "summary.md").read_text()
    assert "完整通过得分 = 100" in text
    assert "检索 **70%**、导航 **20%**、鲁棒性 **10%**" in text
    assert "准确性 **40**、完整性 **30**、清晰度 **20**、无臆造 **10**" in text
    assert "66.7% (2/3)" in text
    assert "route_missing" in text and "没有生成路线" in text


def test_route_oracle_rejects_wrong_origin_and_forged_geometry():
    oracle = Turn(message="route", route_mode="walking", route_origin=[121.47, 31.23],
                  route_destination=[121.49, 31.23], forbid_route=False, required_tools=["route_plan_tool"])
    point = lambda lng: {"lng": lng, "lat": 31.23, "coord_system": "gcj02", "source": "route", "precision": "approx"}
    route = {"origin": point(121.47), "destination": point(121.49), "polyline": [point(121.47), point(121.49)],
             "mode": "walking", "provider": "amap", "route_kind": "provider", "degraded": False,
             "distance_m": 2000, "duration_s": 1800}
    snapshot = {"turn_start": 0, "response": {"reply": "done", "route": route}, "state": {
        "status": "completed", "turn_index": 1, "working_memory": {"artifacts": {}, "artifact_meta": {"route": {"turn_index": 1}}},
        "turns": [{"role": "tool", "name": "route_plan_tool", "payload": {"status": "completed", "result": {"route": json.loads(json.dumps(route))}}}]}}
    assert grade_turn(oracle, snapshot)["hard_pass"]
    route["origin"] = point(122)
    result = grade_turn(oracle, snapshot)
    assert "route_origin_mismatch" in result["failures"]
    assert "route_not_from_tool_result" in result["failures"]


def test_matrix_and_cases_validate_without_secrets(tmp_path, monkeypatch):
    cfg = config(tmp_path, monkeypatch, "EVAL_MODELS=deepseek,glm,kimi,mimo\nEVAL_CASES=datasets/public/benchmark.yaml\nEVAL_DATA=../data/local/arcades.geocoded.sample.jsonl\n")
    assert len(cfg.cases) == 38
    assert plan(cfg)["planned_attempts"] == 152
    assert all(not cfg.ready(m) for m in cfg.models.values())
    assert {g: sum(c.group == g for c in cfg.cases) for g in ["retrieval", "navigation", "robustness"]} == {
        "retrieval": 32, "navigation": 4, "robustness": 2}


def test_benchmark_has_broad_city_and_landmark_coverage(tmp_path, monkeypatch):
    cfg = config(tmp_path, monkeypatch, "EVAL_CASES=datasets/public/benchmark.yaml\nEVAL_DATA=../data/local/arcades.geocoded.sample.jsonl\n")
    rows = [json.loads(line) for line in cfg.data.read_text().splitlines() if line]
    requested_cities = {"广州市", "深圳市", "南京市", "济南市", "武汉市", "合肥市"}
    target_rows = [row for row in rows if row["city_name"] in requested_cities and row["arcades"]]

    assert {row["city_name"] for row in target_rows} == requested_cities
    assert len(target_rows) >= 18
    assert all(sum(row["city_name"] == city for row in target_rows) >= 3 for city in requested_cities)
    expected_ids = {sid for case in cfg.cases for turn in case.turns for sid in (turn.shop_ids or [])}
    targets = [row for row in target_rows if row["source_id"] in expected_ids]
    assert all(sum(row["city_name"] == city for row in targets) >= 3 for city in requested_cities)


def test_benchmark_matches_verified_local_sources():
    from evaluate.build_benchmark import build, OUT
    import yaml
    cases, manifest = build()
    assert yaml.safe_load((OUT / "benchmark.yaml").read_text()) == cases
    assert json.loads((OUT / "benchmark.sources.json").read_text()) == manifest


def test_real_benchmark_targets_are_retrievable_by_production_store():
    from evaluate.build_benchmark import DATA, build
    from app.infra.db.local import LocalArcadeStore
    _, manifest = build()
    store = LocalArcadeStore.from_jsonl(DATA)
    assert store.health()["bad_lines"] == 0
    defaults = dict(keyword=None, province_code=None, city_code=None, county_code=None,
                    has_arcades=True, page=1, page_size=200)
    for target in manifest["targets"]:
        shops, _ = store.list_shops(**defaults, shop_name=target["name"],
                                   city_name=target["city"], title_name=target["title"])
        # Name search is fuzzy: e.g. 南京鸽屋 also retrieves its 仙林 branch.
        # The case specifies 金銮大厦, so its final oracle remains the one ID.
        assert target["source_id"] in [s["source_id"] for s in shops], target
        assert [s["source_id"] for s in shops if s["address"] == target["address"]] == [target["source_id"]], target
    for city, lng, lat, ids in [("上海市", 121.47, 31.23, [6, 1]), ("北京市", 116.37, 39.90, [2, 4])]:
        shops, _ = store.list_shops(**defaults, city_name=city, title_name="maimai", sort_by="distance",
                                   sort_order="asc", origin_lng=lng, origin_lat=lat, origin_coord_system="wgs84")
        assert [s["source_id"] for s in shops if s["source_id"] in ids] == ids


def test_empty_clarification_does_not_require_a_fake_query_artifact():
    snapshot = {"turn_start": 0, "state": {"status": "completed", "turn_index": 1, "turns": [],
                "working_memory": {"artifacts": {}}}, "response": {"reply": "你在哪个城市？", "shops": []}}
    assert grade_turn(Turn(message="附近？", shop_ids=[]), snapshot)["hard_pass"]
    snapshot["state"]["working_memory"]["artifacts"]["shops"] = []
    assert "shops_stale_or_unproven" in grade_turn(Turn(message="附近？", shop_ids=[]), snapshot)["failures"]


def test_judge_evidence_removes_recursive_raw_and_model_history():
    from evaluate.online import judge_evidence
    result = judge_evidence({"case": {}, "snapshots": [{"evidence_id": "e1", "state": {"turns": [
        {"role": "assistant", "content": "private reasoning"},
        {"role": "tool", "name": "db_query_tool", "payload": {"status": "completed", "result": {
            "shops": [{"source_id": 1, "price": "2", "raw": {"duplicate": "large"}}]}}}
    ]}, "response": {"reply": "2元"}}]})
    serialized = json.dumps(result)
    assert "private reasoning" not in serialized and "duplicate" not in serialized
    assert result["snapshots"][0]["tool_results"][0]["result"]["shops"][0]["price"] == "2"


def test_judge_has_independent_output_budget(tmp_path, monkeypatch):
    cfg = config(tmp_path, monkeypatch, "EVAL_JUDGE_ENABLED=true\nEVAL_MAX_OUTPUT_TOKENS=1024\n")
    assert cfg.judge.max_tokens == 4096
