"""
Tool-specific guidance for the agent planner.

These guidance strings are injected into the planner system prompt
when the agent has access to specific toolsets.
"""

CLICKUP_GUIDANCE = r"""
## ClickUp Tools

### Available Tools
- get_authorized_user — current authenticated user (id, name, email)
- get_authorized_teams_workspaces — all workspaces (team_id, name) and team members
- get_spaces — spaces in a workspace
- get_folders — folders in a space
- get_lists — lists in a folder
- get_folderless_lists — lists directly in a space (no folder)
- get_tasks — filter/search tasks across workspace, space, folder, or list
- get_task — full details of a single task
- search_tasks — find tasks by keyword (name, description, custom field text)
- create_task — create a new task in a list
- update_task — update fields on an existing task
- get_comments — comments on a task or replies to a comment
- create_task_comment — add a comment or reply to a comment on a task
- create_checklist — add a checklist to a task
- create_checklist_item — add an item to a checklist
- update_checklist_item — check/uncheck or rename a checklist item
- get_workspace_docs — all docs in a workspace
- get_doc_pages — pages in a doc
- get_doc_page — full content of a single page
- create_doc — create a new doc
- create_doc_page — add a page to a doc
- update_doc_page — edit content or title of a page
- create_space — create a new space in a workspace
- create_folder — create a new folder in a space
- create_list — create a new list in a folder or folderless list in a space
- update_list — rename or update settings of a list

### Dependencies
- get_spaces              depends on: get_authorized_teams_workspaces
- get_folders             depends on: get_spaces
- get_lists               depends on: get_folders
- get_folderless_lists    depends on: get_spaces
- get_tasks               depends on: get_authorized_teams_workspaces
- get_task                depends on: get_tasks | search_tasks | create_task
- search_tasks            depends on: get_authorized_teams_workspaces
- create_task             depends on: get_lists | get_folderless_lists
- update_task             depends on: get_tasks | search_tasks | create_task
- get_comments            depends on: get_tasks | search_tasks
- create_task_comment     depends on: get_tasks | search_tasks
- create_checklist        depends on: get_tasks | search_tasks
- create_checklist_item   depends on: create_checklist | get_task
- update_checklist_item   depends on: create_checklist | get_task
- get_workspace_docs      depends on: get_authorized_teams_workspaces
- get_doc_pages           depends on: get_workspace_docs | create_doc
- get_doc_page            depends on: get_doc_pages
- create_doc              depends on: get_authorized_teams_workspaces
- create_doc_page         depends on: get_workspace_docs | create_doc
- update_doc_page         depends on: get_doc_pages | create_doc_page
- create_space            depends on: get_authorized_teams_workspaces
- create_folder           depends on: get_spaces
- create_list             depends on: get_folders
- update_list             depends on: get_lists | get_folderless_lists
- get_workspace_members   depends on: get_authorized_teams_workspaces (use for all members of a workspace; not list-specific)

### Critical Rules
- team_id and workspace_id are the same value — from get_authorized_teams_workspaces
- A space has two kinds of lists: folder lists (get_folders → get_lists) and folderless lists (get_folderless_lists). Both must be checked when searching all lists in a space.
- **Task by name:** Call **search_tasks**(team_id, keyword) first to get task_id, then use it for get_task, update_task, create_task_comment, get_comments, create_checklist, create_checklist_item, update_checklist_item. Subtask: search_tasks → get_task for list_id → create_task(list_id, name, parent=task_id).
- When creating a task as a subtask, pass the parent task_id in the parent field. The list_id must be the same list the parent task belongs to (get it from get_task(parent_id)).
- Never fabricate IDs — always obtain team_id, space_id, folder_id, list_id, task_id, doc_id, page_id, checklist_id, checklist_item_id from a prior tool call or explicit user input.
- get_authorized_user is the source for the current user's id — use it when the user says "me", "my tasks", or "assign to me".
"""


CONFLUENCE_GUIDANCE = r"""
## Confluence-Specific Guidance

### Tool Selection — Use the Right Confluence Tool for Every Task

| User intent | Correct Confluence tool | Key parameters |
|---|---|---|
| List all spaces | `confluence.get_spaces` | (no required args) |
| List pages in a space | `confluence.get_pages_in_space` | `space_id` |
| Read / get page content | `confluence.get_page_content` | `page_id` |
| Search for a page by title | `confluence.search_pages` | `title` |
| Search for content by topic/keyword | `confluence.search_content` | `query` |
| Create a new page | `confluence.create_page` | `space_id`, `page_title`, `page_content` |
| Update an existing page | `confluence.update_page` | `page_id`, `page_content` |
| Get a specific page's metadata | `confluence.get_page` | `page_id` |

**R-CONF-1: NEVER use retrieval for Confluence page content.**
When the user asks for the content, body, summary, text, or details of a Confluence page — always use `confluence.get_page_content`, not `retrieval.search_internal_knowledge`.
- ❌ "Get the content of page X" → Do NOT use retrieval → ✅ Use `confluence.get_page_content`
- ❌ "Summarize the page" → Do NOT use retrieval → ✅ Use `confluence.get_page_content`
- ❌ "What's in the Overview page?" → Do NOT use retrieval → ✅ Use `confluence.get_page_content`

**R-CONF-2: NEVER use retrieval to get page_id or space_id.**
Retrieval returns formatted text, not structured JSON — you cannot extract IDs from it. Use service tools instead.
- ❌ retrieval → extract page_id → WRONG, retrieval can't return usable IDs
- ✅ `confluence.search_pages` → extract `results[0].id` → use as `page_id`
- ✅ `confluence.get_spaces` → extract `results[0].id` → use as `space_id`

**R-CONF-3: Space ID resolution — `get_pages_in_space` accepts keys directly (NO cascade needed).**
1. Check Reference Data for a `confluence_space` entry → use its `id` field directly as `space_id` if numeric, OR its `key` field directly as `space_id`.
2. If the user mentions a space name, key (e.g., "SD", "~abc123"), or the Reference Data has a `key` → **pass it directly to `get_pages_in_space`**. The tool resolves keys to numeric IDs internally.
3. **NEVER cascade `get_spaces` → `get_pages_in_space`** just to resolve a space key to an ID. This is handled internally by `get_pages_in_space`.
4. Only call `get_spaces` first if the user wants to LIST all spaces AND THEN get pages — and in that case use `[0]` index only, never a JSONPath filter like `[?(@.key=='value')]`.
5. Exception: cascade IS appropriate when creating a page (`create_page`) and you don't know the numeric `space_id` yet.

**R-CONF-4: Page ID resolution — check conversation history first.**
1. Check if the page was mentioned or created in conversation history → use that `page_id` directly
2. Check Reference Data for a `confluence_page` entry → use its `id` directly
3. If the user provided a `page_id` → use it directly
4. Only if none of the above → cascade: call `confluence.search_pages` first, then use the result
   - Note: search might return empty results if the page title doesn't match — handle this gracefully

**R-CONF-5: Exact parameter names (never substitute).**
- `confluence.search_pages` → parameter is `title` (NOT `query`, NOT `cql`)
- `confluence.search_content` → parameter is `query` (NOT `title`, NOT `cql`)
- `confluence.create_page` → parameters are `page_title`, `page_content`, `space_id` (NOT `title`, NOT `content`)
- `confluence.update_page` → parameters are `page_id`, `page_content`, `page_title` (optional)
- `confluence.get_page_content` → parameter is `page_id` (NOT `id`, NOT `pageId`)

**R-CONF-6: Confluence storage format for create/update.**
When generating page content for `create_page` or `update_page`, use HTML storage format:
- Heading 1: `<h1>Title</h1>`
- Heading 2: `<h2>Section</h2>`
- Paragraph: `<p>Text here</p>`
- Bold: `<strong>bold</strong>`, Italic: `<em>italic</em>`
- Bullet list: `<ul><li>item</li><li>item</li></ul>`
- Numbered list: `<ol><li>step</li><li>step</li></ol>`
- Code block: `<pre><code>code here</code></pre>`
- Table: `<table><tr><th>Col</th></tr><tr><td>val</td></tr></table>`

**R-CONF-7: NEVER use retrieval when Confluence tools can directly serve the request.**
- List spaces → `confluence.get_spaces`
- List pages in a space → `confluence.get_pages_in_space`
- Read page → `confluence.get_page_content`
- None of these should ever be replaced by retrieval

**R-CONF-8: For information queries about topics/concepts, use `confluence.search_content` — NEVER ask for space_id/page_id.**
When the user asks about a topic, concept, policy, process, or any information query (even if vague), use `confluence.search_content` to search across all Confluence content. This tool searches full page content, comments, and labels — exactly like the Confluence search bar.

**CRITICAL: NEVER ask for space_id or page_id when the user is asking an information query.**

Examples:
- ❌ "What is HR policy?" → Do NOT ask for space_id/page_id → ✅ Use `confluence.search_content` with `query="HR policy"`
- ❌ "Tell me about deployment process" → Do NOT ask for space_id/page_id → ✅ Use `confluence.search_content` with `query="deployment process"`
- ❌ "Find information about onboarding" → Do NOT ask for space_id/page_id → ✅ Use `confluence.search_content` with `query="onboarding"`
- ❌ "What are the API guidelines?" → Do NOT ask for space_id/page_id → ✅ Use `confluence.search_content` with `query="API guidelines"`
- ✅ "Get page content for page 12345" → Use `confluence.get_page_content` with `page_id="12345"` (user provided specific page ID)
- ✅ "List pages in space SD" → Use `confluence.get_pages_in_space` with `space_id="SD"` (user provided specific space)

**When to use `confluence.search_content`:**
- User asks "what is X", "tell me about X", "find information about X", "search for X"
- User asks about a topic, concept, policy, process, or documentation
- User query is an information/knowledge request (not a specific page/space request)
- Query could match content across multiple pages/spaces

**When NOT to use `confluence.search_content`:**
- User provides a specific page_id → Use `confluence.get_page_content`
- User provides a specific space_id and wants pages in that space → Use `confluence.get_pages_in_space`
- User provides a specific page title → Use `confluence.search_pages` (title-only search)
- User wants to create/update a page → Use `confluence.create_page` / `confluence.update_page`

**Parameter usage:**
- `query`: The search query string (e.g., "HR policy", "deployment process")
- `space_id`: Optional — only use if user explicitly mentions a specific space to limit search
- `content_types`: Optional — defaults to both "page" and "blogpost"
- `limit`: Optional — defaults to 25 results

**Example — information query:**
```json
{
  "tools": [
    {"name": "confluence.search_content", "args": {"query": "HR policy"}}
  ]
}
```

**Example — information query with space restriction:**
```json
{
  "tools": [
    {"name": "confluence.search_content", "args": {"query": "onboarding process", "space_id": "HR"}}
  ]
}
```
"""


GITHUB_GUIDANCE = r"""
## GitHub-Specific Guidance

### CRITICAL: Owner context first — never pass "me" to list_repositories or repo-scoped tools
- **Only** `github.get_owner` accepts **owner=`me`** (to get the authenticated user's profile). The response includes **login** (the actual username).
- **All other tools** (list_repositories, list_issues, get_repository, get_issue, list_pull_requests, etc.) require the **actual GitHub login** (username or org name). **Never** pass the literal "me" to these tools — the API will fail with 404.
- When the user says "my repos", "my issues", "my repo X", etc.: **first** call **`github.get_owner`(owner=`me`)** to get the authenticated user; then use the **`login`** from that response as **user** or **owner** in every subsequent tool call.
- Do NOT ask the user "What is your GitHub username?" when they said "my" — resolve it by calling get_owner(owner="me").

### Parameter rules
- **get_owner(owner, owner_type):** Use **owner=`me`** only here to get the authenticated user; response has **login**. For another user use owner=username; for an org use owner=orgname and owner_type="organization".
- **list_repositories(user, type, per_page, page):** **user** must be a real login (from get_owner result or message). **type**: "owner", "all", "member". Never pass "me". **per_page**: default 10 when omitted, max 50. **page**: default 1. For "give all repos" / "my repos" plan only **one** list_repositories call (returns first 10 by default); do not plan multiple pages.
- **Repo-scoped tools** (get_repository, list_issues, get_issue, create_issue, update_issue, close_issue, list_issue_comments, get_issue_comment, create_issue_comment, get_pull_request, get_pull_request_commits, get_pull_request_file_changes, get_pull_request_reviews, create_pull_request_review, list_pull_requests, create_pull_request, merge_pull_request, list_pull_request_comments, create_pull_request_review_comment, edit_pull_request_review_comment): **owner** and **repo** must be real values. Never pass "me" as owner.
- **list_issues(owner, repo, state, labels, assignee, per_page, page):** Returns **only issues** (pull requests are excluded). state = "open" | "closed" | "all". Use labels/assignee when user filters. per_page (default 10, max 50), page (default 1) for pagination.
- **list_pull_requests(owner, repo, state, head, base):** state = "open" | "closed" | "all". head/base filter by branch when needed.
- **search_repositories(query):** Use for "find repos about X", "search for Python repos". Query examples: "machine learning", "language:python stars:>100", "react in:name".
- **create_repository(name, private, description, auto_init):** Creates a repo under the authenticated user. Do NOT call get_owner before create_repository. **name** is the only required param (take from the user query). For anything not said: **private** = true, **description** = omit, **auto_init** = true. Never ask the user for private/public, description, or README — use defaults and run the tool.

### Tool Selection — Use the Right GitHub Tool for Every Task

| User intent | Correct GitHub tool | Key parameters |
|---|---|---|
| Who am I? / My profile / Get my login for later steps | `github.get_owner` | owner=`me`, owner_type=user |
| Another user's or org's profile | `github.get_owner` | owner=username or orgname, owner_type=user or organization |
| List my repos / Give all my repositories | First get_owner(me), then `github.list_repositories` | user=<login from get_owner>, type=owner |
| List repos for user X (e.g. darshangodase) | `github.list_repositories` | user=darshangodase (no get_owner needed) |
| Get one repo details (owner/repo known or from list) | `github.get_repository` | owner, repo |
| Create a new repository | `github.create_repository` | name only (from query). No get_owner. Defaults: private=true, description=omit, auto_init=true. Never ask for optional fields. |
| List issues in a repo (open/closed/all) | `github.list_issues` | owner, repo, state (open/closed/all), optional labels, assignee |
| Get issue
| List / Get / Create **issue comments** | `list_issue_comments` / `get_issue_comment` / `create_issue_comment` | owner, repo, number (issue); comment_id for get |
| List PRs in a repo / Get PR
| **Review this PR** / **What changes in this PR** / What files changed | `github.get_pull_request_file_changes` | owner, repo, number (PR). Returns list of changed files with path, status, additions, deletions. |
| PRs on repo X / Give PR on repo <name> (user gave repo name) | get_owner(me) then `github.list_pull_requests` | owner=<login>, repo=<name user said>. Do NOT use search_repositories. |
| **Reviews on a PR** (who approved / requested changes) | `github.get_pull_request_reviews` | owner, repo, number. Returns who approved, requested changes, or left a review comment. |
| **Submit a PR review** (approve / request changes / comment) | `github.create_pull_request_review` | owner, repo, number; event (APPROVE | REQUEST_CHANGES | COMMENT; default COMMENT — omit for general comment); optional body. Use to approve a PR (event=APPROVE), request changes (event=REQUEST_CHANGES, body recommended), or leave a general review comment (event=COMMENT or omit). |
| List / Create / Edit **PR review comments** (line-level) | `list_pull_request_comments` / `create_pull_request_review_comment` (new comment) / `edit_pull_request_review_comment` | New comment: call **get_pull_request_commits**, then set commit_id to **{{github.get_pull_request_commits.last_commit_sha}}** (do not use data[-1].sha). Then create_pull_request_review_comment(owner, repo, number, body, commit_id, path, line). |
| Search GitHub for repos by keyword | `github.search_repositories` | query (e.g. "python web framework") |

### Query → Tool flow (examples)

**"My repos" / "Give all repos" / "List my repositories":** get_owner(owner="me") → **one** list_repositories(user=<login>, type="owner"). Default is 10 repos per page (max 50). Omit per_page/page for default 10; do not plan multiple list_repositories calls.
**"My issues in repo X" / "Open issues in my repo portfolio_new":** get_owner(owner="me") → list_issues(owner=<login>, repo="X" or "portfolio_new", state="open" or "all").
**"List my PRs" (no repo named):** get_owner(owner="me") → list_repositories(user=<login>) → for each repo (or first): list_pull_requests(owner=<from full_name>, repo=<from full_name>, state="open").
**"Give PR on repo X" / "PRs on repo <name>" / "pull requests in repo <name>" (user names the repo):** get_owner(owner="me") → list_pull_requests(owner=<login from get_owner>, repo=<name user said>, state="open" or "all"). Do NOT use search_repositories — the user already gave the repo name; use it as **repo** and **owner** = login from get_owner. Only two tools: get_owner, then list_pull_requests.
**"Summarize my open issues" (no repo):** get_owner(owner="me") → list_repositories(user=<login>) → list_issues(owner, repo, state="open") for relevant repo(s); then summarize from results.
**"Create a repo" / "Create repository X" / "New GitHub repo":** Plan exactly one tool: `github.create_repository` with name from the query. Do NOT call get_owner. Do NOT set needs_clarification. Use defaults for private, description, auto_init. Never ask the user for private/public, description, or README.
**"Get repo darshangodase/portfolio_new" (explicit owner/repo):** get_repository(owner="darshangodase", repo="portfolio_new"). No get_owner needed.
**"Issues in darshangodase/portfolio_new" (explicit):** list_issues(owner="darshangodase", repo="portfolio_new", state="open" or "all"). No get_owner needed.
**"Find repos about machine learning" / "Search for Python repos":** search_repositories(query="machine learning" or "language:python"). Optionally then get_repository(owner, repo) for a chosen result using full_name.
**"Who is pipeshub-ai on GitHub?" (org or user):** get_owner(owner="pipeshub-ai", owner_type="organization" or "user").
**"Comments on issue
**"Add a comment to issue
**"Reviews on PR
**"Reviews on my PRs" / "Reviews on repo X PRs" (no "every"/"all"):** get_owner → list_pull_requests → **one** get_pull_request_reviews(owner, repo, number=**list_pull_requests.data[0].number**).

**"Give repo details then all PR details and every PR reviews" / "Every PR reviews" / "All PRs and every PR reviews":** get_owner(owner="me") → get_repository(owner, repo) → list_pull_requests(owner, repo, state="all") → then **one get_pull_request_reviews per PR**: plan get_pull_request_reviews(owner, repo, number=**list_pull_requests.data[0].number**), get_pull_request_reviews(owner, repo, number=**list_pull_requests.data[1].number**), … up to **list_pull_requests.data[9].number** (indices 0–9). Executor skips steps where the index does not exist.
**"Review comments on PR
**"Comment on this PR" / "Add a review comment on PR
**"Review this PR" / "What changes in this PR?" / "What files changed in PR
**"Approve this PR" / "Approve PR
**"Request changes on this PR" / "Request changes on PR
**"Leave a review on this PR" / "Submit a review" / "Add a general review comment" (no specific line):** create_pull_request_review(owner, repo, number, body="...") — event defaults to COMMENT; omit event for a general comment.

### Rules (R-GITHUB)

**R-GITHUB-1: For "my" (authenticated user) — always get owner context first.**
- "My repos" / "Give all my repos" / "List my repositories" → Step 1: get_owner(owner="me"). Step 2: **exactly one** list_repositories(user=<login>, type="owner"). Do NOT pass user="me". Default 10 repos (max 50); omit per_page/page for default. Do NOT plan multiple list_repositories for different pages.
- "My issues" / "issues in my repo X" / "list issues in my repo portfolio_new" → Step 1: get_owner(owner="me"). Step 2: list_issues(owner=<login>, repo="X" or "portfolio_new", state=...). Do NOT pass owner="me".
- "My PRs" / "pull requests in my repo Y" → Same: get_owner(owner="me") first, then list_pull_requests(owner=<login>, repo=Y). Never owner="me".

**R-GITHUB-2: Never pass "me" to list_repositories, list_issues, get_repository, or any repo-scoped tool.** The API expects a real login. Use the **login** from get_owner(owner="me") result.

**R-GITHUB-3: When repo is not specified (e.g. "list my issues" without naming a repo).**
- Step 1: get_owner(owner="me") → **login**.
- Step 2: list_repositories(user=<login>) → list of repos; each has **full_name** (owner/repo_name) and **name**.
- Step 3: For each repo (or first/few): owner = first part of full_name, repo = second part; call list_issues(owner, repo, ...) or list_pull_requests(owner, repo, ...). Do not ask the user which repo — use the list.

**R-GITHUB-4: Chaining — use previous tool results.**
- **From get_owner(owner=me):** Use the **login** field as **user** in list_repositories and as **owner** in all repo-scoped tools.
- **From list_repositories:** Each item has **full_name** (e.g. "alice/my-repo"). owner = part before "/", repo = part after "/". Use these in list_issues, get_issue, list_pull_requests, get_repository, etc.

**R-GITHUB-5: Parse owner and repo from the user message when given.**
- "username/repo_name" or "repo repo_name by username" → owner=username, repo=repo_name.
- "issue
- "open issues" / "closed PRs" → state="open" or state="closed"; default to state="open" when user says "issues" or "PRs" without specifying.

**R-GITHUB-6: When user provides an explicit owner/repo or username,** pass them directly. For "my" always resolve via get_owner(owner="me") first. For "repos of X" use list_repositories(user=X) directly if X is a known username.

**R-GITHUB-7: Do not ask for clarification** when the user said "my" and you can resolve it with get_owner(owner="me"). Plan get_owner(owner="me") as the first tool.

**R-GITHUB-8: Search vs list vs repo name given.** Use **search_repositories(query)** only when the user wants to find repos by keyword/topic (e.g. "find Python repos", "repos about ML"). Use **list_repositories(user=X)** when the user wants to list repos belonging to a user (e.g. "my repos", "darshangodase's repos"). When the user **names a specific repo** (e.g. "PRs on repo X", "give PR on repo <name>", "issues in repo my-app") — use that name as **repo** and get **owner** from get_owner(owner="me"); then call list_pull_requests(owner=<login>, repo=<name>) or list_issues(owner=<login>, repo=<name>). Do NOT use search_repositories when the user already gave the repo name.

**R-GITHUB-9: No pagination for list_repositories for "all my repos".** When the user asks for "all my repos", "give all repos", "list my repositories": plan only **two tools** — get_owner(owner="me") and **one** list_repositories(user=<login>, type="owner"). Default is 10 repos per page (max 50). Do NOT plan multiple list_repositories calls (e.g. page 1, 2, 3...) for this query.

**R-GITHUB-10: When the user asks to comment on a PR (add a review comment):** Resolve owner, repo, and PR number. Call **get_pull_request_commits**(owner, repo, number), then set **commit_id** to **{{github.get_pull_request_commits.last_commit_sha}}** for **create_pull_request_review_comment**. Do NOT use data[-1].sha. Do not ask the user for a commit SHA — obtain it via get_pull_request_commits.

**R-GITHUB-11: When the user says "review this PR" or "what changes in this PR" or "what files changed":** Call **get_pull_request_file_changes**(owner, repo, number). Do not use get_pull_request (metadata only) or get_pull_request_commits (commits list) for this — use the file changes tool.

**R-GITHUB-12: "Every PR reviews" vs "reviews on my PRs".** When the user wants **"every PR reviews"** or **"all PR details and every PR reviews"** (or similar): plan **one get_pull_request_reviews per PR** — get_pull_request_reviews(owner, repo, number=**list_pull_requests.data[0].number**), get_pull_request_reviews(owner, repo, number=**list_pull_requests.data[1].number**), … up to **list_pull_requests.data[9].number**. The executor skips steps for non-existent indices. When the user asks only **"reviews on my PRs"** (no "every"/"all"), plan **one** get_pull_request_reviews with number=**list_pull_requests.data[0].number**. For get_pull_request_file_changes, get_pull_request_commits, list_pull_request_comments (when user did not say "every"): use a single PR (e.g. data[0].number) unless a PR number was specified.

**R-GITHUB-13: Submit PR review (approve / request changes / general comment).** Use **create_pull_request_review** for an **overall** review: event=APPROVE to approve, event=REQUEST_CHANGES to request changes (include body), or omit event (default COMMENT) for a general review comment. For a **line-level** or **file-level** comment on specific code, use **create_pull_request_review_comment** (requires get_pull_request_commits → commit_id, path, line).

**R-GITHUB-14: Next-page pagination.** For paginated GitHub tools (**list_repositories**, **list_issues**, **list_pull_requests**, **search_repositories**), when the user asks for the next page or more of the same list in the context of a paginated result they were just shown, call the **same tool** again with the **same parameters** except **page** incremented (e.g. if they saw page 1, use page=2). Infer which tool and which parameters from conversation context.

"""


JIRA_GUIDANCE = r"""
## Jira-Specific Guidance

### Tool Selection — Use the Right Jira Tool for Every Task

| User intent | Correct Jira tool | Key parameters |
|---|---|---|
| Search / list issues | `jira.search_issues` | `jql` (required), `maxResults` |
| Get a specific issue | `jira.get_issue` | `issue_key` |
| Create an issue / ticket | `jira.create_issue` | `project_key`, `summary`, `issue_type` |
| Update an issue | `jira.update_issue` | `issue_key`, fields to update |
| Assign an issue | `jira.assign_issue` | `issue_key`, `accountId` |
| Add a comment | `jira.add_comment` | `issue_key`, `comment` |
| Get issue comments | `jira.get_comments` | `issue_key` |
| Transition issue status | `jira.transition_issue` | `issue_key`, `transition_id` or `status` |
| List projects | `jira.get_projects` | (no required args) |
| Find a user by name/email | `jira.search_users` | `query` (name or email) |
| Get sprints | `jira.get_sprints` | `board_id` |

**R-JIRA-1: NEVER fabricate accountIds or user identifiers.**
Always call `jira.search_users(query="name or email")` to resolve a user to their `accountId` before using it in `assign_issue`, `jql`, or any other field. Never invent or guess an accountId.

**R-JIRA-2: Every JQL query MUST include a time filter.**
Unbounded JQL will cause an error. Add a time filter to every JQL string.
- ✅ `project = PA AND assignee = currentUser() AND resolution IS EMPTY AND updated >= -30d`
- ❌ `project = PA AND assignee = currentUser() AND resolution IS EMPTY` ← UNBOUNDED, will fail

Time filter reference:
- Last 7 days: `updated >= -7d`
- Last 30 days: `updated >= -30d`
- Last 90 days: `updated >= -90d`
- This year: `updated >= startOfYear()`
- Custom: `updated >= -60d`

**R-JIRA-3: JQL syntax rules.**
- Unresolved issues: `resolution IS EMPTY` (not `resolution = Unresolved`)
- Current user: `assignee = currentUser()` (parentheses required)
- Empty fields: use `IS EMPTY` or `IS NULL`
- Text values: always quote: `status = "In Progress"`
- Project: use KEY (e.g., `"PA"`), not name or numeric ID

**R-JIRA-4: User lookup before assignment.**
If user wants to assign an issue and provides a name/email, ALWAYS call `jira.search_users` first, then use the returned `accountId` in `jira.assign_issue`. Never skip the lookup step.
```json
{
  "tools": [
    {"name": "jira.search_users", "args": {"query": "john@example.com"}},
    {"name": "jira.assign_issue", "args": {"issue_key": "PA-123", "accountId": "{{jira.search_users.data.results[0].accountId}}"}}
  ]
}
```

**R-JIRA-5: Pagination for "all" requests.**
If the user asks for "all issues", "complete list", or "everything", handle pagination automatically:
1. First call with `maxResults: 100`
2. If response has `nextPageToken` (non-null) or `isLast: false`, add a cascaded second call
3. Continue chaining until `isLast: true` or no token
```json
{
  "tools": [
    {"name": "jira.search_issues", "args": {"jql": "project = PA AND updated >= -60d", "maxResults": 100}},
    {"name": "jira.search_issues", "args": {"jql": "project = PA AND updated >= -60d", "nextPageToken": "{{jira.search_issues.data.nextPageToken}}"}}
  ]
}
```

**R-JIRA-6: Use Reference Data for project keys.**
If Reference Data contains a `jira_project` entry, use its `key` field directly as `project_key`. Do NOT call `jira.get_projects` to re-fetch a project key you already have.

**R-JIRA-7: Topic-based ticket/issue searches — "[topic] tickets" pattern.**
When the user uses a service resource noun like "tickets", "issues", "bugs", or "epics" to describe what they want (even without "find" or "search"), ALWAYS use `jira.search_issues` with a text-based JQL query.
- Pattern: "[topic] tickets" or "[topic] issues" → `text ~ "[topic]" AND updated >= -90d`
- Example: "web connector tickets" → `jql: "text ~ \"web connector\" AND updated >= -90d"`
- Example: "authentication bugs" → `jql: "text ~ \"authentication\" AND issuetype = Bug AND updated >= -90d"`
- Use `updated >= -90d` (or wider) for topic-based searches to ensure broader coverage.
- If the service is also indexed, run `retrieval.search_internal_knowledge` IN PARALLEL with `jira.search_issues`.
```json
{
  "tools": [
    {"name": "retrieval.search_internal_knowledge", "args": {"query": "web connector", "filters": {}}},
    {"name": "jira.search_issues", "args": {"jql": "text ~ \"web connector\" AND updated >= -90d", "maxResults": 50}}
  ]
}
```
"""


MARIADB_GUIDANCE = r"""
## MariaDB-Specific Guidance

### Core Rules
- Use MariaDB tools when the user asks for database data, table details, SQL results, or table definitions.
- Prefer read-safe operations (`SELECT`, metadata introspection tools).
- Do not run destructive SQL (`DROP`, `TRUNCATE`, `DELETE`, `ALTER`) unless the user explicitly asks.
- If table/column context is unclear, discover structure first before executing SQL.
- In multi-step tasks, execute in a strict tool loop: one tool call, inspect result, then choose next tool.

### Tool Action Loop (MANDATORY)
For MariaDB work, follow this loop every time:
1. Choose exactly one next best tool.
2. Call that tool with concrete parameters (no placeholders).
3. Read the returned data/errors.
4. Decide the next single tool call.
5. Repeat until the task is complete.
- Never guess columns/table names when schema tools can confirm them.
- If a step fails, recover with introspection tools (`list_tables`, `fetch_db_schema`, `get_tables_schema`) before retrying SQL.

### Recommended Tool Order by Scenario

#### Case A: User asks a data question but table/column context is unknown
1. `mariadb.fetch_db_schema`
2. `mariadb.execute_query` (final SQL)

#### Case B: User gives table name but not columns
1. `mariadb.get_tables_schema`
2. `mariadb.execute_query`

#### Case C: User asks for table structure / DDL
1. `mariadb.get_table_ddl` (single table)

#### Case D: User asks "show full DB structure"
1. `mariadb.fetch_db_schema`
2. Optionally narrow with `mariadb.get_tables_schema` for key tables

#### Case E: User asks "list what exists"
1. `mariadb.list_tables`

### SQL Construction Guidance
- Select only needed columns; avoid `SELECT *` unless user explicitly wants all fields.
- Add sensible limits for exploratory reads (for example `LIMIT 50`), unless user requests full result.
- Use discovered column names/types from schema tools before writing joins/filters.
- For time-based requests, use explicit date predicates and clear sorting.

### Error Recovery (MariaDB)
- If SQL fails due to missing table/column:
    1. Run `mariadb.list_tables`
    2. Run `mariadb.get_tables_schema`
    3. Retry `mariadb.execute_query` with corrected identifiers
- If results are empty:
    1. Verify filters/date range
    2. Re-check columns/types via `mariadb.get_tables_schema`
    3. Retry with adjusted query

### Planning Examples (one tool after another)

Example 1: "Show total orders by status for last 30 days"
1. `mariadb.fetch_db_schema`
2. `mariadb.get_tables_schema(tables=["orders"])`
3. `mariadb.execute_query(query="SELECT status, COUNT(*) AS total_orders FROM orders WHERE created_at >= NOW() - INTERVAL 30 DAY GROUP BY status ORDER BY total_orders DESC")`

Example 2: "What columns are in invoice?"
1. `mariadb.get_tables_schema(tables=["invoice"])`
2. Optional follow-up: `mariadb.execute_query` only if user asks for row data

Example 3: "Give me the DDL for invoice"
1. `mariadb.get_table_ddl(table="invoice")`
"""


ONEDRIVE_GUIDANCE = r"""
## OneDrive-Specific Guidance

### ⚠️ CRITICAL: drive_id Resolution — ALWAYS Call `get_drives` First

Almost every OneDrive tool requires a `drive_id` parameter. **NEVER ask the user for a drive_id.** Resolve it automatically:

1. Check conversation history / Reference Data for a previously retrieved `drive_id` → use it directly
2. If unknown → call `onedrive.get_drives` first, then use the result in subsequent tools

**The `drive_id` is an internal Graph API identifier (e.g., `b!xxxx...`). Users don't know it. Always resolve it via `get_drives`.**

### ⚠️ CRITICAL: item_id Resolution — Use `search_files` or `get_files`

File operations (rename, delete, move, copy, etc.) require an `item_id`. **NEVER ask the user for an item_id.** Resolve it automatically:

1. Check conversation history / Reference Data for a previously retrieved `item_id` → use it directly
2. If the user mentions a file name → call `onedrive.search_files` to find it
3. If browsing → call `onedrive.get_files` or `onedrive.get_folder_children`

### Cascading Patterns (CRITICAL)

**Pattern: Rename a file by name**
User says: "rename X to Y"
```json
{{
  "tools": [
    {{"name": "onedrive.get_drives", "args": {{}}}},
    {{"name": "onedrive.search_files", "args": {{"drive_id": "{{{{onedrive.get_drives.data.value[0].id}}}}", "query": "X"}}}},
    {{"name": "onedrive.rename_item", "args": {{"drive_id": "{{{{onedrive.get_drives.data.value[0].id}}}}", "item_id": "{{{{onedrive.search_files.data.value[0].id}}}}", "new_name": "Y"}}}}
  ]
}}
```

**Pattern: Delete a file by name**
```json
{{
  "tools": [
    {{"name": "onedrive.get_drives", "args": {{}}}},
    {{"name": "onedrive.search_files", "args": {{"drive_id": "{{{{onedrive.get_drives.data.value[0].id}}}}", "query": "filename"}}}},
    {{"name": "onedrive.delete_item", "args": {{"drive_id": "{{{{onedrive.get_drives.data.value[0].id}}}}", "item_id": "{{{{onedrive.search_files.data.value[0].id}}}}"}}}}
  ]
}}
```

**Pattern: Search files**
```json
{{
  "tools": [
    {{"name": "onedrive.get_drives", "args": {{}}}},
    {{"name": "onedrive.search_files", "args": {{"drive_id": "{{{{onedrive.get_drives.data.value[0].id}}}}", "query": "keyword"}}}}
  ]
}}
```

**Pattern: List files (drive_id already known from conversation history)**
```json
{{
  "tools": [
    {{"name": "onedrive.get_files", "args": {{"drive_id": "b!abc123..."}}}}
  ]
}}
```

### Multiple Drives

If the user has multiple drives and specifies which one (e.g., "my OneDrive (Business)"), match by `name` or `driveType` from `get_drives` results. If ambiguous:
- Use the first drive by default (`value[0]`)
- If the user specifies a drive name, select the matching drive from `get_drives` results

### Never Ask for These

- ❌ "What is your drive_id?" → ✅ Call `onedrive.get_drives` to resolve it
- ❌ "What is the item_id?" → ✅ Call `onedrive.search_files` or `onedrive.get_files` to resolve it
- ❌ "Provide your drive_id" → ✅ Always auto-resolve via API calls
"""


OUTLOOK_GUIDANCE = r"""
## Outlook-Specific Guidance

**Tool Selection**
| User intent | Tool | Required fields | Optional fields |
|---|---|---|---|
| Send new email | `send_email` | `to_recipients`, `subject`, `body` | `body_type`, `cc_recipients`, `bcc_recipients` |
| Reply to email | `reply_to_message` | `message_id`, `comment` | — |
| Reply-all | `reply_all_to_message` | `message_id`, `comment` | — |
| Forward email | `forward_message` | `message_id`, `to_recipients` | `comment` |
| Search/list emails | `search_messages` | at least one of `search` or `filter` | `top`, `orderby` |
| Get specific email | `get_message` | `message_id` | — |
| List calendar events | `get_calendar_events` | `start_datetime`, `end_datetime` | `top` |
| Create calendar event | `create_calendar_event` | `subject`, `start_datetime`, `end_datetime` | `timezone`, `body`, `location`, `attendees`, `is_online_meeting`, `recurrence` |
| Get specific event | `get_calendar_event` | `event_id` | — |
| Update event | `update_calendar_event` | `event_id` + at least one field to change | `subject`, `start_datetime`, `end_datetime`, `timezone`, `body`, `location`, `attendees`, `is_online_meeting`, `recurrence` |
| Delete event | `delete_calendar_event` | `event_id` | — |
| Search events by name | `search_calendar_events_in_range` | `keyword`, `start_datetime`, `end_datetime` | `top` |
| Get recurring events ending | `get_recurring_events_ending` | `end_before` | `end_after`, `timezone`, `top` |
| Delete recurring occurrences | `delete_recurring_event_occurrence` | `event_id`, `occurrence_dates` | `timezone` |
| Get free time slots | `get_free_time_slots` | `start_datetime`, `end_datetime` | `timezone`, `slot_duration_minutes` |
| List mail folders | `get_mail_folders` | — | `top` |

**⚠️ Never assume or fabricate required field values — always resolve them via the decision tree below.**

---
---
**Date Handling**

If a date includes a full month and day (e.g. "March 30"), use it directly.
If only a day is given (e.g. "30th", "till 23rd", "28–30"), always ask for the month before doing anything — never assume or infer it from context or conversation history.
Year always defaults to the current year — never ask the user for the year.
All other date values (e.g. "today", "next Monday", "end of month") should be resolved by the agent — never ask the user for these.
Never perform any create, update, or delete action on an ambiguous date
---

**R-OUT-0: Data Resolution — Fetch-Before-Ask (MANDATORY)**

The Fetch-Before-Ask Decision Tree (run this for every unresolved parameter):


## R-OUT-0: Universal Data Resolution Hierarchy (CRITICAL — applies to EVERY tool call)

Before executing any tool, every required parameter must be resolved. Use this strict
priority order — never skip a tier, never jump to "ask the user" while a higher tier
is available.

### Resolution Tiers (evaluate in order):

**Tier 1 — Explicit in the current message**
The user stated the value directly.
→ "extend the Fixes event by 10 days" → event name = "Fixes", delta = 10 days

**Tier 2 — Derivable from the current message**
The value isn't stated but can be computed from what was said.
→ "by 10 days" = relative delta → new end date = current end date + 10 (fetch current first)
→ "end of year" = December 31 of current year
→ "next quarter" = last day of next fiscal quarter
→ "this week" = Monday 00:00:00 to Sunday 23:59:59
Never ask the user to restate something you can compute yourself.

**Tier 3 — Available in conversation history or prior tool results**
A previous tool call or message already returned this value.
→ event_id was returned in the last search result → reuse it, don't re-fetch
→ user mentioned "the meeting we just looked at" → use the last retrieved event
Always check conversation history before making a redundant API call.

**Tier 4 — Fetchable via an existing tool**
The value doesn't exist yet but a tool can retrieve it right now.
→ Need current recurrence end date? → call search_calendar_events_in_range first
→ Need a message_id? → call search_messages first
→ Need an event_id? → call get_calendar_events for the relevant time window
→ Need company holidays? → search Confluence
This is the fetch-before-ask rule. If a tool can get it, USE the tool.

**Tier 5 — Ask the user (last resort only)**
Only reach this tier if ALL of the following are true:
  a) The value cannot be derived from the current message (not Tier 2)
  b) It does not exist in conversation history (not Tier 3)
  c) No tool can retrieve it — it is subjective, personal, or unknowable by the system
     (e.g., "which project should I assign this to?", "who should I invite?")
When asking, ask for ALL missing Tier-5 values in a single message. Never ask one
at a time across multiple turns.

---

### Applied to common patterns:

| Missing value | Wrong (jump to Tier 5) | Correct (Tier 4) |
|---|---|---|
| event_id for "the standup" | Ask user for event ID | get_calendar_events for likely time range |
| recurrence end date of "Fixes" | Ask user what the current end date is | search_calendar_events_in_range("Fixes") |
| message_id for "John's email" | Ask user for message ID | search_messages("from:john") |
| new end date when user says "by 10 days" | Ask user what the new date should be | fetch current end date → compute + 10 days |
| holidays in extension range | Skip or ask user | search Confluence for holiday calendar |

---

### The Fetch-Before-Ask Decision Tree (run this for every unresolved parameter):

```
Is the value stated or computable from the user's message?
  YES → use it (Tier 1 or 2)
  NO  → Is it in conversation history or prior tool results?
          YES → use it (Tier 3)
          NO  → Does any available tool return this kind of data?
                  YES → call that tool now, then proceed (Tier 4)
                  NO  → ask the user (Tier 5)
```

This hierarchy is non-negotiable. Asking the user for data that a tool can fetch is always wrong, regardless of which workflow is active.

---

**R-OUT-1:** Never use `retrieval.search_internal_knowledge` for Outlook queries — always use Outlook tools.

**R-OUT-2:** Never ask the user for a `message_id` — search via `search_messages` first, then cascade.

**R-OUT-3:** Never ask the user for an `event_id` — fetch via `get_calendar_events` or `search_calendar_events_in_range` first, then cascade.

**R-OUT-4:** Use `search` for keyword queries, `filter` for OData conditions (e.g. `isRead eq false`, date filters).

**R-OUT-5:** Always provide both `start_datetime` and `end_datetime` for calendar tools. Infer sensible defaults from user intent. Use ISO 8601 without `Z`: `2026-03-03T09:00:00`.

---

**R-OUT-6: Recurring events — pass a `recurrence` dict with `pattern` + `range`. NEVER a plain string.**

The `recurrence` field is a plain Python dict (not a nested model). All keys are **camelCase** matching the MS Graph API directly.

---

**`pattern` keys — how often it repeats:**

| `type` value | Extra required keys | Use case |
|---|---|---|
| `"daily"` | *(none)* | Every N days |
| `"weekly"` | `daysOfWeek` (list of strings) | Specific weekdays each week |
| `"absoluteMonthly"` | `dayOfMonth` (int) | Same date each month (e.g. 15th) |
| `"relativeMonthly"` | `daysOfWeek`, `index` | Relative day each month (e.g. first Monday) |
| `"absoluteYearly"` | `dayOfMonth`, `month` | Same date each year (e.g. March 15) |
| `"relativeYearly"` | `daysOfWeek`, `index`, `month` | Relative day each year (e.g. last Friday of March) |

- `interval` (int, default `1`): repeat every N units
- `daysOfWeek` values: `"Sunday"` `"Monday"` `"Tuesday"` `"Wednesday"` `"Thursday"` `"Friday"` `"Saturday"`
- `index` values: `"first"` `"second"` `"third"` `"fourth"` `"last"`
- `dayOfMonth`: int 1–31
- `month`: int 1–12

**`range` keys — when the series ends:**

| `type` value | Extra required keys |
|---|---|
| `"endDate"` | `startDate`, `endDate` (YYYY-MM-DD) |
| `"noEnd"` | `startDate` (YYYY-MM-DD) |
| `"numbered"` | `startDate`, `numberOfOccurrences` (int) |

**⚠️ `startDate` MUST match the date portion of `start_datetime`.**

---

**All 6 pattern type examples:**

```json
// 1. Daily — every day, 30 occurrences
"recurrence": {
  "pattern": {"type": "daily", "interval": 1},
  "range":   {"type": "numbered", "startDate": "2026-03-01", "numberOfOccurrences": 30}
}

// 2. Weekly — Mon, Wed, Fri until Dec 31
"recurrence": {
  "pattern": {"type": "weekly", "interval": 1, "daysOfWeek": ["Monday", "Wednesday", "Friday"]},
  "range":   {"type": "endDate", "startDate": "2026-03-02", "endDate": "2026-12-31"}
}

// 3. absoluteMonthly — 15th of every month, forever
"recurrence": {
  "pattern": {"type": "absoluteMonthly", "interval": 1, "dayOfMonth": 15},
  "range":   {"type": "noEnd", "startDate": "2026-03-15"}
}

// 4. relativeMonthly — last Friday of every month, 6 times
"recurrence": {
  "pattern": {"type": "relativeMonthly", "interval": 1, "daysOfWeek": ["Friday"], "index": "last"},
  "range":   {"type": "numbered", "startDate": "2026-03-28", "numberOfOccurrences": 6}
}

// 5. absoluteYearly — every March 15, forever
"recurrence": {
  "pattern": {"type": "absoluteYearly", "interval": 1, "dayOfMonth": 15, "month": 3},
  "range":   {"type": "noEnd", "startDate": "2026-03-15"}
}

// 6. relativeYearly — first Monday of March every year, 3 times
"recurrence": {
  "pattern": {"type": "relativeYearly", "interval": 1, "daysOfWeek": ["Monday"], "index": "first", "month": 3},
  "range":   {"type": "numbered", "startDate": "2026-03-02", "numberOfOccurrences": 3}
}
```

**Common mistakes to avoid:**
- ❌ `"recurrence": "weekly"` — plain string, NOT valid
- ❌ snake_case keys like `days_of_week`, `day_of_month`, `start_date`, `end_date`, `number_of_occurrences` — NOT valid
- ✅ camelCase keys: `daysOfWeek`, `dayOfMonth`, `startDate`, `endDate`, `numberOfOccurrences`
- ❌ `startDate` set to a datetime string — must be `YYYY-MM-DD` date only
- ❌ `startDate` not matching `start_datetime` date portion — they MUST align

---

**R-OUT-7:** For cross-service tasks, always fetch from Outlook first, then act in the target service. Never pass raw API responses downstream — write clean, human-readable content.

**R-OUT-8:** Use ISO 8601 without `Z` suffix: ✅ `2026-03-03T09:00:00` ❌ `2026-03-03T09:00:00Z`. Always pair with an explicit `timezone` field.

**R-OUT-9/10:** Use `reply_to_message` for replies, `reply_all_to_message` for reply-all, `forward_message` for forwards. Never use `send_email` for replies or forwards.

**R-OUT-11:** `search_messages` defaults to `top: 10`, max 50 per call. For "all" requests, set `top` to the number requested and note if results are limited.

**R-OUT-12:** For recurring-specific tasks: use `get_recurring_events_ending` to find expiring series, `search_calendar_events_in_range` to find by name, `delete_recurring_event_occurrence` to remove specific dates (batch all dates in one call), `update_calendar_event` to extend (preserve pattern, change only `range.endDate`). Always use `seriesMasterId` for series operations.

**R-OUT-13**: *Important* Any action (create, update, delete, cancel, remove) involving a date or date range requires an unambiguous month. If the user provides only a day or day range (e.g. "till 23rd", "28 - 30") without explicitly stating the month, always stop and ask for the month before executing — never assume, infer from context, or carry forward a month from earlier in the conversation.
This applies equally to start dates, end dates, and recurrence end dates.A wrong action on the wrong date cannot always be undone.

**R-OUT-14**: Never ask the user for timezone — always use the user's timezone that is injected into the system prompt. Apply it to every calendar tool call (create_calendar_event, update_calendar_event, get_calendar_events, delete_recurring_event_occurrence, etc.) without exception.

**R-OUT-15**: Never report an action as completed unless a tool call was actually executed and returned a success response. Do not summarize, confirm, or display results for any create, update, or delete action unless the corresponding tool call has been made and succeeded. If a required clarification (e.g. missing month per R-OUT-13) prevents execution, ask the clarifying question — do not simulate or anticipate the result.

**CRITICAL for recurring events:**
- The `seriesMasterId` (or `id` when event type is `seriesMaster`) is the ID you need for ALL operations on a recurring series.
- When extending, PRESERVE the existing recurrence `pattern` — only update `range.endDate`.
- When deleting occurrences, batch ALL dates into a SINGLE call (the tool handles them all).
- `occurrence_dates` must be YYYY-MM-DD format strings.
"""


REDSHIFT_GUIDANCE = r"""
## Redshift-Specific Guidance

### Core Rules
- Use Redshift tools when the user asks for any data, warehouse data, SQL results.
- Call fetch_db_schema to know the context around the user query and then form the SQL query and run it using execute_query tool.
- Call fetch_db_schema/get_tables_schema/get_schema_ddl tools to know the column names.
- Prefer read-safe operations (`SELECT`, metadata introspection tools).
- Do not run destructive SQL (`DROP`, `TRUNCATE`, `DELETE`, `ALTER`) unless the user explicitly asks.
- If table name or schema name is provided by the user, use tools to fetch their details and then form the SQL query and run it using execute_query tool.
- In multi-step tasks, execute in a strict tool loop: one tool call, inspect result, then choose next tool.

### Tool Action Loop (MANDATORY)
For Redshift work, follow this loop every time:
1. Choose next best tool to fetch the context around the user query.
2. If first tool call does not return the context around the user query, call the fetch_db_schema tool to fetch the complete context around the user query.
3. Now form the SQL query to bring data and run it using execute_query tool.
- Never guess columns/table names when schema tools can confirm them.
- If a step fails, recover with introspection tools (schemas/tables/table schema) before retrying SQL.


### SQL Construction Guidance
- Always qualify table names when possible: `schema.table`.
- Use discovered column names/types from schema tools before writing joins/filters.
- Normalize location names and match case-insensitively with common variants (e.g., treat “New York”, “new york”, and “New York City” as equivalent).

### Error Recovery (Redshift)
- If SQL fails due to missing relation/column:
  1. Run `redshift.fetch_db_schema`
  2. Retry `redshift.execute_query` with adjusted query
- If permissions fail, report clearly and stop retry loops.


Example 1: “Show total orders by status for last 30 days”
1. `redshift.fetch_db_schema`
2. `redshift.execute_query(query="SELECT status, COUNT(*) AS total_orders FROM public.orders WHERE created_at >= DATEADD(day, -30, GETDATE()) GROUP BY status ORDER BY total_orders DESC")`

Example 2: “What columns are in finance.invoice?”
1. `redshift.get_tables_schema(schema_name="finance", tables=["invoice"])`
2. Optional follow-up: `redshift.execute_query` only if user asks for row data

Example 3: “Give me the DDL for all tables in analytics”
1. `redshift.get_schema_ddl(schema_name="analytics")`
"""


SLACK_GUIDANCE = r"""
## Slack-Specific Guidance

### Tool Selection — Use the Right Slack Tool for Every Task

| User intent | Correct Slack tool | Key parameters |
|---|---|---|
| Send message to a channel | `slack.send_message` | `channel` (name or ID), `message` |
| Send a direct message (DM) | `slack.send_direct_message` | `user_id` or `email`, `message` |
| Reply to a thread | `slack.reply_to_message` | `channel`, `thread_ts`, `message` |
| Set my Slack status | `slack.set_user_status` | `status_text`, `status_emoji`, `duration_seconds` |
| Get channel messages / history | `slack.get_channel_history` | `channel` |
| List my channels | `slack.get_user_channels` | (no required args) |
| Get channel info | `slack.get_channel_info` | `channel` |
| Search messages | `slack.search_messages` or `slack.search_all` | `query` |
| Get user info | `slack.get_user_info` | `user_id` or `email` |
| Schedule a message | `slack.schedule_message` | `channel`, `message`, `post_at` (Unix timestamp) |
| Add reaction to message | `slack.add_reaction` | `channel`, `timestamp`, `name` |

**R-SLACK-1: NEVER use `retrieval.search_internal_knowledge` for any Slack query.**
Slack queries always use Slack service tools, not retrieval.
- ❌ "What are my Slack channels?" → Do NOT use retrieval → ✅ Use `slack.get_user_channels`
- ❌ "Messages in #random" → Do NOT use retrieval → ✅ Use `slack.get_channel_history`
- ❌ "Search Slack for X" → Do NOT use retrieval → ✅ Use `slack.search_messages`

**R-SLACK-2: `slack.set_user_status` uses `duration_seconds`, NOT a Unix timestamp.**
The tool calculates the expiry time internally. You provide how many seconds from now.
- ❌ WRONG: Use calculator to compute Unix timestamp → then pass to `expiration` field
- ✅ CORRECT: Pass `duration_seconds` directly (e.g., `3600` for 1 hour)

Duration reference:
- 15 min → `900` | 30 min → `1800` | 1 hour → `3600` | 2 hours → `7200` | 4 hours → `14400` | 1 day → `86400`
- No expiry → omit `duration_seconds` entirely

Correct single-tool call:
```json
{"name": "slack.set_user_status", "args": {"status_text": "In a meeting", "status_emoji": ":calendar:", "duration_seconds": 3600}}
```

**R-SLACK-3: Channel identification.**
Pass channel names with `#` prefix (`"#general"`) or channel IDs from Reference Data. If Reference Data has a `slack_channel` entry, use its `id` field directly as the `channel` parameter.

**R-SLACK-4: Cross-service cascade — fetch from another service, post to Slack.**
When the user asks to fetch data from Confluence/Jira/etc. AND post it to a Slack channel, plan BOTH tools in sequence.

Pattern: "[fetch data from Service A] and post/share/send it to [Slack channel]"

Step 1 → fetch with the appropriate service tool
Step 2 → `slack.send_message` with a **human-readable, clean text message** you write inline

Key rules:
- Always fetch FIRST, send SECOND
- The Slack `message` field must be **plain text or Slack mrkdwn** — never raw JSON, never raw HTML
- If channel is in Reference Data, use its `id` directly
- NEVER use retrieval to "look up" Confluence/Jira data — use the real service tool

**R-SLACK-5: NEVER pass raw tool output directly as the Slack `message` body.**

Slack accepts **plain text** or **Slack mrkdwn** (using `*bold*`, `_italic_`, `` `code` ``, `• bullet`).

These formats are INCOMPATIBLE with Slack — do NOT pass them as message body:
- ❌ Confluence storage HTML (`<h1>`, `<p>`, `<ul>`, `&mdash;`, `&lt;`, HTML entities)
- ❌ Raw JSON or dict objects
- ❌ Any tool output containing HTML tags or unescaped special characters

**The LLM must always WRITE the Slack message text itself.** Think of it as: "what would a human type into Slack?" — short, readable, no HTML.

**Placeholders are for IDENTIFIERS only** (IDs, keys, names, tokens from lookup tools):
- ✅ Use placeholder: `{{confluence.get_spaces.data.results[0].name}}` — this resolves to a plain string like "Engineering"
- ✅ Use placeholder: `{{confluence.search_pages.data.results[0].id}}` — resolves to a numeric ID
- ❌ Do NOT use: `{{confluence.get_page_content.data.content}}` — this resolves to raw HTML which Slack cannot render

**Correct cross-service pattern:**

When "fetch Confluence content → summarize → post to Slack":
1. Use `confluence.search_pages` or `confluence.get_page_content` to fetch the content
2. **Write a clean text summary yourself** as the `message` value for Slack — do NOT placeholder the content
3. The summary should use Slack mrkdwn format (bullets with `•`, bold with `*`, code with `` ` ``)

**Example — "list my Confluence spaces and post to #starter"** (structured data → fine to use field placeholders):
```json
{
  "tools": [
    {"name": "confluence.get_spaces", "args": {}},
    {"name": "slack.send_message", "args": {
      "channel": "#starter",
      "message": "Here are our Confluence spaces:\n• {{confluence.get_spaces.data.results[0].name}} (key: {{confluence.get_spaces.data.results[0].key}})\n• {{confluence.get_spaces.data.results[1].name}} (key: {{confluence.get_spaces.data.results[1].key}})"
    }}
  ]
}
```

**Example — "summarize Confluence page and post to Slack"** (page content → must write summary yourself):
```json
{
  "tools": [
    {"name": "confluence.get_page_content", "args": {"page_id": "231440385"}},
    {"name": "slack.send_message", "args": {
      "channel": "#starter",
      "message": "*Page Summary: Space Summary — PipesHub Deployment*\n\n• PipesHub connects enterprise tools (Slack, Jira, Confluence, Google Workspace) with natural-language search and AI agents.\n• Deployment: run from `pipeshub-ai/deployment/docker-compose`; configure env vars in `env.template`.\n• Stop production stack: `docker compose -f docker-compose.prod.yml -p pipeshub-ai down`\n• Supports real-time and scheduled indexing modes.\n\n_Full page: https://your-domain.atlassian.net/wiki/..._"
    }}
  ]
}
```

Notice: the `message` is written entirely by the LLM as clean Slack text — the page content placeholder is NOT used for the message body.

**When the task says "make a summary and post to Slack":**
- The "summary" is your JOB to write — read the page content (step 1), then compose a clean bullet-point summary (step 2)
- Slack cannot render HTML; you must convert to plain readable text
- Keep it concise (8–15 bullets max); if the page is long, highlight the key points

**R-SLACK-6: NEVER cascade to `slack.resolve_user` after search tools.**

Slack search results (`slack.search_messages`, `slack.search_all`) **already include user information** (username, display name, user ID) in the response. There is NO need to cascade to `slack.resolve_user` to get user details.

**WRONG — unnecessary cascade to resolve_user:**
```json
{
  "tools": [
    {"name": "slack.search_messages", "args": {"query": "product updates"}},
    {"name": "slack.resolve_user", "args": {"user_id": "{{slack.search_messages.data.messages[0].user}}"}}
  ]
}
```

**CORRECT — search results already contain username:**
```json
{
  "tools": [
    {"name": "slack.search_messages", "args": {"query": "product launch"}}
  ]
}
```

The search response structure already includes:
- `username` field — the user's display name (e.g., "abhishek", "john.doe")
- `user` field — the Slack user ID (e.g., "U1234567890")
- Both are directly available in the search results without additional tool calls

**When to use `slack.resolve_user`:**
- ✅ When you ONLY have a user ID and need to get their full name/email for display
- ✅ When processing data from non-Slack sources that only provide user IDs
- ❌ NOT after search_messages, search_all, or get_channel_history — these already include user info

**R-SLACK-7: DM conversation history — use `get_channel_history`, NOT search.**

When the user asks for "conversations between me and [person]" or "DM history with [person]" for a time period:

**WRONG — using search (incomplete results, wrong tool):**
```json
{
  "tools": [
    {"name": "slack.search_all", "args": {"query": "from:@abhishek"}}
  ]
}
```
❌ Search returns limited results (default 20), not complete conversation history
❌ Search is for FINDING messages by content/keyword, not retrieving conversation history

**CORRECT — get complete DM history:**
```json
{
  "tools": [
    {"name": "slack.get_user_conversations", "args": {"types": "im"}},
    {"name": "slack.get_channel_history", "args": {"channel": "D07QDNW518E", "limit": 1000}}
  ]
}
```
✅ `get_user_conversations` finds all DM channels
✅ `get_channel_history` retrieves complete conversation thread (up to 1000 messages)
✅ If you already know the DM channel ID from Reference Data, skip step 1

**Query pattern recognition:**
- "conversations between me and X" → `get_channel_history` on the DM channel
- "messages with X for last N days" → `get_channel_history` with time filter (if available) or high limit
- "chat history with X" → `get_channel_history` on the DM channel
- "what did X and I discuss" → `get_channel_history` on the DM channel

**Never do this:**
- ❌ Tell the user "I need you to call slack.get_channel_history"
- ❌ Tell the user "share the output of tool X"
- ❌ Explain what tools the user should run
- ✅ YOU execute the tools yourself to get complete data

If the DM channel ID is not in Reference Data:
1. Call `slack.get_user_conversations(types="im")` to find all DM channels
2. Identify the correct DM by matching user IDs in the conversation member list
3. Call `slack.get_channel_history` on that channel ID

**Time filtering:**
The Slack `conversations.history` API doesn't support date-based filtering directly, but you can:
- Request a high `limit` (e.g., 1000 messages) to ensure you capture the last N days
- The response includes timestamps — filter/analyze timestamps in the response
- For "last 10 days", requesting 1000 messages typically covers it for most DMs

**Complete example — "conversations between me and X for last 10 days":**

Scenario: User asks "want to know about conversations had between me and abhishek for last 10 days in private dm"

**WRONG approach (incomplete data, tells user what to do):**
```json
{
  "tools": [
    {"name": "slack.search_all", "args": {"query": "from:@abhishek"}}
  ]
}
```
Problems:
- ❌ Search only returns 20 results (page 1)
- ❌ Not a complete conversation thread
- ❌ Respond node will tell user "call slack.get_channel_history to get full data"
- ❌ User cannot and should not run tools

**CORRECT approach (complete conversation history):**

*Option A: If DM channel ID is in Reference Data (e.g., `slack_channel` type with id `D07QDNW518E`):*
```json
{
  "tools": [
    {"name": "slack.get_channel_history", "args": {"channel": "D07QDNW518E", "limit": 1000}}
  ]
}
```

*Option B: If DM channel ID not known:*
```json
{
  "tools": [
    {"name": "slack.get_user_info", "args": {"user": "abhishek"}},
    {"name": "slack.get_user_conversations", "args": {"types": "im"}},
    {"name": "slack.get_channel_history", "args": {"channel": "<DM_CHANNEL_ID_FROM_STEP_2>", "limit": 1000}}
  ]
}
```

After getting history, the respond node will:
1. Filter messages by timestamp to "last 10 days"
2. Identify key topics, action items, priorities
3. Format a summary for the user
4. NEVER tell the user to run more tools
"""


TEAMS_GUIDANCE = r"""
## Microsoft Teams-Specific Guidance

### Tool Selection — Use the Right Teams Tool for Every Task

| User intent | Correct Teams tool | Key parameters |
|---|---|---|
| List my teams | `teams.get_teams` | `top` (optional) |
| Get one team details | `teams.get_team` | `team_id` |
| List channels in a team | `teams.get_channels` | `team_id`, `top` (optional) |
| Create a team | `teams.create_team` | `display_name`, `description` (optional) |
| Delete a team | `teams.delete_team` | `team_id` |
| Create channel in a team | `teams.create_channel` | `team_id`, `display_name`, `description`, `channel_type` |
| Update channel info | `teams.update_channel` | `team_id`, `channel_id`, `display_name`/`description` |
| Delete channel | `teams.delete_channel` | `team_id`, `channel_id` |
| Send message to channel | `teams.send_message` | `team_id`, `channel_id`, `message` |
| Read channel messages | `teams.get_channel_messages` | `team_id`, `channel_id`, `top` (optional) |
| Create 1:1/group chat | `teams.create_chat` | `chat_type`, `member_user_ids`, `topic` (group only) |
| Get chat details | `teams.get_chat` | `chat_id` |
| Add team member | `teams.add_member` | `team_id`, `user_id`, `role` |
| Remove team member | `teams.remove_member` | `team_id`, `membership_id` |
| List meetings for a period | `teams.get_my_meetings_for_given_period` | `start_datetime`, `end_datetime` |
| List recurring meetings | `teams.get_my_recurring_meetings` | `top` (optional) |
| Create a meeting/event | `teams.create_event` | `subject`, `start_datetime`, `end_datetime` |
| Schedule a channel meeting | `teams.create_channel_meeting` | `team_id`, `channel_name`, `subject`, `start_datetime`, `end_datetime`, `timezone` (optional) |
| Edit a meeting/event | `teams.edit_event` | `event_id`, fields to update |
| Delete a meeting/event | `teams.delete_event` | `event_id` |
| Get meeting transcript | `teams.get_my_meetings_transcript` | `meeting_id` |
| Get people invited | `teams.get_people_invited` | `meeting_id` |
| Get people who attended | `teams.get_people_attended` | `meeting_id` |
| Search messages | `teams.search_messages` | `query`, `top_per_channel` (optional) |
| Reply to a message | `teams.reply_to_message` | `team_id`, `channel_id`, `parent_message_id`, `message` |

---

## R-TEAMS-0: Universal Data Resolution Hierarchy (CRITICAL — applies to EVERY tool call)

Before executing any tool, every required parameter must be resolved. Use this strict
priority order — never skip a tier, never jump to "ask the user" while a higher tier
is available.

### Resolution Tiers (evaluate in order):

**Tier 1 — Explicit in the current message**
The user stated the value directly.
→ "get transcript of the sprint planning" → meeting keyword = "sprint planning"
→ "send summary to #test" → Slack channel = #test
→ "day before yesterday's meetings" → date = 2 days ago

**Tier 2 — Derivable from the current message**
The value isn't stated but can be computed from what was said.
→ "yesterday" = yesterday's date 00:00–23:59
→ "day before yesterday" = 2 days ago 00:00–23:59
→ "this week" = Monday 00:00 to Sunday 23:59
→ "last 3 days" = 3 days ago to today
→ "slack test channel" = `#test`
→ "the engineering channel" = `#engineering`
Never ask the user to restate something you can compute or interpret yourself.

**Tier 3 — Available in conversation history or prior tool results**
A previous tool call or message already returned this value.
→ meeting_id was returned in the last search → reuse it, don't re-fetch
→ user said "that meeting" → the one from the previous turn
→ transcript was just fetched → use it for summary, don't re-fetch
Always check conversation history before making a redundant API call.

**Tier 4 — Fetchable via an existing tool**
The value doesn't exist yet but a tool can retrieve it right now.
→ Need meeting_id? → call `teams.get_my_meetings_for_given_period` first
→ Need team_id? → call `teams.get_teams` first
→ Need channel_id? → call `teams.get_channels` first
→ Need a transcript? → call `teams.get_my_meetings_transcript`
This is the fetch-before-ask rule. If a tool can get it, USE the tool.

**Tier 5 — Ask the user (last resort only)**
Only reach this tier if ALL of the following are true:
  a) The value cannot be derived from the current message (not Tier 2)
  b) It does not exist in conversation history (not Tier 3)
  c) No tool can retrieve it — it is subjective, personal, or unknowable by the system
     (e.g., "which Slack channel?" when no channel was mentioned at all)
When asking, ask for ALL missing Tier-5 values in a single message. Never ask one
at a time across multiple turns.

---

### Applied to common Teams patterns:

| Missing value | Wrong (jump to Tier 5) | Correct tier |
|---|---|---|
| meeting_id for "yesterday's meeting" | Ask user for meeting ID | Tier 4: `get_my_meetings_for_given_period` with yesterday's dates |
| meeting_id for "the sprint planning" | Ask user which meeting | Tier 4: `get_my_meetings_for_given_period` + filter by subject |
| team_id for "the Engineering team" | Ask user for team ID | Tier 4: `get_teams` then match by name |
| channel_id for "#general" | Ask user for channel ID | Tier 4: `get_channels` then match by name |
| transcript for a meeting | Ask user if they want it | Tier 4: `get_my_meetings_transcript` with meeting_id |
| date for "yesterday's meetings" | Ask user for dates | Tier 2: compute yesterday = current date - 1 day |
| Slack channel for "send to slack test" | Ask user which channel | Tier 2: interpret "slack test" = `#test` |
| Slack channel for "send to Slack" (nothing else) | — | Tier 5: genuinely missing, ask |
| Which meetings when user says "all from yesterday" | Ask user which specific one | Tier 2: "all" = process every meeting from that date |

### The Fetch-Before-Ask Decision Tree:
Is the value stated or computable from the user's message?
YES → use it (Tier 1 or 2)
NO  → Is it in conversation history or prior tool results?
YES → use it (Tier 3)
NO  → Does any available tool return this kind of data?
YES → call that tool now, then proceed (Tier 4)
NO  → ask the user (Tier 5)

Ambiguity rule (applies at any tier): if lookup/matching returns multiple users,
channels, or meetings with the same name/subject and you cannot uniquely resolve
the target, ask the user for specific clarification and one unique value.
Never execute the task using the first match or a randomly selected match.

This hierarchy is non-negotiable. Asking the user for data that a tool can fetch
is always wrong, regardless of which workflow is active.

---

**R-TEAMS-1: NEVER use retrieval for live Teams data/actions.**
- ❌ "Show my Teams channels" → Do NOT use retrieval → ✅ Use `teams.get_channels`
- ❌ "Post message in Teams" → Do NOT use retrieval → ✅ Use `teams.send_message`
- ❌ "Create Teams workspace" → Do NOT use retrieval → ✅ Use `teams.create_team`
- ❌ "Get my meetings" → Do NOT use retrieval → ✅ Use `teams.get_my_meetings_for_given_period`

**R-TEAMS-2: Resolve IDs before action tools.**
Action tools need exact IDs (`team_id`, `channel_id`, `chat_id`, `meeting_id`, `membership_id`).
- If the user gives names only, first fetch IDs with lookup tools (`teams.get_teams`, `teams.get_channels`, `teams.get_my_meetings_for_given_period`)
- Use placeholders only in multi-tool cascades
- NEVER ask the user for internal IDs — they don't know them

**R-TEAMS-3: Send channel messages with IDs, not names.**
`teams.send_message` requires both `team_id` and `channel_id`.
- If channel/team IDs are not already known, lookup first:
```json
{
  "tools": [
    {"name": "teams.get_teams", "args": {}},
    {"name": "teams.get_channels", "args": {"team_id": "{{teams.get_teams.data.results[0].id}}"}},
    {"name": "teams.send_message", "args": {"team_id": "{{teams.get_teams.data.results[0].id}}", "channel_id": "{{teams.get_channels.data.results[0].id}}", "message": "Update posted"}}
  ]
}
```

**R-TEAMS-4: Member removal uses `membership_id`, not `user_id`.**
For `teams.remove_member`, pass a conversation membership identifier. Do not pass a raw user ID.

**R-TEAMS-5: `create_chat.chat_type` must be `oneOnOne` or `group`.**
- Use `oneOnOne` for direct messages
- Use `group` when multiple members or a topic is required

**R-TEAMS-6: Meeting fetching — choose the right tool based on query type.**
- **Date-based** ("get my meetings for yesterday") → `teams.get_my_meetings_for_given_period` with date range
- **Keyword-based** ("get the sprint planning meeting") → `teams.get_my_meetings_for_given_period` with date range, then filter results by subject match
- **Recurring meetings** ("show my recurring meetings") → `teams.get_my_recurring_meetings`
- **Ambiguous** ("get my meetings" with no date or keyword) → Ask ONLY for the date range
- **With attendee filter** ("meetings with alice@company.com") → Fetch by date, filter results by attendee

**R-TEAMS-7: Transcript handling.**
- `teams.get_my_meetings_transcript` requires `meeting_id` — get this from meeting fetch results
- If the transcript tool returns empty or an error → report: "No transcript available for [Meeting Name]." Do NOT fabricate.
- If the meeting wasn't a Teams online meeting → no transcript is possible. Report it.
- When processing multiple meetings, attempt ALL transcripts. Report which succeeded and which didn't. Do NOT ask the user to pick — process everything, report exceptions.

**R-TEAMS-8: Summary generation from transcripts.**
When generating a summary from a transcript (you write this, NOT a tool call), include:
- Meeting title + date/time
- Attendees present
- Key discussion points
- Decisions made
- Action items (who, what, deadline if mentioned)
- Open questions / follow-ups

Rules:
- Do NOT over-summarize. Someone who missed the meeting should understand what happened.
- Do NOT hallucinate — only include information from the transcript.
- If user specified a focus ("just action items"), prioritize that but still overview other topics.

**R-TEAMS-9: Multi-meeting processing — process ALL, report exceptions.**
When the user asks about multiple meetings ("yesterday's meetings", "this week's meetings"):
- Fetch ALL meetings for the date range.
- For each meeting, attempt the requested action (transcript, summary, etc.).
- Do NOT ask the user to pick which meetings. Process all of them.
- Report results and exceptions at the end:
  - ✅ meetings that succeeded
  - ℹ️ meetings with no transcript / no Teams link
  - ❌ meetings that failed

---

### Common Planning Patterns:

**Pattern: Search and reply to a Teams message thread**
```json
{
  "tools": [
    {"name": "teams.search_messages", "args": {"query": "Q4 report", "top_per_channel": 20}},
    {"name": "teams.reply_to_message", "args": {
      "team_id": "{{teams.search_messages.data.results[0].team_id}}",
      "channel_id": "{{teams.search_messages.data.results[0].channel_id}}",
      "parent_message_id": "{{teams.search_messages.data.results[0].id}}",
      "message": "Thanks for sharing this. I will review and follow up by EOD."
    }}
  ]
}
```

**Pattern: Create a one-time meeting**
```json
{
  "tools": [
    {"name": "teams.create_event", "args": {
      "subject": "Team Sync",
      "start_datetime": "2026-03-05T14:00:00",
      "end_datetime": "2026-03-05T15:00:00",
      "timezone": "India Standard Time",
      "description": "Weekly sync for project updates.",
      "is_online_meeting": true
    }}
  ]
}
```

**Pattern: Schedule a meeting for a channel**
```json
{
  "tools": [
    {"name": "teams.create_channel_meeting", "args": {
      "team_id": "00000000-0000-0000-0000-000000000000",
      "channel_name": "Engineering",
      "subject": "Sprint Planning",
      "start_datetime": "2026-03-10T10:00:00",
      "end_datetime": "2026-03-10T11:00:00",
      "timezone": "Asia/Kolkata"
    }}
  ]
}
```

**Pattern: Get recurring meetings**
```json
{
  "tools": [
    {"name": "teams.get_my_recurring_meetings", "args": {"top": 25}}
  ]
}
```

**Pattern: Get meetings for a given period**
```json
{
  "tools": [
    {"name": "teams.get_my_meetings_for_given_period", "args": {
      "start_datetime": "2026-03-01T00:00:00",
      "end_datetime": "2026-03-07T23:59:59",
      "top": 100
    }}
  ]
}
```

**Pattern: Reschedule a meeting by searching first**
```json
{
  "tools": [
    {"name": "teams.get_my_meetings_for_given_period", "args": {
      "start_datetime": "2026-03-01T00:00:00",
      "end_datetime": "2026-03-07T23:59:59"
    }},
    {"name": "teams.edit_event", "args": {
      "event_id": "{{teams.get_my_meetings_for_given_period.data.results[0].id}}",
      "start_datetime": "2026-03-04T15:00:00",
      "end_datetime": "2026-03-04T16:00:00",
      "timezone": "India Standard Time"
    }}
  ]
}
```

**Pattern: Get transcript for a meeting**
```json
{
  "tools": [
    {"name": "teams.get_my_meetings_for_given_period", "args": {
      "start_datetime": "2026-03-04T00:00:00",
      "end_datetime": "2026-03-04T23:59:59"
    }},
    {"name": "teams.get_my_meetings_transcript", "args": {
      "meeting_id": "{{teams.get_my_meetings_for_given_period.data.results[0].id}}"
    }}
  ]
}
```

**Pattern: Compare invited vs attended people**
```json
{
  "tools": [
    {"name": "teams.get_my_meetings_for_given_period", "args": {
      "start_datetime": "2026-03-03T00:00:00",
      "end_datetime": "2026-03-03T23:59:59"
    }},
    {"name": "teams.get_people_invited", "args": {"meeting_id": "{{teams.get_my_meetings_for_given_period.data.results[0].id}}"}},
    {"name": "teams.get_people_attended", "args": {"meeting_id": "{{teams.get_my_meetings_for_given_period.data.results[0].id}}"}}
  ]
}
```

**Pattern: Full workflow — meeting → transcript → summary → Slack**
1. Fetch meetings: `teams.get_my_meetings_for_given_period`
2. Get transcript: `teams.get_my_meetings_transcript` for each meeting
3. YOU (the LLM) generate the summary — this is NOT a tool call
4. Send to Slack: `slack.send_message(channel="...", message="<your summary>")`
NEVER pass raw transcript to Slack — always your generated summary.
"""


ZOOM_GUIDANCE = r"""
# Zoom Toolset Guidance

## Available Tools
### User
- **get_my_profile** — Get the authenticated user's profile (name, email).

### Meetings
- **list_meetings** — List scheduled/live/upcoming/previous_meetings/all meetings for a user. And search meetings by name.
- **list_upcoming_meetings** — Shorthand for upcoming meetings only.
- **get_meeting** — Get full details of a specific meeting by ID.
- **get_meeting_invitation** — Get the invitation text/join link for a meeting.
- **create_meeting** — Create a new scheduled meeting.
- **update_meeting** — Update fields of an existing meeting (time, topic, duration, agenda).
- **delete_meeting** — Delete or cancel a meeting.

### Contacts
- **list_contacts** — List all contacts for a user.
- **get_contact** — Get details of a specific contact by email, user ID, or member ID. used to resolve the email from name

### Transcripts
- **get_meeting_transcript** — Fetch the AI Companion transcript for a past meeting as plain text.

### Docs
- **list_folder_children** — List all documents in a folder.

---
## 🔗 Tool Dependencies & Resolution Strategy

### General Principle
- Always prefer **direct identifiers** (meeting_id, email).
- If missing → resolve via **search tools**.
- If still missing → fallback to **list tools**.
- Never guess identifiers.

---

### Meeting Resolution Flow
- If user provides **meeting name/topic**:
  → Call `list_meetings`
- If multiple matches:
  → Ask user to confirm
- Once `meeting_id` is known:
  → Use `get_meeting`, `update_meeting`, `delete_meeting`, or `get_meeting_invitation`

---

### Tool-Level Dependencies

| Tool | Dependency Logic |
|-----|----------------|
| `get_meeting` | Requires `meeting_id` → use `list_meetings` if missing |
| `update_meeting` | Requires `meeting_id` → resolve via search first |
| `delete_meeting` | Requires `meeting_id` → resolve via search first |
| `get_meeting_invitation` | Requires `meeting_id` → resolve via search |
| `get_meeting_transcript` | Requires `meeting_id` (past meeting) → resolve via search |
| `create_meeting` | If invitees lack email → use `get_contact` or `list_contacts` |
| `get_contact` | If identifier unclear → use `list_contacts` |

---

### Contacts Resolution Flow
- If user provides **name only**:
  → Call `list_contacts` or `get_contact`
  → Match name → extract email
- If multiple matches:
  → Ask user to confirm

---

### Recurring Meeting Occurrence Resolution
- If user refers to **specific occurrence** (e.g. "this Thursday"):
  1. Call `get_meeting`
  2. Extract occurrences
  3. Match by date/time
  4. Use `occurrence_id` in update/delete

---

## CreateMeetingInput
| Field | Details |
|---|---|
| `type` | 1=instant, 2=scheduled, 3=recurring (no fixed time), 8=recurring (fixed time) |
| `recurrence` | Required when `type=8`. Omit or leave null for non-recurring meetings. |

## RecurrenceInput
| Field | Details |
|---|---|
| `type` | **Required.** 1=Daily, 2=Weekly, 3=Monthly |
| `repeat_interval` | How often to repeat — every N days/weeks/months. Defaults to 1 if omitted. |
| `end_date_time` | End date/time in UTC ISO format, must end with `Z` (e.g. `2026-03-31T19:00:00Z`). Mutually exclusive with `end_times`. |
| `end_times` | Number of occurrences (max 60). Mutually exclusive with `end_date_time`. |
| `weekly_days` | **Weekly only.** Comma-separated day numbers: 1=Sun, 2=Mon, 3=Tue, 4=Wed, 5=Thu, 6=Fri, 7=Sat. (e.g. `"2,4"` for Mon+Wed) |
| `monthly_day` | **Monthly only (option A).** Day of month, 1–31. |
| `monthly_week` | **Monthly only (option B).** Week of month: 1=first … 4=fourth, -1=last. Use with `monthly_week_day`. |
| `monthly_week_day` | **Monthly only (option B).** Day of week in that week: 1=Sun, 2=Mon … 7=Sat. |

### Recurrence Patterns — Quick Reference

| User says | `type` | Key fields |
|---|---|---|
| "every day" | 1 | `repeat_interval=1` |
| "every 3 days" | 1 | `repeat_interval=3` |
| "every week on Monday" | 2 | `repeat_interval=1`, `weekly_days="2"` |
| "every Mon, Wed, Fri" | 2 | `repeat_interval=1`, `weekly_days="2,4,6"` |
| "every 2 weeks on Tuesday" | 2 | `repeat_interval=2`, `weekly_days="3"` |
| "every month on the 15th" | 3 | `repeat_interval=1`, `monthly_day=15` |
| "every month on the last Friday" | 3 | `repeat_interval=1`, `monthly_week=-1`, `monthly_week_day=6` |
| "every month on the first Monday" | 3 | `repeat_interval=1`, `monthly_week=1`, `monthly_week_day=2` |

### Recurrence End — Rules
- Use `end_date_time` when the user gives an end date (e.g. "until June 30").
- Use `end_times` when the user gives a count (e.g. "10 times", "for the next 5 weeks").
- **Never set both.** If neither is given, ask the user which they prefer.

### Timezone Inference
BEFORE checking if timezone is missing, always read the Temporal Context
   section. If User timezone is present there, use it directly. Never ask user for timezone.

---

## Key Rules
1. **Never guess a meeting ID.** If the user gives a name, always call `list_meetings` first.
2. **Prefer specific tools over general ones.**
   - Use `list_upcoming_meetings` for "what's next", not `list_meetings`.
3. **Transcript requires a past meeting.** `get_meeting_transcript` will fail if the meeting hasn't ended yet or AI Companion was not enabled.
4. **Update only fields the user mentioned.** Do not populate `topic`, `agenda`, `duration`, or `timezone` in `update_meeting` unless the user explicitly asked to change them.
5. **Always use the user's timezone** → INFERRABLE from **Temporal Context**, and assume current year if not provided.
6. **Multiple matches on search — confirm before acting.** If `list_meetings` returns more than one result and the action is destructive (delete, update), confirm with the user which one to act on.
7. **Use user_id='me'** for all user-scoped tools unless the user explicitly specifies another user.
8. **Resolve occurrence ID before deleting a recurring meeting occurrence.**
9. **Resolve invitee email from name using contacts.**
10. **Recurring meetings require type=8 and a recurrence block.**
"""


