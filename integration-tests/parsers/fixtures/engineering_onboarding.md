# Welcome to PipesHub Engineering

Hi, and welcome aboard! This doc will get you from zero to your first PR in about two days. Read it top to bottom once, then use it as a reference. If something is outdated or confusing, please fix it — keeping this doc accurate is everyone's job.

**Your buddy for the first two weeks:** Rajat N. (`@rajat` on Slack)  
**Your manager:** Rahul D. (`@rahul`)

---

## Table of Contents

1. [Before day 1](#before-day-1)
2. [Day 1: accounts & access](#day-1-accounts--access)
3. [Day 2: dev environment](#day-2-dev-environment)
4. [How we work](#how-we-work)
5. [Codebase orientation](#codebase-orientation)
6. [Your first task](#your-first-task)
7. [Who to ask about what](#who-to-ask-about-what)
8. [Useful links](#useful-links)

---

## Before day 1

You should have received an email from IT with a link to activate your Google Workspace account (`yourname@pipeshub.com`). Do this before day 1 — everything else depends on it.

If you haven't received it by the day before your start date, email `it@pipeshub.com`.

---

## Day 1: accounts & access

Work through this list in order. Most access is granted automatically once your Google account is active; a few items need a Slack message to the right person.

### Automatic on Google activation
- [ ] Gmail / Google Calendar / Google Drive
- [ ] Notion (company workspace) — sign in with Google at notion.so
- [ ] Linear (issue tracker) — sign in with Google at linear.app
- [ ] Loom — sign in with Google

### Request from your manager
- [ ] AWS console access (send your IAM username request to `#eng-ops`)
- [ ] GitHub org membership (`github.com/pipeshub`) — ask `@rahul` to invite your GitHub handle
- [ ] 1Password Teams — you'll receive an invite email once added

### Set up yourself
- [ ] Slack — download the desktop app, sign in to `pipeshub.slack.com` with your Google account
- [ ] Join these Slack channels: `#engineering`, `#backend`, `#deployments`, `#incidents`, `#general`, `#random`

---

## Day 2: dev environment

### Prerequisites

Make sure the following are installed before cloning:

- Node.js 20+ (`node --version`)
- Python 3.12+ (`python3 --version`)
- Docker Desktop (running)
- `git` configured with your `@pipeshub.com` email

### Clone and bootstrap

```bash
git clone git@github.com:pipeshub/pipeshub-ai.git
cd pipeshub-ai
cp .env.example .env.local
```

Open `.env.local` and fill in the values. The values for local development are pinned in the `#eng-onboarding` Slack channel (don't commit them).

```bash
# Install TypeScript/Node dependencies
npm install

# Install Python dependencies
pip install -r services/requirements.txt --break-system-packages

# Start all services via Docker Compose
docker compose up -d

# Run the TypeScript backend
npm run dev

# In a separate terminal, run the Python service layer
cd services && uvicorn main:app --reload
```

The app should be running at `http://localhost:3000`. The API is at `http://localhost:8000`.

### Run the test suites

```bash
# TypeScript unit tests
npm test

# Python tests
cd services && pytest

# Integration tests (requires Docker running)
npm run test:integration
```

All tests should pass on a clean checkout. If they don't, check `#eng-onboarding` or ping your buddy.

---

## How we work

### Rhythm

| Cadence | Event |
|---|---|
| Daily | Async standup in `#engineering` (post by 10:30 AM IST) |
| Weekly Monday | Sprint kickoff / planning — 30 min, video call |
| Weekly Friday | Demo + retro — 45 min, video call |
| Biweekly | 1:1 with your manager |

### Async first

We default to async communication. Slack is for quick questions and FYIs; Linear is for tracked work; Notion is for decisions and documentation. Avoid pinging people in DMs for things that belong in a thread.

### Pull requests

- Branch naming: `your-name/short-description` (e.g., `arjun/fix-s3-content-type`)
- PRs should have a description, link to the Linear issue, and at least one test change
- Request review from at least one person; two for anything touching auth or credentials
- We squash-merge to `main`
- CI must be green before merging — no exceptions

### Deployments

Deployments to production happen from `main` via GitHub Actions, triggered manually by engineers with `prod-access`. You won't have prod-access in your first 30 days — that's intentional, not a slight.

---

## Codebase orientation

```
pipeshub-ai/
├── src/                  # TypeScript/Node.js backend
│   ├── controllers/      # Express route handlers
│   ├── services/         # Business logic
│   ├── connectors/       # Third-party API clients
│   └── webhooks/         # Inbound webhook handlers
├── services/             # Python async service layer
│   ├── agents/           # LangChain/LangGraph agent definitions
│   ├── connectors/       # Python connector tools (Salesforce, Zoom, etc.)
│   └── schemas/          # Pydantic models
├── tests/                # TypeScript tests
├── services/tests/       # Python tests
└── docs/                 # Architecture docs, ADRs
```

The TypeScript layer handles HTTP ingress, auth, and webhook routing. The Python layer handles agent execution and third-party API tool calls. They communicate over an internal HTTP interface.

Start reading: `src/webhooks/router.ts` → `services/agents/base_agent.py` → `services/connectors/` (pick one connector you're curious about).

---

## Your first task

Your onboarding task is intentionally scoped to be completable in 2–3 days and touches the full stack. It's pinned in Linear as `PIPES-XXX` — your manager will share the specific issue number on day 1.

The goal isn't just to ship the change — it's to experience the full workflow: local dev, testing, PR review, and CI/CD. Don't rush it.

---

## Who to ask about what

| Topic | Person | Where |
|---|---|---|
| Python services / agents | Priya N. | `@priya` on Slack |
| TypeScript backend / webhooks | Arjun M. | `@arjun` on Slack |
| Infrastructure / AWS / deployments | Rahul D. | `@rahul` on Slack |
| Salesforce / CRM connectors | Priya N. | `@priya` on Slack |
| Microsoft 365 connectors | Arjun M. | `@arjun` on Slack |
| HR / payroll / benefits | Meena S. | `@meena` on Slack |
| IT / access issues | `#eng-ops` | Slack channel |

When in doubt, ask in `#engineering` — someone will route you to the right person.

---

## Useful links

- [Architecture overview](../docs/ARCHITECTURE.md)
- [API reference](../docs/API.md)
- [Connector development guide](../docs/CONNECTOR_GUIDE.md)
- [Security & credentials policy](../docs/POLICY_DATA_HANDLING.md)
- Linear workspace: https://linear.app/pipeshub
- Notion hub: https://notion.so/pipeshub
- Status page: https://status.pipeshub.com

---

*Last updated: May 2026 by Arjun M. — if something's wrong, fix it and open a PR.*