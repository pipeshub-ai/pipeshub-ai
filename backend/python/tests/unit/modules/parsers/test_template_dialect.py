"""Unit tests for app.modules.parsers.yaml.template_dialect."""

import pytest
import yaml

from app.modules.parsers.yaml.template_dialect import (
    DIALECTS,
    TemplateDialect,
    detect_template_dialect,
)

HELM = """\
{{- if .Values.ingress.enabled -}}
apiVersion: networking.k8s.io/v1
metadata:
  name: {{ include "pipeshub-ai.fullname" . }}
  labels:
    {{- include "pipeshub-ai.labels" . | nindent 4 }}
{{- end }}
"""

JINJA = """\
{% if enabled %}
name: {{ app_name }}
{% endif %}
"""

ERB = """\
<% if ENV['CI'] %>
ci: true
<% end %>
"""

MUSTACHE = """\
{{#prod}}
replicas: 3
{{/prod}}
"""


class TestDetectsDialects:

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            (HELM, TemplateDialect.GO),
            (JINJA, TemplateDialect.JINJA),
            (ERB, TemplateDialect.ERB),
            (MUSTACHE, TemplateDialect.MUSTACHE),
            # Bare tags with no control structure: still Go (choice is moot).
            ("name: {{ app_name }}\n", TemplateDialect.GO),
            # A tag used as a key.
            ("{{ key }}: value\n", TemplateDialect.GO),
            # Helm whitespace-trim markers also exist in Jinja; ``{% %}`` decides.
            ("{%- if x %}\nk: {{- v }}\n{%- endif %}\n", TemplateDialect.JINJA),
        ],
    )
    def test_dialect(self, text, expected):
        assert detect_template_dialect(text) == expected

    def test_go_markers_beat_mustache_style_section(self):
        # ``{{/* comment */}}`` looks like a Mustache closer but is a Go comment.
        text = '{{/* header */}}\nname: {{ .Values.name }}\n'
        assert detect_template_dialect(text) == TemplateDialect.GO


class TestDoesNotFireOnValidYAML:

    @pytest.mark.parametrize(
        "text",
        [
            "name: Widget\nprice: 100\n",
            # GitHub Actions expressions are valid YAML scalars.
            "run: echo ${{ github.sha }}\n",
            # Tags inside quotes parse fine and are not evidence.
            'value: "{{ .Release.Name }}-redis"\n',
            "image: repo/{{ .Values.tag }}\n",
            # Tag mentioned in a YAML comment only.
            "# use {{ .Values.x }} here\nkey: v\n",
            # Broken for other reasons, no template syntax.
            "a:\n\tb: 1\n",
            "files: *.js\n",
        ],
    )
    def test_none(self, text):
        assert detect_template_dialect(text) is None

    @pytest.mark.parametrize(
        "text",
        [
            "run: echo ${{ github.sha }}\n",
            'value: "{{ .Release.Name }}-redis"\n',
            "image: repo/{{ .Values.tag }}\n",
        ],
    )
    def test_negative_cases_really_are_valid_yaml(self, text):
        assert yaml.safe_load(text) is not None


class TestControlClassification:

    @pytest.mark.parametrize(
        ("dialect", "tag", "role", "label"),
        [
            (TemplateDialect.GO, "{{- if .Values.x }}", "open", "if .Values.x"),
            (TemplateDialect.GO, "{{- else }}", "middle", "else"),
            (TemplateDialect.GO, "{{ else if .Values.y }}", "middle", "else if .Values.y"),
            (TemplateDialect.GO, "{{- end }}", "close", "end"),
            (TemplateDialect.GO, '{{- define "app.labels" -}}', "open", 'define "app.labels"'),
            (TemplateDialect.JINJA, "{% for h in hosts %}", "open", "for h in hosts"),
            (TemplateDialect.JINJA, "{% elif x %}", "middle", "elif x"),
            (TemplateDialect.JINJA, "{% endfor %}", "close", "endfor"),
            (TemplateDialect.ERB, "<% list.each do |n| %>", "open", "list.each do |n|"),
            (TemplateDialect.ERB, "<% elsif x %>", "middle", "elsif x"),
            (TemplateDialect.ERB, "<% end %>", "close", "end"),
            (TemplateDialect.MUSTACHE, "{{#if tls}}", "open", "if tls"),
            (TemplateDialect.MUSTACHE, "{{^prod}}", "open", "not prod"),
            (TemplateDialect.MUSTACHE, "{{else}}", "middle", "else"),
            (TemplateDialect.MUSTACHE, "{{/if}}", "close", "if"),
        ],
    )
    def test_control_tags(self, dialect, tag, role, label):
        control = DIALECTS[dialect].classify(tag)
        assert control is not None
        assert (control.role, control.label) == (role, label)

    @pytest.mark.parametrize(
        ("dialect", "tag"),
        [
            (TemplateDialect.GO, '{{ include "app.name" . }}'),
            (TemplateDialect.GO, "{{- toYaml . | nindent 8 }}"),
            (TemplateDialect.GO, "{{/* comment */}}"),
            (TemplateDialect.GO, "{{- $x := .Values.y }}"),
            (TemplateDialect.JINJA, "{{ app_name }}"),
            (TemplateDialect.JINJA, "{% set x = 1 %}"),
            (TemplateDialect.ERB, "<%= ENV['X'] %>"),
            (TemplateDialect.ERB, "<%# note %>"),
            (TemplateDialect.ERB, "<% x = 1 if y %>"),
            (TemplateDialect.ERB, "<% if x then y end %>"),
            (TemplateDialect.MUSTACHE, "{{name}}"),
            (TemplateDialect.MUSTACHE, "{{! note }}"),
            (TemplateDialect.MUSTACHE, "{{> partial}}"),
        ],
    )
    def test_non_control_tags(self, dialect, tag):
        assert DIALECTS[dialect].classify(tag) is None
