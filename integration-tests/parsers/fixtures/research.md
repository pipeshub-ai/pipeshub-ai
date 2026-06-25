# Multi-Tenant Webhook Routing in AI Agent Connector Architectures

**Authors:** Arjun Mehta¹, Priya Nair², Rahul Desai¹

¹ Department of Computer Science and Engineering, IIIT Naya Raipur  
² PipesHub AI Research, Bengaluru

**Submitted to:** ACM Symposium on Cloud Computing (SoCC) 2026  
**Date:** May 2026  
**arXiv:** [2605.00421](https://arxiv.org/abs/2605.00421) \[cs.DC\]

---

## Abstract

Modern enterprise AI platforms integrate with dozens of third-party SaaS services, each delivering asynchronous events via webhooks. As these platforms scale to thousands of tenant organizations, naive per-tenant webhook endpoint proliferation becomes operationally untenable. This paper presents **PolyRoute**, a multi-tenant webhook routing architecture that consolidates inbound event streams from heterogeneous providers — including Microsoft Graph, Slack, Google Workspace, and Salesforce — into a single ingress surface with per-tenant fan-out. We formalize the routing problem, describe HMAC-based origin verification strategies across provider classes, and evaluate PolyRoute against single-tenant baselines on throughput, latency tail, and operational overhead. Our implementation, deployed in production at PipesHub AI, handles 2.3 million webhook events per day across 1,400 tenant organizations with a p99 routing latency of 18 ms and zero cross-tenant event leakage over a 90-day observation window.

**Keywords:** webhooks, multi-tenancy, event routing, SaaS integration, AI agents, HMAC verification

---

## 1. Introduction

The proliferation of AI agent frameworks has created a new class of integration middleware: platforms that act on behalf of enterprise users by consuming real-time events from SaaS services such as email, calendar, CRM, and communication tools. These platforms must receive asynchronous push notifications — universally delivered over HTTP webhooks — from providers that were designed for single-tenant integrations.

The naive approach registers a unique webhook endpoint per tenant per provider. For a platform with *T* tenants and *P* providers, this yields *T × P* registered endpoints, each requiring independent TLS termination, HMAC secret management, and lifecycle tracking. At the scale of hundreds of tenants, this creates three compounding problems:

1. **Secret sprawl.** Each provider-tenant pair holds a distinct signing secret. Rotation, auditing, and revocation require O(T × P) API calls.
2. **Registration rate limits.** Providers such as Microsoft Graph enforce per-application subscription quotas (currently 50,000 active subscriptions per app), limiting horizontal scale.
3. **Operational surface area.** Health monitoring, re-registration on expiry, and challenge-response renewal must be implemented independently per provider.

PolyRoute addresses all three by consolidating inbound traffic behind a shared ingress and performing tenant resolution and fan-out at the application layer.

---

## 2. Background and Related Work

### 2.1 Webhook Delivery Models

Provider webhook implementations fall into three broad categories based on their verification and registration semantics:

**Challenge-response providers** (Microsoft Graph, Google Workspace) require the receiving endpoint to echo a validation token during subscription creation. This binds the endpoint URL to the subscription at registration time, making URL-per-tenant architectures the path of least resistance.

**HMAC-signed providers** (Slack, GitHub, Stripe, Salesforce) include a signature header derived from a shared secret and the raw request body. The receiver verifies the signature without any prior challenge handshake. Multiple tenants can share a single inbound URL provided the routing layer can resolve the originating tenant from the payload or a URL path component.

**Unsigned providers** (legacy systems, some enterprise software) rely entirely on network-layer controls such as IP allowlisting. These are outside the scope of this paper.

### 2.2 Prior Work

...

---

## 3. The PolyRoute Architecture

### 3.1 Ingress Layer

All inbound webhook traffic is received at a single HTTPS endpoint: `https://hooks.pipeshub.com/inbound/{provider}`. The `{provider}` path component routes the request to a provider-specific **verification handler** before any tenant resolution occurs.

```
POST /inbound/microsoft-graph
POST /inbound/slack
POST /inbound/google
POST /inbound/salesforce
```

This design decouples provider-specific verification logic from tenant routing logic.

### 3.2 Tenant Resolution

After signature verification, PolyRoute resolves the originating tenant using a provider-specific **tenant resolver**. Resolution strategies vary by provider:

| Provider | Resolution Strategy |
|---|---|
| Microsoft Graph | `tenantId` field in JWT subscription notification |
| Slack | Team ID extracted from `team_id` in event payload |
| Google Workspace | Lookup by registered `channelId` (stored at watch creation) |
| Salesforce | Org ID extracted from `organizationId` in Connected App JWT |

Resolved tenant IDs are mapped to internal organization UUIDs via a Redis-backed lookup table with a TTL of 300 seconds, reducing database load during high-volume bursts.

### 3.3 Fan-Out and Delivery

Resolved events are published to a per-tenant Kafka topic partition. Downstream agent workers subscribe to their assigned partitions, ensuring strict event ordering within a tenant while allowing parallel processing across tenants.

---

## 4. Evaluation

### 4.1 Experimental Setup

We deployed PolyRoute in production at PipesHub AI and observed system behavior over a 90-day window (February–April 2026). The cluster comprised 6 ingress nodes (4 vCPU, 8 GB RAM each) behind an AWS Network Load Balancer, and a 3-broker Kafka cluster (r6i.xlarge).

### 4.2 Throughput and Latency

Table 1 summarizes end-to-end routing latency (time from TLS termination to Kafka `produce` acknowledgment) across provider types.

**Table 1.** Routing latency by provider (90-day production data, N = 208M events)

| Provider | p50 (ms) | p95 (ms) | p99 (ms) | p99.9 (ms) |
|---|---|---|---|---|
| Microsoft Graph | 6.1 | 11.4 | 18.3 | 42.1 |
| Slack | 4.8 | 9.2 | 15.6 | 38.7 |
| Google Workspace | 7.3 | 13.1 | 21.2 | 49.4 |
| Salesforce | 5.9 | 10.8 | 17.1 | 40.2 |
| **Overall** | **5.8** | **11.1** | **18.1** | **42.6** |

### 4.3 Security: Cross-Tenant Isolation

Over the 90-day observation window, zero cross-tenant event deliveries were recorded. We define a cross-tenant delivery as any event delivered to a Kafka partition belonging to an organization other than the originating tenant.

---

## 5. Conclusion

PolyRoute demonstrates that multi-tenant webhook consolidation is achievable without sacrificing per-tenant isolation, security, or latency. The provider-agnostic ingress model reduces secret management overhead by 94% compared to per-tenant registration, while the Kafka fan-out architecture maintains strict event ordering guarantees within tenants.

Future work will address webhook replay attacks via nonce caching and extend the tenant resolution model to federated identity providers.

---

## References

[1] Fielding, R. T. (2000). *Architectural Styles and the Design of Network-based Software Architectures*. Doctoral dissertation, UC Irvine.

[2] Hohpe, G., & Woolf, B. (2003). *Enterprise Integration Patterns*. Addison-Wesley.

[3] Microsoft Corporation. (2024). *Microsoft Graph change notifications overview*. Microsoft Learn. https://learn.microsoft.com/en-us/graph/webhooks

[4] Slack Technologies. (2024). *Verifying requests from Slack*. Slack API Documentation. https://api.slack.com/authentication/verifying-requests-from-slack

[5] Apache Software Foundation. (2023). *Apache Kafka documentation: Producer API*. https://kafka.apache.org/documentation/

---

*Correspondence: arjun.mehta@students.iiitnr.ac.in*