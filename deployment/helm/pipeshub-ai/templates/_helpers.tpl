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

{{- /* Validate persistence vs replica count
       The main app Deployment shares ONE PVC across all pods. With a ReadWriteOnce
       volume (the default on EKS gp2/gp3, GKE pd-standard, AKS managed-disk) only
       one pod can attach the volume and the remaining replicas will be stuck Pending
       with "Multi-Attach error". Block the install with a clear message instead.
*/ -}}
{{- $effectiveReplicas := .Values.replicaCount | int }}
{{- if .Values.autoscaling.enabled }}
  {{- $effectiveReplicas = max $effectiveReplicas (.Values.autoscaling.minReplicas | int) }}
{{- end }}
{{- if and .Values.persistence.enabled (gt $effectiveReplicas 1) }}
  {{- $modes := .Values.persistence.accessModes | default (list) }}
  {{- $hasRWX := has "ReadWriteMany" $modes }}
  {{- if not $hasRWX }}
    {{- fail (printf "persistence requires ReadWriteMany when running >1 replica (effective replicas: %d). The app Deployment shares one PVC across all pods; with ReadWriteOnce (EBS gp2/gp3, GKE pd-standard, AKS managed-disk) only one pod can attach and the rest stay Pending with Multi-Attach error. Fix one of: (a) use an RWX StorageClass (EFS on EKS / Filestore on GKE / Azure Files on AKS) with --set 'persistence.accessModes={ReadWriteMany}' --set persistence.storageClass=<rwx-class>; (b) single replica: --set replicaCount=1 --set autoscaling.enabled=false; (c) disable shared persistence: --set persistence.enabled=false." $effectiveReplicas) }}
  {{- end }}
{{- end }}
{{- end }}

{{/*
Validate that SANDBOX_MODE=docker actually has a daemon to reach.

Without one, `run_code` fails at provision with an opaque Docker error in the
middle of a user's conversation. Failing here instead makes the operator
choose how code execution is isolated, at install time, with the options in
front of them.
*/}}
{{- define "pipeshub-ai.validateSandbox" -}}
{{- if eq (include "pipeshub-ai.sandboxMode" .) "docker" }}
  {{- $hasDaemon := or .Values.sandbox.dind.enabled .Values.config.dockerHost }}
  {{- if not $hasDaemon }}
    {{- $socketMounted := false }}
    {{- range .Values.extraVolumeMounts }}
      {{- if contains "docker.sock" (.mountPath | default "") }}
        {{- $socketMounted = true }}
      {{- end }}
    {{- end }}
    {{- if not $socketMounted }}
      {{- fail "config.sandboxMode is \"docker\" but no Docker daemon is configured, so run_code would fail at runtime. Pick one: (a) --set sandbox.dind.enabled=true to run a Docker-in-Docker sidecar (needs a PRIVILEGED container - see sandbox.dind in values.yaml); (b) --set config.dockerHost=tcp://<host>:2375 to use a daemon you already run; (c) mount the node's /var/run/docker.sock via extraVolumes/extraVolumeMounts; (d) --set config.sandboxMode=e2b with an E2B_API_KEY to execute off-cluster; or (e) --set config.sandboxMode=local to run generated code as a subprocess of this pod - NO container isolation, acceptable only for single-tenant development clusters." }}
    {{- end }}
  {{- end }}
{{- end }}
{{- end }}

{{/*
Canonical `config.sandboxMode`, validated against what the service accepts.

The runtime loader upper-cases before matching, so DOCKER/Docker/docker all
select the Docker backend there. Comparing the raw value in the chart meant
`sandboxMode: DOCKER` skipped the daemon validation below while the service
still chose Docker — the install succeeded and run_code failed at provision.
Emitting the canonical spelling into the env var keeps both layers reading
the same thing.
*/}}
{{- define "pipeshub-ai.sandboxMode" -}}
{{- $raw := .Values.config.sandboxMode | toString -}}
{{- $mode := $raw | trim | lower -}}
{{- if not (has $mode (list "local" "docker" "e2b")) -}}
  {{- fail (printf "config.sandboxMode=%q is not a supported coding sandbox. Use one of: local, docker, e2b." $raw) -}}
{{- end -}}
{{- $mode -}}
{{- end -}}

{{/*
Effective DOCKER_HOST, or "" when no daemon is configured.

Single source for the precedence so the deployment's env var and the NOTES
description cannot disagree: an explicitly configured daemon wins, otherwise
the DinD sidecar's loopback address if one is being deployed.
*/}}
{{- define "pipeshub-ai.dockerHost" -}}
{{- if .Values.config.dockerHost -}}
{{- .Values.config.dockerHost -}}
{{- else if .Values.sandbox.dind.enabled -}}
{{- printf "tcp://localhost:%v" .Values.sandbox.dind.port -}}
{{- end -}}
{{- end -}}

{{/*
How run_code actually executes: local | e2b | dind | external | socket.

Deploying the sidecar and USING it are independent — `sandbox.dind.enabled`
creates a privileged container whatever the backend is, while
`config.sandboxMode` and `config.dockerHost` decide what run_code talks to.
Describing one in terms of the other is how the notes came to claim
Docker-in-Docker for an e2b deployment.
*/}}
{{- define "pipeshub-ai.sandboxPath" -}}
{{- $mode := include "pipeshub-ai.sandboxMode" . -}}
{{- if ne $mode "docker" -}}
{{- $mode -}}
{{- else if .Values.config.dockerHost -}}
external
{{- else if .Values.sandbox.dind.enabled -}}
dind
{{- else -}}
socket
{{- end -}}
{{- end -}}

{{/*
Validate the Redis wiring before anything renders.

`redis.external.enabled` only works when paired with a cluster-capable mode
and the bundled subchart switched off -- a pairing the template comment
described but nothing enforced. Left unchecked, `external.enabled=true` alone
renders `REDIS_HOST=""` while `REDIS_MODE=standalone` selects
`StandaloneRedisProvider`, which ignores `REDIS_CLUSTER_ENDPOINTS` and falls
back to localhost, and the subchart still deploys. Both failures are silent.
*/}}
{{- define "pipeshub-ai.validateRedis" -}}
{{- $external := .Values.redis.external -}}
{{- if $external.enabled -}}
  {{- if not $external.clusterEndpoints -}}
    {{- fail "redis.external.enabled=true requires redis.external.clusterEndpoints (comma-separated host:port list)." -}}
  {{- end -}}
  {{- if eq (.Values.redis.mode | default "standalone") "standalone" -}}
    {{- fail "redis.external.enabled=true requires a cluster-capable redis.mode (set redis.mode=cluster, or an EE mode supplied via redis.providerModule). REDIS_MODE=standalone ignores redis.external.clusterEndpoints and would connect to localhost." -}}
  {{- end -}}
  {{- if .Values.redis.enabled -}}
    {{- fail "redis.external.enabled=true also requires redis.enabled=false, otherwise the bundled Redis subchart is deployed and left unused." -}}
  {{- end -}}
{{- else if not .Values.redis.enabled -}}
  {{- fail "redis.enabled=false requires redis.external.enabled=true with redis.external.clusterEndpoints; otherwise nothing provides Redis." -}}
{{- else if ne (.Values.redis.mode | default "standalone") "standalone" -}}
  {{- /*
    The bundled Bitnami subchart is *replication*, not Redis Cluster. Pointing
    a cluster-mode client at `<release>-redis-master` makes it attempt cluster
    discovery (CLUSTER SLOTS) against a server that has none, so it fails to
    connect -- and with no REDIS_CLUSTER_ENDPOINTS emitted there is nothing
    else for it to talk to. A non-standalone mode only makes sense with an
    external endpoint.
  */ -}}
  {{- fail (printf "redis.mode=%s requires redis.external.enabled=true with redis.external.clusterEndpoints. The bundled Redis subchart is a replication deployment, not a Redis Cluster, so a cluster-mode client cannot connect to it. Use redis.mode=standalone with the bundled chart." (.Values.redis.mode | default "standalone")) -}}
{{- end -}}
{{- end -}}
