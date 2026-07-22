# Contribuire a PipesHub Workplace AI

<div align="center">

**Translations:** [English](../../../CONTRIBUTING.md) · [Français](../fr/CONTRIBUTING.md) · [Deutsch](../de/CONTRIBUTING.md) · [简体中文](../zh-CN/CONTRIBUTING.md) · [日本語](../ja/CONTRIBUTING.md) · [Русский](../ru/CONTRIBUTING.md) · [עברית](../he/CONTRIBUTING.md) · [한국어](../ko/CONTRIBUTING.md) · [Español](../es/CONTRIBUTING.md) · [Português](../pt/CONTRIBUTING.md) · [Türkçe](../tr/CONTRIBUTING.md) · [Tiếng Việt](../vi/CONTRIBUTING.md) · **Italiano**

</div>

Benvenuto nel nostro progetto open source! Siamo lieti che tu sia interessato a contribuire. Questo documento fornisce linee guida e istruzioni per aiutarti a iniziare come collaboratore.

## 💻 Build di contributo per sviluppatori

## Indice
- Configurare l'ambiente di sviluppo
- Architettura del progetto
- Flusso di lavoro dei contributi
- Linee guida sullo stile del codice
- Test
- Documentazione
- Linee guida della comunità

## Configurare l'ambiente di sviluppo

### Dipendenze di sistema

#### Linux
```bash
sudo apt update
sudo apt install python3.12-venv
sudo apt-get install libreoffice
sudo apt install libmariadb-dev
```

#### Mac
```bash
# Install Homebrew if not already installed
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"


# Install required packages
brew install python@3.12
brew install libreoffice
brew install mariadb-connector-c # Add to path
```

#### Windows
```bash
- Install Python 3.12
- Consider using WSL2 for a Linux-like environment
```

### Dipendenze dell'applicazione
```bash
1. **Docker** - Install Docker for your platform
2. **Node.js** - Install Node.js(v22.15.0)
3. **Python 3.12** - Install as shown above
4. **Optional debugging tools:**
   - MongoDB Compass or Studio 3T
   - etcd-manager
```

### Avviare i container Docker richiesti

**Redis:**
```bash
docker run -d --name redis --restart always -p 6379:6379 redis:bookworm
```

**Qdrant:** (l'API Key deve corrispondere a .env)
```bash
docker run -p 6333:6333 -p 6334:6334 -e QDRANT__SERVICE__API_KEY=your_qdrant_secret_api_key qdrant/qdrant:v1.13.6
```

**Server ETCD:**


Bash:
```bash
docker run -d --name etcd-server --restart always -p 2379:2379 -p 2380:2380 quay.io/coreos/etcd:v3.5.17 /usr/local/bin/etcd \
  --name etcd0 \
  --data-dir /etcd-data \
  --listen-client-urls http://0.0.0.0:2379 \
  --advertise-client-urls http://0.0.0.0:2379 \
  --listen-peer-urls http://0.0.0.0:2380
```

Powershell:
```powershell
docker run -d --name etcd-server --restart always `
  -p 2379:2379 -p 2380:2380 `
  quay.io/coreos/etcd:v3.5.17 /usr/local/bin/etcd `
  --name etcd0 `
  --data-dir /etcd-data `
  --listen-client-urls http://0.0.0.0:2379 `
  --advertise-client-urls http://0.0.0.0:2379 `
  --listen-peer-urls http://0.0.0.0:2380
```

**ArangoDB:** (la password deve corrispondere a .env)
```bash
docker run -e ARANGO_ROOT_PASSWORD=your_password -p 8529:8529 --name arango --restart always -d arangodb:3.12.4
```

**Neo4j Desktop (al posto di ArangoDB):** PipesHub può usare **Neo4j** come database a grafo (`DATA_STORE=neo4j`) al posto di ArangoDB. È utile se preferisci una GUI locale e non vuoi il container ArangoDB.

1. Installa [Neo4j Desktop](https://neo4j.com/download/), crea un **DBMS locale**, imposta la sua password e **avvialo**.
2. Lascia il listener Bolt predefinito su **localhost:7687** (oppure annota l'host/porta mostrati in Desktop se li hai modificati).
3. **Non** avviare il container Docker di ArangoDB sopra quando usi Neo4j.
4. In `backend/.env` (il modello che copi in `backend/nodejs/apps/.env` e `backend/python/.env`), imposta almeno:
   ```bash
   DATA_STORE=neo4j
   NEO4J_URI=bolt://localhost:7687
   NEO4J_USERNAME=neo4j
   NEO4J_PASSWORD=<same password as your DBMS>
   NEO4J_DATABASE=neo4j
   ```
   I servizi Python leggono `DATA_STORE` e all'avvio scrivono `dataStoreType` nello store KV (etcd/Redis); l'API Node.js lo usa per i controlli di integrità e tratta `NEO4J_*` come la connessione Neo4j attiva.
5. Avvia il servizio Python **connectors** (`python -m app.connectors_main`) prima o insieme al resto dello stack, così che i metadati di distribuzione restino coerenti. Se hai già effettuato il bootstrap su ArangoDB con gli stessi dati etcd, reimposta etcd o la chiave di distribuzione nello store KV prima di cambiare backend del grafo per evitare uno stato incoerente.

Per uno stack completo in Docker con Neo4j invece di ArangoDB, vedi `docker-compose.build.neo4j.yml` in `deployment/docker-compose/` (documentato nel `README.md` del repository).

**MongoDB:** (la password deve corrispondere al MONGO URI di .env)

Bash:
```bash
docker run -d --name mongodb --restart always -p 27017:27017 \
  -e MONGO_INITDB_ROOT_USERNAME=admin \
  -e MONGO_INITDB_ROOT_PASSWORD=password \
  mongo:8.0.6
```

Powershell:
```powershell
docker run -d --name mongodb --restart always -p 27017:27017 `
  -e MONGO_INITDB_ROOT_USERNAME=admin `
  -e MONGO_INITDB_ROOT_PASSWORD=password `
  mongo:8.0.6
```

**Zookeeper:**

Bash:
```bash
docker run -d --name zookeeper --restart always -p 2181:2181 \
  -e ZOOKEEPER_CLIENT_PORT=2181 \
  -e ZOOKEEPER_TICK_TIME=2000 \
  confluentinc/cp-zookeeper:7.9.0
```

Powershell:
```powershell
docker run -d --name zookeeper --restart always -p 2181:2181 `
  -e ZOOKEEPER_CLIENT_PORT=2181 `
  -e ZOOKEEPER_TICK_TIME=2000 `
  confluentinc/cp-zookeeper:7.9.0
```


**Apache Kafka:**

Bash:
```bash
docker run -d --name kafka --restart always --link zookeeper:zookeeper -p 9092:9092 \
  -e KAFKA_BROKER_ID=1 \
  -e KAFKA_ZOOKEEPER_CONNECT=zookeeper:2181 \
  -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://localhost:9092 \
  -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=PLAINTEXT:PLAINTEXT \
  -e KAFKA_INTER_BROKER_LISTENER_NAME=PLAINTEXT \
  -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 \
  confluentinc/cp-kafka:7.9.0
```

Powershell:
```powershell
docker run -d --name kafka --restart always --link zookeeper:zookeeper -p 9092:9092 `
  -e KAFKA_BROKER_ID=1 `
  -e KAFKA_ZOOKEEPER_CONNECT=zookeeper:2181 `
  -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://localhost:9092 `
  -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=PLAINTEXT:PLAINTEXT `
  -e KAFKA_INTER_BROKER_LISTENER_NAME=PLAINTEXT `
  -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 `
  confluentinc/cp-kafka:7.9.0
```

### Avviare il servizio backend Node.js
```bash
cd backend/nodejs/apps
cp ../../env.template .env  # Create .env file from template
npm install
npm run dev
```

### Avviare i servizi backend Python
```bash
cd backend/python
cp ../env.template .env
# Create and activate virtual environment
python3.12 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install uv
pip install uv

# Install dependencies
uv pip install -e .

# Install additional language models
python -m spacy download en_core_web_sm
python -c "import nltk; nltk.download('punkt')"

# Run each service in a separate terminal: First, cd backend/python and activate the existing virtual environment
python -m app.connectors_main
python -m app.indexing_main
python -m app.query_main
python -m app.docling_main
```

### Configurare il frontend
```bash
cd frontend
cp env.template .env  # Modify port if Node.js backend uses a different one
npm install
PORT=3001 npm run dev
```

Quindi apri il browser all'URL mostrato (di solito `http://localhost:3001` quando si usa `PORT=3001`; Next.js usa la porta 3000 per impostazione predefinita se `PORT` non è impostata).

## Architettura del progetto

Il nostro progetto è composto da tre componenti principali:

1. **Frontend**: Applicazione Next.js per l'interfaccia utente
2. **Backend Node.js**: Gestisce le richieste API, l'autenticazione e la logica di business
3. **Servizi Python**: Tre microservizi per:
   - Connectors: Gestisce le connessioni alle fonti di dati
   - Indexing: Gestisce l'indicizzazione e l'elaborazione dei documenti
   - Query: Elabora le richieste di ricerca e recupero

## Flusso di lavoro dei contributi

1. **Fai un fork del repository** sul tuo account GitHub
2. **Clona il tuo fork** sulla tua macchina locale
3. **Crea un nuovo branch** per la tua funzionalità o correzione di bug:
   ```bash
   git checkout -b feature/your-feature-name
   ```
4. **Apporta le tue modifiche** seguendo le nostre linee guida sullo stile del codice
5. **Testa le tue modifiche** in modo approfondito
6. **Esegui il commit delle tue modifiche** con messaggi di commit significativi:
   ```bash
   git commit -m "Add feature: brief description of changes"
   ```
7. **Esegui il push del tuo branch** sul tuo fork GitHub:
   ```bash
   git push origin feature/your-feature-name
   ```
8. **Apri una Pull Request** verso il nostro repository principale
   - Fornisci una descrizione chiara delle modifiche
   - Fai riferimento a eventuali issue correlate
   - Aggiungi screenshot se pertinente

## Linee guida sullo stile del codice

- **Python**: Segui le linee guida PEP 8
- **JavaScript/TypeScript**: Usa ESLint con la configurazione del nostro progetto
- **CSS/SCSS**: Segui la convenzione di denominazione BEM
- **Messaggi di commit**: Usa il formato conventional commits

## Test

- Scrivi test unitari per le nuove funzionalità
- Assicurati che tutti i test passino prima di inviare una PR
- Includi test di integrazione dove appropriato
- Documenta i passaggi di test manuale per le funzionalità complesse

### Eseguire i test unitari Node.js

I test usano **Mocha** come test runner e **c8** per la code coverage. I file di test si trovano in `backend/nodejs/apps/tests/` e seguono la convenzione di denominazione `*.test.ts`. Consulta [`backend/nodejs/apps/tests/README.md`](../../../backend/nodejs/apps/tests/README.md) per tutti i dettagli.

```bash
cd backend/nodejs/apps

# Run all unit tests (parallel, 4 workers)
npm run test

# Run tests with detailed coverage report (text + lcov + html)
npm run test:coverage

# Run tests with coverage thresholds (90% lines/functions/statements, 80% branches)
npm run test:coverage-check

# Run a specific test file
npx mocha --require ts-node/register tests/libs/utils/password.utils.test.ts
```

### Eseguire i test unitari Python

I test usano **pytest** e si trovano in `backend/python/tests/`. I file di test seguono la convenzione di denominazione `test_*.py`. Consulta [`backend/python/tests/README.md`](../../../backend/python/tests/README.md) per tutti i dettagli.

```bash
cd backend/python
source venv/bin/activate

# Run all unit tests
pytest

# Run tests with verbose output
pytest -v

# Run a specific test file
pytest tests/unit/connectors/sources/test_dropbox_connector.py

# Run a specific test function
pytest tests/unit/connectors/sources/test_dropbox_connector.py::test_function_name

# Run tests matching a keyword expression
pytest -k "gmail"

# Run tests with coverage
pytest --cov=app --cov-report=term-missing

# Run tests in parallel (requires pytest-xdist)
pytest -n auto
```

### Eseguire i test E2E del frontend (Playwright)

Il frontend (`frontend/`) usa [Playwright](https://playwright.dev/) per i test end-to-end. I test coprono autenticazione, navigazione, impostazioni dello spazio di lavoro, CRUD delle entità (utenti, gruppi, team), chat e pagine della base di conoscenza. I **dettagli E2E autorevoli** si trovano in [`frontend/tests/e2e/README.md`](../../../frontend/tests/e2e/README.md); quanto segue è un riepilogo rivolto ai collaboratori.

#### Prerequisiti

1. Installa le dipendenze (include `@playwright/test`):
   ```bash
   cd frontend
   npm install
   ```

2. Installa i browser di Playwright:
   ```bash
   npx playwright install chromium
   ```

3. Crea un file `.env.test` dal modello e inserisci le credenziali di test:
   ```bash
   cp .env.test.example .env.test
   ```

   Variabili richieste:
   | Variabile | Descrizione |
   |----------|-------------|
   | `TEST_USER_EMAIL` | Email di un utente amministratore esistente |
   | `TEST_USER_PASSWORD` | Password di quell'utente |
   | `BASE_URL` | Dove Playwright apre l'app (predefinito nella configurazione: `http://localhost:3001`) |
   | `NEXT_PUBLIC_API_BASE_URL` | URL del backend per le chiamate API (seeding/fixtures); predefinito `http://localhost:3000` nei fixture se non impostata |

#### Eseguire i test E2E

Tutti i comandi seguenti vengono eseguiti dalla directory `frontend/`.

| Comando | Descrizione |
|---------|-------------|
| `npm run test:e2e` | Esegue tutti i test (avvia automaticamente il server di sviluppo) |
| `npm run test:e2e:ui` | Apre l'interfaccia di Playwright per il debug interattivo |
| `npm run test:e2e:headed` | Esegue i test in un browser visibile |
| `npm run test:e2e:seed` | Genera dati di test in massa (30 utenti, 30 gruppi, 30 team) |
| `npm run test:e2e:cleanup` | Elimina tutti i dati di test generati |
| `npm run test:e2e:users` | Esegue solo i test relativi agli utenti |
| `npm run test:e2e:groups` | Esegue solo i test relativi ai gruppi |
| `npm run test:e2e:teams` | Esegue solo i test relativi ai team |
| `npm run test:e2e:report` | Apre il report di test HTML |
| `npm run test:e2e:coverage` | Esegue tutti i test con la code coverage V8 |
| `npm run test:e2e:coverage-report` | Apre il report HTML della coverage |

#### Code coverage

Esegui `npm run test:e2e:coverage` per raccogliere la code coverage V8. I report vengono generati in `coverage/e2e/` nei formati V8, LCOV e riepilogo console. Apri il report HTML con `npm run test:e2e:coverage-report`.

#### Debug e output dettagliato

Per osservare l'esecuzione dei test in un browser visibile e acquisire tracce complete (inclusi i test superati):

```bash
# Visible browser + trace for every test
npx playwright test --headed --trace on

# Slow motion — 1 second pause between each action
npx playwright test --headed --trace on --slow-mo=1000

# Record video of every test
npx playwright test --headed --video on

# Screenshot after every test (pass or fail)
npx playwright test --screenshot on
```

| Flag | Cosa fa |
|------|-------------|
| `--headed` | Apre una finestra del browser visibile invece di eseguire in modalità headless |
| `--trace on` | Registra una traccia per ogni test (per impostazione predefinita solo al primo retry) |
| `--slow-mo=N` | Aggiunge una pausa di N millisecondi tra ogni azione di Playwright |
| `--video on` | Registra un video di ogni esecuzione di test |
| `--screenshot on` | Acquisisce uno screenshot dopo ogni test (non solo in caso di errori) |

**Modalità interfaccia interattiva** (consigliata per il debug):

```bash
npm run test:e2e:ui
```

Questo apre l'interfaccia integrata di Playwright con un browser dal vivo, una timeline delle azioni e snapshot del DOM che puoi esaminare passo dopo passo.

**Visualizzare tracce e report dopo un'esecuzione:**

```bash
# Open the HTML report — click any test to see its trace
npx playwright show-report

# Open a specific trace file directly
npx playwright show-trace test-results/<test-folder>/trace.zip
```

#### Progetti di test E2E

Playwright è configurato con quattro progetti che vengono eseguiti in ordine:

1. **setup** — Effettua il login tramite il browser e salva lo stato di autenticazione in `.auth/user.json`.
2. **seed** — Genera dati in massa usando un mix di interazioni con l'interfaccia e chiamate API. Dipende da `setup`.
3. **authenticated** — Tutti i test delle funzionalità che usano lo stato di autenticazione salvato. Dipende da `setup`.
4. **unauthenticated** — Test della pagina di login eseguiti senza autenticazione salvata.

#### Struttura delle directory E2E

```
frontend/tests/e2e/
├── setup/           # Auth setup (login + save storageState)
├── fixtures/        # Shared test fixtures (API context, base)
├── helpers/         # Reusable interaction helpers
│   ├── login.helper.ts
│   ├── entity-table.helper.ts
│   ├── pagination.helper.ts
│   ├── search.helper.ts
│   ├── sidebar-form.helper.ts
│   └── tag-input.helper.ts
├── seed/            # Data seeding and cleanup
├── auth/            # Login and logout tests
├── navigation/      # Routing and sidebar navigation tests
├── workspace/       # Workspace settings page tests
├── users/           # Users table, invite, actions, bulk ops
├── groups/          # Groups table, create, actions
├── teams/           # Teams table, create, actions
├── chat/            # Chat interface tests
└── knowledge-base/  # Knowledge base tests
```

#### Scrivere nuovi test E2E

- I **test autenticati** vanno in una cartella di funzionalità sotto `frontend/tests/e2e/` e importano da `@playwright/test`. Usano automaticamente lo stato di autenticazione salvato.
- I **test basati su API** (seeding, cleanup) importano da `../fixtures/api-context.fixture` in modo relativo rispetto agli altri spec in `tests/e2e/` (vedi `seed/` e `setup/`).
- Gli **helper** in `tests/e2e/helpers/` forniscono funzioni riutilizzabili per le interazioni UI comuni (righe di tabella, paginazione, ricerca, form della barra laterale, input dei tag).

Test di esempio:
```typescript
import { test, expect } from '@playwright/test';

test.describe('My Feature', () => {
  test('loads the page', async ({ page }) => {
    await page.goto('/workspace/my-feature/');
    await expect(page.locator('text="My Feature"')).toBeVisible();
  });
});
```

#### Convenzioni dei dati di seed

- Gli utenti generati seguono il modello `e2e-user-XXXX@e2etest.pipeshub.local`
- I gruppi generati sono denominati `E2E Group XXX`
- I team generati sono denominati `E2E Team XXXX`
- Esegui sempre `npm run test:e2e:cleanup` dopo le esecuzioni con dati generati per rimuovere i dati di test

#### Note CI per E2E

In CI, imposta la variabile d'ambiente `CI=true` per abilitare:
- Nuovi tentativi (2 tentativi per test)
- Worker singolo (esecuzione sequenziale)
- Server di sviluppo nuovo (nessun riutilizzo)

#### Artefatti E2E

I seguenti vengono generati durante le esecuzioni dei test e sono in gitignore:
- `.auth/` — Stato di autenticazione del browser salvato
- `test-results/` — Artefatti di test (screenshot, tracce)
- `playwright-report/` — Report HTML

## Documentazione

- Aggiorna la documentazione per qualsiasi nuova funzionalità o modifica
- Documenta le API con commenti ed esempi appropriati
- Mantieni aggiornati il README e le altre guide

## Linee guida della comunità

- Sii rispettoso e inclusivo in tutte le interazioni
- Fornisci feedback costruttivo sulle pull request
- Aiuta i nuovi collaboratori a iniziare
- Segnala qualsiasi comportamento inappropriato ai manutentori del progetto

---

Grazie per aver contribuito al nostro progetto! Se hai domande o hai bisogno di aiuto, apri una issue o contatta i manutentori.
