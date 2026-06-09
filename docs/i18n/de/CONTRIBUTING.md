# Zu PipesHub Workplace AI beitragen

<div align="center">

**Translations:** [English](../../../CONTRIBUTING.md) · [Français](../fr/CONTRIBUTING.md) · **Deutsch** · [简体中文](../zh-CN/CONTRIBUTING.md) · [日本語](../ja/CONTRIBUTING.md) · [Русский](../ru/CONTRIBUTING.md) · [עברית](../he/CONTRIBUTING.md) · [한국어](../ko/CONTRIBUTING.md) · [Español](../es/CONTRIBUTING.md) · [Português](../pt/CONTRIBUTING.md) · [Türkçe](../tr/CONTRIBUTING.md) · [Tiếng Việt](../vi/CONTRIBUTING.md) · [Italiano](../it/CONTRIBUTING.md)

</div>

Willkommen in unserem Open-Source-Projekt! Wir freuen uns, dass du an einem Beitrag interessiert bist. Dieses Dokument bietet Richtlinien und Anleitungen, die dir den Einstieg als Mitwirkender erleichtern.

## 💻 Entwickler-Beitrags-Build

## Inhaltsverzeichnis
- Einrichten der Entwicklungsumgebung
- Projektarchitektur
- Beitragsworkflow
- Richtlinien zum Codestil
- Tests
- Dokumentation
- Community-Richtlinien

## Einrichten der Entwicklungsumgebung

### Systemabhängigkeiten

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

### Anwendungsabhängigkeiten
```bash
1. **Docker** - Install Docker for your platform
2. **Node.js** - Install Node.js(v22.15.0)
3. **Python 3.12** - Install as shown above
4. **Optional debugging tools:**
   - MongoDB Compass or Studio 3T
   - etcd-manager
```

### Starten der erforderlichen Docker-Container

**Redis:**
```bash
docker run -d --name redis --restart always -p 6379:6379 redis:bookworm
```

**Qdrant:** (Der API Key muss mit der .env übereinstimmen)
```bash
docker run -p 6333:6333 -p 6334:6334 -e QDRANT__SERVICE__API_KEY=your_qdrant_secret_api_key qdrant/qdrant:v1.13.6
```

**ETCD-Server:**


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

**ArangoDB:** (Das Passwort muss mit der .env übereinstimmen)
```bash
docker run -e ARANGO_ROOT_PASSWORD=your_password -p 8529:8529 --name arango --restart always -d arangodb:3.12.4
```

**Neo4j Desktop (anstelle von ArangoDB):** PipesHub kann **Neo4j** als Graphdatenbank (`DATA_STORE=neo4j`) anstelle von ArangoDB verwenden. Das ist nützlich, wenn du eine lokale GUI bevorzugst und den ArangoDB-Container nicht möchtest.

1. Installiere [Neo4j Desktop](https://neo4j.com/download/), erstelle ein **lokales DBMS**, lege dessen Passwort fest und **starte** es.
2. Belasse den Standard-Bolt-Listener auf **localhost:7687** (oder notiere den in Desktop angezeigten Host/Port, falls du ihn geändert hast).
3. Starte den obigen ArangoDB-Docker-Container **nicht**, wenn du Neo4j verwendest.
4. Setze in `backend/.env` (der Vorlage, die du in `backend/nodejs/apps/.env` und `backend/python/.env` kopierst) mindestens:
   ```bash
   DATA_STORE=neo4j
   NEO4J_URI=bolt://localhost:7687
   NEO4J_USERNAME=neo4j
   NEO4J_PASSWORD=<same password as your DBMS>
   NEO4J_DATABASE=neo4j
   ```
   Die Python-Dienste lesen `DATA_STORE` und schreiben beim Start `dataStoreType` in den KV-Store (etcd/Redis); die Node.js-API verwendet dies für Health Checks und behandelt `NEO4J_*` als die aktive Neo4j-Verbindung.
5. Starte den **connectors**-Python-Dienst (`python -m app.connectors_main`) vor oder zusammen mit dem Rest des Stacks, damit die Deployment-Metadaten konsistent bleiben. Wenn du bereits gegen ArangoDB mit denselben etcd-Daten gebootstrapt hast, setze etcd oder den Deployment-Schlüssel im KV-Store zurück, bevor du das Graph-Backend wechselst, um inkonsistente Zustände zu vermeiden.

Für einen vollständigen Stack in Docker mit Neo4j anstelle von ArangoDB siehe `docker-compose.build.neo4j.yml` in `deployment/docker-compose/` (dokumentiert in der `README.md` des Repositorys).

**MongoDB:** (Das Passwort muss mit dem MONGO URI in der .env übereinstimmen)

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

### Starten des Node.js-Backend-Dienstes
```bash
cd backend/nodejs/apps
cp ../../env.template .env  # Create .env file from template
npm install
npm run dev
```

### Starten der Python-Backend-Dienste
```bash
cd backend/python
cp ../env.template .env
# Create and activate virtual environment
python3.12 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -e .

# Install additional language models
python -m spacy download en_core_web_sm
python -c "import nltk; nltk.download('punkt')"

# Run each service in a separate terminal: First, cd backend/python and activate the existing virtual environment
python -m app.connectors_main
python -m app.indexing_main
python -m app.query_main
python -m app.docling_main
```

### Einrichten des Frontends
```bash
cd frontend
cp env.template .env  # Modify port if Node.js backend uses a different one
npm install
PORT=3001 npm run dev
```

Öffne dann deinen Browser unter der angezeigten URL (in der Regel `http://localhost:3001` bei Verwendung von `PORT=3001`; Next.js verwendet standardmäßig Port 3000, wenn `PORT` nicht gesetzt ist).

## Projektarchitektur

Unser Projekt besteht aus drei Hauptkomponenten:

1. **Frontend**: Next.js-Anwendung für die Benutzeroberfläche
2. **Node.js-Backend**: Verarbeitet API-Anfragen, Authentifizierung und Geschäftslogik
3. **Python-Dienste**: Drei Microservices für:
   - Connectors: Verwaltet Verbindungen zu Datenquellen
   - Indexing: Verwaltet die Indexierung und Verarbeitung von Dokumenten
   - Query: Verarbeitet Such- und Abrufanfragen

## Beitragsworkflow

1. **Forke das Repository** in dein GitHub-Konto
2. **Klone deinen Fork** auf deinen lokalen Rechner
3. **Erstelle einen neuen Branch** für deine Funktion oder Fehlerbehebung:
   ```bash
   git checkout -b feature/your-feature-name
   ```
4. **Nimm deine Änderungen vor** und befolge dabei unsere Codestil-Richtlinien
5. **Teste deine Änderungen** gründlich
6. **Committe deine Änderungen** mit aussagekräftigen Commit-Nachrichten:
   ```bash
   git commit -m "Add feature: brief description of changes"
   ```
7. **Pushe deinen Branch** in deinen GitHub-Fork:
   ```bash
   git push origin feature/your-feature-name
   ```
8. **Öffne einen Pull Request** gegen unser Haupt-Repository
   - Gib eine klare Beschreibung der Änderungen an
   - Verweise auf zugehörige Issues
   - Füge gegebenenfalls Screenshots hinzu

## Richtlinien zum Codestil

- **Python**: Befolge die PEP-8-Richtlinien
- **JavaScript/TypeScript**: Verwende ESLint mit unserer Projektkonfiguration
- **CSS/SCSS**: Befolge die BEM-Namenskonvention
- **Commit-Nachrichten**: Verwende das Conventional-Commits-Format

## Tests

- Schreibe Unit-Tests für neue Funktionen
- Stelle sicher, dass alle Tests bestehen, bevor du einen PR einreichst
- Füge gegebenenfalls Integrationstests hinzu
- Dokumentiere manuelle Testschritte für komplexe Funktionen

### Ausführen der Node.js-Unit-Tests

Die Tests verwenden **Mocha** als Test-Runner und **c8** für die Code-Coverage. Die Testdateien befinden sich in `backend/nodejs/apps/tests/` und folgen der Namenskonvention `*.test.ts`. Vollständige Details findest du in [`backend/nodejs/apps/tests/README.md`](../../../backend/nodejs/apps/tests/README.md).

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

### Ausführen der Python-Unit-Tests

Die Tests verwenden **pytest** und befinden sich in `backend/python/tests/`. Die Testdateien folgen der Namenskonvention `test_*.py`. Vollständige Details findest du in [`backend/python/tests/README.md`](../../../backend/python/tests/README.md).

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

### Ausführen der Frontend-E2E-Tests (Playwright)

Das Frontend (`frontend/`) verwendet [Playwright](https://playwright.dev/) für End-to-End-Tests. Die Tests decken Authentifizierung, Navigation, Workspace-Einstellungen, Entitäts-CRUD (Benutzer, Gruppen, Teams), Chat und Knowledge-Base-Seiten ab. Die **maßgeblichen E2E-Details** befinden sich in [`frontend/tests/e2e/README.md`](../../../frontend/tests/e2e/README.md); das Folgende ist eine auf Mitwirkende ausgerichtete Zusammenfassung.

#### Voraussetzungen

1. Installiere die Abhängigkeiten (enthält `@playwright/test`):
   ```bash
   cd frontend
   npm install
   ```

2. Installiere die Playwright-Browser:
   ```bash
   npx playwright install chromium
   ```

3. Erstelle eine `.env.test`-Datei aus der Vorlage und trage die Testanmeldedaten ein:
   ```bash
   cp .env.test.example .env.test
   ```

   Erforderliche Variablen:
   | Variable | Beschreibung |
   |----------|-------------|
   | `TEST_USER_EMAIL` | E-Mail eines vorhandenen Admin-Benutzers |
   | `TEST_USER_PASSWORD` | Passwort dieses Benutzers |
   | `BASE_URL` | Wo Playwright die App öffnet (Standard in der Konfiguration: `http://localhost:3001`) |
   | `NEXT_PUBLIC_API_BASE_URL` | Backend-URL für API-Aufrufe (Seeding/Fixtures); standardmäßig `http://localhost:3000` in den Fixtures, wenn nicht gesetzt |

#### Ausführen der E2E-Tests

Alle folgenden Befehle werden aus dem Verzeichnis `frontend/` ausgeführt.

| Befehl | Beschreibung |
|---------|-------------|
| `npm run test:e2e` | Führt alle Tests aus (startet den Dev-Server automatisch) |
| `npm run test:e2e:ui` | Öffnet die Playwright-UI für interaktives Debugging |
| `npm run test:e2e:headed` | Führt die Tests in einem sichtbaren Browser aus |
| `npm run test:e2e:seed` | Erzeugt Massentestdaten (30 Benutzer, 30 Gruppen, 30 Teams) |
| `npm run test:e2e:cleanup` | Löscht alle erzeugten Testdaten |
| `npm run test:e2e:users` | Führt nur die benutzerbezogenen Tests aus |
| `npm run test:e2e:groups` | Führt nur die gruppenbezogenen Tests aus |
| `npm run test:e2e:teams` | Führt nur die teambezogenen Tests aus |
| `npm run test:e2e:report` | Öffnet den HTML-Testbericht |
| `npm run test:e2e:coverage` | Führt alle Tests mit V8-Code-Coverage aus |
| `npm run test:e2e:coverage-report` | Öffnet den HTML-Coverage-Bericht |

#### Code-Coverage

Führe `npm run test:e2e:coverage` aus, um die V8-Code-Coverage zu erfassen. Die Berichte werden in `coverage/e2e/` in den Formaten V8, LCOV und Konsolenzusammenfassung erzeugt. Öffne den HTML-Bericht mit `npm run test:e2e:coverage-report`.

#### Debugging und ausführliche Ausgabe

Um die Testausführung in einem sichtbaren Browser zu beobachten und vollständige Traces (einschließlich bestandener Tests) zu erfassen:

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

| Flag | Was es bewirkt |
|------|-------------|
| `--headed` | Öffnet ein sichtbares Browserfenster, anstatt headless zu laufen |
| `--trace on` | Zeichnet für jeden Test einen Trace auf (standardmäßig nur beim ersten Retry) |
| `--slow-mo=N` | Fügt zwischen jeder Playwright-Aktion eine Pause von N Millisekunden ein |
| `--video on` | Nimmt ein Video jedes Testlaufs auf |
| `--screenshot on` | Erstellt nach jedem Test einen Screenshot (nicht nur bei Fehlern) |

**Interaktiver UI-Modus** (zum Debuggen empfohlen):

```bash
npm run test:e2e:ui
```

Dies öffnet die integrierte UI von Playwright mit einem Live-Browser, einer Aktionszeitleiste und DOM-Snapshots, die du Schritt für Schritt durchgehen kannst.

**Traces und Berichte nach einem Lauf anzeigen:**

```bash
# Open the HTML report — click any test to see its trace
npx playwright show-report

# Open a specific trace file directly
npx playwright show-trace test-results/<test-folder>/trace.zip
```

#### E2E-Testprojekte

Playwright ist mit vier Projekten konfiguriert, die der Reihe nach ausgeführt werden:

1. **setup** — Meldet sich über den Browser an und speichert den Authentifizierungszustand in `.auth/user.json`.
2. **seed** — Erzeugt Massendaten mit einer Mischung aus UI-Interaktionen und API-Aufrufen. Hängt von `setup` ab.
3. **authenticated** — Alle Funktionstests, die den gespeicherten Authentifizierungszustand verwenden. Hängt von `setup` ab.
4. **unauthenticated** — Tests der Anmeldeseite, die ohne gespeicherte Authentifizierung ausgeführt werden.

#### E2E-Verzeichnisstruktur

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

#### Neue E2E-Tests schreiben

- **Authentifizierte Tests** kommen in einen Funktionsordner unter `frontend/tests/e2e/` und importieren aus `@playwright/test`. Sie verwenden automatisch den gespeicherten Authentifizierungszustand.
- **API-basierte Tests** (Seeding, Cleanup) importieren aus `../fixtures/api-context.fixture` relativ zu anderen Specs in `tests/e2e/` (siehe `seed/` und `setup/`).
- **Helfer** in `tests/e2e/helpers/` stellen wiederverwendbare Funktionen für gängige UI-Interaktionen bereit (Tabellenzeilen, Paginierung, Suche, Seitenleisten-Formulare, Tag-Eingabe).

Beispieltest:
```typescript
import { test, expect } from '@playwright/test';

test.describe('My Feature', () => {
  test('loads the page', async ({ page }) => {
    await page.goto('/workspace/my-feature/');
    await expect(page.locator('text="My Feature"')).toBeVisible();
  });
});
```

#### Konventionen für Seed-Daten

- Erzeugte Benutzer folgen dem Muster `e2e-user-XXXX@e2etest.pipeshub.local`
- Erzeugte Gruppen werden `E2E Group XXX` benannt
- Erzeugte Teams werden `E2E Team XXXX` benannt
- Führe nach Läufen mit erzeugten Daten immer `npm run test:e2e:cleanup` aus, um die Testdaten zu entfernen

#### E2E-CI-Hinweise

Setze in CI die Umgebungsvariable `CI=true`, um Folgendes zu aktivieren:
- Wiederholungen (2 Versuche pro Test)
- Einzelner Worker (sequenzielle Ausführung)
- Frischer Dev-Server (keine Wiederverwendung)

#### E2E-Artefakte

Folgendes wird während der Testläufe erzeugt und ist per gitignore ausgeschlossen:
- `.auth/` — Gespeicherter Browser-Authentifizierungszustand
- `test-results/` — Testartefakte (Screenshots, Traces)
- `playwright-report/` — HTML-Bericht

## Dokumentation

- Aktualisiere die Dokumentation für alle neuen Funktionen oder Änderungen
- Dokumentiere APIs mit passenden Kommentaren und Beispielen
- Halte die README und andere Leitfäden aktuell

## Community-Richtlinien

- Sei in allen Interaktionen respektvoll und inklusiv
- Gib konstruktives Feedback zu Pull Requests
- Hilf neuen Mitwirkenden beim Einstieg
- Melde unangemessenes Verhalten an die Projektbetreuer

---

Danke, dass du zu unserem Projekt beiträgst! Wenn du Fragen hast oder Hilfe benötigst, öffne ein Issue oder wende dich an die Betreuer.
