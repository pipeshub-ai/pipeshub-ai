# Jak přispívat do PipesHub Workplace AI

<div align="center">

**Translations:** [English](../../../CONTRIBUTING.md) · [Français](../fr/CONTRIBUTING.md) · [Deutsch](../de/CONTRIBUTING.md) · [简体中文](../zh-CN/CONTRIBUTING.md) · [日本語](../ja/CONTRIBUTING.md) · [Русский](../ru/CONTRIBUTING.md) · [עברית](../he/CONTRIBUTING.md) · [한국어](../ko/CONTRIBUTING.md) · [Español](../es/CONTRIBUTING.md) · [Português](../pt/CONTRIBUTING.md) · [Türkçe](../tr/CONTRIBUTING.md) · [Tiếng Việt](../vi/CONTRIBUTING.md) · [Italiano](../it/CONTRIBUTING.md) · **Čeština**

Aktuální je anglický originál. Překlady mohou zaostávat; dokud nebudou dotaženy, řiďte se u označených issues a místního nastavení anglickou verzí.

</div>

Vítejte v našem open source projektu! Těší nás, že máte zájem přispívat. Tento dokument obsahuje pokyny a návody, které vám pomohou začít jako přispěvatel.

Dotazy: [Discord](https://discord.com/invite/K5RskzJBm2), [GitHub Discussions](https://github.com/pipeshub-ai/pipeshub-ai/discussions), nebo otevřete issue. Vyberte si některé z níže uvedených označených issues, nebo otevřete pull request se svou změnou.

## Obsah
- [Na čem pracovat](#na-čem-pracovat)
- [Dokumentace](#dokumentace)
- [Nové konektory](#nové-konektory)
- [Nastavení vývojového prostředí](#nastavení-vývojového-prostředí)
- [Architektura projektu](#architektura-projektu)
- [Postup přispívání](#postup-přispívání)
- [Pravidla stylu kódu](#pravidla-stylu-kódu)
- [Testování](#testování)
- [Pravidla komunity](#pravidla-komunity)

## Na čem pracovat

Označené issues jsou dobrým výchozím bodem, ale vítány jsou i jiné užitečné změny.

1. [`first-timers-only`](https://github.com/pipeshub-ai/pipeshub-ai/labels/first-timers-only) — malé a jasně vymezené; vhodné jako první open source PR.
2. [`good first issue`](https://github.com/pipeshub-ai/pipeshub-ai/labels/good%20first%20issue) — jeden večer, jedna oblast.
3. [`help wanted`](https://github.com/pipeshub-ai/pipeshub-ai/labels/help%20wanted) — několik dní samostatné práce.

Napište k issue komentář, aby ostatní věděli, že na něm pracujete. Pokud už opravu máte, otevřete pull request a odkažte na issue, nebo problém popište přímo v PR.

## Dokumentace

Dokumentace je na dvou místech:

- Tento repozitář: `README.md`, `docs/`, tento soubor a OpenAPI v `backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml`. Když měníte HTTP routy, aktualizujte i soubor OpenAPI.
- [`pipeshub-ai/documentation`](https://github.com/pipeshub-ai/documentation), z něhož se publikuje [docs.pipeshub.com](https://docs.pipeshub.com).

Postup změny stránky:

1. Forkněte repozitář, ve kterém se soubor nachází.
2. Upravte jej. Verze, cesty a instalační příkazy sladěte s tímto repozitářem na větvi `main`.
3. Otevřete pull request.

Práce pouze na Markdownu nevyžaduje Docker ani místní stack. Pokud přidáváte nebo měníte funkci produktu, aktualizujte pokud možno ve stejném PR i stránku, která ji popisuje.

## Nové konektory

Nový konektor je rozsáhlá práce. Výchozím bodem je [CONNECTOR.md](../../../CONNECTOR.md) a podle [CONNECTOR_CHECKLIST.md](../../../CONNECTOR_CHECKLIST.md) jej bude recenzent kontrolovat; rozsah je vhodné nejprve probrat na Discordu.

## Nastavení vývojového prostředí

Pokud upravujete pouze dokumentaci, tuto část přeskočte.

Potřebujete klon tohoto repozitáře na svém počítači. Forkněte jej na GitHubu a poté:

```bash
git clone https://github.com/<your-username>/pipeshub-ai.git
cd pipeshub-ai
```

Produkt tvoří dva druhy procesů:

- **Aplikační služby** — Node.js API, služby Python FastAPI a Next.js UI. To jsou programy v tomto repozitáři.
- **Úložiště** — databáze, se kterými tyto programy komunikují: Redis (konfigurace a události), Qdrant (vyhledávací vektory), Neo4j (znalostní graf) a MongoDB (relace a metadata). Nejde o kód PipesHub; běží jako samostatné kontejnery.

Lokálně to lze spustit dvěma způsoby:

1. **Vše v Dockeru** — jeden instalátor spustí úložiště i aplikační služby jako kontejnery. Použijte, pokud chcete vyzkoušet celou instanci bez ručního spouštění jednotlivých procesů.
2. **Aplikační služby ze zdrojových kódů** — úložiště stále spouštíte v Dockeru, ale API, služby v Pythonu a UI spouštíte sami ze souborů v tomto checkoutu (`npm run dev`, `python -m app.embedding_main` a tak dále). Vaše úpravy se načtou bez nutnosti znovu sestavovat image Dockeru. Použijte, pokud tento kód měníte.

### Spuštění všeho v Dockeru

Instalátor spustí úložiště a aplikační služby společně:

```bash
./install.sh --build
```

Zbytek této části se týká spouštění aplikačních služeb ze zdrojových kódů.

### Systémové balíčky

#### Linux

```bash
sudo apt update
sudo apt install python3.12-venv
sudo apt-get install libreoffice
sudo apt install libmariadb-dev
```

#### macOS

Pokud chybí `brew`, nainstalujte [Homebrew](https://brew.sh) a poté:

```bash
brew install python@3.12
brew install libreoffice
brew install mariadb-connector-c
```

#### Windows

Nainstalujte Python 3.12, nebo použijte WSL2 a postupujte podle kroků pro Linux.

### Aplikační nástroje

1. **Docker** — Redis, Qdrant, Neo4j a MongoDB (výchozí úložiště níže)
2. **Node.js 22** — API v `backend/nodejs/apps`, frontend ve `frontend`. Kořenový `package.json` neexistuje.
3. **Python 3.12** — služby FastAPI v `backend/python`

### Soubory prostředí

Zkopírujte `backend/env.template` do obou backendových stromů. Výchozí hodnoty jsou `DATA_STORE=neo4j`, `MESSAGE_BROKER=redis` a `KV_STORE_TYPE=redis`.

```bash
cp backend/env.template backend/nodejs/apps/.env
cp backend/env.template backend/python/.env
```

Hesla a API klíč Qdrantu v těchto souborech musí odpovídat kontejnerům v dalším kroku.

### Výchozí úložiště

Spusťte Redis, Qdrant, Neo4j a MongoDB. Hesla a klíče musí odpovídat výše uvedeným souborům `.env`.

**Redis** (konfigurační KV a Redis Streams jako sběrnice událostí):

```bash
docker run -d --name redis --restart always -p 6379:6379 redis:7.4-bookworm
```

**Qdrant** (vektory). API klíč musí odpovídat `QDRANT_API_KEY` v `.env`:

```bash
docker run -p 6333:6333 -p 6334:6334 -e QDRANT__SERVICE__API_KEY=your_qdrant_secret_api_key qdrant/qdrant:v1.15
```

**Neo4j** (graf). Heslo musí odpovídat `NEO4J_PASSWORD` v `.env`. Místo kontejneru lze použít i Neo4j Desktop; Bolt zůstává na `localhost:7687`.

```bash
docker run -d --name neo4j --restart always -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/your_neo4j_password neo4j:5.26.0
```

**MongoDB** (relace a metadata). Uživatelské jméno a heslo musí odpovídat `MONGO_URI` v `.env`:

```bash
docker run -d --name mongodb --restart always -p 27017:27017 \
  -e MONGO_INITDB_ROOT_USERNAME=admin \
  -e MONGO_INITDB_ROOT_PASSWORD=password \
  mongo:8.0.17
```

### Volitelná úložiště

Nespouštějte je, pokud nezměníte výchozí hodnoty v env:

- ArangoDB místo Neo4j: `DATA_STORE=arangodb`. Nespouštějte jej vedle Neo4j.
- Kafka místo Redis Streams: `MESSAGE_BROKER=kafka` (ZooKeeper a Kafka).
- etcd místo Redis KV: `KV_STORE_TYPE=etcd`.

Pokud přepínáte grafový nebo KV backend nad existujícími daty, nejprve v KV úložišti resetujte `dataStoreType`. Python tento klíč zapisuje při startu z `DATA_STORE`; Node API jej čte pro health checky, takže hodnota zbylá z předchozího backendu nebude souhlasit s `.env`.

### Node.js API (port 3000)

```bash
cd backend/nodejs/apps
npm install
npm run dev
```

### Služby v Pythonu

Virtualenv vytvořte jen jednou. Poté spusťte každý proces ve vlastním terminálu s aktivovaným venv. Pokud používáte lokální model HuggingFace, spusťte **embedding** před indexing a query.

```bash
cd backend/python
python3.12 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install uv
uv pip install -e .
python -c "import nltk; nltk.download('punkt')"

python -m app.embedding_main
python -m app.connectors_main
python -m app.indexing_main
python -m app.query_main
python -m app.docling_main
```

Parsing (`app.parsing_main`, 8092) a extraction (`app.extraction_main`, 8093) pouze s `USE_PARSING_SERVICE=true`. Tato proměnná není v `backend/env.template`; nastavte ji v `backend/python/.env`.

### Frontend (port 3001)

Pokud není nastaven `PORT`, Next.js použije port 3000, který koliduje s API.

```bash
cd frontend
cp env.template .env
npm install
PORT=3001 npm run dev
```

Otevřete `http://localhost:3001`. Ve Windows PowerShellu použijte místo `cp` příkaz `Copy-Item` a `$env:PORT = '3001'`.

### Kontrola stavu

```bash
./scripts/check_system_health.sh
```

Skript pingne API (3000), UI (3001) a služby v Pythonu. Parsing a extraction se kontrolují jen tehdy, je-li v shellu `USE_PARSING_SERVICE=true` (před spuštěním skriptu `export USE_PARSING_SERVICE=true`).

## Architektura projektu

Tři aplikační vrstvy plus úložiště:

1. **Frontend** — Next.js UI (`frontend/`). Lokálně `npm run dev` na portu **3001**.
2. **Node.js API** — Express (`backend/nodejs/apps`). Lokálně `npm run dev` na portu **3000**. Autentizace, organizace, znalostní báze, objektové úložiště, API gateway.
3. **Služby Python FastAPI** (`backend/python/`):
   - **Embedding** (8002, `app.embedding_main`) — lokální HuggingFace / SentenceTransformers přes API kompatibilní s OpenAI. Indexing a query jej volají pro výchozí dense embeddingy. Cloudoví poskytovatelé embeddingů tento proces nepotřebují.
   - **Connectors** (8088, `app.connectors_main`) — OAuth, obnova tokenů a integrace datových zdrojů.
   - **Indexing** (8091, `app.indexing_main`) — parsování, dělení na chunky, embedding; zapisuje vektory a uzly grafu.
   - **Query** (8000, `app.query_main`) — vyhledávání, RAG, agenti.
   - **Docling** (8081, `app.docling_main`) — PDF a další složité dokumenty.
   - **Parsing** (8092) a **Extraction** (8093) — volitelné; pouze s `USE_PARSING_SERVICE=true`.

**Úložiště (výchozí hodnoty v `backend/env.template`):** Redis (konfigurační KV a Redis Streams jako sběrnice událostí), Qdrant (vektory), Neo4j (graf), MongoDB (relace a metadata). ArangoDB může nahradit Neo4j. Kafka může ve větším nasazení nahradit Redis Streams. etcd může nahradit Redis jako KV úložiště.

V Dockeru (`./install.sh --build`) obsluhuje Node API i sestavené UI společně na portu **3000**.

## Postup přispívání

1. **Forkněte repozitář** do svého účtu na GitHubu
2. **Naklonujte svůj fork** na svůj počítač
3. **Vytvořte novou větev** pro svou funkci nebo opravu chyby:
   ```bash
   git checkout -b feature/your-feature-name
   ```
4. **Proveďte změny** v souladu s našimi pravidly stylu kódu. Úpravy dokumentace nevyžadují místní stack.
5. **Důkladně otestujte** své změny
6. **Commitněte změny** se smysluplnými commit zprávami:
   ```bash
   git commit -m "Add feature: brief description of changes"
   ```
7. **Pushněte větev** do svého forku na GitHubu:
   ```bash
   git push origin feature/your-feature-name
   ```
8. **Otevřete pull request** proti našemu hlavnímu repozitáři
   - Jasně popište změny
   - Odkažte na související issues
   - Pokud je to vhodné, přidejte snímky obrazovky

Pull requesty z forků spouštějí integrační testy bez tajných údajů repozitáře:
CI spouští pouze pomocné unit testy v `integration-tests/unit/`. Správce
spustí plnou sadu nad zrevidovaným commitem pomocí *Run workflow* na
`integration-tests.yml` s hashem daného commitu (viz
[integration-tests/README.md](../../../integration-tests/README.md)).

## Pravidla stylu kódu

- **Python**: dodržujte pokyny PEP 8
- **JavaScript/TypeScript**: používejte ESLint s konfigurací našeho projektu
- **CSS/SCSS**: dodržujte konvenci pojmenování BEM
- **Commit zprávy**: používejte formát conventional commits

## Testování

- Pište unit testy pro nové funkce
- Před odesláním PR se ujistěte, že všechny testy procházejí
- Kde je to vhodné, přidejte integrační testy
- U složitých funkcí zdokumentujte kroky ručního testování

### Kontrola všeho najednou

```bash
scripts/verify.sh          # every suite that runs without Docker or network
scripts/verify.sh --list   # what would run, and why anything is skipped
```

Pokrývá sady testů pro Python, Node, frontend, Electron a shell a vypíše
jediný souhrn. Sada, kterou na vašem počítači nelze spustit, se nahlásí jako přeskočená
s uvedením důvodu, nikdy jako úspěšná.

Nespouští `integration-tests/`, které potřebují celý stack a sdílené
cloudové úložiště a trvají hodiny — ty běží v CI. Spouští ale
`backend/python/tests/integration`, které navzdory názvu adresáře běží převážně
in-process; sady, které tam skutečně potřebují nějakou službu, se samy přeskočí, pokud
služba chybí.

### Spouštění unit testů Node.js

Testy používají **Mocha** jako test runner a **c8** pro pokrytí kódu. Testovací soubory jsou v `backend/nodejs/apps/tests/` a dodržují konvenci pojmenování `*.test.ts`. Podrobnosti najdete v [`backend/nodejs/apps/tests/README.md`](../../../backend/nodejs/apps/tests/README.md).

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

### Spouštění unit testů v Pythonu

Testy používají **pytest** a jsou v `backend/python/tests/`. Testovací soubory dodržují konvenci pojmenování `test_*.py`. Podrobnosti najdete v [`backend/python/tests/README.md`](../../../backend/python/tests/README.md).

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

### Spouštění unit testů frontendu

Unit testy používají **Vitest** a jsou ve složkách `__tests__` vedle testovaného kódu v `frontend/app/` a `frontend/lib/`, pojmenované `*.test.ts` nebo `*.test.tsx`.

```bash
cd frontend

# Run all unit tests
npm run test:unit

# Run them with coverage, as CI does (reports land in coverage/unit)
npm run test:unit:coverage
```

CI spouští příkaz s pokrytím a selže, pokud pokrytí klesne pod minimum nastavené v `coverage.thresholds` ve `frontend/vitest.config.ts`. Pokud to vaše změna způsobí, přidejte testy místo snižování minima.

### Spouštění E2E testů frontendu (Playwright)

Frontend (`frontend/`) používá pro end-to-end testování [Playwright](https://playwright.dev/). Testy pokrývají autentizaci, navigaci, nastavení workspace, CRUD entit (uživatelé, skupiny, týmy), chat a stránky znalostní báze. **Závazné podrobnosti k E2E** jsou v [`frontend/tests/e2e/README.md`](../../../frontend/tests/e2e/README.md); níže je souhrn určený přispěvatelům.

#### Předpoklady

1. Nainstalujte závislosti (zahrnuje `@playwright/test`):
   ```bash
   cd frontend
   npm install
   ```

2. Nainstalujte prohlížeče pro Playwright:
   ```bash
   npx playwright install chromium
   ```

3. Vytvořte ze šablony soubor `.env.test` a vyplňte testovací přihlašovací údaje:
   ```bash
   cp .env.test.example .env.test
   ```

   Povinné proměnné:
   | Proměnná | Popis |
   |----------|-------------|
   | `TEST_USER_EMAIL` | E-mail existujícího uživatele s rolí admin |
   | `TEST_USER_PASSWORD` | Heslo tohoto uživatele |
   | `BASE_URL` | Kde Playwright otevře aplikaci (výchozí v konfiguraci: `http://localhost:3001`) |
   | `NEXT_PUBLIC_API_BASE_URL` | URL backendu pro volání API (seeding/fixtures); není-li nastavena, fixtures použijí `http://localhost:3000` |

#### Spouštění E2E testů

Všechny níže uvedené příkazy se spouštějí z adresáře `frontend/`.

| Příkaz | Popis |
|---------|-------------|
| `npm run test:e2e` | Spustí všechny testy (automaticky spustí dev server) |
| `npm run test:e2e:ui` | Otevře UI Playwrightu pro interaktivní ladění |
| `npm run test:e2e:headed` | Spustí testy ve viditelném prohlížeči |
| `npm run test:e2e:seed` | Naplní hromadná testovací data (30 uživatelů, 30 skupin, 30 týmů) |
| `npm run test:e2e:cleanup` | Smaže všechna naplněná testovací data |
| `npm run test:e2e:users` | Spustí pouze testy týkající se uživatelů |
| `npm run test:e2e:groups` | Spustí pouze testy týkající se skupin |
| `npm run test:e2e:teams` | Spustí pouze testy týkající se týmů |
| `npm run test:e2e:report` | Otevře HTML report testů |
| `npm run test:e2e:coverage` | Spustí všechny testy s pokrytím kódu V8 |
| `npm run test:e2e:coverage-report` | Otevře HTML report pokrytí |

#### Pokrytí kódu

Spuštěním `npm run test:e2e:coverage` shromáždíte pokrytí kódu V8. Reporty se generují v `coverage/e2e/` ve formátech V8, LCOV a souhrnu v konzoli. HTML report otevřete pomocí `npm run test:e2e:coverage-report`.

#### Ladění a podrobný výstup

Chcete-li sledovat průběh testů ve viditelném prohlížeči a zachytit úplné trace (včetně úspěšných testů):

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

| Přepínač | Co dělá |
|------|-------------|
| `--headed` | Otevře viditelné okno prohlížeče místo headless běhu |
| `--trace on` | Zaznamená trace pro každý test (ve výchozím nastavení jen při prvním opakování) |
| `--slow-mo=N` | Přidá pauzu N milisekund mezi jednotlivé akce Playwrightu |
| `--video on` | Nahraje video každého běhu testu |
| `--screenshot on` | Pořídí snímek obrazovky po každém testu (nejen při selhání) |

**Interaktivní režim UI** (doporučeno pro ladění):

```bash
npm run test:e2e:ui
```

Otevře vestavěné UI Playwrightu s živým prohlížečem, časovou osou akcí a snímky DOM, kterými můžete procházet krok za krokem.

**Zobrazení trace a reportů po běhu:**

```bash
# Open the HTML report — click any test to see its trace
npx playwright show-report

# Open a specific trace file directly
npx playwright show-trace test-results/<test-folder>/trace.zip
```

#### Projekty E2E testů

Playwright je nakonfigurován se čtyřmi projekty, které běží v tomto pořadí:

1. **setup** — Přihlásí se přes prohlížeč a uloží stav autentizace do `.auth/user.json`.
2. **seed** — Naplní hromadná data kombinací interakcí v UI a volání API. Závisí na `setup`.
3. **authenticated** — Všechny testy funkcí, které používají uložený stav autentizace. Závisí na `setup`.
4. **unauthenticated** — Testy přihlašovací stránky, které běží bez uložené autentizace.

#### Struktura adresářů E2E

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

#### Psaní nových E2E testů

- **Autentizované testy** patří do složky dané funkce pod `frontend/tests/e2e/` a importují z `@playwright/test`. Automaticky používají uložený stav autentizace.
- **Testy založené na API** (seeding, cleanup) importují z `../fixtures/api-context.fixture` relativně k ostatním specům v `tests/e2e/` (viz `seed/` a `setup/`).
- **Helpery** v `tests/e2e/helpers/` poskytují znovupoužitelné funkce pro běžné interakce s UI (řádky tabulek, stránkování, vyhledávání, formuláře v postranním panelu, zadávání tagů).

Ukázkový test:
```typescript
import { test, expect } from '@playwright/test';

test.describe('My Feature', () => {
  test('loads the page', async ({ page }) => {
    await page.goto('/workspace/my-feature/');
    await expect(page.locator('text="My Feature"')).toBeVisible();
  });
});
```

#### Konvence pro seed data

- Naplnění uživatelé mají tvar `e2e-user-XXXX@e2etest.pipeshub.local`
- Naplněné skupiny se jmenují `E2E Group XXX`
- Naplněné týmy se jmenují `E2E Team XXXX`
- Po bězích se seed daty vždy spusťte `npm run test:e2e:cleanup`, aby se testovací data odstranila

#### Poznámky k E2E v CI

V CI nastavte proměnnou prostředí `CI=true`, čímž zapnete:
- Opakování (2 pokusy na test)
- Jediný worker (sekvenční běh)
- Čerstvý dev server (bez znovupoužití)

#### Artefakty E2E

Během běhů testů se generují následující soubory, které jsou ignorovány gitem:
- `.auth/` — Uložený stav autentizace prohlížeče
- `test-results/` — Testovací artefakty (snímky obrazovky, trace)
- `playwright-report/` — HTML report

## Pravidla komunity

- Buďte ve všech interakcích ohleduplní a inkluzivní
- Poskytujte k pull requestům konstruktivní zpětnou vazbu
- Pomáhejte novým přispěvatelům začít
- Nevhodné chování hlaste správcům projektu

---

Děkujeme, že přispíváte do našeho projektu! Máte-li dotazy nebo potřebujete pomoc, otevřete issue, zeptejte se na [Discordu](https://discord.com/invite/K5RskzJBm2) nebo se obraťte na správce.
