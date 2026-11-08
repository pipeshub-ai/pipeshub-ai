# Slack Huddle notes

Both Slack authorizations can discover channel-shared Huddle canvases with `files.list(types=canvas)` and read their Markdown through `canvases.getContent`. Tokens require `files:read` and `canvases:read`, in addition to existing connector scopes. Personal OAuth requests the added scope; existing installations need reauthorization. `canvases:write` is only needed for controlled permission testing, never for indexing.

The workspace connector reads only existing configured channels where the bot is already a member. It never joins channels. Direct individual Canvas grants are deliberately not enumerated or indexed. A separate namespaced record per channel carries that channel's verified permissions with inheritance disabled. Removing one share revokes only its record's grants. The personal connector remains gated to its creator.

The connector verifies file metadata before writing and streaming a Canvas. Public and private shares are read from both `channels` and `groups`: private channels may also have C-prefixed IDs. A live disposable Canvas test confirmed that access grants populate these fields and revocation removes them. Slack can briefly return stale metadata immediately after a successful revocation. Access changes therefore take effect after Slack propagates the change and a successful connector sync reconciles it; this is not immediate revocation.

A confirmed missing/deleted file removes the indexed copy; a confirmed lost channel share revokes access. Unknown metadata, timeouts, rate limits and missing scopes retain the prior record and mark the sync incomplete. Already indexed notes are refreshed even when their creation date falls outside the discovery window. Each scan currently pages through stored connector records to locate prior Huddle copies, and makes metadata/content requests per Canvas; large tenants should measure this cost before enabling broad sync.

Only the Canvas content is indexed. A separate Huddle transcript file is not fetched. Canvas attachments (`quip` or `canvas`) bypass generic attachment processing so they cannot inherit a broader group ACL. Legacy attachments discovered for the same Huddle are retired after the replacement is written.

Slack tokens are sent only to HTTPS files.slack.com download URLs; external URLs and redirects are rejected. This may reject a download needing a redirect rather than forwarding the token to another host.

## Validation

- Unit and synthetic Slack behavior tests cover discovery pagination, content edits, separate channel shares, source removals, access loss, personal isolation, missing scopes and unsafe download URLs.
- The existing processor write path grants and revokes direct Huddle permissions against both Neo4j and ArangoDB.
- A disposable private channel and bot-owned Canvas validated grant/revoke metadata using a separate test app. These are API permission tests, not an end-to-end production PipesHub indexing or search test.
- A real Huddle Canvas was read through the connected Slack tool. Production connector tokens, search/citations and separate transcripts were not validated.

Sources: [Canvas APIs](https://docs.slack.dev/surfaces/canvases/), [getContent](https://docs.slack.dev/reference/methods/canvases.getContent/), [file metadata](https://docs.slack.dev/reference/objects/file-object/), [grant access](https://docs.slack.dev/reference/methods/canvases.access.set/), [revoke access](https://docs.slack.dev/reference/methods/canvases.access.delete/).
