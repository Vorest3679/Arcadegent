"""Run deterministic regression suites and save independent, machine-readable evidence."""

from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from uuid import uuid4
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SUITES = {
    "contracts": ["evaluate/checks"],
    "backend": ["evaluate/checks", "backend/app/tests"],
}


def summarize_junit(path: Path) -> dict:
    cases = list(ET.parse(path).iter("testcase"))
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    evidence = []
    for case in cases:
        status = next((name for tag, name in [("error", "errors"), ("failure", "failed"),
                      ("skipped", "skipped")] if case.find(tag) is not None), "passed")
        counts[status] += 1
        evidence.append({"case_id": f'{case.get("classname", "")}.{case.get("name", "")}',
                         "status": status, "duration_s": float(case.get("time", "0"))})
    return {"total": len(cases), **counts, "cases": evidence}


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] != "check":
        from evaluate.online import main as online_main
        return online_main(arguments)
    parser = argparse.ArgumentParser(description="Arcadegent 离线回归测试（不产生模型排名）")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="运行 pytest 并保存 JSON、JUnit 和 Markdown 报告")
    check.add_argument("--suite", choices=SUITES, default="backend")
    check.add_argument("--output", type=Path, help="新报告目录；相对路径以项目根目录为准")
    check.add_argument("-k", default="", help="pytest 用例名称筛选")
    args = parser.parse_args(argv)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    directory = args.output or Path("evaluate/reports") / run_id
    directory = (ROOT / directory).resolve()
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error(f"报告目录已存在，禁止覆盖：{directory}")
    command = [sys.executable, "-m", "pytest", "-c", str(ROOT / "pytest.ini"),
               *SUITES[args.suite], "-q", f"--junitxml={directory / 'junit.xml'}"]
    if args.k:
        command += ["-k", args.k]
    started = perf_counter()
    env = dict(os.environ, PYTHONPATH=str(ROOT / "backend") + os.pathsep + str(ROOT),
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True)
    log = result.stdout + result.stderr
    (directory / "pytest.log").write_text(log, encoding="utf-8")
    print(log, end="")
    summary = {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "cases": []}
    if (directory / "junit.xml").exists():
        summary = summarize_junit(directory / "junit.xml")
    summary.update(schema_version=1, run_id=run_id, mode="offline_contracts", suite=args.suite,
                   exit_code=result.returncode, duration_s=perf_counter() - started,
                   complete=result.returncode == 0 and summary["total"] > 0 and summary["skipped"] == 0,
                   model_ranking=None, browser_success_rate=None)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True)
    summary["git_sha"] = revision.stdout.strip() if revision.returncode == 0 else None
    diff = subprocess.run(["git", "diff", "HEAD", "--", "backend", "evaluate", "pytest.ini"],
                          cwd=ROOT, capture_output=True)
    summary["dirty_patch_sha256"] = hashlib.sha256(diff.stdout).hexdigest() if diff.returncode == 0 else None
    summary["input_sha256"] = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for folder in [ROOT / "evaluate/checks", ROOT / "evaluate/fixtures"]
        for path in sorted(folder.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }
    summary["python_version"] = sys.version
    (directory / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (directory / "summary.md").write_text(
        f"# Arcadegent 离线回归测试\n\n运行：`{run_id}`；测试集：`{args.suite}`\n\n"
        f"通过 {summary['passed']} / {summary['total']}；失败 {summary['failed']}；"
        f"错误 {summary['errors']}；跳过 {summary['skipped']}。\n\n"
        f"退出码：{result.returncode}；完整通过：{summary['complete']}。\n\n"
        "这些数据是确定性契约检查，不代表真实模型质量、实时地图或浏览器全链路成功率。\n\n"
        "详细证据：[JUnit](junit.xml)、[测试日志](pytest.log)、[JSON](summary.json)。\n",
        encoding="utf-8",
    )
    print(f"报告：{directory / 'summary.md'}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
