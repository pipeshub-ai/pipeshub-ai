"""Self-contained HTML reports.

No CDN, no build step, inline SVG charts: a report has to survive being scp'd
off the test box and opened from a file:// URL.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

CSS = """
:root { color-scheme: light dark; --fg:#1a1a1a; --bg:#fff; --muted:#666; --line:#e3e3e3;
        --accent:#2f6feb; --good:#1a7f37; --bad:#cf222e; --warn:#9a6700; --card:#fafafa; }
@media (prefers-color-scheme: dark) {
  :root { --fg:#e6e6e6; --bg:#151515; --muted:#9b9b9b; --line:#333; --card:#1e1e1e; }
}
* { box-sizing: border-box; }
body { margin:0; padding:32px; font:14px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;
       color:var(--fg); background:var(--bg); }
h1 { font-size:22px; margin:0 0 4px; } h2 { font-size:16px; margin:32px 0 10px; }
.sub { color:var(--muted); margin-bottom:24px; font-size:13px; }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; }
.card { border:1px solid var(--line); border-radius:8px; padding:14px; background:var(--card); }
.card .k { color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.05em; }
.card .v { font-size:24px; font-weight:600; margin-top:4px; font-variant-numeric:tabular-nums; }
.card .n { color:var(--muted); font-size:12px; margin-top:2px; }
table { border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }
th,td { text-align:right; padding:6px 10px; border-bottom:1px solid var(--line); }
th:first-child,td:first-child { text-align:left; }
th { color:var(--muted); font-weight:500; font-size:12px; }
.scroll { overflow-x:auto; }
.banner { padding:12px 14px; border-radius:8px; margin:16px 0; border:1px solid; }
.banner.ok { border-color:var(--good); color:var(--good); }
.banner.bad { border-color:var(--bad); color:var(--bad); }
.banner.warn { border-color:var(--warn); color:var(--warn); }
.good { color:var(--good); } .bad { color:var(--bad); } .muted { color:var(--muted); }
code { font-family:ui-monospace,Menlo,Consolas,monospace; font-size:12px; }
svg { max-width:100%; height:auto; display:block; }
details { margin:10px 0; } summary { cursor:pointer; color:var(--accent); }
"""

PALETTE = ["#2f6feb", "#cf222e", "#1a7f37", "#9a6700", "#8250df", "#0f8f8f", "#bf5b17"]


def _esc(value: Any) -> str:
    return html.escape(str(value))


def line_chart(
    series: dict[str, list[tuple[float, float]]],
    *,
    width: int = 900,
    height: int = 240,
    x_label: str = "seconds",
    y_label: str = "",
) -> str:
    """Minimal multi-series SVG line chart."""
    pad_l, pad_r, pad_t, pad_b = 56, 130, 12, 30
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b

    points = [p for pts in series.values() for p in pts]
    if not points:
        return '<p class="muted">no data</p>'
    x_max = max(p[0] for p in points) or 1.0
    y_max = max(p[1] for p in points) or 1.0

    def sx(x: float) -> float:
        return pad_l + (x / x_max) * plot_w

    def sy(y: float) -> float:
        return pad_t + plot_h - (y / y_max) * plot_h

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{_esc(y_label)} over {_esc(x_label)}">']
    parts.append(
        f'<line x1="{pad_l}" y1="{pad_t + plot_h}" x2="{pad_l + plot_w}" y2="{pad_t + plot_h}" '
        'stroke="currentColor" stroke-opacity=".3"/>'
        f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{pad_t + plot_h}" '
        'stroke="currentColor" stroke-opacity=".3"/>'
    )
    for frac in (0.0, 0.5, 1.0):
        y = pad_t + plot_h - frac * plot_h
        parts.append(
            f'<text x="{pad_l - 8}" y="{y + 4}" text-anchor="end" font-size="10" '
            f'fill="currentColor" fill-opacity=".6">{y_max * frac:.4g}</text>'
        )
    parts.append(
        f'<text x="{pad_l + plot_w / 2}" y="{height - 6}" text-anchor="middle" font-size="10" '
        f'fill="currentColor" fill-opacity=".6">{_esc(x_label)}</text>'
    )

    for i, (name, pts) in enumerate(sorted(series.items())):
        colour = PALETTE[i % len(PALETTE)]
        path = " ".join(
            f"{'M' if j == 0 else 'L'}{sx(x):.1f},{sy(y):.1f}" for j, (x, y) in enumerate(sorted(pts))
        )
        parts.append(f'<path d="{path}" fill="none" stroke="{colour}" stroke-width="1.6"/>')
        ly = pad_t + 14 * i + 10
        parts.append(
            f'<rect x="{pad_l + plot_w + 14}" y="{ly - 8}" width="9" height="9" fill="{colour}"/>'
            f'<text x="{pad_l + plot_w + 28}" y="{ly}" font-size="11" fill="currentColor">{_esc(name)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _card(key: str, value: str, note: str = "", cls: str = "") -> str:
    note_html = f'<div class="n">{_esc(note)}</div>' if note else ""
    return f'<div class="card"><div class="k">{_esc(key)}</div><div class="v {cls}">{_esc(value)}</div>{note_html}</div>'


def _validity_banners(doc: dict[str, Any]) -> str:
    summary = doc.get("summary") or {}
    out = []
    if summary.get("indexing_leaked"):
        out.append(
            '<div class="banner bad"><b>Run invalid.</b> Records were indexed despite '
            "<code>enable_manual_sync</code>. The indexing service competed for resources, so these "
            "numbers do not isolate the connector service. Check the filter actually applied.</div>"
        )
    if summary.get("records_diverged"):
        offenders = []
        for _iteration, diverging in (summary.get("divergence") or {}).items():
            for cid, count in sorted(diverging.items()):
                offenders.append(f"<code>{cid}</code>: {count}")
        listed = ", ".join(offenders[:20]) or "see result.json"
        more = "" if len(offenders) <= 20 else f" (+{len(offenders) - 20} more)"
        out.append(
            '<div class="banner bad"><b>Run invalid.</b> Connectors disagree on their final '
            "record count, so at least one synced twice and duplicated work. Throughput here is "
            "<i>inflated</i>, not improved: the headline rate is total records over elapsed time, "
            f"and duplicates raise the numerator. Offenders — {listed}{more}.</div>"
        )
    if not summary.get("all_complete", True):
        out.append(
            '<div class="banner warn"><b>Incomplete.</b> At least one iteration timed out before every '
            "unit reached its expected record count. Throughput is computed over what was observed and "
            "understates a healthy run.</div>"
        )
    if summary and not summary.get("cv_known", True):
        out.append(
            '<div class="banner warn"><b>Spread unknown.</b> Only one measured iteration, so '
            "there is no run-to-run variance to report and this run cannot qualify an A/B "
            "comparison. Set <code>repeat: 3</code> or higher before comparing.</div>"
        )
    elif summary:
        cvv = summary["records_per_s"]["cv"]
        if summary.get("reproducible"):
            out.append(
                f'<div class="banner ok"><b>Reproducible.</b> Throughput varied {cvv:.1%} across '
                f"{summary['iterations']} iterations (threshold {summary['cv_threshold']:.0%}). "
                "Differences larger than that against another run are real.</div>"
            )
        else:
            out.append(
                f'<div class="banner bad"><b>Not reproducible enough to A/B.</b> Throughput varied '
                f"{cvv:.1%} across {summary['iterations']} iterations, above the "
                f"{summary['cv_threshold']:.0%} threshold. Raise <code>repeat</code>, quiet the host, or "
                "increase <code>scale</code> before trusting a comparison.</div>"
            )
    if not doc.get("source", {}).get("deterministic", True):
        out.append(
            '<div class="banner warn"><b>Non-deterministic source.</b> This load source is backed by an '
            "external SaaS API. Useful as a realistic run; not comparable between runs.</div>"
        )
    return "".join(out)


def render(doc: dict[str, Any]) -> str:
    summary = doc.get("summary") or {}
    scenario = doc.get("scenario") or {}
    iterations = [it for it in doc.get("iterations", []) if it.get("kind") == "measured"]

    rate = summary.get("records_per_s", {})
    p99 = summary.get("api_p99_ms", {})
    gap = summary.get("api_max_gap_s", {})
    conf = summary.get("confluence_ms", {})
    neo = summary.get("neo4j_ms", {})

    cards = "".join(
        [
            _card("throughput", f"{rate.get('median', 0):.2f}", "records/s (median)"),
            _card("spread", f"{rate.get('cv', 0):.1%}", "coefficient of variation",
                  "good" if summary.get("reproducible") else "bad"),
            _card("duration", f"{summary.get('duration_s', {}).get('median', 0):.0f}s", "median sync"),
            _card("api p99", f"{p99.get('median', 0):.0f}ms", f"worst {p99.get('max', 0):.0f}ms"),
            _card("longest API stall", f"{gap.get('median', 0):.1f}s", f"worst {gap.get('max', 0):.1f}s"),
            _card("confluence avg", f"{conf.get('mean_median', 0):.1f}ms",
                  f"p50 {conf.get('p50_median', 0):.0f} / p99 {conf.get('p99_median', 0):.0f}"),
            _card("neo4j avg", f"{neo.get('mean_median', 0):.1f}ms",
                  f"p50 {neo.get('p50_median', 0):.0f} / p99 {neo.get('p99_median', 0):.0f}"),
            _card("records", f"{doc.get('expected_total', 0):,}",
                  f"{scenario.get('units', 0)} connector(s) x {scenario.get('scale', 0)}"),
        ]
    )

    # Charts come from the median iteration so the picture matches the headline.
    ranked = sorted(iterations, key=lambda it: it["throughput"]["records_per_s"])
    representative = ranked[len(ranked) // 2] if ranked else None

    body = [
        f"<h1>{_esc(doc.get('run_id', 'load test'))}</h1>",
        f'<div class="sub">{_esc(scenario.get("source", "?"))} &middot; '
        f'{_esc(doc.get("target", {}).get("base_url", ""))} &middot; '
        f'connector-only (indexing disabled via <code>enable_manual_sync</code>)</div>',
        _validity_banners(doc),
        f'<div class="grid">{cards}</div>',
    ]

    if representative:
        tp_series = {
            f"connector {i + 1}": [(p[0], p[1]) for p in pts]
            for i, (_, pts) in enumerate(sorted(representative["throughput"]["series"].items()))
        }
        body += [
            "<h2>Records discovered over time</h2>",
            line_chart(tp_series, y_label="records"),
        ]

        cpu = {
            svc: [(p[0], p[1]) for p in pts]
            for svc, pts in representative["resources"]["series"].items()
        }
        rss = {
            svc: [(p[0], p[2]) for p in pts]
            for svc, pts in representative["resources"]["series"].items()
        }
        body += [
            "<h2>CPU per service (% of one core)</h2>",
            line_chart(cpu, y_label="cpu %"),
            "<h2>Resident memory per service (MB)</h2>",
            line_chart(rss, y_label="MB"),
        ]

        body += _resource_focus_charts(
            representative, title_prefix="Measured sync — "
        )

    profiled = doc.get("profiled_iteration")
    if isinstance(profiled, dict) and profiled.get("resources"):
        body += _resource_focus_charts(
            profiled, title_prefix="Profiled sync — "
        )

    body += ["<h2>Per-iteration</h2>", _iterations_table(iterations)]
    body += ["<h2>Resource summary</h2>", _resource_table(representative)]
    body += ["<h2>Stateful containers</h2>", _container_table(representative)]
    if isinstance(profiled, dict):
        body += [
            "<h2>Stateful containers (profiled sync)</h2>",
            _container_table(profiled),
        ]

    profiles = doc.get("profiles") or []
    if profiles:
        body += ["<h2>Profiles</h2>", _profiles_section(profiles, doc)]

    body += [
        "<h2>Scenario</h2>",
        f'<div class="scroll"><pre><code>{_esc(json.dumps(scenario, indent=2))}</code></pre></div>',
    ]

    return (
        f"<title>{_esc(doc.get('run_id', 'load test'))}</title>"
        f"<style>{CSS}</style>" + "".join(body)
    )


def _iterations_table(iterations: list[dict[str, Any]]) -> str:
    if not iterations:
        return '<p class="muted">no measured iterations</p>'
    rows = []
    for it in iterations:
        tp = it["throughput"]
        lat = it.get("latency") or {}
        through = lat.get("through") or {}
        control = lat.get("control") or {}
        conf = lat.get("confluence") or {}
        neo = lat.get("neo4j") or {}
        rows.append(
            f"<tr><td>#{it['index'] + 1}</td>"
            f"<td>{tp['records_per_s']:.2f}</td><td>{tp['duration_s']:.1f}</td>"
            f"<td>{tp['final_records']:,}</td>"
            f"<td>{through.get('p50', 0):.0f}</td><td>{through.get('p99', 0):.0f}</td>"
            f"<td>{through.get('max', 0):.0f}</td>"
            f"<td>{through.get('max_gap_s', 0):.1f}</td><td>{control.get('p99', 0):.0f}</td>"
            f"<td>{conf.get('mean', 0):.1f}</td><td>{conf.get('p99', 0):.0f}</td>"
            f"<td>{conf.get('count', 0):,}</td>"
            f"<td>{neo.get('mean', 0):.1f}</td><td>{neo.get('p99', 0):.0f}</td>"
            f"<td>{neo.get('count', 0):,}</td>"
            f"<td>{through.get('errors', 0)}</td></tr>"
        )
    return (
        '<div class="scroll"><table><thead><tr>'
        "<th>iter</th><th>rec/s</th><th>sync s</th><th>records</th>"
        "<th>api p50 ms</th><th>api p99 ms</th><th>api max ms</th><th>max stall s</th>"
        "<th>control p99 ms</th>"
        "<th>conf avg ms</th><th>conf p99 ms</th><th>conf n</th>"
        "<th>neo4j avg ms</th><th>neo4j p99 ms</th><th>neo4j n</th>"
        "<th>errors</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
        '<p class="muted">“api” is GET /connectors (Node → connector). “conf” / “neo4j” are '
        "outbound call times recorded on the connector workers. “control” is Node /org/health.</p>"
    )


def _neo4j_series(iteration: dict[str, Any]) -> dict[str, list[tuple[float, float]]] | None:
    containers = (iteration.get("containers") or {}).get("series") or {}
    pts = containers.get("neo4j")
    if not pts:
        return None
    return {
        "cpu": [(p[0], p[1]) for p in pts],
        "mem": [(p[0], p[2]) for p in pts],
    }


def _resource_focus_charts(iteration: dict[str, Any], *, title_prefix: str = "") -> list[str]:
    """Connector process + Neo4j container CPU/memory over the full iteration."""
    out: list[str] = []
    series = (iteration.get("resources") or {}).get("series") or {}
    conn = series.get("connector")
    if conn:
        out += [
            f"<h2>{_esc(title_prefix)}Connector CPU (% of one core, all workers summed)</h2>",
            line_chart({"connector": [(p[0], p[1]) for p in conn]}, y_label="cpu %"),
            f"<h2>{_esc(title_prefix)}Connector RSS (MB, all workers summed)</h2>",
            line_chart({"connector": [(p[0], p[2]) for p in conn]}, y_label="MB"),
        ]
    neo = _neo4j_series(iteration)
    if neo:
        out += [
            f"<h2>{_esc(title_prefix)}Neo4j container CPU (%)</h2>",
            line_chart({"neo4j": neo["cpu"]}, y_label="cpu %"),
            f"<h2>{_esc(title_prefix)}Neo4j container memory (MB)</h2>",
            line_chart({"neo4j": neo["mem"]}, y_label="MB"),
        ]
    return out


def _resource_table(iteration: dict[str, Any] | None) -> str:
    if not iteration:
        return '<p class="muted">no data</p>'
    services = iteration["resources"]["services"]
    if not services:
        return '<p class="muted">no per-process samples collected</p>'
    rows = []
    for service, s in sorted(services.items()):
        rows.append(
            f"<tr><td>{_esc(service)}</td><td>{s['cpu_mean']:.0f}</td><td>{s['cpu_p95']:.0f}</td>"
            f"<td>{s['cpu_max']:.0f}</td><td>{s['rss_mean_mb']:.0f}</td><td>{s['rss_max_mb']:.0f}</td>"
            f"<td>{s['threads_max']:.0f}</td><td>{s['fds_max']:.0f}</td></tr>"
        )
    return (
        '<div class="scroll"><table><thead><tr><th>service</th><th>cpu mean %</th><th>cpu p95 %</th>'
        "<th>cpu max %</th><th>rss mean MB</th><th>rss max MB</th><th>threads</th><th>fds</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
        '<p class="muted">Every service runs as a sibling process in one container, so these are '
        "per-process figures; CPU is percent of a single core and can exceed 100 for multi-threaded work.</p>"
    )


def _container_table(iteration: dict[str, Any] | None) -> str:
    if not iteration:
        return '<p class="muted">no data</p>'
    containers = (iteration.get("containers") or {}).get("containers") or {}
    if not containers:
        return '<p class="muted">no docker stats collected</p>'
    rows = []
    for name, s in sorted(containers.items()):
        rows.append(
            f"<tr><td>{_esc(name)}</td><td>{s['cpu_mean']:.0f}</td><td>{s['cpu_p95']:.0f}</td>"
            f"<td>{s['cpu_max']:.0f}</td><td>{s['mem_mean_mb']:.0f}</td><td>{s['mem_max_mb']:.0f}</td>"
            f"<td>{s.get('mem_limit_mb', 0):.0f}</td></tr>"
        )
    return (
        '<div class="scroll"><table><thead><tr><th>container</th><th>cpu mean %</th>'
        "<th>cpu p95 %</th><th>cpu max %</th><th>mem mean MB</th><th>mem max MB</th>"
        "<th>limit MB</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
        '<p class="muted">From <code>docker stats</code> over the iteration. CPU can exceed 100% '
        "when the container uses multiple cores.</p>"
    )


def enrich_containers(doc: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    """Fill `containers` on each iteration from on-disk JSONL (for older runs)."""
    from . import metrics

    for key in ("iterations",):
        for it in doc.get(key) or []:
            if (it.get("containers") or {}).get("series"):
                continue
            path = run_dir / it.get("directory", "") / "containers.jsonl"
            if not path.exists():
                continue
            started = float(it.get("started_at") or 0)
            it["containers"] = metrics.container_stats(
                metrics.read_jsonl(path), started
            ).as_dict()
    profiled = doc.get("profiled_iteration")
    if isinstance(profiled, dict) and not (profiled.get("containers") or {}).get("series"):
        path = run_dir / profiled.get("directory", "") / "containers.jsonl"
        if path.exists():
            started = float(profiled.get("started_at") or 0)
            profiled["containers"] = metrics.container_stats(
                metrics.read_jsonl(path), started
            ).as_dict()
    return doc


def _profiles_section(profiles: list[dict[str, Any]], doc: dict[str, Any]) -> str:
    out = [
        '<p class="muted">Captured in a separate iteration so profiling overhead never touches the '
        "measured numbers. “Longest uninterrupted frame” is the longest run of consecutive samples "
        "that never left a frame — on a single-loop service, that is the stall, and it names the "
        "function responsible.</p>"
    ]
    for profile in profiles:
        out.append(f"<h3>{_esc(profile['service'])} (pid {profile['pid']})</h3>")
        if profile.get("error"):
            out.append(f'<p class="bad">{_esc(profile["error"])}</p>')
        blocking = profile.get("blocking") or []
        if blocking:
            rows = "".join(
                f"<tr><td><code>{_esc(b['frame'])}</code></td><td>{_esc(b['thread'])}</td>"
                f"<td>{b['duration_ms']:.0f}</td><td>{b['samples']}</td>"
                f"<td class=\"muted\">{'waiting' if b.get('idle') else ''}</td></tr>"
                for b in blocking
            )
            out.append(
                '<div class="scroll"><table><thead><tr><th>frame</th><th>thread</th>'
                "<th>uninterrupted ms</th><th>samples</th><th></th></tr></thead><tbody>"
                + rows
                + "</tbody></table></div>"
                '<p class="muted">Rows marked <i>waiting</i> are threads parked for work '
                "(a pool worker on its queue, the loop in epoll) — long by nature and not a stall; "
                "they are ranked last.</p>"
            )
        pyspy_rel = profile.get("pyspy_flamegraph")
        if pyspy_rel:
            out.append(
                f'<p><a href="{_esc(pyspy_rel)}">py-spy&rsquo;s own flame graph</a> '
                "&mdash; a second capture taken right after this one, so it covers an "
                "adjacent window and will not match frame-for-frame.</p>"
            )
        svg_rel = profile.get("flamegraph")
        if svg_rel:
            svg_path = Path(doc["_dir"]) / svg_rel if "_dir" in doc else None
            if svg_path and svg_path.exists():
                out.append(
                    "<details><summary>flame graph</summary>"
                    + svg_path.read_text(encoding="utf-8")
                    + "</details>"
                )
            else:
                out.append(f'<p><a href="{_esc(svg_rel)}">flame graph</a></p>')
    return "".join(out)


def write(doc: dict[str, Any], directory: Path) -> Path:
    doc = {**doc, "_dir": str(directory)}
    path = directory / "report.html"
    path.write_text(render(doc), encoding="utf-8")
    return path
