"""Tests for scripts/i18n_backtranslate_check.py (run: python3 -m unittest discover -s scripts -p 'test_*.py')."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import i18n_backtranslate_check as qa  # noqa: E402

SOURCE = {
    "a.files_one": "One file",
    "a.files_other": "{{count}} files",
    "a.hello": "Hello <bold>{{name}}</bold>, welcome back",
    "a.brand": "Claude Code",
}


def check(locale: str, target: dict[str, str]) -> qa.Report:
    report = qa.Report()
    qa.check_locale(locale, SOURCE, target, set(target), report)
    return report


class CheckLocaleTest(unittest.TestCase):
    def test_a_good_czech_translation_passes(self):
        report = check("cs-CZ", {
            "a.files_one": "Jeden soubor", "a.files_few": "{{count}} soubory", "a.files_many": "{{count}} souboru",
            "a.files_other": "{{count}} souborů", "a.hello": "Ahoj <bold>{{name}}</bold>, vítej zpět",
            "a.brand": "Claude Code",
        })
        self.assertEqual(report.errors, [])
        self.assertEqual([r.flags for r in report.rows["cs-CZ"]], [[], [], [], [], [], []])

    def test_mechanical_errors_fail(self):
        report = check("cs-CZ", {
            "a.files_one": "Jeden soubor", "a.files_few": "soubory", "a.files_other": "{{count}} souborů",
            "a.hello": "Ahoj {{jmeno}}, vítej zpět",
        })
        joined = "\n".join(report.errors)
        self.assertIn("a.files_many: missing plural category", joined)
        self.assertIn("a.files_few: drops the count", joined)
        self.assertIn("a.hello: variables ['jmeno'] / en-US ['name']", joined)
        self.assertIn("a.hello: tags [] / en-US ['bold']", joined)

    def test_a_hindi_one_without_the_count_fails(self):
        # CLDR's Hindi `one` is 0 and 1: "एक फ़ाइल" would also be shown for 0 files.
        report = check("hi-IN", {"a.files_one": "एक फ़ाइल", "a.files_other": "{{count}} फ़ाइलें"})
        self.assertEqual(report.errors, ["hi-IN: a.files_one: drops the count, but this language's `one` also covers 0"])
        fixed = check("hi-IN", {"a.files_one": "{{count}} फ़ाइल", "a.files_other": "{{count}} फ़ाइलें"})
        self.assertEqual(fixed.errors, [])
        # German `one` is only 1, so the en-US wording without the count is fine there.
        self.assertEqual(check("de-DE", {"a.files_one": "Eine Datei", "a.files_other": "{{count}} Dateien"}).errors, [])

    def test_warnings_flag_without_failing(self):
        report = check("hi-IN", {
            "a.files_one": "{{count}} फ़ाइल", "a.files_other": "{{count}} files",
            "a.hello": "Hello <bold>{{name}}</bold>, welcome back", "a.brand": "Claude Code",
        })
        self.assertEqual(report.errors, [])
        flags = {r.key: r.flags for r in report.rows["hi-IN"]}
        self.assertEqual(flags["a.hello"], ["untranslated"])
        self.assertEqual(flags["a.brand"], [])
        cs = check("cs-CZ", {"a.hello": "Hello <bold>{{name}}</bold>, welcome back"})
        self.assertEqual(cs.rows["cs-CZ"][0].flags, ["untranslated"])
        long = check("de-DE", {"a.hello": "Hallo <bold>{{name}}</bold>, " + "willkommen " * 10})
        self.assertTrue(long.rows["de-DE"][0].flags[0].startswith("length_x"))

    def test_english_locale_lists_only_differences(self):
        report = check("en-IN", {"a.brand": "Claude Code", "a.hello": "Hello <bold>{{name}}</bold>, welcome"})
        self.assertEqual([r.key for r in report.rows["en-IN"]], ["a.hello"])

    def test_sheet_groups_by_locale(self):
        report = check("cs-CZ", {"a.hello": "Hello <bold>{{name}}</bold>, welcome back", "a.brand": "Claude | Code"})
        text = qa.sheet(report, "0123456789abcdef")
        self.assertIn("## cs-CZ", text)
        self.assertIn("| cs-CZ | 2 | 1 |", text)
        self.assertIn("Claude \\| Code", text)

    def test_review_sheet_lists_only_strings_the_assistant_changed(self):
        report = check("cs-CZ", {"a.hello": "Ahoj <bold>{{name}}</bold>, vítejte zpět", "a.brand": "Claude Code"})
        report.rows["cs-CZ"][1].flags.append("assistant")
        text = qa.sheet(report, "0123456789abcdef", review_since="fedcba9876543210")
        self.assertIn("--review-since fedcba987654", text)
        self.assertIn("| cs-CZ | 1 | 0 |", text)
        self.assertIn("vítejte zpět", text)
        self.assertNotIn("`a.brand`", text)


if __name__ == "__main__":
    unittest.main()
