# PipesHub AI — Developer Scripts

## `onboard.py` — Automated Onboarding

Automates the full post-install onboarding so you never have to click through
the wizard on a fresh dev setup.

**What it does (in order):**

| Step | Action |
|------|--------|
| 0 | Wait for the API server to be reachable |
| 1 | Create the org + admin account (skipped if one already exists) |
| 2 | Authenticate and obtain an access token |
| 3 | Configure the LLM (AI model) |
| 4 | Configure the embedding model |
| 5 | Configure SMTP |
| 6 | Mark onboarding as `configured` |

Every step is **idempotent** — running the script a second time will skip steps
that are already complete.

---

### Quick start

```bash
# 1. Install the two Python dependencies (one-time)
pip install requests pyyaml

# 2. Create your config file from the example — store it OUTSIDE the repo
cp scripts/onboarding.config.example.yml ~/pipeshub.config.yml

# 3. Fill in your values (admin credentials, API keys, SMTP, …)
#    See the comments inside the file for all available options.

# 4. Start PipesHub services, then run:
python scripts/onboard.py ~/pipeshub.config.yml
```

The config file lives outside the repo, so your secrets are never at risk of being committed.

---

### Options

```
python scripts/onboard.py --help

  CONFIG_FILE     Path to your YAML config file (required, can live anywhere)

  --dry-run       Print what would happen without making any API calls
```

```bash
# dry-run example
python scripts/onboard.py ~/pipeshub.config.yml --dry-run
```

---

### Supported providers

**LLM (`llm.provider`)**

| Value | Description |
|-------|-------------|
| `openAI` | OpenAI (GPT-4o, GPT-4, …) |
| `anthropic` | Anthropic (Claude 3.5, …) |
| `gemini` | Google Gemini |
| `groq` | Groq fast inference |
| `mistral` | Mistral AI |
| `cohere` | Cohere |
| `xai` | xAI (Grok) |
| `azureOpenAI` | Azure OpenAI Service |
| `ollama` | Local Ollama server |
| `openAICompatible` | Any OpenAI-compatible endpoint |

**Embedding (`embedding.provider_type`)**

| Value | Description |
|-------|-------------|
| `default` | Platform built-in embeddings (no API key needed) |
| `openAI` | OpenAI `text-embedding-*` models |
| `gemini` | Google Gemini embeddings |
| `cohere` | Cohere embed |
| `ollama` | Local Ollama (`nomic-embed-text`, …) |
| `sentenceTransformers` | Hugging Face sentence-transformers |
| `fastembed` | FastEmbed local models |

---

### Minimal config examples

**OpenAI LLM + default embeddings + no SMTP**

```yaml
api_base_url: "http://localhost:3000"
admin:
  email: "dev@example.com"
  password: "Dev@1234!"
  full_name: "Dev User"
llm:
  provider: "openAI"
  api_key: "sk-..."
  model: "gpt-4o"
embedding:
  provider_type: "default"
smtp:
  skip: true
```

**Local Ollama (no external API keys)**

```yaml
api_base_url: "http://localhost:3000"
admin:
  email: "dev@example.com"
  password: "Dev@1234!"
  full_name: "Dev User"
llm:
  provider: "ollama"
  endpoint: "http://localhost:11434"
  model: "llama3"
  api_key: ""
embedding:
  provider_type: "ollama"
  endpoint: "http://localhost:11434"
  model: "nomic-embed-text"
  api_key: ""
smtp:
  skip: true
```
