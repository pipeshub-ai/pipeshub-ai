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
Drop null entries. Helm keeps a null when a later values file clears a key,
and a nodeSelector value of null is not a label.
*/}}
{{- define "pipeshub-ai.compactMap" -}}
{{- $clean := dict -}}
{{- range $key, $value := . -}}
{{- if not (kindIs "invalid" $value) -}}
{{- $_ := set $clean $key $value -}}
{{- end -}}
{{- end -}}
{{- if $clean -}}
{{- toYaml $clean -}}
{{- end -}}
{{- end -}}

{{/*
Selector labels
*/}}
{{- define "pipeshub-ai.selectorLabels" -}}
app.kubernetes.io/name: {{ include "pipeshub-ai.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Labels of the parsing pods. Their application name differs from the main Deployment's, so
nothing that selects the main pods (its selectors, its policies) also selects these.
*/}}
{{/*
Name of the parsing Deployment, Service and PDB, and the host in PARSING_SERVICE_URL: the full name with
"-parsing" appended, kept within the 63-character DNS label limit by shortening the full name, not the suffix.
*/}}
{{- define "pipeshub-ai.parsingName" -}}
{{- $name := printf "%s-parsing" (include "pipeshub-ai.fullname" . | trunc 55 | trimSuffix "-") -}}
{{- if eq $name (include "pipeshub-ai.fullname" .) -}}
{{- fail (printf "the parsing workload name %q equals the main application's name; change nameOverride or fullnameOverride" $name) -}}
{{- end -}}
{{- $name -}}
{{- end }}

{{- define "pipeshub-ai.parsingSelectorLabels" -}}
{{- $parsing := printf "%s-parsing" (include "pipeshub-ai.name" . | trunc 55 | trimSuffix "-") -}}
{{- if eq $parsing (include "pipeshub-ai.name" .) -}}
{{- fail (printf "the parsing pods' application name %q equals the main application's name; change nameOverride" $parsing) -}}
{{- end -}}
app.kubernetes.io/name: {{ $parsing }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "pipeshub-ai.parsingLabels" -}}
helm.sh/chart: {{ include "pipeshub-ai.chart" . }}
{{ include "pipeshub-ai.parsingSelectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- /* The name and instance keys are the selector's, set above; they cannot be overridden here. */}}
{{- with omit (default dict .Values.global.commonLabels) "app.kubernetes.io/name" "app.kubernetes.io/instance" }}
{{ toYaml . }}
{{- end }}
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
Qdrant API key for the chart-created Secret: qdrant.apiKey, else the key this
release's Secret already holds, so an upgrade need not pass it again. Never
generated: `helm template` and GitOps renders cannot see the cluster, and a
fresh key per render would put the app and Qdrant on different keys.
*/}}
{{- define "pipeshub-ai.qdrantApiKey" -}}
{{- if .Values.qdrant.apiKey -}}
{{- .Values.qdrant.apiKey -}}
{{- else -}}
{{- $existing := lookup "v1" "Secret" .Release.Namespace (printf "%s-secrets" (include "pipeshub-ai.fullname" .)) -}}
{{- if and $existing $existing.data (hasKey $existing.data "qdrant-api-key") -}}
{{- index $existing.data "qdrant-api-key" | b64dec -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Pod-template annotation value that changes with the Qdrant key, so a rotation
restarts the app and Qdrant together instead of leaving one on the old key.
Keyed with secretKey (required whenever the chart creates the Secret) so
anyone who can read pod specs cannot brute-force a weak key offline.
*/}}
{{- define "pipeshub-ai.qdrantApiKeyChecksum" -}}
{{- printf "%s:%s" (required "secretKey is required" .Values.secretKey | toString) (include "pipeshub-ai.qdrantApiKey" .) | sha256sum | trunc 16 -}}
{{- end -}}

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
    {{- if .Values.arango.enabled }}
      {{- if not .Values.arango.auth.rootPassword }}
        {{- fail "arango.auth.rootPassword is required when ArangoDB is enabled. Set via --set arango.auth.rootPassword=<password>" }}
      {{- end }}
      {{- if eq .Values.arango.auth.rootPassword "root" }}
        {{- fail "arango.auth.rootPassword must not use default placeholder 'root'. Set a secure password." }}
      {{- end }}
    {{- end }}

    {{- /* Validate Qdrant API key */ -}}
    {{- $qdrantApiKey := include "pipeshub-ai.qdrantApiKey" . }}
    {{- if not $qdrantApiKey }}
      {{- fail "qdrant.apiKey is required. Set via --set qdrant.apiKey=\"$(openssl rand -hex 32)\" and keep it for later upgrades." }}
    {{- end }}
    {{- if has $qdrantApiKey (list "api_key" "your_qdrant_api_key" "your_qdrant_secret_api_key" "qdrant") }}
      {{- fail (printf "qdrant.apiKey must not use default placeholder '%s'. Set a secure key via --set qdrant.apiKey=\"$(openssl rand -hex 32)\"; see \"Qdrant API key\" in the chart README." $qdrantApiKey) }}
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
Validate that SANDBOX_MODE=docker actually has a daemon to reach, and that
SANDBOX_MODE=local carries the dev flag the service would otherwise refuse it
without.

Without one, `run_code` fails at provision with an opaque Docker error in the
middle of a user's conversation. Failing here instead makes the operator
choose how code execution is isolated, at install time, with the options in
front of them.

Also refuses a Docker socket mounted into the application container: that is
the raw daemon API with no policy in between, i.e. root on the node.
*/}}
{{- define "pipeshub-ai.validateSandbox" -}}
{{- if and (eq (include "pipeshub-ai.sandboxMode" .) "local") (ne (include "pipeshub-ai.sandboxAllowLocal" .) "true") }}
  {{- fail "config.sandboxMode is \"local\", which runs generated code as a subprocess of this pod with no container isolation, and the service refuses it unless config.sandboxAllowLocal=true. Set that only on a single-tenant development cluster; otherwise use docker or e2b." }}
{{- end }}
{{- $socketError := "mounts a Docker socket into the application container, which gives it, and any code it runs, root on the node. Use --set sandbox.dind.enabled=true (the app then reaches the daemon only through the docker-proxy policy sidecar) or --set config.sandboxMode=e2b." }}
{{- range $mounts := list .Values.extraVolumeMounts .Values.volumeMounts }}
  {{- range $mounts }}
    {{- if kindIs "map" . }}
      {{- $named := list (.mountPath | default "") (.subPath | default "") (.subPathExpr | default "") }}
      {{- if or (eq (toString (.name | default "")) "dind-sock") (include "pipeshub-ai.namesRuntimeSocket" $named) }}
        {{- fail (printf "extraVolumeMounts/volumeMounts %s" $socketError) }}
      {{- end }}
    {{- end }}
  {{- end }}
{{- end }}
{{- range $volumes := list .Values.extraVolumes .Values.volumes }}
  {{- range $volumes }}
    {{- if and (kindIs "map" .) (kindIs "map" .hostPath) }}
      {{- if or (eq (toString (.hostPath.type | default "")) "Socket") (include "pipeshub-ai.hostPathReachesRuntimeSocket" (toString (.hostPath.path | default ""))) }}
        {{- fail (printf "extraVolumes/volumes %s" $socketError) }}
      {{- end }}
    {{- end }}
  {{- end }}
{{- end }}
{{- if eq (include "pipeshub-ai.sandboxMode" .) "docker" }}
  {{- if not (or .Values.sandbox.dind.enabled .Values.config.dockerHost) }}
    {{- fail "config.sandboxMode is \"docker\" but no Docker daemon is configured, so run_code would fail at runtime. Pick one: (a) --set sandbox.dind.enabled=true to run a Docker-in-Docker sidecar behind the docker-proxy policy sidecar (needs a PRIVILEGED container - see sandbox.dind in values.yaml); (b) --set config.dockerHost=tcp://<host>:2375 to use a daemon you run, behind the same policy proxy (python -m app.docker_proxy_main); (c) --set config.sandboxMode=e2b with an E2B_API_KEY to execute off-cluster; or (d) --set config.sandboxMode=local --set config.sandboxAllowLocal=true to run generated code as a subprocess of this pod - NO container isolation, acceptable only for single-tenant development clusters." }}
  {{- end }}
{{- end }}
{{- end }}

{{/*
Container-runtime sockets on a node. Any of them is as good as the Docker API:
whoever can write to it can start a privileged container. Each is listed once
under /run and expanded to the /var/run spelling as well, because /var/run is
usually a symlink to /run and a hostPath under either reaches the same socket.
*/}}
{{- define "pipeshub-ai.runtimeSocketPaths" -}}
{{- $paths := list -}}
{{- range list "/run/docker.sock" "/run/containerd/containerd.sock" "/run/k3s/containerd/containerd.sock" "/run/crio/crio.sock" "/run/podman/podman.sock" "/run/dockershim.sock" "/run/cri-dockerd.sock" -}}
  {{- $paths = append (append $paths .) (printf "/var%s" .) -}}
{{- end -}}
{{- $paths | toJson -}}
{{- end -}}

{{/*
"true" when any of the given strings names a runtime socket file. Used on mount
paths and subPaths, where only the file name is visible.
*/}}
{{- define "pipeshub-ai.namesRuntimeSocket" -}}
{{- $hit := false -}}
{{- $sockets := include "pipeshub-ai.runtimeSocketPaths" . | fromJsonArray -}}
{{- range $value := . -}}
  {{- range $socket := $sockets -}}
    {{- if and $value (contains (base $socket) (toString $value)) -}}
      {{- $hit = true -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{- if $hit }}true{{ end -}}
{{- end -}}

{{/*
"true" when a hostPath is a runtime socket or a directory that holds one, so /,
/run and /var/run are caught as well as the socket file itself.
*/}}
{{- define "pipeshub-ai.hostPathReachesRuntimeSocket" -}}
{{- $hit := false -}}
{{- if . -}}
  {{- $path := clean . -}}
  {{- range $socket := include "pipeshub-ai.runtimeSocketPaths" . | fromJsonArray -}}
    {{- if or (eq $path "/") (eq $path $socket) (hasPrefix (printf "%s/" $path) $socket) (contains (base $socket) $path) -}}
      {{- $hit = true -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{- if $hit }}true{{ end -}}
{{- end -}}

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
Canonical `config.sandboxAllowLocal`: "true" or "false".

Parsed the way the service parses SANDBOX_ALLOW_LOCAL (1/true/yes/on, any
case), so the install check and the rendered env var cannot disagree. A plain
truthiness test let `--set-string config.sandboxAllowLocal=false` (a
non-empty string) past the check, and the service then refused local anyway.
*/}}
{{- define "pipeshub-ai.sandboxAllowLocal" -}}
{{- $raw := .Values.config.sandboxAllowLocal | default false | toString | trim | lower -}}
{{- ternary "true" "false" (has $raw (list "1" "true" "yes" "on")) -}}
{{- end -}}

{{/*
Effective DOCKER_HOST, or "" when no daemon is configured.

Single source for the precedence so the deployment's env var and the NOTES
description cannot disagree: an explicitly configured daemon wins, otherwise
the docker-proxy sidecar's loopback address if DinD is being deployed. The
proxy binds 127.0.0.1 only, so "localhost" resolving to ::1 first would miss.
*/}}
{{- define "pipeshub-ai.dockerHost" -}}
{{- if .Values.config.dockerHost -}}
{{- .Values.config.dockerHost -}}
{{- else if .Values.sandbox.dind.enabled -}}
{{- printf "tcp://127.0.0.1:%v" .Values.sandbox.dind.port -}}
{{- end -}}
{{- end -}}

{{/*
How run_code actually executes: local | e2b | dind | external.

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
{{- else -}}
dind
{{- end -}}
{{- end -}}

{{/*
Validate the Redis wiring before anything renders.

external.enabled needs either host (standalone, for example ElastiCache with
cluster mode disabled) or clusterEndpoints (Redis Cluster). Mixing them, or
pairing host with a cluster mode, used to render REDIS_HOST empty while
REDIS_MODE=standalone, and the client then connected to localhost.
*/}}
{{- define "pipeshub-ai.validateRedis" -}}
{{- if and .Values.redis.builtin.enabled .Values.redis.external.enabled -}}
  {{- fail "redis.builtin.enabled and redis.external.enabled cannot both be true." -}}
{{- end -}}
{{- $external := .Values.redis.external -}}
{{- $redisMode := .Values.redis.mode | default "standalone" -}}
{{- if $external.enabled -}}
  {{- if and $external.host $external.clusterEndpoints -}}
    {{- fail "redis.external.host and redis.external.clusterEndpoints cannot both be set. Use host for a standalone Redis (ElastiCache with cluster mode disabled) or clusterEndpoints for Redis Cluster." -}}
  {{- end -}}
  {{- if and $external.host (ne $redisMode "standalone") -}}
    {{- fail "redis.external.host requires redis.mode=standalone. A cluster-mode client ignores the host and needs redis.external.clusterEndpoints." -}}
  {{- end -}}
  {{- if and (not $external.host) (not $external.clusterEndpoints) -}}
    {{- fail "redis.external.enabled=true requires redis.external.host (standalone) or redis.external.clusterEndpoints (comma-separated host:port list)." -}}
  {{- end -}}
  {{- if and $external.clusterEndpoints (eq $redisMode "standalone") -}}
    {{- fail "redis.external.enabled=true requires a cluster-capable redis.mode (set redis.mode=cluster, or an EE mode supplied via redis.providerModule). REDIS_MODE=standalone ignores redis.external.clusterEndpoints and would connect to localhost." -}}
  {{- end -}}
  {{- if .Values.redis.enabled -}}
    {{- fail "redis.external.enabled=true also requires redis.enabled=false, otherwise the bundled Redis subchart is deployed and left unused." -}}
  {{- end -}}
{{- else if and (not .Values.redis.enabled) .Values.redis.builtin.enabled -}}
  {{- if ne (.Values.redis.mode | default "standalone") "standalone" -}}
    {{- fail "redis.builtin is a replication deployment, not a Redis Cluster. Use redis.mode=standalone with it. Redis Cluster still requires redis.external.enabled=true and redis.external.clusterEndpoints." -}}
  {{- end -}}
{{- else if and .Values.redis.enabled .Values.redis.builtin.enabled -}}
  {{- fail "redis.enabled and redis.builtin.enabled cannot both be true. Use redis.enabled=true for the Bitnami subchart or redis.enabled=false with redis.builtin.enabled=true for the chart-owned master and replica." -}}
{{- else if not .Values.redis.enabled -}}
  {{- fail "redis.enabled=false requires redis.external.enabled=true with redis.external.host or redis.external.clusterEndpoints, or redis.builtin.enabled=true; otherwise nothing provides Redis." -}}
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

{{/*
Validate that each store the app is told to use is actually deployed.

The services default DATA_STORE to arangodb and read KV_STORE_TYPE as given,
so a missing graph database or an etcd store with no etcd renders cleanly and
only fails once the pod is crash-looping. An ExternalSecret with no secretKey
mapping likewise renders, and the pod then waits forever on a key that the
synced Secret never gets.
*/}}
{{- define "pipeshub-ai.validateStores" -}}
{{- if and (not .Values.neo4j.enabled) (not .Values.arango.enabled) -}}
  {{- fail "No graph database is enabled. Set neo4j.enabled=true (default) or arango.enabled=true." -}}
{{- end -}}
{{- if and (eq (.Values.config.kvStoreType | default "redis") "etcd") (not .Values.etcd.enabled) -}}
  {{- fail "config.kvStoreType=etcd requires etcd.enabled=true; otherwise the app has no etcd to connect to." -}}
{{- end -}}
{{- if and .Values.neo4j.enabled (gt (int .Values.neo4j.replicaCount) 1) (not (contains "enterprise" (.Values.neo4j.image.tag | toString))) -}}
  {{- fail "neo4j.replicaCount > 1 requires an Enterprise image (image.tag must contain \"enterprise\"). Neo4j Community has no clustering; extra replicas are separate databases and split writes. Keep replicaCount at 1, or switch neo4j.image.tag to an enterprise build you are licensed for." -}}
{{- end -}}
{{- if and .Values.mongodb.enabled .Values.mongodb.builtin.enabled -}}
  {{- fail "mongodb.enabled and mongodb.builtin.enabled cannot both be true. Use mongodb.enabled=true for the Bitnami subchart (existing installs) or mongodb.enabled=false and mongodb.builtin.enabled=true for the chart-owned replica set (new installs)." -}}
{{- end -}}
{{- if and (not .Values.mongodb.enabled) (not .Values.mongodb.builtin.enabled) -}}
  {{- fail "MongoDB is not deployed. Set mongodb.enabled=true (Bitnami subchart) or mongodb.builtin.enabled=true (chart-owned replica set)." -}}
{{- end -}}
{{- if and .Values.secretManagement.externalSecrets.enabled (not .Values.secretManagement.externalSecrets.remoteRefs.secretKey) -}}
  {{- fail "secretManagement.externalSecrets.remoteRefs.secretKey is required when externalSecrets is enabled; the app cannot start without secret-key." -}}
{{- end -}}
{{- end -}}

{{/*
Most app replicas this release can run: replicaCount, or the autoscaler's
ceiling when autoscaling is on.
*/}}
{{- define "pipeshub-ai.maxReplicas" -}}
{{- $replicas := .Values.replicaCount | int -}}
{{- if .Values.autoscaling.enabled -}}
{{- $replicas = max $replicas (.Values.autoscaling.maxReplicas | int) -}}
{{- end -}}
{{- $replicas -}}
{{- end -}}

{{/*
The environment every application-image container of this release shares (the
main app pod and the parsing pod): ports, stores, queues, secrets. Lines carry
their final indentation (12 spaces), so include it directly after `env:` on the same line. The caller
adds what is specific to its role after it.
*/}}
{{- define "pipeshub-ai.appEnv" }}
            # Core environment settings
            - name: PORT
              value: {{ .Values.config.backendPort | quote }}
            # Most app pods this release may run; the app refuses local blob
            # storage above one (each pod would keep its own files).
            - name: PIPESHUB_APP_MAX_REPLICAS
              value: {{ include "pipeshub-ai.maxReplicas" . | quote }}
            - name: NODEJS_PORT
              value: {{ .Values.config.backendPort | quote }}
            - name: NODE_ENV
              value: {{ .Values.config.nodeEnv | quote }}
            - name: LOG_LEVEL
              value: {{ .Values.config.logLevel | quote }}
            - name: LOG_FORMAT
              value: {{ .Values.config.logFormat | default "text" | quote }}
            - name: HIDE_SECRET_CONFIG
              value: {{ .Values.config.hideSecretConfig | quote }}
{{- if ne (toString .Values.telemetry.collection.enabled) "" }}
            - name: TELEMETRY_COLLECTION_FLAG
              value: {{ .Values.telemetry.collection.enabled | quote }}
{{- end }}
            {{- if ne (toString .Values.config.maxConcurrentParsing) "" }}
            - name: MAX_CONCURRENT_PARSING
              value: {{ .Values.config.maxConcurrentParsing | quote }}
            {{- end }}
            {{- if ne (toString .Values.config.maxConcurrentIndexing) "" }}
            - name: MAX_CONCURRENT_INDEXING
              value: {{ .Values.config.maxConcurrentIndexing | quote }}
            {{- end }}
            {{- if ne (toString .Values.config.maxPendingIndexingTasks) "" }}
            - name: MAX_PENDING_INDEXING_TASKS
              value: {{ .Values.config.maxPendingIndexingTasks | quote }}
            {{- end }}
            {{- if ne (toString .Values.config.queryUvicornWorkers) "" }}
            - name: QUERY_UVICORN_WORKERS
              value: {{ .Values.config.queryUvicornWorkers | quote }}
            {{- end }}
            - name: PIPESHUB_AGENT_TRANSPORT
              value: {{ .Values.config.agentTransport | quote }}
            - name: PIPESHUB_ACCESSIBLE_RECORDS_CACHE
              value: {{ .Values.config.accessibleRecordsCache | quote }}
            - name: PIPESHUB_ACCESSIBLE_RECORDS_CACHE_TTL
              value: {{ .Values.config.accessibleRecordsCacheTtl | quote }}
            - name: PIPESHUB_SIGNED_URL_CACHE_SECONDS
              value: {{ .Values.config.signedUrlCacheSeconds | quote }}
            - name: PIPESHUB_AGENT_MAX_TURN_OUTPUT_CHARS
              value: {{ .Values.config.agentMaxTurnOutputChars | quote }}
            - name: HIGH_ACCURACY_MODE
              value: {{ .Values.config.highAccuracyMode | quote }}
            - name: DISTRIBUTED_INDEXING_CONCURRENCY
              value: {{ .Values.config.distributedIndexingConcurrency | quote }}
            - name: MCP_ALLOW_CUSTOM_STDIO
              value: {{ .Values.config.mcpAllowCustomStdio | toString | lower | quote }}
            # Coding sandbox. The service has no default: unset, unknown, or
            # "local" without SANDBOX_ALLOW_LOCAL drops the code tools.
            - name: SANDBOX_MODE
              value: {{ include "pipeshub-ai.sandboxMode" . | quote }}
            - name: SANDBOX_ALLOW_LOCAL
              value: {{ include "pipeshub-ai.sandboxAllowLocal" . | quote }}
            - name: SANDBOX_DOCKER_IMAGE
              value: {{ .Values.config.sandboxDockerImage | quote }}
            - name: SANDBOX_ALLOW_NETWORK
              value: {{ .Values.config.sandboxAllowNetwork | quote }}
            - name: SANDBOX_EGRESS_NETWORK
              value: {{ .Values.config.sandboxEgressNetwork | quote }}
            - name: SANDBOX_EGRESS_ALLOW_CIDRS
              value: {{ .Values.config.sandboxEgressAllowCidrs | default "" | quote }}
            - name: SANDBOX_DISK_QUOTA
              value: {{ .Values.config.sandboxDiskQuota | quote }}
            {{- with (include "pipeshub-ai.dockerHost" .) }}
            - name: DOCKER_HOST
              value: {{ . | quote }}
            {{- end }}
            {{- with .Values.extraEnv }}
            {{- tpl (toYaml .) $ | nindent 12 }}
            {{- end }}
            {{- if .Values.s3SignatureV4Preload }}
            - name: NODE_OPTIONS
              value: "--require /etc/pipeshub/s3-sigv4/force-sigv4.js"
            {{- end }}
            # What this pod asked for. Limits reach the process as the cgroup; these two let the
            # capacity plan size a pod that sets requests only. Absent requests read as 0 (ignored).
            - name: PIPESHUB_CPU_REQUEST_MILLI
              valueFrom:
                resourceFieldRef:
                  resource: requests.cpu
                  divisor: 1m
            - name: PIPESHUB_MEMORY_REQUEST
              valueFrom:
                resourceFieldRef:
                  resource: requests.memory
            # Resource-governor tuning. Every one defaults to "" and is left out of the
            # environment when empty, so the
            # governor derives it from the pod's CPU/memory limits; set one
            # only to pin it. Mirrors the same list in docker-compose.yml.
            {{- if ne (toString .Values.config.governorHeavyParseSlotsPerCpu) "" }}
            - name: GOVERNOR_HEAVY_PARSE_SLOTS_PER_CPU
              value: {{ .Values.config.governorHeavyParseSlotsPerCpu | quote }}
            {{- end }}
            {{- if ne (toString .Values.config.governorLightParseSlotsPerCpu) "" }}
            - name: GOVERNOR_LIGHT_PARSE_SLOTS_PER_CPU
              value: {{ .Values.config.governorLightParseSlotsPerCpu | quote }}
            {{- end }}
            {{- if ne (toString .Values.config.governorLightParseMax) "" }}
            - name: GOVERNOR_LIGHT_PARSE_MAX
              value: {{ .Values.config.governorLightParseMax | quote }}
            {{- end }}
            - name: PARSE_MAX_ORG_SHARE
              value: {{ .Values.config.parseMaxOrgShare | quote }}
            - name: CODE_EDGE_BUILD_RECONCILE_INTERVAL_SECONDS
              value: {{ .Values.config.codeEdgeBuildReconcileIntervalSeconds | quote }}
            - name: CODE_EDGE_FULL_REBUILD_INTERVAL_SECONDS
              value: {{ .Values.config.codeEdgeFullRebuildIntervalSeconds | quote }}
            - name: CODE_EDGE_BUILD_MAX_CONCURRENT
              value: {{ .Values.config.codeEdgeBuildMaxConcurrent | quote }}
            - name: CODE_EDGE_BUILD_MAX_PER_ORG
              value: {{ .Values.config.codeEdgeBuildMaxPerOrg | quote }}
            - name: CODE_EDGE_BUILD_MAX_BLOCKS
              value: {{ .Values.config.codeEdgeBuildMaxBlocks | quote }}
            - name: CODE_EDGE_BUILD_MAX_FAILURES
              value: {{ .Values.config.codeEdgeBuildMaxFailures | quote }}
            - name: CODE_GRAPH_ENABLED
              value: {{ .Values.config.codeGraphEnabled | quote }}
            - name: CODE_GRAPH_BACKFILL
              value: {{ .Values.config.codeGraphBackfill | quote }}
            {{- if ne (toString .Values.config.governorIndexHeadroom) "" }}
            - name: GOVERNOR_INDEX_HEADROOM
              value: {{ .Values.config.governorIndexHeadroom | quote }}
            {{- end }}
            {{- if ne (toString .Values.config.governorIndexMax) "" }}
            - name: GOVERNOR_INDEX_MAX
              value: {{ .Values.config.governorIndexMax | quote }}
            {{- end }}
            {{- if ne (toString .Values.config.governorIndexHeavyWorkingSetGb) "" }}
            - name: GOVERNOR_INDEX_HEAVY_WORKING_SET_GB
              value: {{ .Values.config.governorIndexHeavyWorkingSetGb | quote }}
            {{- end }}
            {{- if ne (toString .Values.config.governorIndexLightWorkingSetGb) "" }}
            - name: GOVERNOR_INDEX_LIGHT_WORKING_SET_GB
              value: {{ .Values.config.governorIndexLightWorkingSetGb | quote }}
            {{- end }}
            {{- if ne (toString .Values.config.governorHeavyParseWorkingSetGb) "" }}
            - name: GOVERNOR_HEAVY_PARSE_WORKING_SET_GB
              value: {{ .Values.config.governorHeavyParseWorkingSetGb | quote }}
            {{- end }}
            {{- if ne (toString .Values.config.governorBaselineMemoryMb) "" }}
            - name: GOVERNOR_BASELINE_MEMORY_MB
              value: {{ .Values.config.governorBaselineMemoryMb | quote }}
            {{- end }}
            {{- if ne (toString .Values.config.parsePoolWorkers) "" }}
            - name: PARSE_POOL_WORKERS
              value: {{ .Values.config.parsePoolWorkers | quote }}
            {{- end }}
            {{- if ne (toString .Values.config.embeddingServerMaxConcurrency) "" }}
            - name: EMBEDDING_SERVER_MAX_CONCURRENCY
              value: {{ .Values.config.embeddingServerMaxConcurrency | quote }}
            {{- end }}
            {{- if ne (toString .Values.config.embeddingServerQueryReservedSlots) "" }}
            - name: EMBEDDING_SERVER_QUERY_RESERVED_SLOTS
              value: {{ .Values.config.embeddingServerQueryReservedSlots | quote }}
            {{- end }}
            {{- if ne (toString .Values.config.embeddingBatchConcurrency) "" }}
            - name: EMBEDDING_BATCH_CONCURRENCY
              value: {{ .Values.config.embeddingBatchConcurrency | quote }}
            {{- end }}
            {{- if ne (toString .Values.config.maxConcurrentIndexingLlmCalls) "" }}
            - name: MAX_CONCURRENT_INDEXING_LLM_CALLS
              value: {{ .Values.config.maxConcurrentIndexingLlmCalls | quote }}
            {{- end }}
            {{- if ne (toString .Values.config.cpuBudget) "" }}
            - name: PIPESHUB_CPU_BUDGET
              value: {{ .Values.config.cpuBudget | quote }}
            {{- end }}
            {{- if ne (toString .Values.config.memoryBudget) "" }}
            - name: PIPESHUB_MEMORY_BUDGET
              value: {{ .Values.config.memoryBudget | quote }}
            {{- end }}
            {{- if ne (toString .Values.config.capacityProfile) "" }}
            - name: PIPESHUB_CAPACITY_PROFILE
              value: {{ .Values.config.capacityProfile | quote }}
            {{- end }}
            {{- if ne (toString .Values.config.capacityCellCpus) "" }}
            - name: PIPESHUB_CAPACITY_CELL_CPUS
              value: {{ .Values.config.capacityCellCpus | quote }}
            {{- end }}
            {{- if ne (toString .Values.config.cpuOversub) "" }}
            - name: PIPESHUB_CPU_OVERSUB
              value: {{ .Values.config.cpuOversub | quote }}
            {{- end }}
            - name: INDEXING_SPLIT_LEASE_POOLS
              value: {{ .Values.config.indexingSplitLeasePools | quote }}
            - name: INDEXING_CONCURRENCY_KEY_PREFIX
              value: {{ printf "%s:indexing:concurrency" (include "pipeshub-ai.fullname" .) | quote }}
            - name: INDEXING_CONCURRENCY_LEASE_SECONDS
              value: {{ .Values.config.indexingConcurrencyLeaseSeconds | quote }}
            - name: INDEXING_CONCURRENCY_REDIS_TIMEOUT_SECONDS
              value: {{ .Values.config.indexingConcurrencyRedisTimeoutSeconds | quote }}
            - name: INDEXING_CONCURRENCY_REDIS_MAX_CONNECTIONS
              value: {{ .Values.config.indexingConcurrencyRedisMaxConnections | quote }}
            - name: SHUTDOWN_TASK_TIMEOUT
              value: {{ .Values.config.shutdownTaskTimeout | quote }}
            - name: STALE_INDEXING_RECOVERY_STARTUP_GRACE_SECONDS
              value: {{ .Values.config.staleIndexingRecoveryStartupGraceSeconds | quote }}
            - name: STALE_INDEXING_RECOVERY_INTERVAL_SECONDS
              value: {{ .Values.config.staleIndexingRecoveryIntervalSeconds | quote }}
            - name: KV_STORE_TYPE
              value: {{ .Values.config.kvStoreType | quote }}
            - name: ALLOWED_ORIGINS
              value: {{ .Values.config.allowedOrigins | quote }}
            - name: PIPESHUB_BLOCK_PRIVATE_ADDRESSES
              value: {{ .Values.config.blockPrivateAddresses | default "" | quote }}
            - name: PIPESHUB_NEXTCLOUD_ALLOW_INSECURE_HTTP
              value: {{ .Values.config.nextcloudAllowInsecureHttp | default "" | quote }}
            - name: SAML_SP_ENTITY_ID
              value: {{ .Values.config.samlSpEntityId | default .Values.config.frontendPublicUrl | default "pipeshub" | quote }}
            
            # Security settings
            - name: SECRET_KEY
              valueFrom:
                secretKeyRef:
                  name: {{ include "pipeshub-ai.secretName" . }}
                  key: secret-key
            
            # Public endpoints
            - name: CONNECTOR_PUBLIC_BACKEND
              value: {{ .Values.config.connectorPublicBackend | quote }}
            - name: FRONTEND_PUBLIC_URL
              value: {{ .Values.config.frontendPublicUrl | quote }}
            
            # Internal service URLs
            - name: QUERY_BACKEND
              value: "http://localhost:8000"
            - name: CONNECTOR_BACKEND
              value: "http://localhost:8088"
            - name: INDEXING_BACKEND
              value: "http://localhost:8091"
            - name: EMBEDDING_SERVER_URL
              value: "http://localhost:8002"
            
            {{- if .Values.etcd.enabled }}
            # ETCD config
            - name: ETCD_URL
              value: "http://{{ include "pipeshub-ai.fullname" . }}-etcd:2379"
            - name: ETCD_HOST
              value: "{{ include "pipeshub-ai.fullname" . }}-etcd"
            {{- end }}
            
            {{- if .Values.arango.enabled }}
            # ArangoDB config
            - name: ARANGO_URL
              value: "http://{{ include "pipeshub-ai.fullname" . }}-arango:8529"
            - name: ARANGO_DB_NAME
              value: "es"
            - name: ARANGO_USERNAME
              value: "root"
            - name: ARANGO_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: {{ include "pipeshub-ai.arangoSecretName" . }}
                  key: arango-password
            {{- end }}
            {{- if and .Values.arango.enabled (not .Values.neo4j.enabled) }}
            - name: DATA_STORE
              value: "arangodb"
            {{- end }}
            {{- if .Values.neo4j.enabled }}
            # Neo4j config (app uses Neo4j as graph DB when DATA_STORE=neo4j)
            - name: DATA_STORE
              value: "neo4j"
            - name: NEO4J_URI
              value: "bolt://{{ include "pipeshub-ai.fullname" . }}-neo4j:7687"
            - name: NEO4J_USERNAME
              value: {{ .Values.neo4j.auth.username | quote }}
            - name: NEO4J_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: {{ include "pipeshub-ai.neo4jSecretName" . }}
                  key: neo4j-password
            - name: NEO4J_DATABASE
              value: {{ .Values.neo4j.auth.database | quote }}
            {{- end }}
            
            # Message broker config
            - name: MESSAGE_BROKER
              value: {{ .Values.messageBroker.type | default "redis" | quote }}
            - name: REDIS_STREAMS_MAXLEN
              value: {{ .Values.messageBroker.redisStreamsMaxLen | default "500000" | quote }}
            - name: REDIS_STREAMS_RETENTION_MODE
              value: {{ .Values.messageBroker.redisStreamsRetentionMode | default "after_ack" | quote }}
            - name: FAIR_SCHEDULING_LANE_COUNT
              value: {{ .Values.messageBroker.laneCount | default "8" | quote }}
            - name: FAIR_SCHEDULING_LANE_ASSIGNMENT
              value: {{ .Values.messageBroker.laneAssignment | default "assigned" | quote }}

            {{- if eq .Values.messageBroker.type "kafka" }}
            # Kafka config
            - name: KAFKA_BROKERS
              value: "{{ include "pipeshub-ai.fullname" . }}-kafka:9092"
            {{- end }}
            
            # Redis config
            {{- /*
              Connection target:
                - in-cluster Redis (Bitnami or chart-owned): <release>-redis-master. That Service
                  follows the current master, so the app does not need Sentinel.
                - redis.external.host: a standalone primary (ElastiCache cluster mode disabled).
                  Its DNS name follows a failover. redis.mode stays standalone, so Celery uses
                  the same endpoint.
                - redis.external.clusterEndpoints: Redis Cluster or MemoryDB. redis.mode=cluster,
                  and Celery needs celery.brokerUrl because it cannot speak cluster protocol.
            */}}
            {{- include "pipeshub-ai.validateRedis" . }}
            {{- $ext := .Values.redis.external }}
            {{- $redis := dict "host" (printf "%s-redis-master" .Release.Name) "port" "6379" "scheme" "redis" "target" "" }}
            {{- if and $ext.enabled $ext.host }}
            {{- $_ := set $redis "host" ($ext.host | toString) }}
            {{- $_ := set $redis "port" ($ext.port | default 6379 | toString) }}
            {{- else if $ext.enabled }}
            {{- $_ := set $redis "host" "" }}
            {{- end }}
            {{- if and $ext.enabled .Values.redis.clientTls.enabled }}
            {{- $_ := set $redis "scheme" "rediss" }}
            {{- end }}
            {{- $_ := set $redis "target" (printf "%s:%s" (get $redis "host") (get $redis "port")) }}
            {{- if and $ext.enabled (not $ext.host) }}
            {{- $_ := set $redis "target" (trim (index (splitList "," (printf "%s," ($ext.clusterEndpoints | toString))) 0)) }}
            {{- end }}
            {{- if and $ext.enabled $ext.host }}
            - name: REDIS_HOST
              value: {{ get $redis "host" | quote }}
            - name: REDIS_PORT
              value: {{ get $redis "port" | quote }}
            {{- else if $ext.enabled }}
            - name: REDIS_HOST
              value: ""
            - name: REDIS_PORT
              value: "6379"
            - name: REDIS_CLUSTER_ENDPOINTS
              value: {{ $ext.clusterEndpoints | quote }}
            - name: REDIS_CLUSTER_NAT_MAP
              value: {{ $ext.natMap | default "" | quote }}
            {{- else }}
            - name: REDIS_HOST
              value: {{ get $redis "host" | quote }}
            - name: REDIS_PORT
              value: "6379"
            {{- end }}
            {{- if .Values.redis.auth.enabled }}
            - name: REDIS_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: {{ include "pipeshub-ai.redisSecretName" . }}
                  key: redis-password
            - name: REDIS_URL
              value: "{{ get $redis "scheme" }}://:$(REDIS_PASSWORD)@{{ get $redis "target" }}"
            {{- else }}
            - name: REDIS_PASSWORD
              value: ""
            - name: REDIS_URL
              value: "{{ get $redis "scheme" }}://{{ get $redis "target" }}"
            {{- end }}
            - name: REDIS_MODE
              value: {{ .Values.redis.mode | default "standalone" | quote }}
            - name: REDIS_KEY_NAMESPACE
              value: {{ .Values.redis.keyNamespace | default "" | quote }}
            - name: REDIS_CLUSTER_SCALE_READS
              value: {{ .Values.redis.clusterScaleReads | default "master" | quote }}
            - name: REDIS_PROVIDER_MODULE
              value: {{ .Values.redis.providerModule | default "" | quote }}
            - name: REDIS_TLS_ENABLED
              value: {{ .Values.redis.clientTls.enabled | default false | quote }}
            - name: REDIS_TLS_REJECT_UNAUTHORIZED
              value: {{ .Values.redis.clientTls.rejectUnauthorized | default true | quote }}
            - name: REDIS_TLS_CA_PATH
              value: {{ .Values.redis.clientTls.caPath | default "" | quote }}
            # Celery has no Redis Cluster transport; required when redis.mode
            # is not "standalone" (fails fast at startup otherwise).
            - name: CELERY_BROKER_URL
              value: {{ .Values.celery.brokerUrl | default "" | quote }}
            - name: CELERY_RESULT_BACKEND
              value: {{ .Values.celery.resultBackend | default "" | quote }}
            
            # MongoDB config
            {{- $isReplicaSet := eq (.Values.mongodb.architecture | default "standalone") "replicaset" }}
            {{- $mongoHost := ternary (printf "%s-mongodb-headless" .Release.Name) (printf "%s-mongodb" .Release.Name) $isReplicaSet }}
            {{- $mongoRsParam := ternary (printf "&replicaSet=%s" (.Values.mongodb.replicaSetName | default "rs0")) "" $isReplicaSet }}
            {{- if .Values.mongodb.auth.enabled }}
            - name: MONGO_USERNAME
              valueFrom:
                secretKeyRef:
                  name: {{ include "pipeshub-ai.mongodbSecretName" . }}
                  key: mongodb-username
            - name: MONGO_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: {{ include "pipeshub-ai.mongodbSecretName" . }}
                  key: mongodb-password
            - name: MONGO_URI
              value: "mongodb://$(MONGO_USERNAME):$(MONGO_PASSWORD)@{{ $mongoHost }}:27017/?authSource=admin{{ $mongoRsParam }}"
            {{- else }}
            - name: MONGO_URI
              value: "mongodb://{{ $mongoHost }}:27017/{{ if $mongoRsParam }}?{{ trimPrefix "&" $mongoRsParam }}{{ end }}"
            {{- end }}
            - name: MONGO_DB_NAME
              value: "es"
            # Single source of truth: tied to mongodb.architecture so this env var
            # cannot drift from the rendered MONGO_URI. Overriding architecture
            # alone (e.g. --set mongodb.architecture=standalone) used to leave
            # REPLICA_SET_AVAILABLE=true and crash the API at first transaction.
            - name: REPLICA_SET_AVAILABLE
              value: {{ ternary "true" "false" $isReplicaSet | quote }}
            
            # Qdrant config
            - name: QDRANT_API_KEY
              valueFrom:
                secretKeyRef:
                  name: {{ include "pipeshub-ai.qdrantSecretName" . }}
                  key: qdrant-api-key
            - name: QDRANT_HOST
              value: "{{ include "pipeshub-ai.fullname" . }}-qdrant"
            - name: QDRANT_GRPC_PORT
              value: "6334"
            - name: QDRANT_PORT
              value: "6333"
            {{- with (((.Values.qdrant).resources).limits).memory }}
            # Qdrant cannot see its own memory limit; the app compares the search
            # index's size with it (docs/deployment/vector-db-sizing.md).
            - name: QDRANT_MEMORY_LIMIT
              value: {{ . | quote }}
            {{- end }}

            # Vector DB collection layout. Only read on the first startup that
            # finds nothing persisted; from then on the stored value wins, and a
            # value here that contradicts it fails startup — points already sit
            # in collections a different strategy would not resolve.
            - name: VECTOR_COLLECTION_STRATEGY
              value: {{ .Values.vectorDb.collectionStrategy | default "single" | quote }}

            - name: HOME
              value: /home/user
            # Chromium is installed into /root during the image build. HOME is
            # /home/user, and that path is an emptyDir, so Playwright would
            # look for a browser that is not there.
            - name: PLAYWRIGHT_BROWSERS_PATH
              value: /root/.cache/ms-playwright

            {{- $trustProxy := .Values.trustProxy | default (ternary "1" "" .Values.ingress.enabled) }}
            {{- if $trustProxy }}
            - name: TRUST_PROXY
              value: {{ $trustProxy | quote }}
            {{- end }}
            - name: MAX_AUTH_REQUESTS_PER_MINUTE
              value: {{ .Values.maxAuthRequestsPerMinute | default 10 | quote }}
            - name: ADDRESS_RATE_LIMIT_PER_MINUTE
              value: {{ .Values.addressRateLimitPerMinute | default 60 | quote }}
{{- end }}
