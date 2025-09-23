<a name="readme-top"></a>

<h2 align="center">
<a href="https://www.pipeshub.com/">
<img width="50%" src="https://raw.githubusercontent.com/pipeshub-ai/media-assets/refs/heads/main/images/pipeshub-logo.svg"/> 
</a>
</h2>

<p align="center"></p>
<p align="center">Workplace AI Platform</p>

<p align="center" style="display: flex; justify-content: center; gap: 20px; align-items: center;">
  <a href="https://trendshift.io/repositories/14618" target="_blank">
    <img src="https://trendshift.io/api/badge/repositories/14618" alt="pipeshub-ai%2Fpipeshub-ai | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/>
  </a>

<!--Links in Readme-->
<p align="center">
  <a href="https://docs.pipeshub.com/" target="_blank">
    <img src="https://img.shields.io/badge/Docs-View-informational?style=flat-square&logo=readthedocs&logoColor=white" 
        alt="Docs" style="height:28px;">
  </a>
  &nbsp;&nbsp;
  <a href="https://discord.com/invite/K5RskzJBm2" target="_blank">
    <img src="https://img.shields.io/discord/1359557598222745670?label=Discord&logo=discord&logoColor=white&style=flat-square" 
        alt="Discord" style="height:28px;">
  </a>
</p>

<p align="center">
  <a href="https://github.com/pipeshub-ai/pipeshub-ai/stargazers">
    <img src="https://img.shields.io/github/stars/pipeshub-ai/pipeshub-ai?style=social" alt="GitHub Repo stars">
  </a>
  &nbsp;
  <a href="https://github.com/pipeshub-ai/pipeshub-ai/network/members">
    <img src="https://img.shields.io/github/forks/pipeshub-ai/pipeshub-ai?style=social" alt="GitHub forks">
  </a>
  &nbsp;
  <a href="https://opensource.org/licenses/Apache-2.0">
    <img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License: Apache-2.0">
  </a>
</p>

<p align="center">
  <a href="https://github.com/pipeshub-ai/pipeshub-ai/issues">
    <img src="https://img.shields.io/github/issues/pipeshub-ai/pipeshub-ai" alt="GitHub issues">
  </a>
  &nbsp;
  <a href="https://github.com/pipeshub-ai/pipeshub-ai/pulls">
    <img src="https://img.shields.io/github/issues-pr/pipeshub-ai/pipeshub-ai" alt="GitHub pull requests">
  </a>
  &nbsp;
  <a href="https://github.com/pipeshub-ai/pipeshub-ai/graphs/contributors">
    <img src="https://img.shields.io/github/contributors/pipeshub-ai/pipeshub-ai" alt="Contributors">
  </a>
  &nbsp;
  <img src="https://img.shields.io/github/last-commit/pipeshub-ai/pipeshub-ai" alt="Last commit">
</p>

<p align="center">
  <a href="https://x.com/PipesHub">
    <img src="https://img.shields.io/twitter/follow/PipesHub?style=social" alt="Twitter Follow">
  </a>
  <a href="https://www.linkedin.com/company/pipeshub/" target="_blank">
    <img src="https://img.shields.io/badge/LinkedIn-Follow-blue?logo=linkedin&logoColor=white&style=flat-square" alt="Follow on LinkedIn">
  </a>
</p>

<!--Intro-->

<strong>[PipesHub](https://github.com/pipeshub-ai/pipeshub-ai)</strong> is the workplace AI platform for enterprises to improve how businesses operate and help employees and AI agents work more efficiently.
In most companies, important work data is spread across multiple apps like Google Workspace, Microsoft 365, Slack, Jira, Confluence, and more. PipesHub AI helps you quickly find the right information using natural language search—just like Google.
It can answer questions, provide insights, and more. The platform not only delivers the most relevant results but also shows where the information came from, with proper citations, using Knowledge Graphs and Page Ranking.
Beyond search, our platform allows enterprises to create custom apps and AI agents using a No-Code interface.

<h2>High Level Architecture Diagram</h2>
  <img
    width="800"
    height="1200"
    src="https://raw.githubusercontent.com/pipeshub-ai/media-assets/refs/heads/main/images/ad.jpg"
    alt="PipesHub System Architecture"
  />

<h2>Spotlight Features</h2>

#### Your Workplace, Our AI.

<p align="center">
  <a href="https://youtu.be/nQcMHxORWtY">
    <img src="https://img.youtube.com/vi/nQcMHxORWtY/0.jpg" alt="PipesHub Workplace AI" width="600" style="border-radius:10px"/>
    <br>
    <img src="https://img.shields.io/badge/Watch%20Video-FF0000?style=plastic&logo=youtube&logoColor=white" alt="Watch Video">
  </a>
</p>

## Unmatched Value of PipesHub

- **Choose Any Model, Your Way** – Bring your preferred deep learning models for both indexing and inference with total flexibility.
- **Real-Time or Scheduled Indexing** – Index data as it flows or schedule it to run exactly when you need.
- **Access-Driven Visibility** – Source-level permissions ensure every document is shown only to those who are authorized.
- **Built-In Data Security** – Sensitive information stays secure, always..
- **Deploy Anywhere** – Fully supports both on-premise and cloud-based deployments.
- **Knowledge Graph Backbone** – All data is seamlessly structured into a powerful knowledge graph.
- **Enterprise-Grade Connectors** – Scalable, reliable, and built for secure access across your organization.
- **Modular & Scalable Architecture** – Every service is loosely coupled to scale independently and adapt to your needs.

## Connectors

- Google Drive
- Gmail
- Google Calendar
- OneDrive
- SharePoint Online
- Outlook
- Outlook Calendar
- Slack
- Notion
- Jira
- Confluence
- Microsoft Teams

## File Formats Supported

- PDF(including scanned PDFs)
- Docx/Doc
- XLSX/XLS
- PPTX/PPT
- CSV
- Markdown
- HTML
- Text
- Google docs, slides, sheets
- Images
- Audio
- Video

## RoadMap

- Code Search
- Workplace AI Agents
- MCP
- APIs and SDKs
- Personalized Search
- Highly available and scalable Kubernetes deployment
- PageRank

## 🚀 Deployment Guide

PipesHub — the Workplace AI Platform — can be run locally or deployed on the cloud using Docker Compose.

---

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

# 🚀 Start the development deployment with build
docker compose -f docker-compose.dev.yml -p pipeshub-ai up --build -d

# 🛑 To stop the services
docker compose -f docker-compose.dev.yml -p pipeshub-ai down
```

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

## 👥 Contributing

Want to join our community of developers? Please check out our [Contributing Guide](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/CONTRIBUTING.md) for more details on how to set up the development environment, our coding standards, and the contribution workflow.

---

<a href="https://star-history.com/#pipeshub-ai/pipeshub-ai&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=pipeshub-ai/pipeshub-ai&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=pipeshub-ai/pipeshub-ai&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=pipeshub-ai/pipeshub-ai&type=Date" />
  </picture>
</a>
