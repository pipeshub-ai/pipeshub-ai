"""
HTML integration test report builder.

Parses failure output for a clear root-cause line, cascade hints, and
emits a single self-contained HTML file with full tracebacks.
"""

from __future__ import annotations

import html
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class TestReportEntry:
    nodeid: str
    outcome: str  # passed | failed | skipped
    duration: float
    err_full: Optional[str] = None  # full longrepr text for failures
    stdout_captured: Optional[str] = None
    stderr_captured: Optional[str] = None


def _parse_failure_summary(traceback_text: str) -> Tuple[str, str]:
    """
    Return (root_cause_line, where_line).
    root_cause: last E   line or AssertionError line.
    where: first file:line in traceback (often where test broke).
    """
    if not traceback_text or not traceback_text.strip():
        return ("(no failure output captured)", "")

    lines = traceback_text.strip().split("\n")
    root = ""
    # Prefer last E   line (pytest exception summary)
    for line in reversed(lines):
        stripped = line.strip()
        if stripped.startswith("E   "):
            root = stripped[4:].strip()
            break
        if stripped.startswith("E       "):
            root = stripped[8:].strip()
            if root:
                break
    if not root:
        # AssertionError: message
        for line in reversed(lines):
            if "Error" in line or "AssertionError" in line:
                root = line.strip()
                break
    if not root:
        root = lines[-1].strip()[:500]

    where = ""
    for line in lines:
        # connectors/s3/s3_integration_test.py:129: in test_03
        m = re.match(r"^([^:]+\.py):\d+:\s+in\s+", line.strip())
        if m:
            where = line.strip()
            break
    return (root, where)


def _cascade_hint(root_cause: str, traceback_text: str) -> Optional[str]:
    """If this looks like a downstream failure, return a short hint."""
    text = (root_cause + "\n" + traceback_text).lower()
    if "keyerror" in text and "_state" in text:
        return (
            "Cascade failure: shared session state (_state) is missing a key because "
            "an earlier ordered test in this class did not run successfully. "
            "Fix the first failure in this class first; the rest often pass once state is set."
        )
    if "keyerror" in text and "connector_id" in text:
        return (
            "connector_id was never set—typically test_03 (init connector) failed first. "
            "Resolve the HTTP/API error in that step."
        )
    return None


def _suite_from_nodeid(nodeid: str) -> Tuple[str, str]:
    """Return (suite_key, class_or_file) for grouping."""
    # connectors/s3/s3_integration_test.py::TestS3FullLifecycle::test_03
    parts = nodeid.split("::")
    path_part = parts[0].replace("\\", "/")
    if "/" in path_part:
        # connectors/s3/... -> s3
        segs = path_part.split("/")
        if len(segs) >= 2 and segs[0] == "connectors":
            suite = segs[1]
        else:
            suite = Path(path_part).stem
    else:
        suite = Path(path_part).stem
    cls = parts[1] if len(parts) > 1 else ""
    return (suite, cls)


def _test_name_from_nodeid(nodeid: str) -> str:
    parts = nodeid.split("::")
    return parts[-1] if parts else nodeid


CSS = """    :root {
      --bg: #0f1419;
      --surface: #1a2332;
      --border: #2d3a4d;
      --text: #e6edf3;
      --muted: #8b9cb3;
      --pass: #3fb950;
      --fail: #f85149;
      --skip: #d29922;
      --accent: #58a6ff;
    }
    * { box-sizing: border-box; }
    body {
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      margin: 0;
      padding: 1.5rem;
      max-width: 1100px;
      margin-left: auto;
      margin-right: auto;
    }
    h1 { font-size: 1.5rem; margin-top: 0; }
    h2 { font-size: 1.15rem; margin-top: 2rem; border-bottom: 1px solid var(--border); padding-bottom: 0.35rem; }
    h3.suite-title { font-size: 1rem; margin-top: 1.5rem; color: var(--muted); }
    .verdict {
      border-radius: 10px;
      padding: 1.25rem 1.5rem;
      margin-bottom: 1.5rem;
    }
    .verdict-pass { background: rgba(63, 185, 80, 0.12); border: 1px solid var(--pass); }
    .verdict-fail { background: rgba(248, 81, 73, 0.12); border: 1px solid var(--fail); }
    .verdict-warn { background: rgba(210, 153, 34, 0.12); border: 1px solid var(--skip); }
    .verdict h2 { margin: 0 0 0.5rem 0; border: none; padding: 0; font-size: 1.35rem; }
    .verdict-pass h2 { color: var(--pass); }
    .verdict-fail h2 { color: var(--fail); }
    .verdict-warn h2 { color: var(--skip); }
    .verdict p { margin: 0; color: var(--muted); }
    .cards {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
      gap: 0.75rem;
      margin: 1rem 0 2rem 0;
    }
    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0.75rem;
      text-align: center;
    }
    .card-value { font-size: 1.5rem; font-weight: 700; }
    .card-pass .card-value { color: var(--pass); }
    .card-fail .card-value { color: var(--fail); }
    .card-skip .card-value { color: var(--skip); }
    .card-label { font-size: 0.75rem; color: var(--muted); text-transform: uppercase; }
    table.meta { width: 100%; border-collapse: collapse; margin: 1rem 0; font-size: 0.9rem; }
    table.meta th { text-align: left; padding: 0.4rem 0.75rem 0.4rem 0; color: var(--muted); width: 180px; vertical-align: top; }
    table.meta td { padding: 0.4rem 0; }
    table.meta code { background: var(--surface); padding: 0.15rem 0.4rem; border-radius: 4px; }
    .section-intro { color: var(--muted); margin-bottom: 1rem; }
    .failure-card {
      background: var(--surface);
      border: 1px solid var(--fail);
      border-radius: 8px;
      padding: 1rem 1.25rem;
      margin-bottom: 1.25rem;
    }
    .failure-title { margin: 0 0 0.5rem 0; color: var(--fail); }
    .failure-meta { margin: 0.25rem 0; font-size: 0.9rem; color: var(--muted); }
    .root-cause {
      background: rgba(248, 81, 73, 0.08);
      border-left: 4px solid var(--fail);
      padding: 0.75rem 1rem;
      margin: 0.75rem 0;
      font-family: ui-monospace, monospace;
      font-size: 0.9rem;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .cascade-hint {
      background: rgba(210, 153, 34, 0.1);
      border: 1px solid var(--skip);
      border-radius: 6px;
      padding: 0.75rem 1rem;
      margin: 0.75rem 0;
      font-size: 0.9rem;
      color: var(--text);
    }
    .what-happened { font-size: 0.9rem; margin: 0.75rem 0; }
    pre.traceback {
      background: #0d1117;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 1rem;
      overflow-x: auto;
      font-size: 0.8rem;
      line-height: 1.4;
      white-space: pre-wrap;
      word-break: break-word;
      max-height: 560px;
      overflow-y: auto;
    }
    pre.captured { background: #0d1117; border: 1px solid var(--border); border-radius: 6px; padding: 0.75rem; font-size: 0.8rem; max-height: 240px; overflow: auto; }
    .suite-table { width: 100%; border-collapse: collapse; margin-bottom: 1.5rem; font-size: 0.9rem; }
    .suite-table th, .suite-table td { padding: 0.5rem 0.6rem; text-align: left; border-bottom: 1px solid var(--border); }
    .suite-table th { color: var(--muted); font-weight: 600; }
    .row-pass { }
    .row-fail { background: rgba(248, 81, 73, 0.06); }
    .row-skip { }
    .status { font-weight: 600; }
    .status.pass { color: var(--pass); }
    .status.fail { color: var(--fail); }
    .status.skip { color: var(--skip); }
    .dur { color: var(--muted); white-space: nowrap; }
    pre.rerun {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 1rem;
      font-size: 0.85rem;
      overflow-x: auto;
    }
    nav.toc { margin: 1rem 0 2rem 0; font-size: 0.9rem; }
    nav.toc a { color: var(--accent); margin-right: 1rem; }
"""


def write_html_report(
    entries: List[TestReportEntry],
    report_path: Path,
    *,
    timestamp_title: str,
    timestamp_file: str,
    env_label: str,
    base_url: str,
    exitstatus: int,
    session_wall_s: Optional[float] = None,
) -> None:
    """Write a single self-contained HTML report."""
    passed = sum(1 for e in entries if e.outcome == "passed")
    failed = sum(1 for e in entries if e.outcome == "failed")
    skipped = sum(1 for e in entries if e.outcome == "skipped")
    total = len(entries)
    executed = passed + failed
    pass_rate = (100.0 * passed / executed) if executed else 0.0

    verdict_class = "verdict-pass" if failed == 0 and skipped == 0 else ("verdict-fail" if failed else "verdict-warn")
    verdict_title = "PASSED" if failed == 0 else "FAILED"
    if total == 0:
        verdict_class = "verdict-warn"
        verdict_title = "NO RESULTS"
        verdict_p = (
            "No test results were collected. Run pytest from the <code>integration-tests</code> directory "
            "(e.g. <code>cd integration-tests &amp;&amp; pytest -m integration -v</code>) so conftest hooks run and populate the report."
        )
    elif failed:
        verdict_p = (
            f"{failed} test(s) failed — {passed} passed, {skipped} skipped. "
            "See <strong>What failed</strong> below for root cause, cascade hints, and full tracebacks."
        )
    else:
        verdict_p = f"All {passed} executed test(s) passed. {skipped} skipped."

    failed_entries = [e for e in entries if e.outcome == "failed"]
    sum_dur = sum(e.duration for e in entries)

    # Group by suite
    by_suite: Dict[str, List[TestReportEntry]] = defaultdict(list)
    for e in entries:
        suite, _ = _suite_from_nodeid(e.nodeid)
        by_suite[suite].append(e)

    lines: List[str] = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '  <meta charset="utf-8"/>',
        '  <meta name="viewport" content="width=device-width, initial-scale=1"/>',
        f"  <title>Pipeshub integration test report — {html.escape(timestamp_title)}</title>",
        "  <style>",
        CSS,
        "  </style>",
        "</head>",
        "<body>",
        "  <h1>Pipeshub integration test report</h1>",
        f'  <div class="verdict {verdict_class}">',
        f"    <h2>Verdict: {verdict_title}</h2>",
        f"    <p>{verdict_p}</p>",
        "  </div>",
        '  <div class="cards">',
        f'    <div class="card card-pass"><div class="card-value">{passed}</div><div class="card-label">Passed</div></div>',
        f'    <div class="card card-fail"><div class="card-value">{failed}</div><div class="card-label">Failed</div></div>',
        f'    <div class="card card-skip"><div class="card-value">{skipped}</div><div class="card-label">Skipped</div></div>',
        f'    <div class="card"><div class="card-value">{total}</div><div class="card-label">Total (call phase)</div></div>',
        f'    <div class="card"><div class="card-value">{pass_rate:.1f}%</div><div class="card-label">Pass rate (executed)</div></div>',
        "  </div>",
        '  <section class="section">',
        "    <h2>Run metadata</h2>",
        '    <table class="meta">',
    ]

    def meta_row(th: str, td: str) -> None:
        lines.append(f"      <tr><th>{html.escape(th)}</th><td>{td}</td></tr>")

    meta_row("Report file", f"<code>{html.escape(report_path.name)}</code>")
    meta_row("Generated (UTC)", f"<code>{html.escape(timestamp_title)}</code>")
    meta_row("Environment", f"<code>{html.escape(env_label)}</code>")
    meta_row("Base URL", f"<code>{html.escape(base_url or '(not set)')}</code>")
    meta_row("Python", f"<code>{html.escape(sys.version.split()[0])}</code>")
    try:
        import pytest as _pytest

        meta_row("pytest", f"<code>{getattr(_pytest, '__version__', '?')}</code>")
    except Exception:
        pass
    if session_wall_s is not None:
        meta_row("Session wall time", f"<code>{session_wall_s:.1f}s</code>")
    meta_row("Sum of test durations", f"<code>{sum_dur:.1f}s</code>")
    meta_row("Exit code", f"<code>{exitstatus} (0 = success)</code>")
    lines.append("    </table>")
    lines.append("  </section>")

    # TOC
    lines.append('  <nav class="toc">')
    lines.append("    <strong>Jump to:</strong>")
    if failed_entries:
        lines.append(f'    <a href="#failures">Failures ({len(failed_entries)})</a>')
    lines.append('    <a href="#all-results">All results by suite</a>')
    lines.append('    <a href="#passed">Passed only</a>')
    lines.append('    <a href="#slowest">Slowest</a>')
    lines.append('    <a href="#rerun">Re-run</a>')
    lines.append("  </nav>")

    # Failures
    if failed_entries:
        lines.append(
            '  <section class="section section-failures" id="failures">'
            "<h2>What failed — root cause and full output</h2>"
            '<p class="section-intro">Each failure shows a <strong>parsed root cause</strong> (exception/assertion line), '
            "optional <strong>cascade hint</strong> when a later test failed because an earlier step did not set shared state, "
            "then the <strong>full traceback</strong> (what was asserted and where) and any captured stdout/stderr.</p>"
        )
        for i, e in enumerate(failed_entries, 1):
            tb = (e.err_full or "").strip()
            root, where = _parse_failure_summary(tb)
            hint = _cascade_hint(root, tb)
            suite, cls = _suite_from_nodeid(e.nodeid)
            test_name = _test_name_from_nodeid(e.nodeid)
            lines.append(f'            <article class="failure-card" id="failure-{i}">')
            lines.append(f'              <h4 class="failure-title">{i}. <code>{html.escape(test_name)}</code></h4>')
            lines.append(
                f'              <p class="failure-meta"><strong>Full node ID:</strong> <code>{html.escape(e.nodeid)}</code></p>'
            )
            lines.append(
                f'              <p class="failure-meta"><strong>Suite:</strong> {html.escape(suite)}'
                + (f" / {html.escape(cls)}" if cls else "")
                + f" &middot; <strong>Duration:</strong> {e.duration:.2f}s</p>"
            )
            if where:
                lines.append(
                    f'              <p class="failure-meta"><strong>Where it broke (first frame):</strong> <code>{html.escape(where)}</code></p>'
                )
            lines.append('              <p class="what-happened"><strong>Root cause (parsed):</strong></p>')
            lines.append(f'              <div class="root-cause">{html.escape(root)}</div>')
            if hint:
                lines.append(f'              <div class="cascade-hint"><strong>Cascade / context:</strong> {html.escape(hint)}</div>')
            lines.append(
                '              <p class="what-happened"><strong>Full traceback / assertion output:</strong> '
                "(read from the bottom up for the exception message; assertion failures show expected vs actual above).</p>"
            )
            lines.append(f'              <pre class="traceback" tabindex="0">{html.escape(tb)}</pre>')
            if e.stdout_captured and e.stdout_captured.strip():
                lines.append('              <p class="what-happened"><strong>Captured stdout:</strong></p>')
                lines.append(f'              <pre class="captured">{html.escape(e.stdout_captured.strip())}</pre>')
            if e.stderr_captured and e.stderr_captured.strip():
                lines.append('              <p class="what-happened"><strong>Captured stderr:</strong></p>')
                lines.append(f'              <pre class="captured">{html.escape(e.stderr_captured.strip())}</pre>')
            lines.append("            </article>")
        lines.append("            </section>")

    # All results by suite
    lines.append('  <section class="section" id="all-results">')
    lines.append("    <h2>All results by suite</h2>")
    lines.append(
        '<p class="section-intro">Every test with status and duration. Failed tests are expanded above with full detail.</p>'
    )
    if total == 0:
        lines.append(
            '<p class="section-intro" style="color:var(--skip);">No tests were collected for this run. Run from <code>integration-tests</code> with <code>pytest -m integration -v</code>.</p>'
        )
    for suite in sorted(by_suite.keys()):
        suite_entries = by_suite[suite]
        lines.append(f'    <h3 class="suite-title">Suite: <code>{html.escape(suite)}</code> — all results</h3>')
        lines.append(
            '<table class="suite-table"><thead><tr><th>Status</th><th>Time</th><th>Test</th></tr></thead><tbody>'
        )
        for e in suite_entries:
            status = e.outcome.upper()
            row_class = f"row-{e.outcome}"
            name = _test_name_from_nodeid(e.nodeid)
            lines.append(
                f'<tr class="{row_class}"><td class="status {e.outcome}">{status}</td>'
                f'<td class="dur">{e.duration:.2f}s</td><td><code>{html.escape(name)}</code></td></tr>'
            )
        lines.append("</tbody></table>")
    lines.append("  </section>")

    # Passed only
    passed_entries = [e for e in entries if e.outcome == "passed"]
    if passed_entries:
        lines.append('  <section class="section section-passed" id="passed">')
        lines.append("<h2>What passed</h2>")
        lines.append(
            '<p class="section-intro">These tests completed without uncaught exceptions; assertions passed.</p>'
        )
        for suite in sorted({ _suite_from_nodeid(e.nodeid)[0] for e in passed_entries }):
            pe = [e for e in passed_entries if _suite_from_nodeid(e.nodeid)[0] == suite]
            lines.append(f'    <h3 class="suite-title">Suite: <code>{html.escape(suite)}</code> — passed</h3>')
            lines.append(
                '<table class="suite-table"><thead><tr><th>Status</th><th>Time</th><th>Test</th></tr></thead><tbody>'
            )
            for e in pe:
                name = _test_name_from_nodeid(e.nodeid)
                lines.append(
                    f'<tr class="row-pass"><td class="status pass">PASS</td>'
                    f'<td class="dur">{e.duration:.2f}s</td><td><code>{html.escape(name)}</code></td></tr>'
                )
            lines.append("</tbody></table>")
        lines.append("</section>")

    # Slowest
    sorted_by_dur = sorted(entries, key=lambda e: e.duration, reverse=True)[:15]
    lines.append('  <section class="section" id="slowest"><h2>Slowest tests</h2>')
    lines.append('<table class="suite-table"><thead><tr><th>Rank</th><th>Duration</th><th>Test</th></tr></thead><tbody>')
    for rank, e in enumerate(sorted_by_dur, 1):
        name = _test_name_from_nodeid(e.nodeid)
        lines.append(
            f"<tr><td>{rank}</td><td>{e.duration:.2f}s</td><td><code>{html.escape(name)}</code></td></tr>"
        )
    lines.append("</tbody></table></section>")

    # Rerun
    lines.append('  <section class="section" id="rerun">')
    lines.append("    <h2>Re-run</h2>")
    lines.append("    <p>From repo root:</p>")
    lines.append(
        '    <pre class="rerun">cd integration-tests &amp;&amp; source .venv/bin/activate\n'
        "# All integration tests:\npytest -m integration -v\n"
        "# By connector marker:\npytest -m s3 -v\npytest -m gcs -v\n"
        "pytest -m azure_blob -v\npytest -m azure_files -v\n"
        "# Longer tracebacks in terminal:\npytest -m integration -v --tb=long</pre>"
    )
    lines.append("  </section>")

    lines.append(
        f'  <p style="margin-top:2rem;color:var(--muted);font-size:0.85rem;">Report file: <code>{html.escape(report_path.name)}</code></p>'
    )
    lines.append("</body>")
    lines.append("</html>")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
