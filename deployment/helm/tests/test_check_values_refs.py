"""Run: python3 -m unittest discover -s deployment/helm/tests -p 'test_*.py'"""

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_values_refs as refs  # noqa: E402


class TemplateRefs(unittest.TestCase):
    def test_direct_and_root_reads(self) -> None:
        text = "{{ .Values.config.backendPort }} {{ $.Values.zookeeper.replicaCount }}"
        self.assertEqual(refs.template_refs(text), {"config.backendPort", "zookeeper.replicaCount"})

    def test_member_reads_through_an_alias(self) -> None:
        text = textwrap.dedent("""\
            {{- $external := .Values.redis.external -}}
            {{- if $external.enabled -}}{{ $external.clusterEndpoints }}{{- end -}}
            """)
        self.assertEqual(
            refs.template_refs(text),
            {"redis.external", "redis.external.enabled", "redis.external.clusterEndpoints"},
        )

    def test_alias_used_whole_adds_nothing_beyond_its_path(self) -> None:
        text = "{{- range $k, $v := .Values.qdrant.env }}{{ $k }}: {{ $v | quote }}{{- end }}"
        self.assertEqual(refs.template_refs(text), {"qdrant.env"})

    def test_alias_name_is_matched_whole(self) -> None:
        text = "{{ $ext := .Values.redis.external }}{{ $external.enabled }}"
        self.assertEqual(refs.template_refs(text), {"redis.external"})


class MissingRefs(unittest.TestCase):
    def chart(self, values: str, template: str) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "templates").mkdir()
        (root / "values.yaml").write_text(textwrap.dedent(values), encoding="utf-8")
        (root / "templates" / "t.yaml").write_text(textwrap.dedent(template), encoding="utf-8")
        return root

    def test_key_nested_under_the_wrong_parent_is_reported(self) -> None:
        chart = self.chart(
            """\
            config:
              nodeEnv: production
            sandbox:
              backendPort: 3001
            """,
            "{{ .Values.config.nodeEnv }} {{ .Values.config.backendPort }}",
        )
        self.assertEqual(refs.missing_refs(chart), ["config.backendPort"])

    def test_missing_member_read_through_an_alias_is_reported(self) -> None:
        chart = self.chart(
            """\
            redis:
              external:
                enabled: false
            """,
            "{{- $external := .Values.redis.external -}}{{ $external.enabled }}{{ $external.clusterEndpoints }}",
        )
        self.assertEqual(refs.missing_refs(chart), ["redis.external.clusterEndpoints"])

    def test_documented_optional_keys_are_allowed(self) -> None:
        chart = self.chart("redis:\n  auth:\n    enabled: true\n", "{{ .Values.redis.auth.password }}")
        self.assertEqual(refs.missing_refs(chart), [])

    def test_this_chart_is_clean(self) -> None:
        chart = Path(__file__).resolve().parent.parent / "pipeshub-ai"
        self.assertEqual(refs.missing_refs(chart), [])


if __name__ == "__main__":
    unittest.main()
