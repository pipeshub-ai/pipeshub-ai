## Documentation

For comprehensive guidance and resources, refer to our documentation:

- **GitHub Repository**: [PipesHub Documentation](https://github.com/pipeshub-ai/documentation)
- **Documentation Website**: [PipesHub Docs](https://docs.pipeshub.com/)

In-repo design docs:

- [Connectors — design, decisions, permissions, error logging and testing for building a connector](./connectors/) (start at [CONNECTOR.md](../CONNECTOR.md))
- [Indexing service — architecture, data flow, admission control, and the throughput-collapse root cause](./indexing-service.md)
- [Non-streaming chat endpoints — HLD, LLD, data flow, error contract, and tests](./non-streaming-chat.md)
- [S3 connector — IAM role (AssumeRole) authentication: base key, trust policy, external ID](./connectors/s3-iam-role.md)
- [Fork chat — start a new chat from any message: approach, what is written to MongoDB, what is copied, failure handling](./fork-chat.md)
- [Retrieval context — how search hits become the ranked, cited records the agent reads](./retrieval-context.md)
- [Search record cut — fusing deeper and spreading a search's results across records (`FUSION_RECORD_CUT`)](./search-record-cut.md)
- [Answer rules — the ANSWER_COMPOSITION_RULES, THOROUGH_RETRIEVAL and MULTI_STEP_ANSWER_RULES switches](./answer-rules.md)
- [Vector database sizing — how much memory Qdrant needs for a given corpus, and the Services page warning](./deployment/vector-db-sizing.md)
- [Web search and fetch — providers, typed failures, address policy, metrics and QA checks](./web-search.md)
- [Query service profiling — one-command load test, flame graphs and resource usage](./query-profiling.md)
