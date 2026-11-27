## Documentation

For comprehensive guidance and resources, refer to our documentation:

- **GitHub Repository**: [PipesHub Documentation](https://github.com/pipeshub-ai/documentation)
- **Documentation Website**: [PipesHub Docs](https://docs.pipeshub.com/)

In-repo design docs:

- [Connectors — design, decisions, permissions, error logging and testing for building a connector](./connectors/) (start at [CONNECTOR.md](../CONNECTOR.md))
- [How the context layer works: Blocks, hierarchy and knowledge graph, agent tools, permissions, agent loop](./context-layer.md)
- [Indexing service — architecture, data flow, admission control, and the throughput-collapse root cause](./indexing-service.md)
- [Non-streaming chat endpoints — HLD, LLD, data flow, error contract, and tests](./non-streaming-chat.md)
- [S3 connector — IAM role (AssumeRole) authentication: base key, trust policy, external ID](./connectors/s3-iam-role.md)
- [Fork chat — start a new chat from any message: approach, what is written to MongoDB, what is copied, failure handling](./fork-chat.md)
- [Retrieval context — how search hits become the ranked, cited records the agent reads](./retrieval-context.md)
- [Search record cut — fusing deeper and spreading a search's results across records (`FUSION_RECORD_CUT`)](./search-record-cut.md)
- [API Toolsets — agent tools from the OpenAPI spec of an internal REST API: setup, writing a spec agents use well, security, design](./api-toolsets.md)
- [Answer rules — the ANSWER_COMPOSITION_RULES, THOROUGH_RETRIEVAL and MULTI_STEP_ANSWER_RULES switches](./answer-rules.md)
- [Vector database sizing — how much memory Qdrant needs for a given corpus, and the Services page warning](./deployment/vector-db-sizing.md)
- [Web search and fetch — providers, typed failures, address policy, metrics and QA checks](./web-search.md)
- [Logging — levels, formats, line shape, field names and the conventions every log line follows](./logging.md)
- [Query service profiling — one-command load test, flame graphs and resource usage](./query-profiling.md)
- [Org context — which org and user every request, message and job acts for, in Python and Node](./org-context.md)
- [Context engineering — what the agent loop sends the model on each call](./context-engineering.md)
- [Multi-agent practices — designing and reviewing the multi-agent runtime](./multi-agent-best-practices.md)
- [AI models — configuring, discovering and listing chat, embedding, image and speech models](./ai-models.md)
- [LLM gateway identity — forwarding the user and their groups to a gateway such as LiteLLM](./llm-gateway-identity.md)
- [Text fragments — citation links that highlight the cited text on the source page](./text-fragments.md)
- [Taxonomy entity resolution — merging spellings of the same category, subcategory and topic](./entity-resolution.md)
- [Named entity extraction — typed entities extracted at indexing and the agent tools that read them](./named-entity-extraction.md)
- [Knowledge Hub browse on Neo4j — the split browse and its permission model](./knowledge-hub-browse.md)
- [Acting on behalf of someone — service-account delegation](./service-account-delegation.md)
- [MCP hardening notes — why the MCP client works the way it does, change by change](./mcp-hardening-notes.md)
- [Agent harness guides — user guide, builder guide, vendor DPA checklist, availability SLO and DR drill](./agent-harness-v2/guides/user-guide.md) (index: [agent-harness-v2/README.md](./agent-harness-v2/README.md))

Upgrading:

- [Upgrading to 1.0: SAML and generic OAuth sign-in are removed](./deployment/upgrading-to-1.0-sign-in.md)
