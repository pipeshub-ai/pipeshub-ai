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
Every image the chart renders goes through pipeshub-ai.imageRef, so
global.imageRegistry alone mirrors all of them (ADR-OPS-01 D9). A reference
whose first path segment names a registry (has a "." or ":", or is
"localhost") keeps it; otherwise the registry is global.imageRegistry, else
the image's own `registry` value, else none (Docker Hub). The path is kept, so
a mirror holds quay.io/coreos/etcd as {mirror}/coreos/etcd.
Usage: include "pipeshub-ai.imageRef" (dict "ref" "repo/name:tag" "global" .Values.global)
       include "pipeshub-ai.image" (dict "image" .Values.qdrant.image "global" .Values.global)
*/}}
{{- define "pipeshub-ai.imageRef" -}}
{{- $ref := .ref | toString -}}
{{- $parts := splitList "/" $ref -}}
{{- $first := first $parts -}}
{{- $named := and (gt (len $parts) 1) (or (contains "." $first) (contains ":" $first) (eq $first "localhost")) -}}
{{- $registry := .global.imageRegistry | default .registry | default "" -}}
{{- if and $registry (not $named) -}}
{{- printf "%s/%s" (trimSuffix "/" $registry) $ref -}}
{{- else -}}
{{- $ref -}}
{{- end -}}
{{- end }}

{{/*
An image block's reference: repository, then ":tag" (else defaultTag), then
"@digest". A digest makes the reference immutable whatever the tag says, so a
re-pushed tag cannot change what a pod runs (SEC-D-04); the tag stays for
people reading the manifest.
*/}}
{{- define "pipeshub-ai.image" -}}
{{- $tag := .image.tag | default .defaultTag | default "" | toString -}}
{{- $digest := .image.digest | default "" | toString -}}
{{- if and $digest (not (regexMatch "^sha256:[0-9a-f]{64}$" $digest)) -}}
{{- fail (printf "%s: image digest %q is not sha256:<64 hex>. Copy the digest from the release notes." .image.repository $digest) -}}
{{- end -}}
{{- if not (or $tag $digest) -}}
{{- fail (printf "%s: set the image's tag or digest." .image.repository) -}}
{{- end -}}
{{- $ref := .image.repository -}}
{{- if $tag -}}{{- $ref = printf "%s:%s" $ref $tag -}}{{- end -}}
{{- if $digest -}}{{- $ref = printf "%s@%s" $ref $digest -}}{{- end -}}
{{- include "pipeshub-ai.imageRef" (dict "ref" $ref "registry" (.image.registry | default "") "global" .global) -}}
{{- end }}

{{/*
The application image: image.tag defaults to the chart's appVersion, never a
moving tag. Usage: include "pipeshub-ai.appImage" .
*/}}
{{- define "pipeshub-ai.appImage" -}}
{{- include "pipeshub-ai.image" (dict "image" .Values.image "global" .Values.global "defaultTag" .Chart.AppVersion) -}}
{{- end }}

{{/*
The code-sandbox image: config.sandboxDockerImage, else the sandbox image of
this release. Usage: include "pipeshub-ai.sandboxImage" .
*/}}
{{- define "pipeshub-ai.sandboxImage" -}}
{{- $ref := .Values.config.sandboxDockerImage | default (printf "pipeshubai/pipeshub-sandbox:%s" .Chart.AppVersion) -}}
{{- include "pipeshub-ai.imageRef" (dict "ref" $ref "global" .Values.global) -}}
{{- end }}

{{/*
The registry engine images are pulled from (HARNESS_ENGINE_IMAGE_REGISTRY):
agentHarness.engineImageRegistry, else global.imageRegistry.
*/}}
{{- define "pipeshub-ai.engineImageRegistry" -}}
{{- .Values.agentHarness.engineImageRegistry | default .Values.global.imageRegistry | default "" -}}
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
  {{- fail "config.sandboxMode is \"local\", which runs generated code as a subprocess of this pod with no container isolation, and the service refuses it unless config.sandboxAllowLocal=true. Set that only on a single-tenant development cluster; otherwise use broker, docker or e2b." }}
{{- end }}
{{- $socketError := "mounts a Docker socket into the application container, which gives it, and any code it runs, root on the node. Use --set sandbox.dind.enabled=true with config.sandboxMode=broker (the daemon then sits in the sandbox-broker pod) or docker (the app then reaches it only through the docker-proxy policy sidecar), or --set config.sandboxMode=e2b." }}
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
{{- range .Values.extraEnv }}
  {{- if and (kindIs "map" .) (eq (toString (.name | default "")) "SANDBOX_MAX_RUN_TIMEOUT_S") }}
    {{- fail "extraEnv sets SANDBOX_MAX_RUN_TIMEOUT_S, which reaches only the app container, so the docker-proxy reaper would keep its own age and remove longer runs mid-way. Set config.sandboxMaxRunTimeoutS instead; it configures both containers." }}
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
{{- if eq (include "pipeshub-ai.sandboxMode" .) "broker" }}
  {{- if not (or .Values.sandbox.dind.enabled .Values.config.dockerHost) }}
    {{- fail "config.sandboxMode is \"broker\" but no Docker daemon is configured for the sandbox broker, so run_code would fail at runtime. Pick one: (a) --set sandbox.dind.enabled=true to run a Docker-in-Docker sidecar in the sandbox-broker pod (needs a PRIVILEGED container - see sandbox.dind in values.yaml; the application pods get no daemon); (b) --set config.dockerHost=tcp://<host>:2375 to use a daemon you run; (c) --set config.sandboxMode=e2b with an E2B_API_KEY to execute off-cluster; or (d) --set config.sandboxMode=local --set config.sandboxAllowLocal=true to run generated code as a subprocess of this pod - NO container isolation, acceptable only for single-tenant development clusters." }}
  {{- end }}
{{- end }}
{{- if .Values.sandbox.dind.enabled }}
  {{- $limits := (.Values.sandbox.dind.resources | default dict).limits | default dict }}
  {{- if not (and $limits.memory $limits.cpu) }}
    {{- fail "sandbox.dind.resources.limits.memory and limits.cpu are required: the code sandbox cap is sized from the dind limits, and a container without a limit would be sized from the whole node." }}
  {{- end }}
  {{- $live := .Values.sandbox.dind.liveLimit | default dict }}
  {{- if $live.enabled }}
    {{- $interval := int ($live.exportIntervalSeconds | default 5) }}
    {{- if or (lt $interval 2) (gt $interval 15) }}
      {{- fail "sandbox.dind.liveLimit.exportIntervalSeconds must be 2..15: docker-proxy treats counters older than 20 s as missing." }}
    {{- end }}
    {{- /* The app needs memHard >= memHigh + 0.01 and otherwise drops both to their defaults; an empty one is its default. */}}
    {{- if or $live.memHigh $live.memHard }}
      {{- $high := float64 ($live.memHigh | default "0.90") }}
      {{- $hard := float64 ($live.memHard | default "0.95") }}
      {{- /* Same tolerance as the app: a gap of exactly 0.01 passes despite float rounding (0.94 - 0.93 < 0.01). */}}
      {{- if lt (subf $hard $high) 0.009999999 }}
        {{- fail "sandbox.dind.liveLimit.memHigh must be below memHard by at least 0.01 (empty values are 0.90 and 0.95): runs are held back above memHigh and the limit halves above memHard." }}
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
{{- $raw := .Values.config.sandboxMode | default "" | toString -}}
{{- $mode := $raw | trim | lower -}}
{{- $brokerImage := eq (include "pipeshub-ai.imageCan" (dict "root" . "cap" "sandbox-broker")) "true" -}}
{{- if eq $mode "" -}}
{{- /* Unset follows the security profile (ADR-OPS-07): upstream's chart ran docker; hardened runs the broker, when
       the image has one. */ -}}
{{- $mode = ternary "broker" "docker" (and $brokerImage (eq (include "pipeshub-ai.hardened" .) "true")) -}}
{{- end -}}
{{- if not (has $mode (list "local" "docker" "e2b" "broker")) -}}
  {{- fail (printf "config.sandboxMode=%q is not a supported coding sandbox. Use one of: broker, local, docker, e2b." $raw) -}}
{{- end -}}
{{- if and (eq $mode "broker") (not $brokerImage) -}}
  {{- fail (printf "config.sandboxMode=broker, but image.capabilities (%q) does not list sandbox-broker: the image is older than the sandbox broker. Use config.sandboxMode=docker, or an image that has it." (.Values.image.capabilities | default "none" | toString)) -}}
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
{{/*
sandbox.capacity.maxTotal when it is a number above 0, else empty: 0 (or
anything not a positive number) means "sized automatically", like "", so it
never turns into the app's 1024 or drops the external-daemon split.
*/}}
{{- define "pipeshub-ai.sandboxMaxTotal" -}}
{{- $raw := .Values.sandbox.capacity.maxTotal -}}
{{- if and $raw (gt (float64 $raw) 0.0) -}}
{{- $total := float64 $raw -}}
{{- if or (ne $total (float64 (int64 $total))) (not (regexMatch "^[0-9]+(\\.0*)?$" (toString $raw | trim))) -}}
{{- fail (printf "sandbox.capacity.maxTotal must be a whole number of sandboxes, got %v" $raw) -}}
{{- end -}}
{{- int64 $total -}}
{{- end -}}
{{- end -}}

{{/*
A Kubernetes quantity as a plain number, so equal amounts written
differently compare equal ("2" and "2000m", "1Gi" and "1024Mi"). Anything
it cannot read is returned unchanged.
*/}}
{{- define "pipeshub-ai.quantity" -}}
{{- $q := toString . | trim -}}
{{- $num := regexFind "^[0-9]+(\\.[0-9]+)?" $q -}}
{{- $suffix := trimPrefix $num $q -}}
{{- $binary := dict "Ki" 1024.0 "Mi" 1048576.0 "Gi" 1073741824.0 "Ti" 1099511627776.0 "Pi" 1125899906842624.0 -}}
{{- $decimal := dict "" 1.0 "k" 1000.0 "M" 1000000.0 "G" 1000000000.0 "T" 1000000000000.0 "P" 1000000000000000.0 -}}
{{- if not $num -}}
{{- $q -}}
{{- else if eq $suffix "m" -}}
{{- divf (float64 $num) 1000.0 -}}
{{- else if hasKey $binary $suffix -}}
{{- mulf (float64 $num) (index $binary $suffix) -}}
{{- else if hasKey $decimal $suffix -}}
{{- mulf (float64 $num) (index $decimal $suffix) -}}
{{- else -}}
{{- $q -}}
{{- end -}}
{{- end -}}

{{/*
How many pods share one external Docker daemon, when each must size its
code sandbox cap to its share of it: the most pods that can run, with
config.dockerHost set, sandbox.capacity.maxTotal empty, and more than one
pod. Empty otherwise.
*/}}
{{- define "pipeshub-ai.sandboxCapacityDivisor" -}}
{{- if and (eq (include "pipeshub-ai.sandboxPath" .) "external") (not (include "pipeshub-ai.sandboxMaxTotal" .)) -}}
{{- $pods := int .Values.replicaCount -}}
{{- if .Values.autoscaling.enabled -}}
{{- $pods = int .Values.autoscaling.maxReplicas -}}
{{- end -}}
{{- if gt $pods 64 -}}
{{- fail (printf "%d pods share one external Docker daemon, but the code sandbox splits its capacity at most 64 ways: lower the pod count (autoscaling.maxReplicas or replicaCount) or set sandbox.capacity.maxTotal" $pods) -}}
{{- end -}}
{{- if gt $pods 1 -}}
{{- $pods -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
The dind container's command when sandbox.dind.liveLimit is on: a loop that
writes the container's own cgroup counters to the socket volume every few
seconds (atomically, through a temp file), where docker-proxy serves them as
GET /_pipeshub/usage, then the image's entrypoint with the container args
unchanged. POSIX sh and busybox only, as in docker:*-dind. The PIPESHUB_*
variables let the chart test run it against fixture files.
*/}}
{{- define "pipeshub-ai.dindExporterScript" -}}
cg="${PIPESHUB_CGROUP_DIR:-/sys/fs/cgroup}"
out="${PIPESHUB_SIDECAR_STATS:-/var/run/dind/pipeshub-sidecar-stats}"
iv="${PIPESHUB_EXPORT_INTERVAL:-5}"
(
  n=0
  while :; do
    n=$((n+1))
    {
      echo "v 1"; echo "seq $n"
      for f in memory.max memory.current cpu.max; do
        if [ -r "$cg/$f" ]; then echo "$f $(cat "$cg/$f")"; fi
      done
      awk '$1=="file"||$1=="shmem"||$1=="anon"{print "memory.stat."$1, $2}' "$cg/memory.stat" 2>/dev/null
      awk '{print "memory.events.local."$1, $2}' "$cg/memory.events.local" 2>/dev/null
      awk '{print "cpu.stat."$1, $2}' "$cg/cpu.stat" 2>/dev/null
      awk '$1=="full"{split($2,a,"=");print "memory.pressure.full.avg10", a[2]}' "$cg/memory.pressure" 2>/dev/null
      awk '$1=="some"{split($2,a,"=");print "cpu.pressure.some.avg10", a[2]}' "$cg/cpu.pressure" 2>/dev/null
      for p in dockerd containerd; do
        pid=$(pidof "$p" 2>/dev/null | cut -d' ' -f1)
        # Optional: a missing process or an unreadable status must not stop the counters being published.
        if [ -n "$pid" ]; then awk -v p="$p" '/^VmRSS:/{print p".rss_kb", $2}' "/proc/$pid/status" 2>/dev/null || true; fi
      done
    } > "$out.tmp" && mv -f "$out.tmp" "$out"
    [ "${PIPESHUB_EXPORT_ONCE:-0}" = 1 ] && exit 0
    sleep "$iv"
  done
) &
if command -v dockerd-entrypoint.sh >/dev/null 2>&1; then exec dockerd-entrypoint.sh "$@"; fi
exec "$@"
{{- end -}}

{{- define "pipeshub-ai.sandboxPath" -}}
{{- $mode := include "pipeshub-ai.sandboxMode" . -}}
{{- if eq $mode "broker" -}}
{{- if .Values.config.dockerHost -}}broker-external{{- else -}}broker-dind{{- end -}}
{{- else if ne $mode "docker" -}}
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
{{- /* The subchart's standalone PVC is dropped from the manifest once
architecture is replicaset, and Helm would delete it on upgrade. lookup is
empty under helm template, so this only fires against a live release. */ -}}
{{- if and .Values.mongodb.enabled (eq (.Values.mongodb.architecture | default "standalone") "replicaset") -}}
  {{- $standaloneClaim := printf "%s-mongodb" .Release.Name -}}
  {{- if lookup "v1" "PersistentVolumeClaim" .Release.Namespace $standaloneClaim -}}
    {{- fail (printf "PVC %s holds data from a standalone MongoDB, and switching to a replica set would make Helm delete it. Keep standalone with --set mongodb.architecture=standalone, or move the data first: see \"Standalone MongoDB to a replica set\" in the chart README." $standaloneClaim) -}}
  {{- end -}}
{{- end -}}
{{- if and .Values.secretManagement.externalSecrets.enabled (not .Values.secretManagement.externalSecrets.remoteRefs.secretKey) -}}
  {{- fail "secretManagement.externalSecrets.remoteRefs.secretKey is required when externalSecrets is enabled; the app cannot start without secret-key." -}}
{{- end -}}
{{- end -}}

{{/*
The sandbox broker's in-cluster address (config.sandboxMode=broker).
*/}}
{{- define "pipeshub-ai.sandboxBrokerUrl" -}}
{{- printf "http://%s-sandbox-broker:8095" (include "pipeshub-ai.fullname" .) -}}
{{- end -}}

{{/*
etcd client settings, for every process that reads the KV store.
*/}}
{{- define "pipeshub-ai.etcdEnv" -}}
{{- if .Values.etcd.enabled }}
# ETCD config: TLS with this pod's own client certificate (SEC-D-06), mounted by pipeshub-ai.etcdTlsMount
- name: ETCD_URL
  value: "https://{{ include "pipeshub-ai.fullname" . }}-etcd:2379"
- name: ETCD_HOST
  value: "{{ include "pipeshub-ai.fullname" . }}-etcd"
- name: ETCD_CA_FILE
  value: /run/pipeshub/etcd-tls/ca.crt
- name: ETCD_CERT_FILE
  value: /run/pipeshub/etcd-tls/tls.crt
- name: ETCD_KEY_FILE
  value: /run/pipeshub/etcd-tls/tls.key
{{- end }}
{{- end -}}

{{/*
The volume and mount behind etcdEnv. Call the volume with (dict "root" . "client" "pipeshub-ai" | "sandbox-broker").
*/}}
{{- define "pipeshub-ai.etcdTlsMount" -}}
{{- if .Values.etcd.enabled }}
- name: etcd-tls
  mountPath: /run/pipeshub/etcd-tls
  readOnly: true
{{- end }}
{{- end -}}

{{- define "pipeshub-ai.etcdTlsVolume" -}}
{{- if .root.Values.etcd.enabled }}
- name: etcd-tls
  secret:
    secretName: {{ include "pipeshub-ai.fullname" .root }}-etcd-client-{{ .client }}
    defaultMode: 0440
{{- end }}
{{- end -}}

{{/*
Redis client settings, for every process that reads the KV store or the broker.
*/}}
{{- define "pipeshub-ai.redisEnv" -}}
{{- include "pipeshub-ai.redisEnvFor" (dict "root" . "auth" true) }}
{{- end -}}

{{/*
redisEnv for a sandbox-plane component (ADR-KEY-02, SEC-D-03): the same target, and no password, since the
component derives its own Redis ACL login from its key.
*/}}
{{- define "pipeshub-ai.redisEnvComponent" -}}
{{- include "pipeshub-ai.redisEnvFor" (dict "root" . "auth" false) }}
{{- end -}}

{{- define "pipeshub-ai.redisEnvFor" -}}
{{- $root := .root }}
{{- $auth := .auth }}
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
{{- include "pipeshub-ai.validateRedis" $root }}
{{- $ext := $root.Values.redis.external }}
{{- $redis := dict "host" (printf "%s-redis-master" $root.Release.Name) "port" "6379" "scheme" "redis" "target" "" }}
{{- if and $ext.enabled $ext.host }}
{{- $_ := set $redis "host" ($ext.host | toString) }}
{{- $_ := set $redis "port" ($ext.port | default 6379 | toString) }}
{{- else if $ext.enabled }}
{{- $_ := set $redis "host" "" }}
{{- end }}
{{- if and $ext.enabled $root.Values.redis.clientTls.enabled }}
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
{{- if not $auth }}
# A sandbox-plane component logs in as its own ACL user (ADR-KEY-02): it gets no Redis password.
{{- else if $root.Values.redis.auth.enabled }}
- name: REDIS_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "pipeshub-ai.redisSecretName" $root }}
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
  value: {{ $root.Values.redis.mode | default "standalone" | quote }}
- name: REDIS_KEY_NAMESPACE
  value: {{ $root.Values.redis.keyNamespace | default "" | quote }}
- name: REDIS_CLUSTER_SCALE_READS
  value: {{ $root.Values.redis.clusterScaleReads | default "master" | quote }}
- name: REDIS_PROVIDER_MODULE
  value: {{ $root.Values.redis.providerModule | default "" | quote }}
- name: REDIS_TLS_ENABLED
  value: {{ $root.Values.redis.clientTls.enabled | default false | quote }}
- name: REDIS_TLS_REJECT_UNAUTHORIZED
  value: {{ $root.Values.redis.clientTls.rejectUnauthorized | default true | quote }}
- name: REDIS_TLS_CA_PATH
  value: {{ $root.Values.redis.clientTls.caPath | default "" | quote }}
{{- end -}}

{{/*
The privileged Docker-in-Docker sidecar: in the sandbox-broker pod with
config.sandboxMode=broker, in the app pod with the legacy docker mode. See
`sandbox.dind` in values.yaml for the privilege trade this makes.
*/}}
{{- define "pipeshub-ai.dindContainer" -}}
- name: dind
  image: {{ include "pipeshub-ai.imageRef" (dict "ref" .Values.sandbox.dind.image "global" .Values.global) | quote }}
  imagePullPolicy: {{ .Values.sandbox.dind.pullPolicy | quote }}
  securityContext:
    # The daemon needs to create namespaces, mount filesystems and
    # manage cgroups, which no unprivileged container can do. This
    # deliberately overrides the pod-level runAsNonRoot.
    privileged: true
    runAsNonRoot: false
    runAsUser: 0
  {{- if .Values.sandbox.dind.liveLimit.enabled }}
  # Starts the loop that writes this container's cgroup counters for
  # docker-proxy (pipeshub-ai.dindExporterScript), then the image's
  # entrypoint with the args below unchanged.
  command: ["/bin/sh", "-c", {{ include "pipeshub-ai.dindExporterScript" . | quote }}, "pipeshub-dind"]
  {{- end }}
  env:
    - name: DOCKER_TLS_CERTDIR
      value: ""
    # For the `docker info` probes below.
    - name: DOCKER_HOST
      value: "unix:///var/run/dind/docker.sock"
    {{- if .Values.sandbox.dind.liveLimit.enabled }}
    - name: PIPESHUB_EXPORT_INTERVAL
      value: {{ .Values.sandbox.dind.liveLimit.exportIntervalSeconds | default 5 | toString | quote }}
    {{- end }}
  # No TCP listener: every container in a pod shares one network
  # namespace, so a loopback port would hand the raw API to the
  # application container too. The socket's volume is mounted only
  # here and in docker-proxy. First arg must be `dockerd`; if it starts
  # with `-`, dockerd-entrypoint.sh adds its default 0.0.0.0:2375 listener.
  args:
    - dockerd
    - "--host=unix:///var/run/dind/docker.sock"
    - "--group={{ .Values.sandbox.dind.socketGroup }}"
  resources:
    {{- toYaml .Values.sandbox.dind.resources | nindent 12 }}
  volumeMounts:
    - name: dind-storage
      mountPath: /var/lib/docker
    - name: dind-sock
      mountPath: /var/run/dind
  livenessProbe:
    exec:
      command: ["docker", "info"]
    initialDelaySeconds: 20
    periodSeconds: 30
    timeoutSeconds: 10
    failureThreshold: 3
  startupProbe:
    # Pulling the sandbox image on a cold cache can take a while;
    # this is what stops a slow first start being read as a crash.
    exec:
      command: ["docker", "info"]
    initialDelaySeconds: 5
    periodSeconds: 5
    timeoutSeconds: 10
    failureThreshold: 30
{{- end -}}

{{/*
/var/lib/docker for the DinD sidecar, and the emptyDir holding its unix socket
(mounted only into `dind` and the one container that talks to the daemon:
`docker-proxy`, in the app pod or the sandbox-broker pod).
*/}}
{{- define "pipeshub-ai.dindVolume" -}}
- name: dind-storage
  {{- if .Values.sandbox.dind.storage.existingClaim }}
  persistentVolumeClaim:
    claimName: {{ .Values.sandbox.dind.storage.existingClaim | quote }}
  {{- else }}
  emptyDir:
    sizeLimit: {{ .Values.sandbox.dind.storage.sizeLimit | quote }}
  {{- end }}
- name: dind-sock
  emptyDir:
    medium: Memory
    sizeLimit: 1Mi
{{- end -}}

{{/*
The Docker API policy proxy sidecar (#3888): the only container besides dind
that mounts the daemon's socket. Called with (dict "root" . "profile" ...):
"broker" in the sandbox-broker pod, which then reaches the daemon only through
it with its own credential (R38IDENT-FU-1); "legacy" in the app pod with the
legacy docker mode. Listens on loopback; admits only that caller's calls.
*/}}
{{- define "pipeshub-ai.dockerProxyContainer" -}}
{{- $root := .root -}}
- name: docker-proxy
  {{- if $root.Values.sandbox.proxy.image }}
  image: {{ include "pipeshub-ai.imageRef" (dict "ref" $root.Values.sandbox.proxy.image "global" $root.Values.global) | quote }}
  {{- else }}
  image: {{ include "pipeshub-ai.appImage" $root | quote }}
  {{- end }}
  imagePullPolicy: {{ $root.Values.image.pullPolicy | default $root.Values.global.imagePullPolicy }}
  # Bypasses the image entrypoint, which drops privileges for the app
  # and needs capabilities this container deliberately does not have.
  command: ["python", "-m", "app.docker_proxy_main"]
  workingDir: /app/python
  securityContext:
    # Not root: the daemon's socket belongs to sandbox.dind.socketGroup, which
    # this container alone runs with (SEC-D-02).
    runAsUser: 1000
    runAsGroup: {{ $root.Values.sandbox.dind.socketGroup }}
    runAsNonRoot: true
    allowPrivilegeEscalation: false
    readOnlyRootFilesystem: true
    capabilities:
      drop: ["ALL"]
    seccompProfile:
      type: RuntimeDefault
  env:
    # Loopback only (also the proxy's default): the pod IP must not
    # serve it, since the proxy has no client authentication.
    - name: DOCKER_PROXY_HOST
      value: "127.0.0.1"
    - name: DOCKER_PROXY_PORT
      value: {{ $root.Values.sandbox.dind.port | quote }}
    - name: DOCKER_PROXY_UPSTREAM
      value: "unix:///var/run/dind/docker.sock"
    - name: DOCKER_PROXY_LOG_LEVEL
      value: {{ $root.Values.sandbox.proxy.logLevel | quote }}
    {{- if eq .profile "broker" }}
    - name: DOCKER_PROXY_BROKER_TOKEN
      valueFrom:
        secretKeyRef:
          name: {{ include "pipeshub-ai.secretName" $root }}
          key: docker-proxy-broker-token
          # An existing or external secret without it still renders; the proxy then says what to set.
          optional: true
    # The broker's scope, as the broker reads it: this install's sandboxes, volumes and networks only.
    - name: SANDBOX_INSTALL_ID
      value: {{ printf "%s-%s" $root.Release.Namespace $root.Release.Name | quote }}
    - name: SANDBOX_DATA_VOLUME
      value: {{ $root.Values.sandboxBroker.dataVolume | default "pipeshub_sandbox_data" | quote }}
    {{- else }}
    - name: DOCKER_PROXY_TOKEN
      valueFrom:
        secretKeyRef:
          name: {{ include "pipeshub-ai.secretName" $root }}
          key: docker-proxy-token
          optional: true
    {{- with $root.Values.sandbox.proxy.allowedImages }}
    - name: DOCKER_PROXY_ALLOWED_IMAGES
      value: {{ . | quote }}
    {{- end }}
    # The same value the app gets: the reaper removes sandbox
    # containers older than this cap plus a margin.
    - name: SANDBOX_MAX_RUN_TIMEOUT_S
      value: {{ $root.Values.config.sandboxMaxRunTimeoutS | quote }}
    # Labels this release's sandbox containers, so releases sharing a
    # daemon never see or reap each other's.
    - name: DOCKER_PROXY_INSTANCE
      value: {{ printf "%s/%s" $root.Release.Namespace (include "pipeshub-ai.fullname" $root) | quote }}
    - name: DOCKER_PROXY_REAP_UNLABELED
      value: {{ $root.Values.sandbox.proxy.reapUnlabeled | default false | toString | quote }}
    {{- if $root.Values.sandbox.dind.liveLimit.enabled }}
    # Written by the loop in the dind container; served as GET /_pipeshub/usage.
    - name: DOCKER_PROXY_SIDECAR_STATS_FILE
      value: "/var/run/dind/pipeshub-sidecar-stats"
    {{- end }}
    {{- end }}
    # Legacy: the image sandboxes run. Broker: the only image the egress firewall's root helper may run, the one the
    # broker runs it from (R44STOP-FU-3).
    - name: SANDBOX_DOCKER_IMAGE
      value: {{ include "pipeshub-ai.sandboxImage" $root | quote }}
    - name: SANDBOX_EGRESS_NETWORK
      value: {{ $root.Values.config.sandboxEgressNetwork | quote }}
    - name: PYTHONDONTWRITEBYTECODE
      value: "1"
  resources:
    {{- toYaml $root.Values.sandbox.proxy.resources | nindent 12 }}
  volumeMounts:
    - name: dind-sock
      mountPath: /var/run/dind
  # A TCP connect, not /_ping: /_ping is forwarded to dockerd, and a
  # slow daemon should not restart the proxy. The listener is on
  # loopback, so a kubelet tcpSocket probe (pod IP) cannot reach it.
  livenessProbe:
    exec:
      command:
        - python
        - -c
        - "import socket; socket.create_connection(('127.0.0.1', {{ $root.Values.sandbox.dind.port }}), timeout=3).close()"
    initialDelaySeconds: 10
    periodSeconds: 30
    timeoutSeconds: 10
    failureThreshold: 3
{{- end -}}

{{/*
DOCKER_HOST for the sandbox broker: a daemon you run (config.dockerHost; front
it with the same policy proxy, python -m app.docker_proxy_main), else the
docker-proxy sidecar in its pod, the only container there with the DinD
socket (R38IDENT-FU-1), as in Compose.
*/}}
{{- define "pipeshub-ai.brokerDockerHost" -}}
{{- if .Values.config.dockerHost -}}
{{- .Values.config.dockerHost -}}
{{- else if .Values.sandbox.dind.enabled -}}
{{- printf "tcp://127.0.0.1:%v" .Values.sandbox.dind.port -}}
{{- end -}}
{{- end -}}

{{/*
Shell lines that raise the open-file soft limit to the given number, for a
server that keeps the limit it is started with (mongod, Qdrant). Kubernetes has
no field for it: a container gets the node runtime's limit, 1024 soft where
containerd runs under systemd's defaults. Raising the soft limit within the
hard one needs no privilege; a hard limit below the number is the node's to
fix, so it is reported and the server starts on what there is.
*/}}
{{- define "pipeshub-ai.raiseOpenFiles" -}}
want={{ int . }}; hard="$(ulimit -Hn)"; soft="$(ulimit -Sn)"
if [ "$hard" != unlimited ] && [ "$hard" -lt "$want" ]; then
  echo "open files: the hard limit is $hard, below the $want this server needs; raise LimitNOFILE of the node's container runtime" >&2
  want="$hard"
fi
if [ "$soft" != unlimited ] && [ "$soft" -lt "$want" ]; then
  ulimit -Sn "$want"
fi
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
The container half of the restricted baseline (SEC-D-10), for containers whose
pod sets runAsNonRoot and the RuntimeDefault seccomp profile.
*/}}
{{- define "pipeshub-ai.restrictedContainer" -}}
securityContext:
  allowPrivilegeEscalation: false
  capabilities:
    drop:
      - ALL
{{- end }}

{{/*
The Kafka SCRAM login (SEC-D-06), for the broker and its init container. Not KAFKA_-prefixed: the cp-kafka image
writes every KAFKA_ variable into server.properties.
*/}}
{{- define "pipeshub-ai.kafkaLoginEnv" -}}
- name: PIPESHUB_KAFKA_USERNAME
  valueFrom:
    secretKeyRef:
      name: {{ include "pipeshub-ai.fullname" . }}-kafka-auth
      key: username
- name: PIPESHUB_KAFKA_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "pipeshub-ai.fullname" . }}-kafka-auth
      key: password
{{- end -}}

{{- /*
R38IDENT-FU-8 (ADR-KEY-02 D8): SECRET_KEY reaches the app as a file in a Secret volume (mode 0400; the pod's fsGroup
may add group read), so it is not in the container's environment, `kubectl describe` or /proc/<pid>/environ.
SECRET_KEY_PREVIOUS / SECRET_KEY_NEXT stay variables: they are set only while a rotation runs, and are optional keys.
*/ -}}
{{- define "pipeshub-ai.secretKeyFilePath" -}}/run/secrets/pipeshub/secret-key{{- end -}}

{{- define "pipeshub-ai.secretKeyAsFile" -}}
{{- if and .Values.secretKeyAsFile (eq (include "pipeshub-ai.imageCan" (dict "root" . "cap" "secret-key-file")) "true") }}true{{ end -}}
{{- end -}}

{{- define "pipeshub-ai.secretKeyMount" -}}
{{- if include "pipeshub-ai.secretKeyAsFile" . }}
- name: secret-key
  mountPath: /run/secrets/pipeshub
  readOnly: true
{{- end }}
{{- end -}}

{{- define "pipeshub-ai.secretKeyVolume" -}}
{{- if include "pipeshub-ai.secretKeyAsFile" . }}
- name: secret-key
  secret:
    secretName: {{ include "pipeshub-ai.secretName" . }}
    defaultMode: 0400
    items:
      - key: secret-key
        path: secret-key
{{- end }}
{{- end -}}

{{/*
The security profile (ADR-OPS-07). `hardened` (PIPESHUB_HARDENED in the app) decides the defaults that would break a
release upgraded from a chart without them: off (the default) renders what that chart rendered, so `helm upgrade` of
an existing release changes no network policy, store identity or wire protocol; true renders the hardened set. The
chart never guesses install from upgrade: `helm template` (GitOps) cannot tell them apart.
"true" or "false", parsed as the services parse PIPESHUB_HARDENED.
*/}}
{{/*
Whether the app image can run a capability (its `ai.pipeshub.capabilities` label, given as image.capabilities):
"true" or "". Empty or "none" is an image older than the label, which has none of them.
Usage: include "pipeshub-ai.imageCan" (dict "root" . "cap" "sandbox-broker")
*/}}
{{- define "pipeshub-ai.imageCan" -}}
{{- $caps := splitList "," (.root.Values.image.capabilities | default "" | toString | replace " " "" | lower) -}}
{{- if has .cap $caps }}true{{ end -}}
{{- end -}}

{{/* The agent harness switch, off for an image without it (the upstream chart had no harness). */}}
{{- define "pipeshub-ai.agentHarnessEnabled" -}}
{{- and .Values.agentHarness.enabled (eq (include "pipeshub-ai.imageCan" (dict "root" . "cap" "agent-harness")) "true") -}}
{{- end -}}

{{- define "pipeshub-ai.hardened" -}}
{{- $raw := .Values.hardened | default false | toString | trim | lower -}}
{{- ternary "true" "false" (has $raw (list "1" "true" "yes" "on")) -}}
{{- end -}}

{{/*
A per-item switch that follows `hardened` while the operator leaves it unset (null or ""); an explicit value wins.
Called with (dict "value" <the item's value> "root" $). "true" or "" (for `if`).
*/}}
{{- define "pipeshub-ai.followsHardened" -}}
{{- $value := .value -}}
{{- if or (kindIs "invalid" $value) (eq (toString $value) "") -}}
{{- if eq (include "pipeshub-ai.hardened" .root) "true" }}true{{ end -}}
{{- else if has ($value | toString | trim | lower) (list "1" "true" "yes" "on") -}}
true
{{- end -}}
{{- end -}}

{{/* networkPolicy.enabled, following `hardened` when unset (upstream's chart rendered none). */}}
{{- define "pipeshub-ai.networkPolicyEnabled" -}}
{{- include "pipeshub-ai.followsHardened" (dict "value" .Values.networkPolicy.enabled "root" .) -}}
{{- end -}}

{{/* Kafka's SCRAM login (SEC-D-06), following `hardened` when kafka.auth.enabled is unset (upstream's was PLAINTEXT). */}}
{{- define "pipeshub-ai.kafkaAuthEnabled" -}}
{{- include "pipeshub-ai.followsHardened" (dict "value" .Values.kafka.auth.enabled "root" .) -}}
{{- end -}}

{{/*
MongoDB's pods as the mongodb user from the start (SEC-D-10), following `hardened` when mongodb.builtin.nonRoot is
unset. Off: upstream's pods, whose entrypoint starts as root and drops to mongodb itself.
*/}}
{{- define "pipeshub-ai.mongodbNonRoot" -}}
{{- include "pipeshub-ai.followsHardened" (dict "value" .Values.mongodb.builtin.nonRoot "root" .) -}}
{{- end -}}

{{/*
The app's (and the sandbox broker's) container securityContext: .Values.securityContext, whose
readOnlyRootFilesystem follows `hardened` while it is unset (upstream's chart had a writable root).
*/}}
{{- define "pipeshub-ai.appSecurityContext" -}}
{{- $ctx := deepCopy .Values.securityContext -}}
{{- if or (not (hasKey $ctx "readOnlyRootFilesystem")) (kindIs "invalid" $ctx.readOnlyRootFilesystem) (eq (toString $ctx.readOnlyRootFilesystem) "") -}}
{{- $_ := set $ctx "readOnlyRootFilesystem" (eq (include "pipeshub-ai.hardened" .) "true") -}}
{{- end -}}
{{- toYaml $ctx -}}
{{- end -}}

{{/*
Qdrant's pod and container securityContext. A non-empty value is the operator's and wins. Unset, hardened runs the
-unprivileged image as uid 1000 (SEC-D-10); compatibility runs it as root, as upstream's root-running image did, so a
volume that image wrote stays writable.
*/}}
{{- define "pipeshub-ai.qdrantPodSecurityContext" -}}
{{- if .Values.qdrant.podSecurityContext -}}
{{- toYaml .Values.qdrant.podSecurityContext -}}
{{- else if eq (include "pipeshub-ai.hardened" .) "true" -}}
runAsNonRoot: true
runAsUser: 1000
runAsGroup: 1000
fsGroup: 1000
fsGroupChangePolicy: OnRootMismatch
seccompProfile:
  type: RuntimeDefault
{{- else -}}
runAsUser: 0
runAsGroup: 0
fsGroup: 1000
fsGroupChangePolicy: OnRootMismatch
{{- end -}}
{{- end -}}

{{- define "pipeshub-ai.qdrantSecurityContext" -}}
{{- if .Values.qdrant.securityContext -}}
{{- toYaml .Values.qdrant.securityContext -}}
{{- else if eq (include "pipeshub-ai.hardened" .) "true" -}}
allowPrivilegeEscalation: false
runAsNonRoot: true
capabilities:
  drop:
    - ALL
{{- else -}}
{}
{{- end -}}
{{- end -}}

{{/*
The environment every application-image container of this release shares (the
main app pod and the parsing pod): ports, stores, queues, secrets. Lines carry
their final indentation (12 spaces), so include it directly after `env:` on the same line. The caller
adds what is specific to its role after it (the main pod: Node and the agent harness).
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
            # The security profile (ADR-OPS-07): the services' hardened defaults, or a release's without them.
            - name: PIPESHUB_HARDENED
              value: {{ include "pipeshub-ai.hardened" . | quote }}
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
              value: {{ include "pipeshub-ai.sandboxImage" . | quote }}
            - name: SANDBOX_ALLOW_NETWORK
              value: {{ .Values.config.sandboxAllowNetwork | quote }}
            - name: SANDBOX_EGRESS_NETWORK
              value: {{ .Values.config.sandboxEgressNetwork | quote }}
            - name: SANDBOX_EGRESS_ALLOW_CIDRS
              value: {{ .Values.config.sandboxEgressAllowCidrs | default "" | quote }}
            - name: SANDBOX_EGRESS_DNS_SERVERS
              value: {{ .Values.config.sandboxEgressDnsServers | default "" | quote }}
            - name: SANDBOX_DISK_QUOTA
              value: {{ .Values.config.sandboxDiskQuota | quote }}
            - name: SANDBOX_MAX_RUN_TIMEOUT_S
              value: {{ .Values.config.sandboxMaxRunTimeoutS | quote }}
            {{- if eq (include "pipeshub-ai.sandboxMode" .) "broker" }}
            # Sandboxes are started by the sandbox-broker Deployment, the only
            # pod with a Docker daemon; this pod has none.
            - name: SANDBOX_BROKER_BACKEND
              value: {{ include "pipeshub-ai.sandboxBrokerUrl" . | quote }}
            {{- else }}
            {{- with (include "pipeshub-ai.dockerHost" .) }}
            - name: DOCKER_HOST
              value: {{ . | quote }}
            # SEC-A-04: the app's credential at the docker-proxy sidecar.
            - name: DOCKER_PROXY_TOKEN
              valueFrom:
                secretKeyRef:
                  name: {{ include "pipeshub-ai.secretName" $ }}
                  key: docker-proxy-token
                  optional: true
            {{- end }}
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
            {{- if include "pipeshub-ai.secretKeyAsFile" . }}
            # R38IDENT-FU-8 (ADR-KEY-02 D8): the root key as a file only this pod's user reads, never in the environment.
            - name: SECRET_KEY_FILE
              value: {{ include "pipeshub-ai.secretKeyFilePath" . | quote }}
            {{- else }}
            - name: SECRET_KEY
              valueFrom:
                secretKeyRef:
                  name: {{ include "pipeshub-ai.secretName" . }}
                  key: secret-key
            {{- end }}
            # Only while SECRET_KEY is rotated (ADR-KEY-01): absent from the secret, the variable is not set.
            - name: SECRET_KEY_NEXT
              valueFrom:
                secretKeyRef:
                  name: {{ include "pipeshub-ai.secretName" . }}
                  key: secret-key-next
                  optional: true
            - name: SECRET_KEY_PREVIOUS
              valueFrom:
                secretKeyRef:
                  name: {{ include "pipeshub-ai.secretName" . }}
                  key: secret-key-previous
                  optional: true
            # ADR-KEY-02: the broker's own key; the app seals the broker's document under it.
            - name: SANDBOX_BROKER_COMPONENT_KEY
              valueFrom:
                secretKeyRef:
                  name: {{ include "pipeshub-ai.secretName" . }}
                  key: sandbox-broker-component-key
                  optional: true
            
            # Public endpoints
            - name: CONNECTOR_PUBLIC_BACKEND
              value: {{ .Values.config.connectorPublicBackend | quote }}
            - name: FRONTEND_PUBLIC_URL
              value: {{ .Values.config.frontendPublicUrl | quote }}
            - name: INTERNAL_ROUTES_PUBLIC_ACCESS
              value: {{ .Values.config.internalRoutesPublicAccess | default "log" | quote }}
            - name: INTERNAL_ROUTES_TRUSTED_CIDRS
              value: {{ .Values.config.internalRoutesTrustedCidrs | default "" | quote }}
            
            # Internal service URLs
            - name: QUERY_BACKEND
              value: "http://localhost:8000"
            - name: CONNECTOR_BACKEND
              value: "http://localhost:8088"
            - name: INDEXING_BACKEND
              value: "http://localhost:8091"
            - name: EMBEDDING_SERVER_URL
              value: "http://localhost:8002"
            
            {{- include "pipeshub-ai.etcdEnv" . | nindent 12 }}
            
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
            - name: KAFKA_BROKERS
              value: "{{ include "pipeshub-ai.fullname" . }}-kafka:9092"
            {{- if include "pipeshub-ai.kafkaAuthEnabled" . }}
            # The SCRAM login store-auth.yaml generates (SEC-D-06)
            - name: KAFKA_SASL_MECHANISM
              value: scram-sha-512
            - name: KAFKA_USERNAME
              valueFrom:
                secretKeyRef:
                  name: {{ include "pipeshub-ai.fullname" . }}-kafka-auth
                  key: username
            - name: KAFKA_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: {{ include "pipeshub-ai.fullname" . }}-kafka-auth
                  key: password
            {{- end }}
            {{- end }}
            
            {{- include "pipeshub-ai.redisEnv" . | nindent 12 }}
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
            {{- /* An arbiter makes majority writes stall when a data member is down, so only arbiter-free sets get w=majority. */}}
            {{- $hasArbiter := and .Values.mongodb.enabled .Values.mongodb.arbiter.enabled }}
            {{- if and $isReplicaSet (not $hasArbiter) }}
            {{- $mongoRsParam = printf "%s&w=majority" $mongoRsParam }}
            {{- end }}
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

{{/*
.value ("8", "8.5", ".5") times ten to the power .exp ("9", "+009", "-3"), as a
float. Leading zeros in the exponent are allowed, as both Kubernetes and Neo4j
allow them. The value is first written as 0.<digits> x 10^m, so the bounds
apply to the number's size, not to the exponent as written:
"85899345920000000000000000000000000000000e-31" is 8Gi. A number of 1e31 or
more fails the render with .what, one below 1e-30 is zero, and the loop runs
at most 31 times.
*/}}
{{- define "pipeshub-ai.timesPow10" -}}
{{- $expText := toString .exp -}}
{{- $expDigits := regexReplaceAll "^0+" (trimPrefix "-" (trimPrefix "+" $expText)) "" | default "0" -}}
{{- if gt (len $expDigits) 4 }}{{ $expDigits = "9999" }}{{ end -}}
{{- $exp := atoi $expDigits -}}
{{- if hasPrefix "-" $expText }}{{ $exp = sub 0 $exp }}{{ end -}}
{{- $parts := splitList "." (toString .value) -}}
{{- $whole := first $parts -}}
{{- $all := join "" $parts -}}
{{- $significant := regexReplaceAll "^0+" $all "" -}}
{{- $v := 0.0 -}}
{{- if $significant -}}
{{- $m := add $exp (sub (len $whole) (sub (len $all) (len $significant))) -}}
{{- if gt $m 31 }}{{ fail .what }}{{ end -}}
{{- if ge $m -29 -}}
{{- $v = float64 (printf "0.%s" (trunc 17 $significant)) -}}
{{- if lt $m 0 -}}
{{- range until (int (sub 0 $m)) }}{{ $v = divf $v 10 }}{{ end -}}
{{- else -}}
{{- range until (int $m) }}{{ $v = mulf $v 10 }}{{ end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- $v -}}
{{- end -}}

{{/*
A Kubernetes memory quantity in whole MiB, following the apimachinery grammar:
a number ("8", "8.5", ".5") and one suffix, binary ("Ki" to "Ei"), decimal
("m", "k" to "E") or a signed exponent ("e9", "e+9", "E-3", "e+009"). A large
integer in a values file arrives as a float and prints as "8.589934592e+09".
The sum is kept in bytes and divided once: sprig's mulf and divf keep 16
digits, so a factor like 1/1048576 would make 8589934592 bytes 8191 MiB.
*/}}
{{- define "pipeshub-ai.memoryMiB" -}}
{{- $q := trimPrefix "+" (toString .) -}}
{{- $n := regexFind "^([0-9]+(\\.[0-9]*)?|\\.[0-9]+)" $q -}}
{{- $suffix := trimPrefix $n $q -}}
{{- $perUnit := dict "" 1 "m" 0.001 "k" 1000 "M" 1000000 "G" 1000000000 "T" 1000000000000 "P" 1000000000000000 "E" 1000000000000000000 "Ki" 1024 "Mi" 1048576 "Gi" 1073741824 "Ti" 1099511627776 "Pi" 1125899906842624 "Ei" 1152921504606846976 -}}
{{- $unreadable := printf "neo4j.resources.limits.memory %q is not a memory quantity the chart can read; set the NEO4J_server_memory_* sizes in neo4j.env" (toString .) -}}
{{- $bytes := 0.0 -}}
{{- if and $n (regexMatch "^[eE][+-]?[0-9]+$" $suffix) -}}
{{- $bytes = include "pipeshub-ai.timesPow10" (dict "value" $n "exp" (substr 1 -1 $suffix) "what" $unreadable) | float64 -}}
{{- else if and $n (hasKey $perUnit $suffix) -}}
{{- $bytes = mulf $n (get $perUnit $suffix) -}}
{{- else -}}
{{- fail $unreadable -}}
{{- end -}}
{{- divf $bytes 1048576 | floor | int64 -}}
{{- end -}}

{{/*
A Neo4j byte size in whole MiB, rounded up, read as Neo4j 5's ByteUnit.parse
does. The number is Java's English NumberFormat: grouping commas, a fraction,
and an exponent only as an uppercase "E" with an optional "-" ("3E9", "3E-3";
"3e9" and "3E+9" are refused). Then optional blanks and one of its unit names,
case-sensitive and binary ("B", "k"/"K"/"KB"/"KiB", "m"/"MB"/"MiB",
"g"/"GB"/"GiB", "TB"/"TiB", "PB"/"PiB", "EB"/"EiB"), and at most
Long.MAX_VALUE bytes. A size Neo4j would refuse fails the render instead.
*/}}
{{- define "pipeshub-ai.neo4jSizeMiB" -}}
{{- $q := trim (toString .value) -}}
{{- $unreadable := printf "neo4j.env.%s %q is not a size the chart can read; set the heap maximum in neo4j.env too" .name (toString .value) -}}
{{- $n := regexFind "^([0-9][0-9,]*(\\.[0-9]*)?|\\.[0-9]+)" $q -}}
{{- $exp := regexFind "^E-?[0-9]+" (trimPrefix $n $q) -}}
{{- $unit := trim (trimPrefix $exp (trimPrefix $n $q)) -}}
{{- $k := 1024 -}}{{- $m := 1048576 -}}{{- $g := 1073741824 -}}
{{- $perUnit := dict "" 1 "B" 1 "KiB" $k "KB" $k "K" $k "kB" $k "kb" $k "k" $k "MiB" $m "MB" $m "M" $m "mB" $m "mb" $m "m" $m "GiB" $g "GB" $g "G" $g "gB" $g "gb" $g "g" $g "TiB" 1099511627776 "TB" 1099511627776 "PiB" 1125899906842624 "PB" 1125899906842624 "EiB" 1152921504606846976 "EB" 1152921504606846976 -}}
{{- if or (not $n) (not (hasKey $perUnit $unit)) -}}
{{- fail $unreadable -}}
{{- end -}}
{{- $number := include "pipeshub-ai.timesPow10" (dict "value" (replace "," "" $n) "exp" (trimPrefix "E" $exp | default "0") "what" $unreadable) | float64 -}}
{{- $bytes := mulf $number (get $perUnit $unit) -}}
{{- if gt $bytes 9223372036854775807.0 }}{{ fail $unreadable }}{{ end -}}
{{- divf $bytes 1048576 | ceil | int64 -}}
{{- end -}}

{{/*
Neo4j does not size its heap or page cache from the container, so with only
the defaults a smaller limit gets settings that no longer fit and the pod is
OOM-killed. Each setting not given in neo4j.env (under its dbms_ or server_
name) is derived from the memory limit: heap maximum half (never below a
starting heap the user set, which the JVM would refuse), starting heap an
eighth (only when the maximum is derived too), page cache a quarter. The limit
is read only when something is derived.
*/}}
{{- define "pipeshub-ai.neo4jMemoryEnv" -}}
{{- $env := .Values.neo4j.env | default dict -}}
{{- $given := dict -}}
{{- range $name := list "heap_initial__size" "heap_max__size" "pagecache_size" -}}
{{- range $prefix := list "NEO4J_dbms_memory_" "NEO4J_server_memory_" -}}
{{- if hasKey $env (print $prefix $name) -}}
{{- $_ := set $given $name (dict "name" (print $prefix $name) "value" (get $env (print $prefix $name))) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- $limit := dig "limits" "memory" "" (.Values.neo4j.resources | default dict) -}}
{{- if and $limit (or (not (hasKey $given "heap_max__size")) (not (hasKey $given "pagecache_size"))) -}}
{{- $mib := include "pipeshub-ai.memoryMiB" $limit | int64 -}}
{{- if not (hasKey $given "heap_max__size") }}
{{- $max := div $mib 2 -}}
{{- if hasKey $given "heap_initial__size" -}}
{{- $max = max $max (include "pipeshub-ai.neo4jSizeMiB" (get $given "heap_initial__size") | int64) -}}
{{- else }}
- name: NEO4J_server_memory_heap_initial__size
  value: "{{ div $mib 8 }}m"
{{- end }}
- name: NEO4J_server_memory_heap_max__size
  value: "{{ $max }}m"
{{- end }}
{{- if not (hasKey $given "pagecache_size") }}
- name: NEO4J_server_memory_pagecache_size
  value: "{{ div $mib 4 }}m"
{{- end }}
{{- end }}
{{- end -}}
