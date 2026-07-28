# תרומה ל-PipesHub Workplace AI

<div align="center">

**Translations:** [English](../../../CONTRIBUTING.md) · [Français](../fr/CONTRIBUTING.md) · [Deutsch](../de/CONTRIBUTING.md) · [简体中文](../zh-CN/CONTRIBUTING.md) · [日本語](../ja/CONTRIBUTING.md) · [Русский](../ru/CONTRIBUTING.md) · **עברית** · [한국어](../ko/CONTRIBUTING.md) · [Español](../es/CONTRIBUTING.md) · [Português](../pt/CONTRIBUTING.md) · [Türkçe](../tr/CONTRIBUTING.md) · [Tiếng Việt](../vi/CONTRIBUTING.md) · [Italiano](../it/CONTRIBUTING.md)

</div>

<div dir="rtl">

ברוכים הבאים לפרויקט הקוד הפתוח שלנו! אנו שמחים שאתם מתעניינים בתרומה. מסמך זה מספק הנחיות והוראות שיעזרו לכם להתחיל כתורמים.

## 💻 בניית תרומה למפתחים

## תוכן העניינים
- הגדרת סביבת הפיתוח
- ארכיטקטורת הפרויקט
- זרימת עבודה של תרומה
- הנחיות סגנון קוד
- בדיקות
- תיעוד
- הנחיות קהילה

## הגדרת סביבת הפיתוח

### תלויות מערכת

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

### תלויות יישום
```bash
1. **Docker** - Install Docker for your platform
2. **Node.js** - Install Node.js(v22.15.0)
3. **Python 3.12** - Install as shown above
4. **Optional debugging tools:**
   - MongoDB Compass or Studio 3T
   - etcd-manager
```

### הפעלת קונטיינרים נדרשים של Docker

**Redis:**
```bash
docker run -d --name redis --restart always -p 6379:6379 redis:bookworm
```

**Qdrant:** (ה-API Key חייב להתאים ל-.env)
```bash
docker run -p 6333:6333 -p 6334:6334 -e QDRANT__SERVICE__API_KEY=your_qdrant_secret_api_key qdrant/qdrant:v1.13.6
```

**שרת ETCD:**


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

**ArangoDB:** (הסיסמה חייבת להתאים ל-.env)
```bash
docker run -e ARANGO_ROOT_PASSWORD=your_password -p 8529:8529 --name arango --restart always -d arangodb:3.12.4
```

**Neo4j Desktop (במקום ArangoDB):** PipesHub יכול להשתמש ב-**Neo4j** כמסד נתונים גרפי (`DATA_STORE=neo4j`) במקום ב-ArangoDB. הדבר שימושי אם אתם מעדיפים GUI מקומי ואינכם רוצים את קונטיינר ArangoDB.

1. התקינו את [Neo4j Desktop](https://neo4j.com/download/), צרו **DBMS מקומי**, הגדירו את הסיסמה שלו, ו**הפעילו** אותו.
2. השאירו את מאזין ה-Bolt המוגדר כברירת מחדל על **localhost:7687** (או רשמו את המארח/הפורט המוצגים ב-Desktop אם שיניתם אותם).
3. **אל** תפעילו את קונטיינר ה-ArangoDB Docker שלמעלה בעת שימוש ב-Neo4j.
4. ב-`backend/.env` (התבנית שאתם מעתיקים אל `backend/nodejs/apps/.env` ו-`backend/python/.env`), הגדירו לכל הפחות:
   ```bash
   DATA_STORE=neo4j
   NEO4J_URI=bolt://localhost:7687
   NEO4J_USERNAME=neo4j
   NEO4J_PASSWORD=<same password as your DBMS>
   NEO4J_DATABASE=neo4j
   ```
   שירותי Python קוראים את `DATA_STORE` וכותבים את `dataStoreType` אל מאגר ה-KV (etcd/Redis) בעת ההפעלה; ה-Node.js API משתמש בכך לבדיקות תקינות ומתייחס אל `NEO4J_*` כחיבור ה-Neo4j הפעיל.
5. הפעילו את שירות ה-Python‏ **connectors** (`python -m app.connectors_main`) לפני שאר הערימה או יחד איתה כדי שמטא-נתוני הפריסה יישארו עקביים. אם כבר ביצעתם אתחול (bootstrap) מול ArangoDB על אותם נתוני etcd, אפסו את etcd או את מפתח הפריסה במאגר ה-KV לפני החלפת ה-backend הגרפי כדי להימנע ממצב לא תואם.

לערימה מלאה ב-Docker עם Neo4j במקום ArangoDB, ראו את `docker-compose.build.neo4j.yml` בתוך `deployment/docker-compose/` (מתועד ב-`README.md` של המאגר).

**MongoDB:** (הסיסמה חייבת להתאים ל-MONGO URI ב-.env)

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

### הפעלת שירות ה-Backend של Node.js
```bash
cd backend/nodejs/apps
cp ../../env.template .env  # Create .env file from template
npm install
npm run dev
```

### הפעלת שירותי ה-Backend של Python
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

### הגדרת ה-Frontend
```bash
cd frontend
cp env.template .env  # Modify port if Node.js backend uses a different one
npm install
PORT=3001 npm run dev
```

לאחר מכן פתחו בדפדפן את כתובת ה-URL המוצגת (בדרך כלל `http://localhost:3001` בעת שימוש ב-`PORT=3001`; אם `PORT` אינו מוגדר, Next.js משתמש בפורט 3000 כברירת מחדל).

## ארכיטקטורת הפרויקט

הפרויקט שלנו מורכב משלושה רכיבים עיקריים:

1. **Frontend**: יישום Next.js לממשק המשתמש
2. **Backend של Node.js**: מטפל בבקשות API, אימות ולוגיקה עסקית
3. **שירותי Python**: שלושה מיקרו-שירותים עבור:
   - Connectors: מטפל בחיבורים למקורות נתונים
   - Indexing: מנהל אינדוקס ועיבוד מסמכים
   - Query: מעבד בקשות חיפוש ואחזור

## זרימת עבודה של תרומה

1. בצעו **fork למאגר** אל חשבון ה-GitHub שלכם
2. בצעו **clone של ה-fork שלכם** למחשב המקומי
3. **צרו ענף חדש** עבור התכונה או תיקון הבאג שלכם:
   ```bash
   git checkout -b feature/your-feature-name
   ```
4. **בצעו את השינויים שלכם** תוך הקפדה על הנחיות סגנון הקוד שלנו
5. **בדקו את השינויים שלכם** ביסודיות
6. **בצעו commit לשינויים שלכם** עם הודעות commit בעלות משמעות:
   ```bash
   git commit -m "Add feature: brief description of changes"
   ```
7. בצעו **push לענף שלכם** אל ה-fork שלכם ב-GitHub:
   ```bash
   git push origin feature/your-feature-name
   ```
8. **פתחו Pull Request** מול המאגר הראשי שלנו
   - ספקו תיאור ברור של השינויים
   - הפנו אל issues רלוונטיים
   - הוסיפו צילומי מסך אם רלוונטי

## הנחיות סגנון קוד

- **Python**: עקבו אחר הנחיות PEP 8
- **JavaScript/TypeScript**: השתמשו ב-ESLint עם תצורת הפרויקט שלנו
- **CSS/SCSS**: עקבו אחר מוסכמת השמות BEM
- **הודעות Commit**: השתמשו בפורמט conventional commits

## בדיקות

- כתבו בדיקות יחידה לתכונות חדשות
- ודאו שכל הבדיקות עוברות לפני שליחת PR
- כללו בדיקות אינטגרציה במקומות המתאימים
- תעדו שלבי בדיקה ידנית עבור תכונות מורכבות

### הרצת בדיקות יחידה של Node.js

הבדיקות משתמשות ב-**Mocha** כמריץ הבדיקות וב-**c8** לכיסוי קוד. קובצי הבדיקה נמצאים ב-`backend/nodejs/apps/tests/` ועוקבים אחר מוסכמת השמות `*.test.ts`. לפרטים מלאים ראו את [`backend/nodejs/apps/tests/README.md`](../../../backend/nodejs/apps/tests/README.md).

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

### הרצת בדיקות יחידה של Python

הבדיקות משתמשות ב-**pytest** ונמצאות ב-`backend/python/tests/`. קובצי הבדיקה עוקבים אחר מוסכמת השמות `test_*.py`. לפרטים מלאים ראו את [`backend/python/tests/README.md`](../../../backend/python/tests/README.md).

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

### הרצת בדיקות E2E של ה-Frontend (Playwright)

ה-Frontend (`frontend/`) משתמש ב-[Playwright](https://playwright.dev/) לבדיקות מקצה לקצה. הבדיקות מכסות אימות, ניווט, הגדרות סביבת עבודה, CRUD של ישויות (משתמשים, קבוצות, צוותים), צ'אט ועמודי בסיס ידע. **פרטי ה-E2E המוסמכים** נמצאים ב-[`frontend/tests/e2e/README.md`](../../../frontend/tests/e2e/README.md); להלן סיכום ממוקד-תורמים.

#### דרישות מקדימות

1. התקינו את התלויות (כולל `@playwright/test`):
   ```bash
   cd frontend
   npm install
   ```

2. התקינו את דפדפני Playwright:
   ```bash
   npx playwright install chromium
   ```

3. צרו קובץ `.env.test` מהתבנית ומלאו את פרטי ההתחברות לבדיקה:
   ```bash
   cp .env.test.example .env.test
   ```

   משתנים נדרשים:
   | משתנה | תיאור |
   |----------|-------------|
   | `TEST_USER_EMAIL` | כתובת הדוא"ל של משתמש מנהל קיים |
   | `TEST_USER_PASSWORD` | הסיסמה של אותו משתמש |
   | `BASE_URL` | היכן Playwright פותח את האפליקציה (ברירת מחדל בתצורה: `http://localhost:3001`) |
   | `NEXT_PUBLIC_API_BASE_URL` | כתובת URL של ה-Backend לקריאות API (seeding/fixtures); ברירת המחדל היא `http://localhost:3000` ב-fixtures אם לא הוגדרה |

#### הרצת בדיקות E2E

כל הפקודות שלהלן מורצות מתוך הספרייה `frontend/`.

| פקודה | תיאור |
|---------|-------------|
| `npm run test:e2e` | מריץ את כל הבדיקות (מפעיל את שרת הפיתוח אוטומטית) |
| `npm run test:e2e:ui` | פותח את ממשק Playwright לניפוי באגים אינטראקטיבי |
| `npm run test:e2e:headed` | מריץ את הבדיקות בדפדפן גלוי |
| `npm run test:e2e:seed` | יוצר נתוני בדיקה בכמות גדולה (30 משתמשים, 30 קבוצות, 30 צוותים) |
| `npm run test:e2e:cleanup` | מוחק את כל נתוני הבדיקה שנוצרו |
| `npm run test:e2e:users` | מריץ רק את הבדיקות הקשורות למשתמשים |
| `npm run test:e2e:groups` | מריץ רק את הבדיקות הקשורות לקבוצות |
| `npm run test:e2e:teams` | מריץ רק את הבדיקות הקשורות לצוותים |
| `npm run test:e2e:report` | פותח את דוח הבדיקות בפורמט HTML |
| `npm run test:e2e:coverage` | מריץ את כל הבדיקות עם כיסוי קוד V8 |
| `npm run test:e2e:coverage-report` | פותח את דוח הכיסוי בפורמט HTML |

#### כיסוי קוד

הריצו `npm run test:e2e:coverage` כדי לאסוף כיסוי קוד V8. הדוחות נוצרים ב-`coverage/e2e/` בפורמטים V8,‏ LCOV וסיכום מסוף. פתחו את דוח ה-HTML באמצעות `npm run test:e2e:coverage-report`.

#### ניפוי באגים ופלט מפורט

כדי לצפות בהרצת הבדיקות בדפדפן גלוי וללכוד מעקבים (traces) מלאים (כולל בדיקות שעברו):

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

| דגל (Flag) | מה הוא עושה |
|------|-------------|
| `--headed` | פותח חלון דפדפן גלוי במקום להריץ ללא ממשק (headless) |
| `--trace on` | מקליט מעקב עבור כל בדיקה (כברירת מחדל מקליט רק בניסיון החוזר הראשון) |
| `--slow-mo=N` | מוסיף השהיה של N מילי-שניות בין כל פעולה של Playwright |
| `--video on` | מקליט וידאו של כל הרצת בדיקה |
| `--screenshot on` | מצלם צילום מסך לאחר כל בדיקה (לא רק בכישלונות) |

**מצב ממשק אינטראקטיבי** (מומלץ לניפוי באגים):

```bash
npm run test:e2e:ui
```

זה פותח את הממשק המובנה של Playwright עם דפדפן חי, ציר זמן של פעולות, ותמונות מצב של ה-DOM שניתן לעבור בהן שלב אחר שלב.

**צפייה במעקבים ובדוחות לאחר הרצה:**

```bash
# Open the HTML report — click any test to see its trace
npx playwright show-report

# Open a specific trace file directly
npx playwright show-trace test-results/<test-folder>/trace.zip
```

#### פרויקטי בדיקות E2E

Playwright מוגדר עם ארבעה פרויקטים שמורצים לפי הסדר:

1. **setup** — מתחבר דרך הדפדפן ושומר את מצב האימות ב-`.auth/user.json`.
2. **seed** — יוצר נתונים בכמות גדולה באמצעות שילוב של אינטראקציות ממשק וקריאות API. תלוי ב-`setup`.
3. **authenticated** — כל בדיקות התכונה שמשתמשות במצב האימות השמור. תלוי ב-`setup`.
4. **unauthenticated** — בדיקות עמוד ההתחברות שרצות ללא אימות שמור.

#### מבנה ספריות E2E

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

#### כתיבת בדיקות E2E חדשות

- **בדיקות מאומתות** ממוקמות בתיקיית תכונה תחת `frontend/tests/e2e/` ומייבאות מ-`@playwright/test`. הן משתמשות אוטומטית במצב האימות השמור.
- **בדיקות מבוססות API** (seeding,‏ cleanup) מייבאות מ-`../fixtures/api-context.fixture` באופן יחסי למפרטים אחרים ב-`tests/e2e/` (ראו `seed/` ו-`setup/`).
- ה-**helpers** ב-`tests/e2e/helpers/` מספקים פונקציות לשימוש חוזר עבור אינטראקציות ממשק נפוצות (שורות טבלה, עימוד, חיפוש, טפסי סרגל צד, קלט תגיות).

בדיקה לדוגמה:
```typescript
import { test, expect } from '@playwright/test';

test.describe('My Feature', () => {
  test('loads the page', async ({ page }) => {
    await page.goto('/workspace/my-feature/');
    await expect(page.locator('text="My Feature"')).toBeVisible();
  });
});
```

#### מוסכמות נתוני Seed

- המשתמשים שנוצרים עוקבים אחר התבנית `e2e-user-XXXX@e2etest.pipeshub.local`
- הקבוצות שנוצרות נקראות `E2E Group XXX`
- הצוותים שנוצרים נקראים `E2E Team XXXX`
- הריצו תמיד `npm run test:e2e:cleanup` לאחר הרצות עם נתונים שנוצרו כדי להסיר את נתוני הבדיקה

#### הערות CI עבור E2E

ב-CI, הגדירו את משתנה הסביבה `CI=true` כדי להפעיל:
- ניסיונות חוזרים (2 ניסיונות לכל בדיקה)
- worker יחיד (הרצה רציפה)
- שרת פיתוח חדש (ללא שימוש חוזר)

#### תוצרי (Artifacts) E2E

הפריטים הבאים נוצרים במהלך הרצות הבדיקה ומוחרגים באמצעות gitignore:
- `.auth/` — מצב אימות הדפדפן השמור
- `test-results/` — תוצרי בדיקה (צילומי מסך, מעקבים)
- `playwright-report/` — דוח HTML

## תיעוד

- עדכנו את התיעוד עבור כל תכונה או שינוי חדשים
- תעדו את ממשקי ה-API עם הערות ודוגמאות מתאימות
- שמרו על README ועל מדריכים אחרים מעודכנים

## הנחיות קהילה

- היו מכבדים ומכילים בכל האינטראקציות
- ספקו משוב בונה על pull requests
- עזרו לתורמים חדשים להתחיל
- דווחו על כל התנהגות לא הולמת למתחזקי הפרויקט

---

תודה שתרמתם לפרויקט שלנו! אם יש לכם שאלות או שאתם זקוקים לעזרה, אנא פתחו issue או פנו אל המתחזקים.

</div>
