# What the simulator's behaviour is based on

Specs kept with the GitLab connector audit, downloaded 2026-10-23. This suite carries a trimmed
copy: `specs/openapi_gitlab_subset.json` (the operations it serves), `specs/supplement.json` and
`specs/connector_tree_query.graphql` (the GraphQL query validated against the full schema):

- `specs/openapi_v2.yaml` (and `.json`, the same document parsed): GitLab's REST OpenAPI document, `doc/api/openapi/openapi_v2.yaml` from gitlab-org/gitlab master. Swagger 2.0, 898 paths.
- `specs/gitlab_graphql_introspection.json` and `specs/gitlab_schema.graphql`: GitLab's GraphQL schema. It came from an anonymous introspection query to gitlab.com.
- `specs/docs/*.md`: the REST reference pages (issues, merge requests, notes, members, users, repositories, repository files, commits, branches, groups, projects, uploads, the REST overview).
- `specs/supplement.json`: the few documented operations and parameters that openapi_v2.yaml leaves out, and three fields it documents wrongly. Each entry names its source.

How the specs are enforced:

- Every REST request is matched to an operation in the OpenAPI document.
- A query parameter GitLab defines, but the simulator does not model, is a gap, so the test fails.
- A parameter GitLab does not define is ignored, as GitLab's Grape API ignores it.
- An enum value or a malformed integer or boolean that GitLab would refuse gets GitLab's 400.
- An operation the simulator does not implement is a gap.
- Every JSON body the simulator sends is validated against the operation's 200 schema. An invalid body is a gap.
- Required fields the simulator does not model are filled with sentinels (`__sim_unmodelled__`, `-987654321`). If the connector reads one, the test fails. The read is caught by wrapping python-gitlab's `RESTObject.__getattr__`.
- If the connector reads a field the payload does not contain, the test fails. The only exceptions are fields GitLab really leaves out of some payloads. They are listed in `world.py` (`ABSENT_ON_PURPOSE`), each with its reason.
- GraphQL: the simulator answers only the connector's tree query, compared as text (whitespace aside) with `specs/connector_tree_query.graphql`, which was validated against the gitlab.com schema when it was saved. Any other query is a gap, so a changed query fails until that file is updated and checked again.

"Live" below means checked read-only on 2026-10-23 against gitlab.com:
- public `gitlab-org/gitlab`;
- the PipesHub test project `pipeshub-group1/it-nested/gl-it-primary` and a private personal project (sizes only).

Only GET requests and one GraphQL read query were sent. The 2026-10-24 rows come from a private scratch project, changed between syncs and deleted afterwards.

| Behaviour | Source | Confidence |
|---|---|---|
| `members/all` lists direct, inherited (ancestor groups) and invited-group members. Not invites, not access requests | GitLab source `lib/api/helpers/members_helpers.rb` (`non_invite.non_request`) and docs | High |
| Members listings include blocked, deactivated and banned users (`state`) and members awaiting a seat (`membership_state: awaiting`) | Same source: nothing filters user state, and `state` is only an opt-in filter (Premium). Live: both fields present on gitlab.com | High that they are listed. Live data had only active members, so blocked rows were not seen live |
| A blocked, deactivated or banned user, or an awaiting membership, gives no access | GitLab policies (`user.active?`); docs on pending members | High (blocked, banned, awaiting). Medium (deactivated: the user can sign back in, and then counts again) |
| Members payload carries `public_email` (null when not public) | Live (gitlab.com). openapi_v2 `API_Entities_Member` requires it. Switch `members_expose_public_email` covers older self-managed versions | High for gitlab.com and current versions |
| Minimal Access (5) does not reach projects | GitLab source `members_finder.rb` (`non_minimal_access`) | High |
| Invited group's access is capped at the share's role | docs/project_members.md | High |
| Guest reads issues. Planner (15) and up read confidential issues and internal notes. The author and assignees of a confidential issue read it too | GitLab permissions docs; MR 257800 (Planner has `read_internal_note`) | High |
| Code and merge requests of a private project need Reporter (20) and up. Public and internal projects: any reader | GitLab permissions docs | High |
| Notes carry `internal` (and the older `confidential`) | GitLab source `lib/api/entities/note.rb`. Live: both keys present | High |
| A new comment moves the issue's `updated_at` | Live: in 12 recent gitlab-org/gitlab issues the newest user note was within ~1 s before the issue's `updated_at`. Switch `note_bumps_issue` | High for new comments. Medium for edits and deletions (not observed separately) |
| `updated_after` / `updated_before` / `created_*` are inclusive ("on or after") | docs/issues.md | High |
| Offset pages (`page=N`), with `x-next-page`, `x-total`, `Link` headers | docs/rest__index.md. Live headers on gitlab.com | High |
| No `x-total` above 10,000 items | docs/rest__index.md ("for performance reasons") | High |
| Project issues accept `pagination=keyset` (GitLab 18.3+). Switch `issues_keyset` | docs/rest__index.md. Live: gitlab.com returns a cursor link | High |
| Projects keyset by id (`id_after`) | docs | High |
| Compare: no `overflow` field. A list cut at the instance's diff limits is flagged `compare_timeout: true` | GitLab source `lib/api/entities/compare.rb`. Live: gitlab-org/gitlab v18.0.0-ee..v18.1.0-ee returned 3,000 diffs, `compare_timeout: true`, no `overflow` key | High |
| Compare cut at `diff_max_files` (1,000 by default, 3,000 on gitlab.com, an admin can set 500 to 3,000). Switch `compare_max_files` | GitLab source `lib/gitlab/git/diff_collection.rb`, `app/models/commit.rb`. Live: 3,000 on gitlab.com | High for files. The line and byte limits are not modelled |
| Compare reports exact-content moves as `renamed_file`. Switch `compare_detects_renames` | Git rename detection (similarity-based in reality) | Medium |
| A project's old path answers 301 to `/projects/:id` after a rename or transfer (GET only) | docs/rest__index.md "Redirects" | High |
| For an old path with a dot (`user.name/x`), gitlab.com's 301 keeps the ref from the first dot (`/projects/<id>.name%2Fx`), so following it ends in 404 | Live 2026-10-24, renaming a scratch project under a user namespace with a dot | High (seen) |
| A deleted project first stays readable, renamed `<path>-deletion_scheduled-<id>`, until it is removed for good | Live 2026-10-24 on gitlab.com | High (seen); not modelled |
| Deleted or unreadable project, issue or merge request: 404 | docs; GitLab answers 404 rather than 403 for things you cannot see | High |
| Issues feature off: issue listing 403 | Behaviour of GitLab feature access levels | Medium |
| Upload download `GET /projects/:id/uploads/:secret/:filename` needs Guest and up (GitLab 17.4+) | docs/project_markdown_uploads.md | High |
| Upload markdown is `/uploads/<32 hex>/<filename>`, the filename a single path segment | docs, existing connector constant | High |
| `paginatedTree` pages folders and files in one stream; `pageInfo.hasNextPage` ends it; webPath `/{path}/-/blob/{ref}/{file}` | GraphQL schema; existing connector code. Live: the connector's exact query validates and runs on gitlab.com | High |
| `paginatedTree(ref:)` takes a branch or a commit sha, and webPath/webUrl name the ref as passed (`/-/blob/<sha>/<file>` for a sha) | Gitaly `internal/gitaly/service/commit/get_tree_entries.go` (`git.NewTreeEntry(revision, ...)`: the entry's commit oid is the requested revision), GitLab `app/presenters/blob_presenter.rb` and `tree_entry_presenter.rb` (path = `File.join(commit_id, path)`). Consistent with the live HEAD check above | High (source); a sha ref was not run live |
| `GET /projects/:id/repository/branches`: sorted by name; `search` is a case-insensitive substring match with an exact match moved first; with `^`, `$` or `*` it is an anchored pattern (`*` any run) and keeps name order; offset pages with the usual headers; `default` marks the default branch. `regex`, `sort` and `page_token` are not modelled. The operation was added to `specs/openapi_gitlab_subset.json` on 2026-10-26 from the same master document (the GET operations already in the subset were unchanged there) | docs/branches.md "List all repository branches"; GitLab source `app/finders/git_refs_finder.rb` (`by_search`, `filter_refs`, `set_exact_match_as_first_result`), `app/finders/branches_finder.rb`, `lib/api/branches.rb`, `lib/gitlab/pagination/gitaly_keyset_pager.rb` (offset pagination unless `pagination=keyset`) | High |
| Branch `merged`: the branch head is in the default branch's history (and it is not the default branch) | docs/branches.md; GitLab `repository.merged_branch_names`. First-parent history only | Medium |
| Rate limit: 429 with `Retry-After` and `RateLimit-*` headers (gitlab.com: 2,000 authenticated requests/min). Switch `rate_limit_per_minute` | GitLab docs on rate limits | Medium (the numbers vary by instance) |
| Grape documents `Issue.assignees`, `MergeRequest.assignees/reviewers` as a single user and `Member.access_level` as a string | Corrected in `specs/supplement.json` from the docs examples and live answers (arrays, integers) | High |

Not modelled (a connector call to these fails the test as a gap):
- webhooks;
- epics and work-item hierarchy;
- compare line and byte limits, and Gitaly's per-diff size cut;
- group shares beyond `shared_with` on projects and groups;
- the auditor role's documented quirks (the connector's own fallback was not exercised);
- LFS;
- wikis and snippets (the connector does not sync them).
