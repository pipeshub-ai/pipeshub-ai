{{- /*
R41OPS-FU-4: when the chart's etcd certificates are reissued. Helm cannot read a
certificate, so each Secret carries the date its certificate stops being valid
(annotation pipeshub.ai/tls-not-after, written at issue). The kept set is reused
only when every certificate is there and has more than a year left; otherwise
the whole set is reissued under a new CA (all or none, as before), and a kept
certificate whose date is missing or unreadable counts as expiring.
*/ -}}

{{- define "pipeshub-ai.etcdTls.leafDays" -}}1825{{- end }}
{{- define "pipeshub-ai.etcdTls.renewDays" -}}365{{- end }}
{{- define "pipeshub-ai.etcdTls.annotation" -}}pipeshub.ai/tls-not-after{{- end }}

{{- /*
The decision, kept apart from lookup so a test can drive it.
Input: dict "kept" (list of dict "crt" "notAfter"), "expected" (how many), "now" (Unix seconds).
Output: YAML {keep: bool, notAfter: "YYYY-MM-DD"}: the earliest kept date when kept,
else the date of a certificate issued now.
*/ -}}
{{- define "pipeshub-ai.etcdTls.decide" -}}
{{- $renewBy := add .now (mul (include "pipeshub-ai.etcdTls.renewDays" .) 86400) }}
{{- $keep := eq (len .kept) (int .expected) }}
{{- $earliest := "" }}
{{- range .kept }}
{{- $date := .notAfter | default "" | toString }}
{{- if or (not .crt) (not (regexMatch "^[0-9]{4}-[0-9]{2}-[0-9]{2}$" $date)) }}
{{- $keep = false }}
{{- else if le (toDate "2006-01-02" $date | unixEpoch | int64) (int64 $renewBy) }}
{{- $keep = false }}
{{- else if or (not $earliest) (lt $date $earliest) }}
{{- $earliest = $date }}
{{- end }}
{{- end }}
{{- if $keep }}
keep: true
notAfter: {{ $earliest | quote }}
{{- else }}
keep: false
notAfter: {{ dateInZone "2006-01-02" (add .now (mul (include "pipeshub-ai.etcdTls.leafDays" .) 86400) | int64) "UTC" | quote }}
{{- end }}
{{- end }}

{{- /*
The live decision for this release: looks up the three Secrets. Under helm template (no lookup) nothing is kept.
Usage: include "pipeshub-ai.etcdTls.state" . | fromYaml
*/ -}}
{{- define "pipeshub-ai.etcdTls.state" -}}
{{- $kept := list }}
{{- range $name := include "pipeshub-ai.etcdTls.names" . | fromYaml | values }}
{{- $existing := lookup "v1" "Secret" $.Release.Namespace $name }}
{{- if and $existing $existing.data (index $existing.data "tls.crt") }}
{{- $annotations := $existing.metadata.annotations | default dict }}
{{- $kept = append $kept (dict "crt" (index $existing.data "tls.crt") "notAfter" (index $annotations (include "pipeshub-ai.etcdTls.annotation" $) | default "")) }}
{{- end }}
{{- end }}
{{- include "pipeshub-ai.etcdTls.decide" (dict "kept" $kept "expected" 3 "now" (now | unixEpoch)) }}
{{- end }}

{{- define "pipeshub-ai.etcdTls.names" -}}
{{- $fullname := include "pipeshub-ai.fullname" . -}}
server: {{ printf "%s-etcd-server-tls" $fullname }}
pipeshub-ai: {{ printf "%s-etcd-client-pipeshub-ai" $fullname }}
sandbox-broker: {{ printf "%s-etcd-client-sandbox-broker" $fullname }}
{{- end }}

{{- /*
A pod annotation that changes exactly when the set is reissued, so the pods that hold a certificate or trust the CA
restart onto the new set together. Usage: include "pipeshub-ai.etcdTls.podAnnotation" .
*/ -}}
{{- define "pipeshub-ai.etcdTls.podAnnotation" -}}
{{- if .Values.etcd.enabled -}}
pipeshub.ai/etcd-tls-not-after: {{ (include "pipeshub-ai.etcdTls.state" . | fromYaml).notAfter | quote }}
{{- end -}}
{{- end }}
