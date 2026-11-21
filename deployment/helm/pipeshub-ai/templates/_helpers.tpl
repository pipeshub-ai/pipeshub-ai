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
{{- if eq $mode "" -}}
{{- /* Unset follows the security profile (ADR-OPS-07): upstream's chart ran docker; hardened runs the broker. */ -}}
{{- $mode = ternary "broker" "docker" (eq (include "pipeshub-ai.hardened" .) "true") -}}
{{- end -}}
{{- if not (has $mode (list "local" "docker" "e2b" "broker")) -}}
  {{- fail (printf "config.sandboxMode=%q is not a supported coding sandbox. Use one of: broker, local, docker, e2b." $raw) -}}
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
  env:
    - name: DOCKER_TLS_CERTDIR
      value: ""
    # For the `docker info` probes below.
    - name: DOCKER_HOST
      value: "unix:///var/run/dind/docker.sock"
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
    # Loopback only: the default 0.0.0.0 would also serve the pod IP.
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

{{- define "pipeshub-ai.secretKeyMount" -}}
{{- if .Values.secretKeyAsFile }}
- name: secret-key
  mountPath: /run/secrets/pipeshub
  readOnly: true
{{- end }}
{{- end -}}

{{- define "pipeshub-ai.secretKeyVolume" -}}
{{- if .Values.secretKeyAsFile }}
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
