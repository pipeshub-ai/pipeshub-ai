# ADR-OPS-04: Helm isolates every pod by default, and the DinD broker's egress is its sandboxes' egress

- Finding: SEC-D-09 (Helm has no `sandbox_plane` and no default-deny network policy, and the privileged DinD pod has open egress).
- Related: ADR-OPS-02 (container baseline), ADR-KEY-02 (the broker's own key and Redis login), ADR-OWD-03 (isolation and tenancy), P5.3 and S17 (the Kubernetes sandbox mode, deferred to the end).

## Context

Compose separates the sandbox plane with Docker networks: `sandbox_plane` and `sandbox_kv` are `internal`, the broker gives every allowlisted sandbox its own `--internal` /29 with no gateway address, and attaches the egress proxy to it at `.2`. Nothing in a sandbox has a route to a store, the host or cloud metadata.

Helm had none of that. `networkPolicy.enabled` was `false`; switched on, its ingress admitted `podSelector: {}` (every pod, every port) and its egress allowed all TCP to every pod. The broker's own policy was `Ingress` only, so the pod that holds the **privileged** `docker:27-dind` sidecar reached the stores, the internet, the node's IMDS and, on EKS, the Pod Identity agent. The Bitnami MongoDB and Redis subcharts shipped their own policies, which admit any source on their port and allow all egress. Policies add up, so any one of these reopened what the others closed.

The fault is shared: the chart's isolation was an opt-in list of permissive rules, not a deny with named exceptions, and nothing checked the graph the rules produced.

Two facts about the DinD shape decide the rest:

1. **Every sandbox's packets leave as the broker pod.** dockerd runs in the broker pod's network namespace and masquerades its bridges; a NetworkPolicy sees the pod's address, never a sandbox's. The broker pod's egress is therefore the ceiling of every legacy open-egress sandbox's egress.
2. **The egress proxy cannot be a pod of its own here.** The proxy knows a sandbox by the address the connection arrives on: each binding is keyed by the proxy's own address on that sandbox's network (`InMemoryEgressBindings.for_local_address`, the `.2` that `DockerEgressWiring` connects it at). Only a container on the same Docker daemon can join those networks. A Deployment behind DinD's masquerade would see one source address for every sandbox and could bind none of them, so it would refuse every request while holding a component key and a Service. A relay inside DinD would have to assert the sandbox's address to the proxy (a PROXY-protocol listener the proxy trusts), which changes the proxy's identity model and is not built.

## Decision

1. **Default-deny, on by default.** `networkPolicy.enabled: true` in `values.yaml` and every shipped values file. `<release>-default-deny` selects every pod in the namespace (`podSelector: {}`, `namespaceDefaultDeny: true`; `false` narrows it to the release's pods) for `Ingress` and `Egress`.
2. **One allow per component pair, nothing wider.** No chart rule names `podSelector: {}`, `namespaceSelector: {}`, a peerless rule, or a whole address family without its link-local range excepted. Every pod gets DNS (`networkPolicy.dns`, kube-dns in kube-system). Then:
   - **app** (`component: main`): from its own replicas on any port and from `networkPolicy.ingressControllers` on 3001 and 8088; to each store on its client ports, the broker on 8095, and `appExternalEgress` (model APIs, connectors, e2b): every address except `169.254.0.0/16`, `fe80::/10`, `fd00:ec2::254` and `networkPolicy.clusterCidrs`. `ingress`, `egress` and `extraEgress` append rules (an ALB targeting pod IPs, ElastiCache, the EKS Pod Identity agent in values-eks).
   - **each store** (MongoDB, Redis, Qdrant, Neo4j, ArangoDB, etcd, Kafka, ZooKeeper, in either the Bitnami or the chart-owned label shape): from its clients on its client ports, and from its own pods on any port (replication, election). Its clients are the app, plus Kafka for ZooKeeper, the initiate Job for MongoDB, and the broker for the KV store it logs in to (ADR-KEY-02). Its egress is DNS, its own pods and the stores it is a client of. The subcharts' own policies are off (`mongodb.networkPolicy.enabled`, `redis.networkPolicy.enabled: false`).
   - **sandbox broker**: from the app on 8095 only; to DNS, its KV store's client port, `config.sandboxEgressAllowCidrs` (an operator's package mirror), `networkPolicy.sandboxBroker.extraEgress` (an external KV), and `registryEgress`: public addresses on 443 only, excepting `169.254.0.0/16`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `100.64.0.0/10`, `fc00::/7`, `fe80::/10` and `clusterCidrs`. No rule reaches the app, another store, metadata or the Pod Identity agent.
3. **Image pulls by address, not by name.** NetworkPolicy cannot name a host, so DinD's pulls are allowed to public addresses on 443. An operator who wants pulls pinned points `global.imageRegistry` at a mirror and replaces `registryEgress.blocks` with the mirror's address; the policy then has no internet rule at all.
4. **Allowlisted sandbox egress stays refused in Helm.** The broker gets no `SANDBOX_EGRESS_PROXY_CONTAINER`, so it refuses an egress allowlist with `egress_proxy_missing` (fail closed), as it did before; coding engines and allowed websites run on Compose or, later, P5.3. No egress-proxy Deployment ships, for the reason in Context 2.
5. **The privileged pod can have nodes of its own.** `sandbox.nodePool` (`nodeSelector`, `tolerations`, `affinity`) schedules the broker apart from the app; empty keeps today's placement. A tainted pool whose nodes carry no workload identity is the recommended shape on EKS.
6. **Off is explicit and loud.** `networkPolicy.enabled: false` renders no policy, and NOTES says on every install that every pod, store and the metadata address are open, naming the privileged daemon. NOTES also names the rule an external KV store needs before the broker can start.
7. **A standing check of the graph, not of the YAML.** `deployment/helm/tests/netpol_check.py` evaluates the rendered policies with the API's semantics (isolation per direction, egress of the source and ingress of the destination) over every rendered pod, the DNS pods, an ingress controller, a pod of another namespace, and outside addresses (metadata, the Pod Identity agent, a VPC host, CGNAT, public v4 and v6). It proves every pod is isolated, that the allowed paths equal an allow-list derived from each pod's role, and flags every widening rule. `check_chart.sh` runs it on every variant; `test_netpol.py` adds the bypass cases (a rule widened to any pod, any namespace, any peer or the whole internet; the broker's `Egress` type dropped; a store that admits the broker) and the opt-out NOTES; `sandbox_topology.py` pins the broker's own rules.

### What the DinD shape still cannot do

- **The policy cannot tell the broker from its sandboxes.** A legacy open-egress sandbox can open whatever the broker pod can: public 443 and, at the network layer, the broker's own KV port. Its own firewall (in-container `OUTPUT` rules installed before any code runs, `egress_firewall.BLOCKED_CIDRS` and the never-allowed link-local ranges) refuses private and link-local addresses above that; the store still needs the broker's ACL password. Two layers, not one, and the inner one lives in the container a kernel escape would control.
- **A DinD escape is root on the node.** The policy bounds where an escaped process can connect from that pod's address, not what it does on the node (host network, kubelet credentials). The node pool and a RuntimeClass (`sandbox.runtimeClassName`) bound that; neither is the default.
- **The app may reach other namespaces' pods by address**, through its internet rule, on CNIs that match pod IPs against ipBlocks, unless `clusterCidrs` lists the pod CIDR. Their own policies decide; the chart cannot know the CIDRs.
- **Policies need an enforcing CNI.** On a CNI without NetworkPolicy support every rule here is inert; NOTES says so.
- **No coding engine and no allowed website in Helm** (Decision 4).

### The deferred path (P5.3, S17)

Each sandbox becomes its own pod (agent-sandbox CRD, a Kata or gVisor RuntimeClass) with its own address. Then the egress proxy can be its own Deployment: a policy lets a sandbox pod reach only the proxy's data port, the proxy keys bindings by the sandbox pod's address, and the proxy's egress is DNS and the gateways (the app's 8096, 8098, 3002). DinD, the privileged container and the shared-address limit above all go. This is the owner's last step and is not built here.

## As built

R41-SUPPLY (SEC-D-09) on `harness/v2-r41supply`.

- **Templates:** `templates/networkpolicy.yaml` (default-deny, the app, one policy per store and label shape, the initiate Job), `templates/_networkpolicy.tpl` (component selectors, the store table, DNS, ipBlocks with the cluster CIDRs), `templates/sandbox-broker.yaml` (the broker's `Ingress` and `Egress` policy, `sandbox.nodePool`), `templates/NOTES.txt` (the opt-out warning, the external-KV rule, the sandbox egress paragraph).
- **Values:** `values.yaml` (`networkPolicy`, `sandbox.nodePool`, the subcharts' policies off), `values-eks.yaml` (ALB ports, Pod Identity for the app only, no ingress controller), `values-cloud.yaml`, `values-local.yaml`, `tests/values-eks-kind.yaml` (on).
- **Tests:** `deployment/helm/tests/netpol_check.py`, `test_netpol.py`, `sandbox_topology.py` (broker egress), `check_chart.sh` (every variant, `broker-etcd`, `netpol-off`); the SEC-D-09 proofs in `backend/python/tests/unit/harness/security/test_sec_d_ops_supply_chain.py`, now plain tests.
- **Checked by render, schema and the path checker** (`deployment/helm/tests/check_chart.sh`, 18 variants on Kubernetes 1.24 and 1.37), not yet on a cluster.

## Deviations

- SEC-D-09 fix item 3 asked for the egress proxy ported to Helm as its own Deployment that sandbox traffic routes through. Not built: in the DinD shape no pod outside the daemon can be told sandboxes apart (Context 2), so allowlisted egress stays refused instead (Decision 4). It comes with P5.3, or earlier with a PROXY-protocol relay the proxy trusts, which changes the proxy's identity model and needs its own design.
- SEC-D-09 fix item 4: a dedicated tainted node pool is a knob (`sandbox.nodePool`), not the default; `values-eks.yaml` keeps the broker on the app node group until `deployment/helm/aws/cluster.yaml` gains a sandbox group.
- The refusals have not been shown on a cluster: a kind run on a policy-enforcing CNI is the follow-up (STATUS, SEC-D-09).

## Owner amendments

None.

## Status

**Accepted** 2026-11-13: decided from first principles, long term; built by R41-SUPPLY.
