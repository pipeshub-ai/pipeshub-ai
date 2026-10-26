# Connector intake: what to find out before writing code

Most of what a connector needs is in the source's public API documentation. Read it first. Ask the person requesting the connector only what the documentation cannot tell you: what they want, what account you can test against, and how the source is used in their organisation.

Write the answers into the PR description under a "Connector intake" heading, so the reviewer can check the build against them.

## Find out yourself, from the vendor's API documentation

Do not ask the human these. Look them up, and cite the documentation page in the PR.

- Which editions exist (cloud, self-hosted / data center) and whether their APIs differ.
- Authentication methods the API supports: OAuth 2.0 (authorization code, with or without refresh tokens, admin consent), API token, basic auth, service account, client credentials.
- The scopes or permissions each endpoint needs.
- How the content is organised: workspaces, spaces, projects, drives, folders, pages, issues, comments, attachments, and how they nest.
- How sharing works: per-item ACLs, inherited from the container, role-based, public or link sharing, groups and whether group membership can be listed.
- How to list users and groups, and whether email addresses are returned (some APIs hide them without an admin scope).
- How to list changes since a point in time: a delta or changes endpoint, `updated_since` filters, cursors, audit logs, webhooks.
- How deletions and moves are reported, if at all.
- Rate limits, pagination style, and maximum page sizes.
- How to download file content, and whether the API can return a temporary signed URL. How long that URL stays valid.
- Whether containers can be listed, or the admin must type their ids.
- Whether members, groups and per-item ACLs can be listed, and on which plan or scope.
- Whether record ids are unique across object types, or only inside one type.
- Whether an association change or a permission change updates the object's modified time.
- The search or list cap (a maximum number of results per query, not only a page size).
- Whether a successful response can be empty when the token lacks an intent or scope (a 200 with no content).
- Whether 401 or 403 counts toward an invalid-request ban.
- Whether the token is static (a bot or API token that never refreshes) or an OAuth token with a refresh token. A static token is not retried on 401 (`02-low-level-design.md`).

## Ask the human

Ask these together, in one message, before you start. Give a recommended answer for each, based on what you found in the documentation, so the human can confirm rather than research.

1. **Which edition, and which account to test against.** "The API differs between Cloud and Data Center. Which do you need, and can you provide a test tenant with credentials?" Without a test account you can write unit tests against fakes, but you cannot verify the connector end to end. Say so.
2. **What to sync.** List the object types you found (for example pages, blog posts, comments, attachments) and ask which are in scope. Recommend the ones search and chat would use.
3. **Who sets it up: an admin for the whole organisation, or each user for themselves.** This decides the connector scope (team or personal) and the auth flow. See `03-decisions.md`.
4. **How permissions must behave.** Ask whether users must see only what they can see in the source. For any enterprise source the answer is yes; confirm it anyway, and say which source sharing features cannot be mirrored (see `06-permissions.md`). If the API cannot list group members or user emails, say what that means: those grants cannot be applied, and the affected items will be visible to fewer people than in the source.
5. **Volume.** Roughly how many users, containers and items. This decides batch sizes, whether a full sync can run on every schedule, and whether incremental sync is required in the first version.
6. **Which filters admins need.** For example: only some spaces, a date range, file types. Offer the standard ones the framework already has (`SyncFilterKey`, `IndexingFilterKey` in `app/connectors/core/registry/filters.py`).
7. **Freshness.** Is a scheduled sync enough, or is near-real-time needed? Webhook handlers are not called by the platform today (see `01-high-level-design.md`), so real-time support needs a decision before you build it. A schedule does not set `fullSync`, so say how long a deletion can wait.
8. **Identity, when the source has no email.** Where the id-to-email mapping comes from. Without it, users cannot pass the App gate. Pseudo-groups do not help if the email will never appear (`06-permissions.md`).
9. **A paid tier, a privileged intent, or an app type that closes an endpoint.** Name what stays hidden if they cannot grant it.

## Stop and ask during the build when

- The documentation and the live API disagree.
- A source permission cannot be expressed in the PipesHub model without either hiding content from people who can see it, or showing content to people who cannot. Never pick the over-sharing option yourself.
- An endpoint you need requires a scope that the human may not be able to grant (an admin-only scope, a paid tier).
- The source has no way to detect deletions, and the only option is a full re-listing.
- You would need to change shared framework code (`app/connectors/core/`, `app/models/`, the graph providers). Changes there affect every connector.
