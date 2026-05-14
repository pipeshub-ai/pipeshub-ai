<div align="center">

<a href="https://www.pipeshub.com"><img src="https://raw.githubusercontent.com/pipeshub-ai/media-assets/refs/heads/main/images/pipeshub-logo.svg" alt="PipesHub" width="280"/></a>

<h3>The Open-Source Workplace AI Platform</h3>

<p>
  <a href="https://www.pipeshub.com/">Website</a> ·
  <a href="https://docs.pipeshub.com/">Docs</a> ·
  <a href="https://discord.com/invite/K5RskzJBm2">Discord</a> ·
  <a href="https://plum-myrtle-9f7.notion.site/Pipeshub-s-Product-Roadmap-33841c164f54803a9989fd0fdbfdb1ee">Roadmap</a>
</p>

<a href="https://trendshift.io/repositories/14618"><img src="https://trendshift.io/api/badge/repositories/14618" alt="PipesHub on Trendshift" height="40"/></a>

<p>
  <a href="https://opensource.org/licenses/Apache-2.0"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License"></a>
  <a href="https://github.com/pipeshub-ai/pipeshub-ai/releases"><img src="https://img.shields.io/github/v/release/pipeshub-ai/pipeshub-ai?color=success&amp;label=release" alt="Release"></a>
  <a href="https://hub.docker.com/r/pipeshubai/pipeshub-ai"><img src="https://img.shields.io/docker/pulls/pipeshubai/pipeshub-ai?logo=docker&amp;logoColor=white" alt="Docker Pulls"></a>
  <a href="https://discord.com/invite/K5RskzJBm2"><img src="https://img.shields.io/discord/1359557598222745670?label=Discord&amp;logo=discord&amp;logoColor=white" alt="Discord"></a>
  <img src="https://img.shields.io/github/last-commit/pipeshub-ai/pipeshub-ai" alt="Last commit">
  <img src="https://img.shields.io/github/commit-activity/m/pipeshub-ai/pipeshub-ai" alt="Commits per month">
  <a href="https://github.com/pipeshub-ai/pipeshub-ai/issues">
    <img src="https://img.shields.io/github/issues/pipeshub-ai/pipeshub-ai" alt="GitHub issues">
  </a>
  <a href="https://github.com/pipeshub-ai/pipeshub-ai/pulls">
    <img src="https://img.shields.io/github/issues-pr/pipeshub-ai/pipeshub-ai" alt="GitHub pull requests">
  </a>
  <a href="https://github.com/pipeshub-ai/pipeshub-ai/stargazers">
    <img src="https://img.shields.io/github/stars/pipeshub-ai/pipeshub-ai" alt="GitHub Repo stars">
  </a>
  <br/>
  <a href="https://x.com/PipesHub"><img src="https://img.shields.io/twitter/follow/PipesHub?style=social" alt="Twitter"></a>
  
</p>

</div>

<h2 id="about-pipeshub">PipesHub - Explainable & Extensible</h2>

<strong>[PipesHub](https://www.pipeshub.com/)</strong> is an open-source, self-hosted AI-native execution layer that connects enterprise knowledge, delivers explainable search with citations, and automates workflows across your systems.


## Features

- 📝 **Explainable Answers:** PipesHub delivers grounded answers with precise block citations to the original documents.
- 🔒 **Permission-Aware Search:** Enforces source-level access controls so users only see what they're authorized to.
- 🕸️ **Knowledge Graph Retrieval:** Graph-backed retrieval that captures relationships across enterprise data.
- 🔌 **Enterprise Connectors:** 30+ connectors with real-time and scheduled indexing out of the box.
- 🔍 **Unified Search, Deep Research, and Agents:** Search, Q&A, deep research, web search, and AI agents on one context layer.
- 📊 **Artifacts and Code Execution:** Generate reports, charts, and dashboards in a safe execution sandbox.
- 🎙️ **Multimodal Support:** Image, diagram, and scanned-file understanding plus voice-based interaction.
- 🤖 **No-Code Agents and Actions:** Build agents visually and execute actions across enterprise tools.
- 🧠 **Bring Your Own Model, Fully Self-Hostable:** Any LLM provider, deployed in your VPC — data never leaves your infrastructure.
- 🛠️ **Developer-First and Extensible:** APIs, SDKs, MCP tools, custom connectors, and independently scalable services.

## PipesHub in Action

### Connectors
<img src="https://raw.githubusercontent.com/pipeshub-ai/media-assets/main/gif/Connector%20Setup%20and%20Query%20with%20Connectors.gif" alt="Connectors" width="100%"/>

### Citations
<img src="https://raw.githubusercontent.com/pipeshub-ai/media-assets/main/gif/Citation%20through%20chat.gif" alt="Citations" width="100%"/>

### All Records
<img src="https://raw.githubusercontent.com/pipeshub-ai/media-assets/main/gif/All%20Records%20Demo.gif" alt="All Records" width="100%"/>

### Knowledge Search
<img src="https://raw.githubusercontent.com/pipeshub-ai/media-assets/main/gif/Knowledge%20Search%20Demo.gif" alt="Knowledge Search" width="100%"/>

## Connectors

<p align="center">
<a href="https://pipeshub.com/connectors"><img src="https://raw.githubusercontent.com/pipeshub-ai/media-assets/main/images/Github%20Connector%20Readme.png" alt="PipesHub Connectors" width="900"/></a>
</p>

## File Formats Supported

| Format | Details |
|--------|---------|
| PDF | Including scanned PDFs |
| Docx / Doc | Microsoft Word |
| XLSX / XLS | Microsoft Excel |
| PPTX / PPT | Microsoft PowerPoint |
| CSV | Comma-separated values |
| Markdown | .md files |
| HTML | Web pages |
| Text | Plain text files |
| Google Docs, Sheets, Slides | Google Workspace formats |
| Images | PNG, JPG, etc. |
| Audio | Audio files |
| Video | Video files |

## Tech Stack

### Frontend

| Technology | Description |
|-----------|-------------|
| Next.js | App Router UI (client-rendered React) |
| TypeScript | Strongly typed JavaScript superset |
| Radix UI Themes | Accessible component primitives and styling |
| Zod | Schema validation and parsing |
| React Hook Form | Flexible form state management |

### Backend

| Technology | Description |
|-----------|-------------|
| FastAPI | High-performance Python web framework |
| LangChain | Framework for LLM pipelines |
| LangGraph | State graph for LLM workflows |
| Qdrant | Vector similarity search engine |
| Neo4j / ArangoDB | Graph database |
| Kafka / Redis Streams | Distributed event streaming platform |
| Redis | Caching |
| Redis / etcd3 | Distributed key-value configuration store |
| Celery | Distributed task queue system |
| Docling | Document parsing and extraction toolkit |
| PyMuPDF | PDF processing library |
| pandas | Data analysis and manipulation |

## 🚀 Deployment Guide

PipesHub (the Workplace AI Platform) can be run locally or deployed on the cloud using Docker Compose.
**Note**: If you are deploying PipesHub on a cloud server, make sure you are using an HTTPS endpoint. PipesHub enforces stricter security checks, and browsers will block certain requests when the application is served over HTTP.
You can use a reverse proxy like Cloudflare, Nginx, or Traefik to terminate SSL/TLS and provide a valid HTTPS certificate.
If you see a white screen after deploying PipesHub while accessing it over HTTP, this is likely the cause. The frontend will refuse to load due to stricter security checks.

---

### 📦 Production Deployment

```bash
# Clone the repository
git clone https://github.com/pipeshub-ai/pipeshub-ai.git

# 📁 Navigate to the deployment folder
cd pipeshub-ai/deployment/docker-compose

# Set Environment Variables
> 👉 Set Environment Variables for secrets, passwords, and the public URLs of the **Frontend** and **Connector** services
> _(Required for webhook notifications and real-time updates)_
> Refer to env.template

# 🚀 Start the production deployment
docker compose -f docker-compose.prod.yml -p pipeshub-ai up -d

# 🛑 To stop the services
docker compose -f docker-compose.prod.yml -p pipeshub-ai down
```

### 📦 Developer Deployment Build

```bash
# Clone the repository
git clone https://github.com/pipeshub-ai/pipeshub-ai.git

# 📁 Navigate to the deployment folder
cd pipeshub-ai/deployment/docker-compose

# Set Optional Environment Variables
> 👉 Set Environment Variables for secrets, passwords, and the public URLs of the **Frontend** and **Connector** services
> _(Required for webhook notifications and real-time updates)_
> Refer to env.template

# 🚀 Start the local build deployment
docker compose -f docker-compose.build.neo4j.yml -p pipeshub-ai up --build -d

# 🛑 To stop the services
docker compose -f docker-compose.build.neo4j.yml -p pipeshub-ai down
```

The main `Dockerfile` pulls pre-built layers from `pipeshubai/pipeshub-ai-base:python-deps` and `pipeshubai/pipeshub-ai-base:runtime` (see [`Dockerfile.base`](Dockerfile.base) in the repo root for build/push commands). To use local tags instead, set `PYTHON_DEPS_IMAGE` and `RUNTIME_BASE_IMAGE` in the environment or in compose build args.

## MCP Server

Use PipesHub with any MCP-compatible client to bring your enterprise context into AI workflows. Check the README for setup and usage.

**Repository:** [pipeshub-ai/mcp-server](https://github.com/pipeshub-ai/mcp-server/)

## SDKs

PipesHub provides developer SDKs for Python, TypeScript, and Go to help you integrate quickly. Check the respective SDK repository README for setup and usage details.

| Name | Description | Link |
|------|-------------|------|
| **Python SDK** | Python SDK for PipesHub | [pipeshub-ai/pipeshub-sdk-python](https://github.com/pipeshub-ai/pipeshub-sdk-python) |
| **TypeScript SDK** | TypeScript SDK for PipesHub | [pipeshub-ai/pipeshub-sdk-typescript](https://github.com/pipeshub-ai/pipeshub-sdk-typescript) |
| **Go SDK** | Go SDK for PipesHub | [pipeshub-ai/pipeshub-sdk-go](https://github.com/pipeshub-ai/pipeshub-sdk-go) |

> Need an SDK in another language? Reach out to us at developer@pipeshub.com

## RoadMap

<p>We ship in the open. Here's what's done and what's next:</p>

<ul>
<li>✅ 🤖 <strong>Workplace AI agents</strong>: first-class no-code agent builder</li>
<li>✅ 🔗 <strong>MCP (Model Context Protocol)</strong> support, both server and client</li>
<li>✅ 🧰 <strong>Developers SDKs</strong></li>
<li>✅ 🔍 <strong>Code search</strong> across GitHub, GitLab, and Bitbucket</li>
<li>⬜ 👤 <strong>Personalized search</strong> based on team, role, and history</li>
<li>✅ ☸️ <strong>Production Kubernetes</strong> deployment with HA defaults</li>
<li>⬜ 📈 <strong>PageRank-augmented relevance</strong> across the knowledge graph</li>
</ul>
<p>👉 <strong><a href="https://plum-myrtle-9f7.notion.site/Pipeshub-s-Product-Roadmap-33841c164f54803a9989fd0fdbfdb1ee">View the full product roadmap on Notion</a></strong></p>

<hr>

## 👥 Contributing

Want to join our community of developers? Please check out our [Contributing Guide](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/CONTRIBUTING.md) for more details on how to set up the development environment, our coding standards, and the contribution workflow.
<h3>Where to go for what</h3>

<table>

<tr><td>Ask a question or get help</td><td><a href="https://discord.com/invite/K5RskzJBm2">Discord</a></td></tr>
<tr><td>Report a bug or request a feature</td><td><a href="https://github.com/pipeshub-ai/pipeshub-ai/issues">GitHub Issues</a></td></tr>
<tr><td>Report a security issue</td><td><a href="https://github.com/pipeshub-ai/pipeshub-ai/blob/main/SECURITY.md">Report Security Issue</a></td></tr>
<tr><td>Read the docs</td><td><a href="https://docs.pipeshub.com/">Pipeshub Docs</a></td></tr>
</tbody>
</table>

## FAQ

### What is PipesHub?

PipesHub is an open-source, self-hosted AI-native execution layer that connects enterprise knowledge, delivers explainable search with citations, and automates workflows across your systems. It provides a unified context layer for search, Q&A, deep research, web search, and AI agents.

### How is PipesHub different from other workplace AI tools?

PipesHub is fully open-source (Apache 2.0) and self-hostable — your data never leaves your infrastructure. It features permission-aware search that enforces source-level access controls, and delivers explainable answers with precise block citations to original documents.

### What connectors does PipesHub support?

PipesHub has 30+ enterprise connectors with real-time and scheduled indexing. It supports file formats like PDF, Docx, XLSX, PPTX, CSV, Markdown, HTML, Google Docs/Sheets/Slides, images, audio, and video.

### How do I deploy PipesHub?

```bash
# Clone the repository
git clone https://github.com/pipeshub-ai/pipeshub-ai.git
cd pipeshub-ai/deployment/docker-compose

# Set Environment Variables (refer to env.template)
# Start production deployment
docker compose -f docker-compose.prod.yml -p pipeshub-ai up -d
```

Note: Use HTTPS for cloud deployments. HTTP may cause frontend security blocks.

### What LLM providers does PipesHub support?

PipesHub is "Bring Your Own Model" — you can use any LLM provider. Deploy in your VPC with your preferred models. The tech stack includes LangChain and LangGraph for LLM pipelines and workflows.

### What is the Knowledge Graph Retrieval feature?

PipesHub uses graph-backed retrieval that captures relationships across enterprise data. It uses Neo4j or ArangoDB as graph databases, combined with Qdrant for vector similarity search.

### Does PipesHub have an MCP server?

Yes. PipesHub provides an MCP server for integration with any MCP-compatible client. Repository: [pipeshub-ai/mcp-server](https://github.com/pipeshub-ai/mcp-server/).

### What SDKs are available?

PipesHub provides SDKs for:
- **Python**: [pipeshub-ai/pipeshub-sdk-python](https://github.com/pipeshub-ai/pipeshub-sdk-python)
- **TypeScript**: [pipeshub-ai/pipeshub-sdk-typescript](https://github.com/pipeshub-ai/pipeshub-sdk-typescript)
- **Go**: [pipeshub-ai/pipeshub-sdk-go](https://github.com/pipeshub-ai/pipeshub-sdk-go)

### Can I build AI agents without coding?

Yes. PipesHub has a no-code agent builder. You can build agents visually and execute actions across enterprise tools without writing code.

### What is the multimodal support?

PipesHub supports image, diagram, and scanned-file understanding, plus voice-based interaction. It uses Docling and PyMuPDF for document parsing, and Azure Document Intelligence or a multimodal LLM (VLM) for scanned PDF OCR.

### How do I troubleshoot deployment issues?

1. Ensure HTTPS is configured for cloud deployments
2. Check Docker compose logs: `docker compose logs`
3. Verify environment variables in env.template
4. Consult [docs.pipeshub.com](https://docs.pipeshub.com/) for detailed guides

### Where can I get help?

- [Discord](https://discord.com/invite/K5RskzJBm2) — Ask questions and get help
- [GitHub Issues](https://github.com/pipeshub-ai/pipeshub-ai/issues) — Report bugs or request features
- [PipesHub Docs](https://docs.pipeshub.com/) — Read the documentation


<hr>
<div align="center">
<h3>⭐ Star us on GitHub!</h3>

<p>It helps the project reach the teams who need it.</p>

<p>
<a href="https://github.com/pipeshub-ai/pipeshub-ai">
<img src="https://raw.githubusercontent.com/pipeshub-ai/media-assets/refs/heads/main/gif/pipeshub.gif" alt="Star PipesHub" width="350"/>
</a>
</p>

<p>
<a href="https://star-history.com/#pipeshub-ai/pipeshub-ai&amp;Date">
<img src="https://api.star-history.com/svg?repos=pipeshub-ai/pipeshub-ai&amp;type=Date" alt="Star History" width="600"/>
</a>
</p>

<p><sub>Built with ❤️ by the <a href="https://www.pipeshub.com/">PipesHub team</a> and contributors around the world.</sub></p>

</div>

