#!/usr/bin/env python3
"""Translation QA for the dashboard's locale files: the keys added or changed since a base revision.

    scripts/i18n_backtranslate_check.py                       # base: merge-base of HEAD and origin/main
    scripts/i18n_backtranslate_check.py --base <rev>          # another base
    scripts/i18n_backtranslate_check.py --sheet <path.md>     # also write the human review sheet
    scripts/i18n_backtranslate_check.py --sheet <p> --review-since <rev>   # sheet lists only strings changed since rev

Each changed string of a locale is compared with its en-US source:

- errors (exit 1): an interpolation variable (``{{name}}``) or a Trans tag (``<bold>``) the source has and the
  translation lacks, or one it adds; a plural group (i18next ``_one``/``_other`` suffixes) without every CLDR
  cardinal category the locale needs; a Hindi ``_one`` without the count when its group has one (CLDR's Hindi
  ``one`` is 0 and 1, so "one file" would also be shown for 0 files; the parity gate lets a plural form name any
  variable of its group);
- warnings: the string is the en-US text (untranslated), a hi/ko/zh string without its own script, or a length far
  from the source's.

The review sheet lists every changed non-English string beside its en-US source, grouped by locale, with the
warnings as flags: a back-translation review for a person, made without any model call.

en-IN is English and only its differences from en-US are listed. With ``--review-since`` the sheet lists only the
strings whose text changed since that revision, flagged ``assistant`` (translated by the coding assistant, not yet
read by a native speaker).

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCALES = "frontend/lib/i18n/locales"
SOURCE = "en-US"
ENGLISH = {"en-IN"}
# CLDR cardinal categories, as Node's Intl.PluralRules gives them (the parity check's source of truth).
PLURALS = {
    "en": ("one", "other"),
    "de": ("one", "other"),
    "hi": ("one", "other"),
    "es": ("one", "many", "other"),
    "cs": ("one", "few", "many", "other"),
    "ko": ("other",),
    "zh": ("other",),
}
# Languages whose `one` category is more than the number 1 (CLDR: hi is i = 0 or n = 1).
COUNTED_ONE = {("one", "hi")}
SCRIPTS = {"hi": r"[\u0900-\u097F]", "ko": r"[\uAC00-\uD7AF]", "zh": r"[\u4E00-\u9FFF]"}
_PLURAL = re.compile(r"^(.*?)(_ordinal)?_(zero|one|two|few|many|other)$")
_VAR = re.compile(r"{{-?\s*([^{}]+?)\s*}}")
_TAG = re.compile(r"</?([A-Za-z0-9]+)\s*/?>")
_WORD = re.compile(r"[A-Za-z]{2,}")
DEFAULT_BASE_OF = ("HEAD", "origin/main")
LONG = 2.5
SHORT = 0.35
# CJK and Hangul write the same message in far fewer characters.
SHORT_DENSE = 0.12
MIN_LEN = 20


def leaves(tree: object, prefix: str = "") -> dict[str, str]:
    if not isinstance(tree, dict):
        return {prefix: tree} if isinstance(tree, str) else {}
    out: dict[str, str] = {}
    for key, value in tree.items():
        out.update(leaves(value, f"{prefix}.{key}" if prefix else key))
    return out


def tokens(text: str) -> tuple[frozenset[str], frozenset[str]]:
    return (
        frozenset(m.group(1).split(",")[0].strip() for m in _VAR.finditer(text)),
        frozenset(m.group(1) for m in _TAG.finditer(text)),
    )


def plural(key: str) -> tuple[str, str] | None:
    match = _PLURAL.match(key)
    return (f"{match.group(1)}{match.group(2) or ''}", match.group(3)) if match else None


def _group_other(key: str) -> str | None:
    parts = plural(key)
    return f"{parts[0]}_other" if parts else None


def language(locale: str) -> str:
    return locale.split("-")[0]


def plain(text: str) -> str:
    return _TAG.sub("", _VAR.sub("", text))


@dataclass
class Finding:
    key: str
    source: str
    text: str
    flags: list[str] = field(default_factory=list)


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    rows: dict[str, list[Finding]] = field(default_factory=dict)


def source_of(key: str, source: dict[str, str]) -> str | None:
    if key in source:
        return source[key]
    parts = plural(key)
    return source.get(f"{parts[0]}_other") if parts else None


def check_locale(
    locale: str, source: dict[str, str], target: dict[str, str], changed: set[str], report: Report
) -> None:
    lang = language(locale)
    rows = report.rows.setdefault(locale, [])
    groups: dict[str, set[str]] = {}
    for key in source:
        parts = plural(key)
        if parts and not parts[0].endswith("_ordinal"):
            groups.setdefault(parts[0], set()).add(key)
    for group in sorted(g for g, keys in groups.items() if keys & changed):
        for category in PLURALS.get(lang, ("other",)):
            if f"{group}_{category}" not in target:
                report.errors.append(f"{locale}: {group}_{category}: missing plural category")
    for key in sorted(changed):
        text = target.get(key)
        src = source_of(key, source)
        if text is None or src is None:
            continue
        parts = plural(key)
        group_src = [source[k] for k in groups.get(parts[0], ())] if parts else [src]
        want_vars = frozenset().union(*(tokens(s)[0] for s in group_src))
        got_vars, got_tags = tokens(text)
        src_vars, src_tags = tokens(src)
        finding = Finding(key, src, text)
        # A `_one` may say "one file" without {{count}}; it may never name a variable the group lacks.
        if parts and parts[1] != "other":
            if not got_vars <= want_vars:
                report.errors.append(f"{locale}: {key}: variables {sorted(got_vars - want_vars)} not in en-US")
            # These categories hold more than one number: they must say which.
            if "count" in want_vars and "count" not in got_vars and parts[1] in ("two", "few", "many"):
                report.errors.append(f"{locale}: {key}: drops the count, but this category covers several numbers")
            if "count" in want_vars and "count" not in got_vars and (parts[1], lang) in COUNTED_ONE:
                report.errors.append(f"{locale}: {key}: drops the count, but this language's `{parts[1]}` also covers 0")
        elif got_vars != src_vars:
            report.errors.append(f"{locale}: {key}: variables {sorted(got_vars)} / en-US {sorted(src_vars)}")
        if got_tags != src_tags:
            report.errors.append(f"{locale}: {key}: tags {sorted(got_tags)} / en-US {sorted(src_tags)}")
        if locale not in ENGLISH:
            words = _WORD.findall(plain(text))
            # Product names ("Claude Code", "Claude Max") are the same in every language.
            prose = len(words) >= 2 and not all(w[0].isupper() for w in words)
            if text == src and prose:
                finding.flags.append("untranslated")
            elif lang in SCRIPTS and prose and not re.search(SCRIPTS[lang], text):
                finding.flags.append("no_native_script")
        elif text == src:
            continue
        if len(src) >= MIN_LEN:
            ratio = len(text) / len(src)
            low = SHORT_DENSE if lang in ("ko", "zh") else SHORT
            if ratio > LONG or ratio < low:
                finding.flags.append(f"length_x{ratio:.2f}")
        rows.append(finding)


def git_show(rev: str, path: str) -> dict[str, str]:
    shown = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=ROOT, capture_output=True, text=True)
    return leaves(json.loads(shown.stdout)) if shown.returncode == 0 else {}


def default_base() -> str:
    found = subprocess.run(["git", "merge-base", *DEFAULT_BASE_OF], cwd=ROOT, capture_output=True, text=True)
    if found.returncode != 0:
        sys.exit(f"no merge-base of {' and '.join(DEFAULT_BASE_OF)}; pass --base")
    return found.stdout.strip()


def run(base: str, locales_dir: Path, review_since: str | None = None) -> Report:
    report = Report()
    source = leaves(json.loads((locales_dir / f"{SOURCE}.json").read_text(encoding="utf-8")))
    base_source = git_show(base, f"{LOCALES}/{SOURCE}.json")
    source_changed = {k for k, v in source.items() if base_source.get(k) != v}
    for path in sorted(locales_dir.glob("*.json")):
        locale = path.stem
        if locale == SOURCE:
            continue
        target = leaves(json.loads(path.read_text(encoding="utf-8")))
        base_target = git_show(base, f"{LOCALES}/{path.name}")
        changed = {k for k, v in target.items() if base_target.get(k) != v}
        changed |= {k for k in target if k in source_changed or _group_other(k) in source_changed}
        check_locale(locale, source, target, changed, report)
        if review_since:
            before = git_show(review_since, f"{LOCALES}/{path.name}")
            for row in report.rows[locale]:
                if before.get(row.key) != row.text:
                    row.flags.append("assistant")
    return report


def cell(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def sheet(report: Report, base: str, review_since: str | None = None) -> str:
    since = f" --review-since {review_since[:12]}" if review_since else ""
    scope = (f"Only the strings whose text changed since {review_since[:12]} are listed, each flagged `assistant`: "
             "machine-translated by the coding assistant (Claude), without a translation service, and not yet read "
             "by a native speaker." if review_since else "Every locale string added or changed since the base is listed.")
    lines = [
        "# i18n review sheet, 2026-10",
        "",
        f"Generated by `scripts/i18n_backtranslate_check.py --base {base[:12]}{since} --sheet <this file>`; do not edit by hand.",
        scope,
        "Each string sits beside its en-US source, for a person who reads the language to check the meaning.",
        "Flags: `untranslated` (the en-US text), `no_native_script` (hi/ko/zh text with no Devanagari, Hangul or Han),",
        "`length_xN` (N times the source's length). Mechanical errors (variables, tags, plural categories, a Hindi",
        "`_one` without the count) fail the script and are fixed before this sheet is written.",
        "",
        "| Locale | Strings | Flagged |",
        "|---|---|---|",
    ]
    shown = {loc: [r for r in rows if not review_since or "assistant" in r.flags] for loc, rows in report.rows.items()}
    for locale, rows in shown.items():
        lines.append(f"| {locale} | {len(rows)} | {sum(1 for r in rows if set(r.flags) - {'assistant'})} |")
    for locale, listed in shown.items():
        lines += ["", f"## {locale}", ""]
        if locale in ENGLISH:
            lines += ["English locale: only strings that differ from en-US are listed.", ""]
        listed.sort(key=lambda r: (not r.flags, r.key))
        lines += ["| Key | en-US | " + locale + " | Flags |", "|---|---|---|---|"]
        lines += [f"| `{r.key}` | {cell(r.source)} | {cell(r.text)} | {' '.join(r.flags)} |" for r in listed]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", help="revision to diff against (default: merge-base of HEAD and origin/main)")
    parser.add_argument("--sheet", type=Path, help="write the review sheet here")
    parser.add_argument("--review-since", help="flag strings changed since this revision `assistant`; the sheet lists only those")
    parser.add_argument("--locales", type=Path, default=ROOT / LOCALES, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    base = args.base or default_base()
    report = run(base, args.locales, args.review_since)
    for locale, rows in report.rows.items():
        counts: dict[str, int] = {}
        for row in rows:
            for flag in row.flags:
                name = flag.split("_x")[0] if flag.startswith("length") else flag
                counts[name] = counts.get(name, 0) + 1
        summary = ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "no warnings"
        print(f"{locale:6} {len(rows):5} changed  {summary}")
    for error in report.errors:
        print(f"ERROR {error}", file=sys.stderr)
    if args.sheet:
        args.sheet.write_text(sheet(report, base, args.review_since), encoding="utf-8")
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
