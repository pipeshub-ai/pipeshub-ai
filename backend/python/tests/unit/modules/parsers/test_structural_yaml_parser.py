"""Unit tests for app.modules.parsers.yaml.structural_yaml_parser."""

import re

import pytest

from app.models.blocks import BlockType, DataFormat, GroupSubType, GroupType
from app.modules.parsers.yaml.structural_yaml_parser import (
    MAX_BLOCK_LINES,
    StructuralYAMLParser,
)
from app.modules.parsers.yaml.template_dialect import TemplateDialect

TAG = re.compile(r"\{\{.*?\}\}", re.S)

HELM_DEPLOYMENT = """\
{{- /* Validate required secrets before deployment */ -}}
{{- include "app.validateRequiredSecrets" . }}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "app.fullname" . }}
  labels:
    {{- include "app.labels" . | nindent 4 }}
spec:
  {{- if not .Values.autoscaling.enabled }}
  replicas: {{ .Values.replicaCount }}
  {{- end }}
  template:
    spec:
      {{- if .Values.affinity }}
      affinity:
        {{- toYaml .Values.affinity | nindent 8 }}
      {{- else }}
      affinity:
        podAntiAffinity:
          topologyKey: kubernetes.io/hostname
      {{- end }}
      containers:
        - name: {{ .Chart.Name }}
          env:
            {{- if .Values.redis.external.enabled }}
            - name: REDIS_HOST
              value: {{ .Values.redis.external.host | quote }}
            {{- else }}
            - name: REDIS_HOST
              value: "{{ .Release.Name }}-redis-master"
            {{- end }}
          volumeMounts:
            - name: data
              mountPath: /data
"""


@pytest.fixture
def parser():
    return StructuralYAMLParser()


def _blocks_of(container, group_index):
    return [b for b in container.blocks if b.parent_index == group_index]


def _joined(container):
    return "\n".join(b.data for b in container.blocks)


class TestHelmTemplate:

    def test_every_tag_survives_verbatim(self, parser):
        result = parser.parse_text(HELM_DEPLOYMENT, "deployment.yaml", TemplateDialect.GO)
        joined = _joined(result.container)
        for tag in TAG.findall(HELM_DEPLOYMENT):
            assert tag in joined
        # Every source line is present exactly once across blocks (header
        # comments aside), i.e. nothing is dropped or duplicated.
        body = "\n".join(
            line for line in joined.split("\n") if not line.startswith("# ")
        )
        assert body == HELM_DEPLOYMENT.rstrip("\n")

    def test_root_group_and_formats(self, parser):
        result = parser.parse_text(HELM_DEPLOYMENT, "deployment.yaml", TemplateDialect.GO)
        root = result.container.block_groups[0]
        assert root.sub_type == GroupSubType.RECORD
        assert root.name == "deployment.yaml"
        assert root.format == DataFormat.YAML
        assert "go-templated" in root.data["summary"]
        assert result.document_count == 1
        assert all(b.type == BlockType.TEXT and b.format == DataFormat.YAML for b in result.container.blocks)

    def test_small_file_is_one_block_with_line_number(self, parser):
        result = parser.parse_text(HELM_DEPLOYMENT, "deployment.yaml", TemplateDialect.GO)
        assert len(result.container.blocks) == 1
        block = result.container.blocks[0]
        assert block.citation_metadata.line_number == 3  # first content line
        assert block.citation_metadata.section_title == "deployment.yaml"

    def test_large_file_splits_by_structure_and_keeps_scope_context(self, parser):
        # Pad env so the container item exceeds MAX_BLOCK_LINES and gets split.
        padding = "".join(
            f"            - name: VAR_{i}\n              value: \"{i}\"\n" for i in range(MAX_BLOCK_LINES)
        )
        text = HELM_DEPLOYMENT.replace("          volumeMounts:", padding + "          volumeMounts:")
        result = parser.parse_text(text, "deployment.yaml", TemplateDialect.GO)
        container = result.container

        names = [g.name for g in container.block_groups]
        assert "spec" in names
        assert "spec.template.spec.containers[0]" in names
        assert "spec.template.spec.containers[0].env" in names

        env_group = next(g for g in container.block_groups if g.name == "spec.template.spec.containers[0].env")
        env_blocks = _blocks_of(container, env_group.index)
        assert len(env_blocks) >= 2
        assert all(b.data.startswith("# spec.template.spec.containers[0].env") for b in env_blocks)
        assert all(b.data.count("\n") + 1 <= MAX_BLOCK_LINES + 1 for b in env_blocks)

        # The if/else/end around REDIS_HOST is short, so it stays in one block
        # with both branches and their conditions.
        redis = next(b for b in env_blocks if "REDIS_HOST" in b.data)
        assert "{{- if .Values.redis.external.enabled }}" in redis.data
        assert "{{- else }}" in redis.data
        assert redis.data.count("REDIS_HOST") == 2

    def test_block_inside_open_scope_is_labelled(self, parser):
        body = "".join(f"  key_{i}: {i}\n" for i in range(MAX_BLOCK_LINES))
        text = (
            "{{- if .Values.enabled }}\n"
            "spec:\n" + body +
            "{{- else }}\n"
            "spec:\n" + body +
            "{{- end }}\n"
        )
        result = parser.parse_text(text, "big.yaml", TemplateDialect.GO)
        heads = [b.data.split("\n")[0] for b in result.container.blocks]
        assert any("scope: if .Values.enabled" in h for h in heads)
        assert any("scope: else (after: if .Values.enabled)" in h for h in heads)

    def test_multi_document_groups_named_by_kind_and_name(self, parser):
        text = (
            "{{- if .Values.kafka.enabled }}\n"
            "apiVersion: v1\nkind: Service\nmetadata:\n  name: {{ include \"app.fullname\" . }}-kafka\n"
            "---\n"
            "apiVersion: apps/v1\nkind: StatefulSet\nmetadata:\n  name: {{ include \"app.fullname\" . }}-kafka\n"
            "{{- end }}\n"
        )
        result = parser.parse_text(text, "kafka.yaml", TemplateDialect.GO)
        assert result.document_count == 2
        groups = result.container.block_groups
        assert groups[1].name == 'Service {{ include "app.fullname" . }}-kafka'
        assert groups[2].name == 'StatefulSet {{ include "app.fullname" . }}-kafka'
        assert groups[1].type == GroupType.KEY_VALUE_AREA
        # The scope opened before the first document still labels the second.
        second = _blocks_of(result.container, groups[2].index)[0]
        assert "scope: if .Values.kafka.enabled" in second.data.split("\n")[0]

    def test_helpers_tpl_defines_chunk_per_definition(self, parser):
        text = (
            '{{/*\nExpand the name.\n*/}}\n'
            '{{- define "app.name" -}}\n'
            '{{- default .Chart.Name .Values.nameOverride | trunc 63 }}\n'
            '{{- end }}\n\n'
            '{{- define "app.labels" -}}\n'
            'helm.sh/chart: {{ include "app.chart" . }}\n'
            '{{- end }}\n'
        )
        result = parser.parse_text(text, "_helpers.tpl", TemplateDialect.GO)
        joined = _joined(result.container)
        assert 'define "app.name"' in joined
        assert 'define "app.labels"' in joined
        assert "helm.sh/chart" in joined


class TestOtherDialectsAndPlainFallback:

    def test_jinja_scopes(self, parser):
        text = "{% if enabled %}\nname: {{ app }}\n{% else %}\nname: none\n{% endif %}\n"
        result = parser.parse_text(text, "play.yml", TemplateDialect.JINJA)
        assert "{% else %}" in _joined(result.container)

    def test_plain_broken_yaml_is_still_chunked(self, parser):
        text = "a:\n\tb: 1\nfiles: *.js\ndesc: Note: see docs\n"
        result = parser.parse_text(text, "broken.yaml", None)
        container = result.container
        assert "chunked by structure" in container.block_groups[0].data["summary"]
        assert _joined(container) == text.rstrip("\n")

    def test_block_scalar_body_is_not_parsed_for_structure(self, parser):
        text = "script: |\n  {{ not_a_tag }}\n  - not: a-key\nnext: 1\n"
        result = parser.parse_text(text, "ci.yaml", TemplateDialect.GO)
        assert len(result.container.blocks) == 1
        assert result.container.blocks[0].data == text.rstrip("\n")
