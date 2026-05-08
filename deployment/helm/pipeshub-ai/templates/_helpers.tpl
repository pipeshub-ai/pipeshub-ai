{{/*
Expand the name of the chart.
*/}}
{{- define "pipeshub-ai.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "pipeshub-ai.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "pipeshub-ai.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "pipeshub-ai.labels" -}}
helm.sh/chart: {{ include "pipeshub-ai.chart" . }}
{{ include "pipeshub-ai.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- with .Values.global.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{/*
Component-specific labels for main app
*/}}
{{- define "pipeshub-ai.app.labels" -}}
{{ include "pipeshub-ai.labels" . }}
app.kubernetes.io/component: app
{{- end }}

{{/*
Component-specific labels for MongoDB
*/}}
{{- define "pipeshub-ai.mongodb.labels" -}}
{{ include "pipeshub-ai.labels" . }}
app.kubernetes.io/component: mongodb
{{- end }}

{{/*
Component-specific labels for Redis
*/}}
{{- define "pipeshub-ai.redis.labels" -}}
{{ include "pipeshub-ai.labels" . }}
app.kubernetes.io/component: redis
{{- end }}

{{/*
Component-specific labels for Neo4j
*/}}
{{- define "pipeshub-ai.neo4j.labels" -}}
{{ include "pipeshub-ai.labels" . }}
app.kubernetes.io/component: neo4j
{{- end }}

{{/*
Image helper with registry support
Usage: include "pipeshub-ai.image" (dict "image" .Values.image "global" .Values.global "context" $)
*/}}
{{- define "pipeshub-ai.image" -}}
{{- $registry := .global.imageRegistry | default "" -}}
{{- $repository := .image.repository -}}
{{- $tag := .image.tag | toString -}}
{{- if $registry }}
{{- printf "%s/%s:%s" $registry $repository $tag }}
{{- else }}
{{- printf "%s:%s" $repository $tag }}
{{- end }}
{{- end }}

{{/*
Service account name helper
*/}}
{{- define "pipeshub-ai.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "pipeshub-ai.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "pipeshub-ai.selectorLabels" -}}
app.kubernetes.io/name: {{ include "pipeshub-ai.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Helper functions for secret management
*/}}

{{/*
Determine if we should create secrets (not using external or existing secrets)
*/}}
{{- define "pipeshub-ai.createSecrets" -}}
{{- if and (not .Values.secretManagement.externalSecrets.enabled) (not .Values.secretManagement.existingSecrets.enabled) }}
true
{{- end }}
{{- end }}

{{/*
Get the name of the secret containing application secrets (secretKey, etc.)
*/}}
{{- define "pipeshub-ai.secretName" -}}
{{- if .Values.secretManagement.existingSecrets.enabled }}
{{- .Values.secretManagement.existingSecrets.appSecretName }}
{{- else if .Values.secretManagement.externalSecrets.enabled }}
{{- default (printf "%s-secrets" (include "pipeshub-ai.fullname" .)) .Values.secretManagement.externalSecrets.targetSecretName }}
{{- else }}
{{- printf "%s-secrets" (include "pipeshub-ai.fullname" .) }}
{{- end }}
{{- end }}

{{/*
Get the name of the secret containing MongoDB credentials
*/}}
{{- define "pipeshub-ai.mongodbSecretName" -}}
{{- if .Values.secretManagement.existingSecrets.enabled }}
{{- .Values.secretManagement.existingSecrets.mongodbSecretName | default (include "pipeshub-ai.secretName" .) }}
{{- else }}
{{- printf "%s-secrets" (include "pipeshub-ai.fullname" .) }}
{{- end }}
{{- end }}

{{/*
Get the name of the secret containing Redis credentials
*/}}
{{- define "pipeshub-ai.redisSecretName" -}}
{{- if .Values.secretManagement.existingSecrets.enabled }}
{{- .Values.secretManagement.existingSecrets.redisSecretName | default (include "pipeshub-ai.secretName" .) }}
{{- else }}
{{- printf "%s-secrets" (include "pipeshub-ai.fullname" .) }}
{{- end }}
{{- end }}

{{/*
Get the name of the secret containing Neo4j credentials
*/}}
{{- define "pipeshub-ai.neo4jSecretName" -}}
{{- if .Values.secretManagement.existingSecrets.enabled }}
{{- .Values.secretManagement.existingSecrets.neo4jSecretName | default (include "pipeshub-ai.secretName" .) }}
{{- else }}
{{- printf "%s-secrets" (include "pipeshub-ai.fullname" .) }}
{{- end }}
{{- end }}

{{/*
Get the name of the secret containing ArangoDB credentials
*/}}
{{- define "pipeshub-ai.arangoSecretName" -}}
{{- if .Values.secretManagement.existingSecrets.enabled }}
{{- .Values.secretManagement.existingSecrets.arangoSecretName | default (include "pipeshub-ai.secretName" .) }}
{{- else }}
{{- printf "%s-secrets" (include "pipeshub-ai.fullname" .) }}
{{- end }}
{{- end }}

{{/*
Get the name of the secret containing Qdrant credentials
*/}}
{{- define "pipeshub-ai.qdrantSecretName" -}}
{{- if .Values.secretManagement.existingSecrets.enabled }}
{{- .Values.secretManagement.existingSecrets.qdrantSecretName | default (include "pipeshub-ai.secretName" .) }}
{{- else }}
{{- printf "%s-secrets" (include "pipeshub-ai.fullname" .) }}
{{- end }}
{{- end }}

{{/*
Validate required secrets are provided when not using external or existing secrets
This helper is called during template rendering to fail fast with clear error messages
*/}}
{{- define "pipeshub-ai.validateRequiredSecrets" -}}
{{- if not .Values.secretManagement.externalSecrets.enabled }}
  {{- if not .Values.secretManagement.existingSecrets.enabled }}
    {{- if not .Values.secretKey }}
      {{- fail "secretKey is required when not using external secrets or existing secrets. Generate with: openssl rand -hex 32" }}
    {{- end }}
    
    {{- /* Validate MongoDB credentials */ -}}
    {{- if and .Values.mongodb.auth.enabled (not .Values.mongodb.auth.rootPassword) }}
      {{- fail "mongodb.auth.rootPassword is required when MongoDB auth is enabled. Set via --set mongodb.auth.rootPassword=<password>" }}
    {{- end }}
    
    {{- /* Validate Redis credentials */ -}}
    {{- if and .Values.redis.auth.enabled (not .Values.redis.auth.password) }}
      {{- fail "redis.auth.password is required when Redis auth is enabled. Set via --set redis.auth.password=<password>" }}
    {{- end }}
    
    {{- /* Validate Neo4j credentials */ -}}
    {{- if .Values.neo4j.enabled }}
      {{- if not .Values.neo4j.auth.password }}
        {{- fail "neo4j.auth.password is required when Neo4j is enabled. Set via --set neo4j.auth.password=<password>" }}
      {{- end }}
      {{- if eq .Values.neo4j.auth.password "your_password" }}
        {{- fail "neo4j.auth.password must not use default placeholder 'your_password'. Set a secure password." }}
      {{- end }}
    {{- end }}
    
    {{- /* Validate ArangoDB credentials */ -}}
    {{- if and .Values.arango.enabled (not .Values.arango.auth.rootPassword) }}
      {{- fail "arango.auth.rootPassword is required when ArangoDB is enabled. Set via --set arango.auth.rootPassword=<password>" }}
    {{- end }}
  {{- end }}
{{- else }}
  {{- if not .Values.secretManagement.externalSecrets.secretStoreRef.name }}
    {{- fail "secretManagement.externalSecrets.secretStoreRef.name is required when externalSecrets is enabled" }}
  {{- end }}
{{- end }}

{{- if .Values.secretManagement.existingSecrets.enabled }}
  {{- if not .Values.secretManagement.existingSecrets.appSecretName }}
    {{- fail "secretManagement.existingSecrets.appSecretName is required when existingSecrets is enabled" }}
  {{- end }}
{{- end }}
{{- end }}
