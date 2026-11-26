# Vendor DPA checklist for the agent harness

Checklist item C16.04. Use it before you turn on a SaaS model provider, a SaaS sandbox, a coding engine or Opik
for an organization. It lists what PipesHub sends to each kind of vendor, and what to collect and check before
you enable it.

This page is not legal advice. It does not say what any vendor's terms are. Each **Legal** box is for your legal or
privacy team to fill in from the vendor's current contract. Nothing on this page has been checked against a
vendor's terms.

## How to use it

1. Find each vendor your install actually uses. The table under "Which vendors are in play" says where each one is
   switched on.
2. For each vendor, collect the documents under "Collect from every vendor".
3. Work through that vendor's section below. Tick a box only when you hold the evidence for it.
4. Keep the filled-in copy with your other vendor records. Review it when the vendor changes its terms, or when you
   turn on a new provider, engine version or region.

## Which vendors are in play

A self-hosted install with local models, the sandbox broker and no Opik sends harness data to no vendor at all.
Each row below adds one.

| Kind | Turned on by | Receives data? | Where it is set |
|---|---|---|---|
| Model provider (Anthropic, OpenAI, Azure OpenAI, Azure AI, Bedrock, Gemini, Vertex AI, Mistral, Cohere, Together, OpenRouter, xAI, Groq, MiniMax, Fireworks, and others) | An admin adds a model under AI models | Yes, when the provider is a hosted service. Ollama, LM Studio and a self-hosted OpenAI-compatible server stay on your network | The provider list is `backend/python/app/config/ai_models/providers/`; see [ai-models.md](../../ai-models.md) |
| LLM gateway (LiteLLM Proxy, OpenRouter, an OpenAI-compatible gateway) | An admin adds it as a model connection | Yes, if hosted. With identity forwarding on, it also gets the user id and, where an edition provides them, group names | [llm-gateway-identity.md](../../llm-gateway-identity.md) |
| SaaS sandbox (E2B) | The operator sets `SANDBOX_MODE=e2b` and `E2B_API_KEY` | Yes: the code and files `run_code` runs on | [FLAGS.md](../FLAGS.md) (`SANDBOX_MODE`, `E2B_API_KEY`). The default, `broker`, runs sandboxes on your own Docker host |
| Coding engine vendor (Claude Code by Anthropic, Codex by OpenAI, opencode, Gemini CLI by Google through ACP) | The operator sets `HARNESS_ENGINE_RUNS=true`; an admin makes an agent an engine agent | Not through the engine itself, in the default setup (see below) | `backend/python/harness_engines/allowlist.json`, `backend/python/harness_engines/acp_agents.json` |
| Anthropic, for personal Claude accounts in Claude Code | The operator starts the egress tunnels overlay, and an admin allows personal accounts | Yes: that person's Claude Code traffic goes straight to Anthropic under their own account | `deployment/docker-compose/docker-compose.egress-tunnels.yml`; [GVISOR.md](../../../deployment/docker-compose/GVISOR.md), "Personal Claude accounts" |
| Opik (Opik Cloud, or your own Opik server) | The operator sets `OPIK_API_KEY` (Opik Cloud) or `OPIK_URL_OVERRIDE` (self-hosted) | Yes, when it is Opik Cloud | `deployment/docker-compose/docker-compose.yml` (the Opik variables), `backend/python/app/utils/opik.py` |

Out of scope here, but review them the same way if you use them: hosted web search providers (Serper, Tavily, Exa,
Brave; see [web-search.md](../../web-search.md)), the OpenTelemetry collector you point `OTEL_EXPORTER_OTLP_ENDPOINT`
at, cloud blob storage (S3, Azure Blob), and the SaaS tools your connectors and toolsets write to.

## Collect from every vendor

For each vendor in play, collect and file:

- [ ] The signed data processing agreement (DPA), or the vendor's statement that its standard DPA applies to your
      plan. **Legal:** which plan or contract it covers.
- [ ] The list of the vendor's sub-processors, and how the vendor tells you about changes.
- [ ] Where the vendor stores and processes data (regions), and whether you can pin a region. Compare it with the
      region your org's harness data lives in (C16.01: every harness document carries its region).
- [ ] How long the vendor keeps prompts, outputs, logs and files, and whether you can set that to zero.
- [ ] Whether the vendor trains models on your data, and how that is turned off for your plan. **Legal:** get it
      in writing.
- [ ] Transfer terms for data leaving your jurisdiction (for example, standard contractual clauses).
- [ ] The vendor's security reports (for example, SOC 2 Type II or ISO 27001), and the date of the latest one.
- [ ] Breach notification terms: how fast, and to whom.
- [ ] How you ask the vendor to delete data, and how you confirm it was deleted.
- [ ] A named owner on your side who reviews this vendor each year.

## Model providers

What PipesHub sends: the conversation, the agent's instructions, retrieved document text, tool arguments and tool
results, and the files a run reads. Engine runs send the same through the Model Gateway, using the org's own
provider keys. For one turn, a provider sees roughly what the user and the agent see.

- [ ] Use an API or enterprise plan, not a consumer plan. Consumer terms often differ on retention and training.
      **Legal:** confirm per provider.
- [ ] Retention: record the provider's default for API traffic, and whether zero retention is available to you.
- [ ] Abuse monitoring: some providers keep flagged traffic for review even under zero retention. **Legal:**
      record whether this applies and for how long.
- [ ] Region: if the region data is processed in matters, pick a regional endpoint or deployment (Azure OpenAI, Bedrock and Vertex AI
      let you choose one) and record it.
- [ ] Cloud-hosted models (Azure, Bedrock, Vertex AI) may fall under your existing cloud agreement instead of a
      separate DPA. **Legal:** confirm which agreement covers model traffic.
- [ ] Gateways (LiteLLM Proxy, OpenRouter): a hosted gateway is a processor in its own right, and it forwards to
      further providers. List both. With identity forwarding on, the gateway also receives user ids and, where an
      edition provides them, group names.

## SaaS sandbox: E2B

Used only when the operator sets `SANDBOX_MODE=e2b`. It serves the `run_code` tool. Engine runs never use it:
they need `SANDBOX_MODE=broker` ([FLAGS.md](../FLAGS.md), "Turning things on").

What PipesHub sends: the code `run_code` runs, and the files and data the run puts into the sandbox. That can
include document text and tool results.

- [ ] DPA and sub-processors, as above.
- [ ] Where the sandboxes run, and whether you can choose the region.
- [ ] What the vendor keeps after a sandbox ends (files, logs, snapshots), and for how long.
- [ ] Whether vendor staff can reach a running sandbox.
- [ ] If any of this is not acceptable, keep the default, `SANDBOX_MODE=broker`, which runs sandboxes on your own
      hosts.

## Coding engine vendors

The engines are programs PipesHub runs inside its own sandboxes. Each version is pinned by the hash of its binary
in `backend/python/harness_engines/allowlist.json`. Today that lists opencode, Codex, Claude Code, and Gemini CLI
and opencode through the generic ACP driver.

In the default setup, the engine does not talk to its vendor. A sandbox's only way out is the egress proxy, which
forwards only to PipesHub's Model Gateway and Tool Gateway. Model calls then go to the org's own configured model
provider, which the "Model providers" section covers. The engine launchers also switch off the engines' own update,
sharing and telemetry paths (for example, the `OPENCODE_DISABLE_*` settings in `acp_agents.json`, and
`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` for Claude Code).

So for an engine vendor, check:

- [ ] The licence of each engine version you run (part of admitting a new version; see the comment in
      `allowlist.json`). **Legal:** confirm the licence allows your use.
- [ ] That no vendor DPA is needed for the engine itself, because no data reaches the vendor. Re-check this if you
      add egress hosts or the tunnels overlay.
- [ ] Where the binary comes from. opencode, Codex and Gemini CLI images are built from the vendor's published
      release asset, checked against the hash in `allowlist.json`. Claude Code is proprietary and PipesHub does
      not ship it: your admin supplies the binary and builds the image
      (`deployment/sandbox-images/claude-code/build.sh --binary PATH`), and any other hash is refused.
- [ ] Claude Code: because you supply the binary, **Legal:** confirm your Anthropic terms allow running it this
      way, with model traffic going to your own provider through the Model Gateway.

### Personal Claude accounts (the sign-in add-on)

This is the one case where an engine talks to its vendor directly. It needs the tunnels overlay
(`docker-compose.egress-tunnels.yml`) and an admin who allows personal accounts under **AI accounts and access**.
It is off by default. A person then signs in to their own Claude account inside their own sandbox, and their Claude
Code traffic goes to Anthropic under that account's terms, not under your org's model provider contract.

- [ ] **Legal:** decide whether people may use their own Claude accounts for company work at all, and on which
      Anthropic plans.
- [ ] **Legal:** record which Anthropic terms apply to a person's own account (consumer or business), including
      retention and training.
- [ ] Record who in the org has it turned on. Leave the overlay off if you have not decided.

## Opik

Opik tracing is off unless the operator sets `OPIK_API_KEY` (Opik Cloud) or `OPIK_URL_OVERRIDE` (your own Opik
server). With it on, PipesHub sends prompts, model answers, tool arguments and results, and indexed document text
to Opik. The service logs this warning at start (`opik.content_sent` in `backend/python/app/utils/opik.py`).

When a conversation is erased, PipesHub deletes its traces in Opik. That covers only traces written by this version
or later. Traces from the legacy path and from indexing name no conversation, so PipesHub cannot find or delete
them (`backend/python/app/services/opik_traces.py`).

- [ ] Prefer a self-hosted Opik (`OPIK_URL_OVERRIDE`) if traces must stay on your network. Then Opik is not a
      vendor, but its storage is covered by your own retention.
- [ ] For Opik Cloud: DPA, sub-processors, region and retention, as above.
- [ ] Set a retention period in Opik itself, because PipesHub does not delete traces it cannot link to a
      conversation.
- [ ] Record the date Opik was turned on. Traces from before the version that links them to conversations are not
      deleted on erasure.

## Open items for legal

These are questions this repository cannot answer:

- Which plan or contract each vendor's DPA covers for your org.
- Each vendor's current retention, training and abuse-monitoring terms.
- Whether personal Claude accounts are allowed for company work.
- Whether a cloud agreement you already hold covers model traffic to Azure, Bedrock or Vertex AI.
