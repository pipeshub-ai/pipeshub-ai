{{/*
Network isolation (ADR-OPS-04, SEC-D-09). networkpolicy.yaml renders a default-deny
policy and one allow policy per component; these helpers hold what they share.
*/}}

{{/*
The pods of one chart component, as a podSelector body.
Usage: include "pipeshub-ai.np.component" (dict "root" $ "component" "main")
*/}}
{{- define "pipeshub-ai.np.component" -}}
matchLabels:
  {{- if eq .component "parsing" }}
  {{- /* parsing.yaml's pods: an application name of their own, so selected by it and their workload label. */}}
  {{- include "pipeshub-ai.parsingSelectorLabels" .root | nindent 2 }}
  pipeshub.ai/workload: parsing
  {{- else }}
  {{- include "pipeshub-ai.selectorLabels" .root | nindent 2 }}
  app.kubernetes.io/component: {{ .component }}
  {{- end }}
{{- end }}

{{/*
The stores this release runs in the namespace, as YAML (read back with fromYaml):
  <store>: { selectors: [<matchLabels>...], ports: [<client port>...], clients: [<component>...] }
A store with two label shapes (the Bitnami subchart and the chart-owned
StatefulSet) lists both; each gets its own policy, as one podSelector cannot OR
two label sets. `clients` are the components that may open its client ports;
pods of the same store reach each other on any port (replication, election). The parsing pods
(parsing.enabled) read their configuration from the key-value store, so they are a client of Redis and etcd.
*/}}
{{- define "pipeshub-ai.np.stores" -}}
{{- $chart := include "pipeshub-ai.selectorLabels" . | fromYaml -}}
{{- $instance := dict "app.kubernetes.io/instance" .Release.Name -}}
{{- $broker := eq (include "pipeshub-ai.sandboxMode" .) "broker" -}}
{{- $stores := dict -}}
{{- $mongo := list -}}
{{- if .Values.mongodb.enabled }}{{ $mongo = append $mongo (merge (dict "app.kubernetes.io/name" "mongodb") $instance) }}{{ end -}}
{{- if .Values.mongodb.builtin.enabled }}{{ $mongo = append $mongo (merge (dict "app.kubernetes.io/component" "mongodb") $chart) }}{{ end -}}
{{- if $mongo }}{{ $_ := set $stores "mongodb" (dict "selectors" $mongo "ports" (list 27017) "clients" (list "main" "mongodb-initiate")) }}{{ end -}}
{{- $redis := list -}}
{{- if and .Values.redis.enabled (not .Values.redis.external.enabled) }}{{ $redis = append $redis (merge (dict "app.kubernetes.io/name" "redis") $instance) }}{{ end -}}
{{- if .Values.redis.builtin.enabled }}{{ $redis = append $redis (merge (dict "app.kubernetes.io/component" "redis") $chart) }}{{ end -}}
{{- if $redis -}}
{{- $ports := ternary (list 6379 26379) (list 6379) (and .Values.redis.enabled .Values.redis.sentinel.enabled) -}}
{{- /* ADR-KEY-02: the broker reads its sealed document with its own Redis ACL login. */ -}}
{{- $clients := ternary (list "main" "sandbox-broker") (list "main") (and $broker (eq .Values.config.kvStoreType "redis")) -}}
{{- if .Values.parsing.enabled }}{{ $clients = append $clients "parsing" }}{{ end -}}
{{- $_ := set $stores "redis" (dict "selectors" $redis "ports" $ports "clients" $clients) -}}
{{- end -}}
{{- if .Values.etcd.enabled -}}
{{- $clients := ternary (list "main" "sandbox-broker") (list "main") (and $broker (eq .Values.config.kvStoreType "etcd")) -}}
{{- if .Values.parsing.enabled }}{{ $clients = append $clients "parsing" }}{{ end -}}
{{- $_ := set $stores "etcd" (dict "selectors" (list (merge (dict "app.kubernetes.io/component" "etcd") $chart)) "ports" (list 2379) "clients" $clients) -}}
{{- end -}}
{{- $_ := set $stores "qdrant" (dict "selectors" (list (merge (dict "app.kubernetes.io/component" "qdrant") $chart)) "ports" (list 6333 6334) "clients" (list "main")) -}}
{{- if .Values.neo4j.enabled }}{{ $_ := set $stores "neo4j" (dict "selectors" (list (merge (dict "app.kubernetes.io/component" "neo4j") $chart)) "ports" (list 7687 7474) "clients" (list "main")) }}{{ end -}}
{{- if .Values.arango.enabled }}{{ $_ := set $stores "arango" (dict "selectors" (list (merge (dict "app.kubernetes.io/component" "arango") $chart)) "ports" (list 8529) "clients" (list "main")) }}{{ end -}}
{{- if eq .Values.messageBroker.type "kafka" -}}
{{- $_ := set $stores "kafka" (dict "selectors" (list (merge (dict "app.kubernetes.io/component" "kafka") $chart)) "ports" (list 9092) "clients" (list "main")) -}}
{{- $_ := set $stores "zookeeper" (dict "selectors" (list (merge (dict "app.kubernetes.io/component" "zookeeper") $chart)) "ports" (list 2181) "clients" (list "kafka")) -}}
{{- end -}}
{{- toYaml $stores -}}
{{- end }}

{{/*
Egress rules (a YAML list) to every store `component` is a client of.
Usage: include "pipeshub-ai.np.storeEgress" (dict "root" $ "component" "main")
*/}}
{{- define "pipeshub-ai.np.storeEgress" -}}
{{- $stores := include "pipeshub-ai.np.stores" .root | fromYaml -}}
{{- range $name, $store := $stores }}
{{- if has $.component $store.clients }}
{{- range $store.selectors }}
- to:
    - podSelector:
        matchLabels:
          {{- toYaml . | nindent 10 }}
  ports:
    {{- range $store.ports }}
    - protocol: TCP
      port: {{ . }}
    {{- end }}
{{- end }}
{{- end }}
{{- end }}
{{- end }}

{{/*
The DNS egress rule every pod gets.
*/}}
{{- define "pipeshub-ai.np.dnsEgress" -}}
- to:
    {{- toYaml .Values.networkPolicy.dns | nindent 4 }}
  ports:
    - protocol: UDP
      port: 53
    - protocol: TCP
      port: 53
{{- end }}

{{/*
ipBlock peers for a list of { cidr, except } blocks. A whole address family
(0.0.0.0/0, ::/0) also excepts networkPolicy.clusterCidrs of that family; a
narrower block keeps only its own except list, since every except must fall
inside its cidr.
Usage: include "pipeshub-ai.np.ipBlocks" (dict "blocks" .blocks "cluster" .Values.networkPolicy.clusterCidrs)
*/}}
{{- define "pipeshub-ai.np.ipBlocks" -}}
{{- range .blocks }}
{{- $v6 := contains ":" .cidr }}
{{- $except := .except | default list }}
{{- if or (eq .cidr "0.0.0.0/0") (eq .cidr "::/0") }}
{{- range $.cluster }}
{{- if eq (contains ":" .) $v6 }}{{ $except = append $except . }}{{ end }}
{{- end }}
{{- end }}
- ipBlock:
    cidr: {{ .cidr }}
    {{- with ($except | uniq) }}
    except:
      {{- toYaml . | nindent 6 }}
    {{- end }}
{{- end }}
{{- end }}
