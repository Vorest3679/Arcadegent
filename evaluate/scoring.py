"""Hard outcome checks remain separate from LLM answer quality."""
import math
from statistics import median


def grade_turn(oracle, snapshot):
    failures = []
    state = snapshot["state"]
    response = snapshot.get("response") or {}
    memory = state["working_memory"]
    artifacts = memory.get("artifacts", {})
    if state["status"] != "completed":
        failures.append("execution_failed")
    if not response.get("reply", "").strip():
        failures.append("answer_missing")
    tools = [t for t in state["turns"][snapshot["turn_start"]:] if t["role"] == "tool"]
    successful = {t.get("name") for t in tools if t["payload"].get("status") == "completed"}
    called = {t.get("name") for t in tools}
    if not set(oracle.required_tools) <= successful:
        failures.append("required_tool_evidence_missing")
    if set(oracle.forbidden_tools) & called:
        failures.append("forbidden_tool")
    if oracle.shop_ids is not None:
        shops = response.get("shops", [])
        ids = [s.get("source_id") for s in shops]
        if (ids != oracle.shop_ids if oracle.ordered else sorted(ids) != sorted(oracle.shop_ids)):
            failures.append("shop_oracle_mismatch")
        # A clarification with no search and no shop artifact has nothing to prove
        # fresh. Queries (including empty results) still require current evidence.
        needs_shop_evidence = bool(ids) or "shops" in artifacts or "db_query_tool" in oracle.required_tools
        if needs_shop_evidence and memory.get("artifact_meta", {}).get("shops", {}).get("turn_index") != state["turn_index"]:
            failures.append("shops_stale_or_unproven")
    response_ids = [shop.get("source_id") for shop in response.get("shops", [])]
    if not set(oracle.required_shop_ids) <= set(response_ids):
        failures.append("required_shop_missing")
    if set(oracle.forbidden_shop_ids) & set(response_ids):
        failures.append("forbidden_shop_returned")
    if oracle.ordered_prefix and response_ids[:len(oracle.ordered_prefix)] != oracle.ordered_prefix:
        failures.append("shop_order_prefix_mismatch")
    for assertion in oracle.tool_argument_assertions:
        found = False
        for turn in tools:
            if turn.get("name") != assertion.tool:
                continue
            evidence = turn.get("payload", {}).get("argument_evidence") or {}
            arguments = evidence.get("parsed_arguments" if assertion.stage == "raw" else "prepared_arguments")
            if isinstance(arguments, dict) and all(arguments.get(key) == value for key, value in assertion.contains.items()):
                found = True
                break
        if not found:
            failures.append("tool_argument_constraint_missing")
    route = response.get("route") or artifacts.get("route")
    if oracle.forbid_route and route:
        failures.append("forbidden_route")
    if oracle.route_mode:
        if not route:
            failures.append("route_missing")
        else:
            if route.get("degraded") or route.get("route_kind") != "provider" or route.get("provider") != "amap":
                failures.append("route_not_online")
            if "route_plan_tool" not in successful:
                failures.append("route_evidence_missing")
            if memory.get("artifact_meta", {}).get("route", {}).get("turn_index") != state["turn_index"]:
                failures.append("route_stale")
            if route.get("mode") != oracle.route_mode or len(route.get("polyline", [])) < 2:
                failures.append("route_mode_or_geometry_invalid")
            end = route.get("destination") or {}
            if end.get("coord_system") != "gcj02" or distance(end, oracle.route_destination) > 100:
                failures.append("route_destination_mismatch")
            start = route.get("origin") or {}
            if start.get("coord_system") != "gcj02" or distance(start, oracle.route_origin) > 100:
                failures.append("route_origin_mismatch")
            if not all(valid(route.get(k)) and route[k] > 0 for k in ["distance_m", "duration_s"]):
                failures.append("route_metrics_invalid")
            path = route.get("polyline") or []
            if path and (any(p.get("coord_system") != "gcj02" for p in path)
                         or distance(path[0], oracle.route_origin) > 100
                         or distance(path[-1], oracle.route_destination) > 100):
                failures.append("route_geometry_endpoints_mismatch")
            tool_routes = [t["payload"].get("result", {}).get("route") for t in tools
                           if t.get("name") == "route_plan_tool" and t["payload"].get("status") == "completed"]
            if route not in tool_routes:
                failures.append("route_not_from_tool_result")
    for phrase in oracle.answer_contains:
        if phrase.casefold() not in response.get("reply", "").casefold():
            failures.append("answer_constraint_missing")
    for phrase in oracle.answer_not_contains:
        if phrase.casefold() in response.get("reply", "").casefold():
            failures.append("answer_forbidden_claim")
    return {"hard_pass": not failures, "failures": failures}


def distance(point, expected):
    try:
        lng, lat = float(point["lng"]), float(point["lat"])
        if not all(math.isfinite(v) for v in [lng, lat, *expected]):
            return math.inf
        a, b = math.radians(lat), math.radians(expected[1])
        h = math.sin((a-b)/2)**2 + math.cos(a)*math.cos(b)*math.sin(math.radians(lng-expected[0])/2)**2
        return 6371000 * 2 * math.asin(min(1, math.sqrt(h)))
    except (ValueError, TypeError, KeyError):
        return math.inf


def usage(records):
    usages = [(r.get("response") or {}).get("usage") or {} for r in records]
    result = {"requests": len(records)}
    for key in ["input_tokens", "output_tokens", "total_tokens", "cached_input_tokens", "reasoning_tokens"]:
        values = [u.get(key) for u in usages]
        if key == "total_tokens":
            values = [u.get(key) if u.get(key) is not None else
                      u["input_tokens"] + u["output_tokens"] if valid(u.get("input_tokens")) and valid(u.get("output_tokens")) else None for u in usages]
        known = [v for v in values if valid(v)]
        result[key] = sum(known) if len(known) == len(values) and values else None
        result["known_" + key] = sum(known) if known else None
    result["complete_requests"] = sum(valid(u.get("input_tokens")) and valid(u.get("output_tokens")) for u in usages)
    return result


def valid(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def cost(records, values, prefix):
    total = 0.0
    for record in records:
        u = (record.get("response") or {}).get("usage") or {}
        try:
            ip = float(values[prefix + "INPUT_PRICE"])
            op = float(values[prefix + "OUTPUT_PRICE"])
            cp_raw = values.get(prefix + "CACHED_INPUT_PRICE", "")
            cp = float(cp_raw) if cp_raw else ip
            i, o = u["input_tokens"], u["output_tokens"]
            if not all(valid(v) for v in [i, o, ip, op, cp]):
                return None
            cached = u.get("cached_input_tokens")
            if cp != ip and not valid(cached):
                return None
            cached = cached if valid(cached) else 0
            if cached > i:
                return None
            total += ((i-cached)*ip + cached*cp + o*op) / 1_000_000
        except (KeyError, TypeError, ValueError):
            return None
    return total if records else None


def aggregate(attempts):
    groups = {}
    for model in sorted({a["model_profile"] for a in attempts}):
        rows = [a for a in attempts if a["model_profile"] == model]
        ran = [a for a in rows if a["status"] != "not_run"]
        groups[model] = {"planned": len(rows), "started": len(ran),
            "hard_passed": sum(a.get("hard_pass") is True for a in ran),
            "quality_passed": sum(a.get("quality_pass") is True for a in ran),
            "quality_scored": sum(a.get("quality_pass") is not None for a in ran),
            "fully_passed": sum(a.get("hard_pass") is True and a.get("quality_pass") is True for a in ran),
            "duration_ms_p50": median([a["duration_ms"] for a in ran]) if ran else None}
        groups[model]["by_group"] = {
            group: {"started": sum(a["group"] == group for a in ran),
                    "hard_passed": sum(a["group"] == group and a.get("hard_pass") is True for a in ran)}
            for group in ["retrieval", "navigation", "robustness"]}
        by_group = groups[model]["by_group"]
        groups[model]["weighted_hard_success"] = sum(
            weight * by_group[group]["hard_passed"] / by_group[group]["started"]
            for group, weight in [("retrieval", .7), ("navigation", .2), ("robustness", .1)]
        ) if all(g["started"] for g in by_group.values()) else None
    return groups
