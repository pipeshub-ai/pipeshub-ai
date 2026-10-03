# PipesHub FAQ

### What is PipesHub?

PipesHub is the open-source context layer for AI agents. It turns the knowledge stored across your company's business systems into a permission-aware workspace that agents can search, `grep`, navigate and cite.

It connects systems such as Slack, Google Drive, GitHub, Microsoft 365 and Notion, then makes what they hold available in two ways: permission-aware search with citations for your team, and trusted context for your AI agents through APIs, SDKs and MCP. Agents get the same governed view of your company's knowledge that a person would, with the same access controls applied, so they can answer from real company data instead of guessing across tools. You can use the built-in search experience, or build your own agents, workflows and applications on top of it.

## Can I use PipesHub headless, without its UI?

Yes. The PipesHub web app uses the same API you can call yourself, so anything you do in the UI you can also do in code: connect sources, upload files, manage users and permissions, search, chat, and build and run agents.

- **REST API:** an [OpenAPI spec](../backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml) with about 300 endpoints. Browse it at `/api/v1/docs` on your instance.
- **SDKs:** [Python](https://github.com/pipeshub-ai/pipeshub-sdk-python), [TypeScript](https://github.com/pipeshub-ai/pipeshub-sdk-typescript) and [Go](https://github.com/pipeshub-ai/pipeshub-sdk-go).
- **MCP:** for Claude Code, Cursor, Codex and other MCP clients.

Choose how your code signs in:
- **Personal access token or OAuth:** acts as one person and sees only what that person can see.
- **Service account:** for background jobs, with its own permissions.
- **OAuth app:** lets each user of your app sign in as themselves ("Sign in with PipesHub").

Teams use this to build their own products on PipesHub, such as workflow builders, legal and contract-management (CLM) tools, support consoles and internal agents, without showing the PipesHub UI.

## How is PipesHub different from other workplace AI tools?

Most tools hand an AI model a few retrieved text chunks. PipesHub gives agents tools to explore your company's knowledge the way a coding agent explores a repository: hybrid search, `grep` over records, folder navigation and knowledge-graph lookups, each checked against the requesting user's permissions in the source system. Every source becomes Blocks, so answers cite the exact page, table cell, row or slide. It is fully open source (Apache 2.0) and self-hostable, so your data never leaves your infrastructure. See [Why a context layer?](../README.md#why-a-context-layer)

## What connectors does PipesHub support?

PipesHub has 40+ connectors across 30+ systems, with real-time and scheduled indexing. See the [connectors overview](https://docs.pipeshub.com/connectors/overview).

## What file formats can PipesHub index?

PDF (including scans), Microsoft Office (Word, Excel, PowerPoint), Google Docs/Sheets/Slides, Markdown, HTML, CSV, plain text, and images. Audio and video can be stored but are not indexed yet. Storage accepts a wider set of MIME types — see [Supported MIME Types](https://docs.pipeshub.com/system-overview/storage).

Whatever the format, PipesHub stores the text, so agents can search and `grep` a PDF or a slide deck just like a text file.

## How do I deploy PipesHub?

```bash
curl -fsSL https://get.pipeshub.com/install | bash
```

This writes Compose files into `./pipeshub` and starts the interactive installer. Open **http://localhost:3000** when it finishes. Use HTTPS for cloud deployments — HTTP may cause frontend security blocks.

Developers building from source should clone the repository and run `./install.sh` (or `./install.sh --build`) from the repo root. See the [Deployment Guide](../README.md#-deployment-guide).

## What LLM providers does PipesHub support?

PipesHub is "Bring Your Own Model" — you can use any LLM provider. Deploy in your VPC with your preferred models.

## What is the tech stack?

PipesHub has three parts:

- **Web app** (Next.js) — search, chat, and admin in the browser.
- **API** (Node.js) — accounts, permissions, knowledge bases, and files.
- **Python services** — connectors sync your sources; indexing parses documents; query answers with citations.

Those services call **AI models you bring**. An **embedding model** turns parsed text into vectors for search. An **LLM** writes the cited answer. Use any provider or a local model (Ollama); a local embedding server is the default.

Data sits in a knowledge graph (Neo4j by default, or ArangoDB), a vector store (Qdrant), and MongoDB. Redis is the cache. Files live on disk or object storage. Services hand work to each other over Redis on a local machine, or Kafka in a larger deployment. See the [system overview](https://docs.pipeshub.com/system-overview).

## What is the Knowledge Graph Retrieval feature?

At indexing time PipesHub extracts entities (people, projects, customers, products) and links them to the records that mention them, merging duplicates. At answer time agents use the graph two ways: search results are enriched with parent and related records, and agents can look up an entity and fetch every record that mentions it. The graph runs on Neo4j (default) or ArangoDB, alongside Qdrant for vector search.

## Does PipesHub have an MCP server?

Yes. PipesHub provides an MCP server for integration with any MCP-compatible client. Repository: [pipeshub-ai/mcp-server](https://github.com/pipeshub-ai/mcp-server/).

## What SDKs are available?

PipesHub provides SDKs for:
- **Python**: [pipeshub-ai/pipeshub-sdk-python](https://github.com/pipeshub-ai/pipeshub-sdk-python)
- **TypeScript**: [pipeshub-ai/pipeshub-sdk-typescript](https://github.com/pipeshub-ai/pipeshub-sdk-typescript)
- **Go**: [pipeshub-ai/pipeshub-sdk-go](https://github.com/pipeshub-ai/pipeshub-sdk-go)

## Can I build AI agents without coding?

Yes. PipesHub has a no-code agent builder. You can build agents visually and execute actions across enterprise tools without writing code.

## What is the multimodal support?

PipesHub supports image, diagram, and scanned-file understanding, plus voice-based interaction. It uses Docling and pdfplumber for document parsing, or a multimodal LLM (VLM) for scanned PDF OCR.

## How do I troubleshoot deployment issues?

1. Ensure HTTPS is configured for cloud deployments
2. Check Docker compose logs: `docker compose logs`
3. Verify environment variables in env.template
4. Consult [docs.pipeshub.com](https://docs.pipeshub.com/) for detailed guides

## Where can I get help?

- [Discord](https://discord.com/invite/K5RskzJBm2) — Ask questions and get help
- [GitHub Issues](https://github.com/pipeshub-ai/pipeshub-ai/issues) — Report bugs or request features
- [PipesHub Docs](https://docs.pipeshub.com/) — Read the documentation
