<a name="readme-top"></a>

<h2 align="center">
<a href="https://www.pipeshub.com/">
<img width="50%" src="https://raw.githubusercontent.com/pipeshub-ai/media-assets/refs/heads/main/images/pipeshub-logo.svg"/> 
</a>
</h2>

<p align="center"></p>
<p align="center">Workplace AI Platform</p>

<!--Links in Readme-->
<p align="center">
<a href="https://docs.pipeshub.com/" target="_blank">
    <img src="https://img.shields.io/badge/docs-view-blue" alt="Documentation">
</a>
</p>

<!--Intro-->

<strong>[PipesHub](https://github.com/pipeshub-ai/pipeshub-ai)</strong> is the workplace AI platform for enterprises to improve how businesses operate and help employees and AI agents work more efficiently.
In most companies, important work data is spread across multiple apps like Google Workspace, Microsoft 365, Slack, Jira, Confluence, and more. PipesHub AI helps you quickly find the right information using natural language search—just like Google.
It can answer questions, provide insights, and more. The platform not only delivers the most relevant results but also shows where the information came from, with proper citations, using Knowledge Graphs and Page Ranking.
Beyond search, our platform allows enterprises to create custom apps and AI agents using a No-Code interface.

<h2>High Level Architecture Diagram</h2>
  <img
    width="700"
    height="500"
    src="https://raw.githubusercontent.com/pipeshub-ai/media-assets/refs/heads/main/images/Architecture%20Diagram.svg"
    alt="PipesHub System Architecture"
  />

<h2>Spotlight Features</h2>

#### Your Workplace, Our AI.
<p align="center">
  <a href="https://www.youtube.com/watch?v=czDNbS_OEDQ">
    <img src="https://img.youtube.com/vi/czDNbS_OEDQ/maxresdefault.jpg" alt="PipesHub Workplace AI" width="600" style="border-radius:10px"/>
    <br>
    <img src="https://img.shields.io/badge/Watch%20Video-red?style=for-the-badge&logo=youtube&logoColor=white" alt="Watch on YouTube"/>
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
- Onedrive(Release coming this month)
- Sharepoint Online(Release coming this month)
- Outlook(Release coming this month)
- Outlook Calendar(Release coming this month)
- Slack(Release coming this month)
- Notion(Release coming this month)
- Jira(Release coming this month)
- Confluence(Release coming this month)
- MS Teams(Release coming this month)

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
- Images(Coming Soon)
- Audio(Coming Soon)
- Video(Coming Soon)

## RoadMap

- Code Search
- Workplace AI Agents
- MCP
- APIs and SDKs
- Personalized Search
- Highly available and scalable Kubernetes deployment
- PageRank

## 📦 Developer Build

#### 🔁 Clone the repository

```bash
git clone https://github.com/pipeshub-ai/pipeshub-ai.git
```

#### 📁 Navigate to the deployment folder

```bash
cd pipeshub-ai/deployment/docker-compose
```

# Set Optional Environment Variables

> 👉 Set Environment Variables for secrets, passwords, and the public URLs of the **Frontend** and **Connector** services  
> _(Required for webhook notifications and real-time updates)_
> Refer to env.template

#### 🚀 Start the development deployment with build

```bash
docker compose -f docker-compose.dev.yml -p pipeshub-ai up --build -d
```

#### 🛑 To stop the services

```bash
docker compose -f docker-compose.dev.yml -p pipeshub-ai down
```

## 🚀 Deployment Guide

PipesHub — the Workplace AI Platform — can be run locally or deployed on the cloud using Docker Compose.

---

### 📦 Production Deployment

```bash
# Clone the repository
git clone https://github.com/pipeshub-ai/pipeshub-ai.git

# Navigate to the deployment folder
cd pipeshub-ai/deployment/docker-compose

# Set Environment Variables
> 👉 Set Environment Variables for secrets, passwords, and the public URLs of the **Frontend** and **Connector** services
> _(Required for webhook notifications and real-time updates)_
> Refer to env.template

# Start the production deployment
docker compose -f docker-compose.prod.yml -p pipeshub-ai up -d

# To stop the services
docker compose -f docker-compose.prod.yml -p pipeshub-ai down
```

## 👥 Contributing

Want to join our community of developers? Please check out our [Contributing Guide](https://github.com/pipeshub-ai/blob/main/CONTRIBUTING.md) for more details on how to set up the development environment, our coding standards, and the contribution workflow.
