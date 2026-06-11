# Changelog

All notable changes to this project will be documented in this file.

The format follows Keep a Changelog,
and this project adheres to Semantic Versioning.

---

# Unreleased

# Added

- Dark mode support across all dashboard views
- Export to CSV for analytics reports
# Changed

- Improved token refresh logic to reduce 401 errors on slow connections
---

# 2.4.0 – 2026-05-12

# Added

- Webhook retry queue with exponential backoff (max 5 attempts)
- GET /v1/connectors/:id/status endpoint for health polling
- Support for multi-tenant Slack app routing via shared signing secrets
# Changed

- POST /v1/agents/run now accepts an optional timeout_ms parameter (default: 30000)
- Python async service layer upgraded from Python 3.10 to 3.12
# Fixed

- SignatureDoesNotMatch 403 error caused by Content-Type mismatch in S3 presigned URL uploads (#412)
- OneDrive sharedWithMe pagination silently dropping items beyond page 2 (#398)
# Deprecated

- legacy_auth flag in connector config — will be removed in v3.0.0
---

# 2.3.1 – 2026-04-01

# Fixed

- Recurring calendar events failing silently when recurrenceTimeZone was missing from PATCH body
- MongoDB aggregation pipeline performance regression in archived conversations controller (was O(n²), now O(n log n))
---

# 2.3.0 – 2026-03-18

# Added

- Salesforce connector: Lead conversion via convertLead invocable action with Tooling API fallback
- Zoom Docs toolset: create, read, update, delete operations
- isExternal field filtering in ArangoDB and Neo4j queries for legacy document compatibility
# Changed

- Zoom transcript endpoint path corrected from /meetings/{id}/recordings/transcript to /meetings/{id}/recordings
# Removed

- SalesforceAgent.list_price_books() — merged into get_products() with include_pricing=True
---

# 2.2.0 – 2026-02-10

# Added

- Microsoft Teams meeting discovery including ad-hoc meetings
- OneDrive toolset modernization: Pydantic v2 schemas, async methods, rich @tool metadata
# Fixed

- EWS fallback for suppressing cancellation emails on recurring event deletion
- MS Graph PATCH stripping recurrence data when _dict_to_event omitted recurrence handling
---

# 1.0.0 – 2025-11-01

# Added

- Initial public release
- Connectors: Outlook, OneDrive, Slack, Jira
- Multi-tenant webhook routing architecture
- HMAC signature verification for all inbound webhooks
