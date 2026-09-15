"""Failure and missing-coverage accounting in saved regression evidence."""

from evaluate.__main__ import summarize_junit


def test_report_keeps_failures_collection_errors_and_skips(tmp_path):
    path = tmp_path / "junit.xml"
    path.write_text('''<testsuites><testsuite>
      <testcase classname="a" name="ok" time="0.5"/>
      <testcase classname="a" name="bad"><failure message="wrong"/></testcase>
      <testcase classname="a" name="missing"><skipped/></testcase>
      <testcase classname="a" name="collection"><error/></testcase>
    </testsuite></testsuites>''')
    report = summarize_junit(path)
    assert {key: report[key] for key in ["total", "passed", "failed", "errors", "skipped"]} == {
        "total": 4, "passed": 1, "failed": 1, "errors": 1, "skipped": 1,
    }
    assert report["cases"][0]["duration_s"] == 0.5
