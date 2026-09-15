"""Append-only human answer-quality review and paired saved-run comparison."""
import json
from pathlib import Path


def export_review(directory):
    from evaluate.online import read_rows
    rows = read_rows(directory / "attempts.jsonl")
    target = directory / "review-template.jsonl"
    with target.open("x", encoding="utf-8") as handle:
        for row in rows:
            if not row.get("snapshots"):
                continue
            handle.write(json.dumps({"attempt_id": row["attempt_id"], "case_id": row["case_id"],
                "quality_pass": None, "reviewer": "", "reason": "", "evidence_refs": [s["evidence_id"] for s in row["snapshots"]],
                "snapshots": row["snapshots"]}, ensure_ascii=False) + "\n")
    return target


def import_review(directory, source):
    from evaluate.online import Evidence, read_rows
    attempts = {row["attempt_id"]: row for row in read_rows(directory / "attempts.jsonl")}
    reviews = read_rows(source)
    if not reviews:
        raise ValueError("empty reviews")
    # Validate the entire batch before writing any changes.
    for row in reviews:
        attempt = attempts.get(row.get("attempt_id"))
        if attempt is None or type(row.get("quality_pass")) is not bool:
            raise ValueError("unknown attempt or missing boolean quality_pass")
        if not isinstance(row.get("reviewer"), str) or not row["reviewer"].strip() or not isinstance(row.get("reason"), str) or not row["reason"].strip():
            raise ValueError("reviewer and reason required")
        allowed = {s["evidence_id"] for s in attempt.get("snapshots", [])}
        refs = row.get("evidence_refs")
        if not isinstance(refs, list) or not refs or not all(isinstance(r, str) and r in allowed for r in refs):
            raise ValueError("invalid evidence reference")
    evidence = Evidence(directory, {})
    for row in reviews:
        evidence.write("human_reviews", {key: row[key] for key in ["attempt_id", "quality_pass", "reviewer", "reason", "evidence_refs"]})
    effective = {}
    for review in read_rows(directory / "human_reviews.jsonl"):
        effective[review["attempt_id"]] = review
    evidence.json("human_summary.json", {"reviewed": len(effective),
        "quality_passed": sum(r["quality_pass"] for r in effective.values()),
        "fully_passed": sum(r["quality_pass"] and attempts[id].get("hard_pass") is True for id, r in effective.items()),
        "note": "人工复核只裁定质量，不覆盖硬约束；自动评分保留在 scores.jsonl。"})
    return len(reviews)


def compare(baseline, candidate):
    from evaluate.online import read_rows
    bm = json.loads((baseline / "manifest.json").read_text())
    cm = json.loads((candidate / "manifest.json").read_text())
    for field in ["data_sha256", "case_sha256", "map_mode"]:
        if bm.get(field) != cm.get(field):
            raise ValueError("comparison requires matching dataset, cases and map mode")
    def index(directory):
        rows = read_rows(directory / "attempts.jsonl")
        return {(a["model_profile"], a["case_id"], a["repeat_index"]): a for a in rows if a["status"] != "not_run"}
    before, after = index(baseline), index(candidate)
    pairs = sorted(before.keys() & after.keys())
    return {"paired_attempts": len(pairs), "unpaired_baseline": len(before)-len(pairs),
        "unpaired_candidate": len(after)-len(pairs),
        "regressions": [list(k) for k in pairs if before[k].get("hard_pass") is True and after[k].get("hard_pass") is not True],
        "improvements": [list(k) for k in pairs if before[k].get("hard_pass") is not True and after[k].get("hard_pass") is True],
        "note": "按同名 profile/case/repeat 配对，仅比较硬约束；不推断统计显著性，不覆盖人工裁定。"}
