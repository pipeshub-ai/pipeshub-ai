# Участие в разработке PipesHub Workplace AI

<div align="center">

**Translations:** [English](../../../CONTRIBUTING.md) · [Français](../fr/CONTRIBUTING.md) · [Deutsch](../de/CONTRIBUTING.md) · [简体中文](../zh-CN/CONTRIBUTING.md) · [日本語](../ja/CONTRIBUTING.md) · **Русский** · [עברית](../he/CONTRIBUTING.md) · [한국어](../ko/CONTRIBUTING.md) · [Español](../es/CONTRIBUTING.md) · [Português](../pt/CONTRIBUTING.md) · [Türkçe](../tr/CONTRIBUTING.md) · [Tiếng Việt](../vi/CONTRIBUTING.md) · [Italiano](../it/CONTRIBUTING.md)

</div>

Добро пожаловать в наш проект с открытым исходным кодом! Мы рады, что вы заинтересованы во внесении вклада. Этот документ содержит рекомендации и инструкции, которые помогут вам начать работу в качестве контрибьютора.

## 💻 Сборка для вклада разработчиков

## Содержание
- Настройка среды разработки
- Архитектура проекта
- Рабочий процесс внесения вклада
- Рекомендации по стилю кода
- Тестирование
- Документация
- Правила сообщества

## Настройка среды разработки

### Системные зависимости

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

### Зависимости приложения
```bash
1. **Docker** - Install Docker for your platform
2. **Node.js** - Install Node.js(v22.15.0)
3. **Python 3.12** - Install as shown above
4. **Optional debugging tools:**
   - MongoDB Compass or Studio 3T
   - etcd-manager
```

### Запуск необходимых контейнеров Docker

**Redis:**
```bash
docker run -d --name redis --restart always -p 6379:6379 redis:bookworm
```

**Qdrant:** (API Key должен совпадать с .env)
```bash
docker run -p 6333:6333 -p 6334:6334 -e QDRANT__SERVICE__API_KEY=your_qdrant_secret_api_key qdrant/qdrant:v1.13.6
```

**Сервер ETCD:**


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

**ArangoDB:** (пароль должен совпадать с .env)
```bash
docker run -e ARANGO_ROOT_PASSWORD=your_password -p 8529:8529 --name arango --restart always -d arangodb:3.12.4
```

**Neo4j Desktop (вместо ArangoDB):** PipesHub может использовать **Neo4j** в качестве графовой базы данных (`DATA_STORE=neo4j`) вместо ArangoDB. Это удобно, если вы предпочитаете локальный графический интерфейс и не хотите использовать контейнер ArangoDB.

1. Установите [Neo4j Desktop](https://neo4j.com/download/), создайте **локальную СУБД (DBMS)**, задайте её пароль и **запустите** её.
2. Оставьте Bolt-слушатель по умолчанию на **localhost:7687** (или запишите хост/порт, показанные в Desktop, если вы их изменили).
3. **Не** запускайте указанный выше контейнер Docker с ArangoDB при использовании Neo4j.
4. В `backend/.env` (шаблон, который вы копируете в `backend/nodejs/apps/.env` и `backend/python/.env`) задайте как минимум:
   ```bash
   DATA_STORE=neo4j
   NEO4J_URI=bolt://localhost:7687
   NEO4J_USERNAME=neo4j
   NEO4J_PASSWORD=<same password as your DBMS>
   NEO4J_DATABASE=neo4j
   ```
   Python-сервисы читают `DATA_STORE` и при запуске записывают `dataStoreType` в KV-хранилище (etcd/Redis); Node.js API использует это для проверок работоспособности и рассматривает `NEO4J_*` как активное подключение к Neo4j.
5. Запустите Python-сервис **connectors** (`python -m app.connectors_main`) до или вместе с остальной частью стека, чтобы метаданные развёртывания оставались согласованными. Если вы уже выполнили инициализацию против ArangoDB на тех же данных etcd, сбросьте etcd или ключ развёртывания в KV-хранилище перед сменой графового бэкенда, чтобы избежать несогласованного состояния.

Полный стек в Docker с Neo4j вместо ArangoDB см. в `docker-compose.build.neo4j.yml` в `deployment/docker-compose/` (описано в `README.md` репозитория).

**MongoDB:** (пароль должен совпадать с MONGO URI в .env)

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

### Запуск Node.js-бэкенд-сервиса
```bash
cd backend/nodejs/apps
cp ../../env.template .env  # Create .env file from template
npm install
npm run dev
```

### Запуск Python-бэкенд-сервисов
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

### Настройка фронтенда
```bash
cd frontend
cp env.template .env  # Modify port if Node.js backend uses a different one
npm install
PORT=3001 npm run dev
```

Затем откройте в браузере показанный URL (обычно `http://localhost:3001` при использовании `PORT=3001`; если `PORT` не задан, Next.js по умолчанию использует порт 3000).

## Архитектура проекта

Наш проект состоит из трёх основных компонентов:

1. **Фронтенд**: приложение Next.js для пользовательского интерфейса
2. **Бэкенд Node.js**: обрабатывает запросы к API, аутентификацию и бизнес-логику
3. **Сервисы Python**: три микросервиса для:
   - Connectors: обрабатывает подключения к источникам данных
   - Indexing: управляет индексацией и обработкой документов
   - Query: обрабатывает запросы поиска и извлечения

## Рабочий процесс внесения вклада

1. **Сделайте форк репозитория** в свой аккаунт GitHub
2. **Клонируйте свой форк** на локальную машину
3. **Создайте новую ветку** для вашей функции или исправления ошибки:
   ```bash
   git checkout -b feature/your-feature-name
   ```
4. **Внесите изменения**, следуя нашим рекомендациям по стилю кода
5. Тщательно **протестируйте изменения**
6. **Зафиксируйте изменения** осмысленными сообщениями коммитов:
   ```bash
   git commit -m "Add feature: brief description of changes"
   ```
7. **Запушьте свою ветку** в свой форк на GitHub:
   ```bash
   git push origin feature/your-feature-name
   ```
8. **Откройте Pull Request** в наш основной репозиторий
   - Предоставьте чёткое описание изменений
   - Сошлитесь на связанные issue
   - При необходимости добавьте скриншоты

## Рекомендации по стилю кода

- **Python**: следуйте рекомендациям PEP 8
- **JavaScript/TypeScript**: используйте ESLint с нашей конфигурацией проекта
- **CSS/SCSS**: следуйте соглашению об именовании BEM
- **Сообщения коммитов**: используйте формат conventional commits

## Тестирование

- Пишите модульные тесты для новых функций
- Перед отправкой PR убедитесь, что все тесты проходят
- Включайте интеграционные тесты там, где это уместно
- Документируйте шаги ручного тестирования для сложных функций

### Запуск модульных тестов Node.js

Тесты используют **Mocha** в качестве средства запуска тестов и **c8** для покрытия кода. Файлы тестов находятся в `backend/nodejs/apps/tests/` и следуют соглашению об именовании `*.test.ts`. Полные сведения см. в [`backend/nodejs/apps/tests/README.md`](../../../backend/nodejs/apps/tests/README.md).

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

### Запуск модульных тестов Python

Тесты используют **pytest** и находятся в `backend/python/tests/`. Файлы тестов следуют соглашению об именовании `test_*.py`. Полные сведения см. в [`backend/python/tests/README.md`](../../../backend/python/tests/README.md).

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

### Запуск E2E-тестов фронтенда (Playwright)

Фронтенд (`frontend/`) использует [Playwright](https://playwright.dev/) для сквозного (end-to-end) тестирования. Тесты охватывают аутентификацию, навигацию, настройки рабочего пространства, CRUD сущностей (пользователи, группы, команды), чат и страницы базы знаний. **Авторитетные сведения по E2E** находятся в [`frontend/tests/e2e/README.md`](../../../frontend/tests/e2e/README.md); далее приведена краткая сводка для контрибьюторов.

#### Предварительные требования

1. Установите зависимости (включая `@playwright/test`):
   ```bash
   cd frontend
   npm install
   ```

2. Установите браузеры Playwright:
   ```bash
   npx playwright install chromium
   ```

3. Создайте файл `.env.test` из шаблона и заполните тестовые учётные данные:
   ```bash
   cp .env.test.example .env.test
   ```

   Обязательные переменные:
   | Переменная | Описание |
   |----------|-------------|
   | `TEST_USER_EMAIL` | Email существующего пользователя-администратора |
   | `TEST_USER_PASSWORD` | Пароль этого пользователя |
   | `BASE_URL` | Где Playwright открывает приложение (по умолчанию в конфигурации: `http://localhost:3001`) |
   | `NEXT_PUBLIC_API_BASE_URL` | URL бэкенда для вызовов API (сидинг/фикстуры); по умолчанию `http://localhost:3000` в фикстурах, если не задано |

#### Запуск E2E-тестов

Все приведённые ниже команды выполняются из каталога `frontend/`.

| Команда | Описание |
|---------|-------------|
| `npm run test:e2e` | Запускает все тесты (автоматически запускает dev-сервер) |
| `npm run test:e2e:ui` | Открывает интерфейс Playwright для интерактивной отладки |
| `npm run test:e2e:headed` | Запускает тесты в видимом браузере |
| `npm run test:e2e:seed` | Создаёт массовые тестовые данные (30 пользователей, 30 групп, 30 команд) |
| `npm run test:e2e:cleanup` | Удаляет все созданные тестовые данные |
| `npm run test:e2e:users` | Запускает только тесты, связанные с пользователями |
| `npm run test:e2e:groups` | Запускает только тесты, связанные с группами |
| `npm run test:e2e:teams` | Запускает только тесты, связанные с командами |
| `npm run test:e2e:report` | Открывает HTML-отчёт о тестах |
| `npm run test:e2e:coverage` | Запускает все тесты с покрытием кода V8 |
| `npm run test:e2e:coverage-report` | Открывает HTML-отчёт о покрытии |

#### Покрытие кода

Запустите `npm run test:e2e:coverage`, чтобы собрать покрытие кода V8. Отчёты создаются в `coverage/e2e/` в форматах V8, LCOV и сводки в консоли. Откройте HTML-отчёт с помощью `npm run test:e2e:coverage-report`.

#### Отладка и подробный вывод

Чтобы наблюдать за выполнением тестов в видимом браузере и фиксировать полные трассировки (включая пройденные тесты):

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

| Флаг | Что он делает |
|------|-------------|
| `--headed` | Открывает видимое окно браузера вместо запуска без интерфейса |
| `--trace on` | Записывает трассировку для каждого теста (по умолчанию только при первом повторе) |
| `--slow-mo=N` | Добавляет паузу в N миллисекунд между каждым действием Playwright |
| `--video on` | Записывает видео каждого запуска теста |
| `--screenshot on` | Делает скриншот после каждого теста (не только при сбоях) |

**Интерактивный режим UI** (рекомендуется для отладки):

```bash
npm run test:e2e:ui
```

Это откроет встроенный интерфейс Playwright с живым браузером, временной шкалой действий и снимками DOM, по которым можно перемещаться по шагам.

**Просмотр трассировок и отчётов после запуска:**

```bash
# Open the HTML report — click any test to see its trace
npx playwright show-report

# Open a specific trace file directly
npx playwright show-trace test-results/<test-folder>/trace.zip
```

#### Проекты E2E-тестов

Playwright настроен на четыре проекта, выполняемых по порядку:

1. **setup** — выполняет вход через браузер и сохраняет состояние аутентификации в `.auth/user.json`.
2. **seed** — создаёт массовые данные, используя сочетание взаимодействий с UI и вызовов API. Зависит от `setup`.
3. **authenticated** — все функциональные тесты, использующие сохранённое состояние аутентификации. Зависит от `setup`.
4. **unauthenticated** — тесты страницы входа, выполняемые без сохранённой аутентификации.

#### Структура каталогов E2E

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

#### Написание новых E2E-тестов

- **Аутентифицированные тесты** размещаются в папке функции внутри `frontend/tests/e2e/` и импортируются из `@playwright/test`. Они автоматически используют сохранённое состояние аутентификации.
- **Тесты на основе API** (сидинг, очистка) импортируются из `../fixtures/api-context.fixture` относительно других спецификаций в `tests/e2e/` (см. `seed/` и `setup/`).
- **Хелперы** в `tests/e2e/helpers/` предоставляют повторно используемые функции для распространённых взаимодействий с UI (строки таблиц, пагинация, поиск, формы боковой панели, ввод тегов).

Пример теста:
```typescript
import { test, expect } from '@playwright/test';

test.describe('My Feature', () => {
  test('loads the page', async ({ page }) => {
    await page.goto('/workspace/my-feature/');
    await expect(page.locator('text="My Feature"')).toBeVisible();
  });
});
```

#### Соглашения о тестовых данных (seed)

- Создаваемые пользователи следуют шаблону `e2e-user-XXXX@e2etest.pipeshub.local`
- Создаваемые группы именуются `E2E Group XXX`
- Создаваемые команды именуются `E2E Team XXXX`
- Всегда запускайте `npm run test:e2e:cleanup` после прогонов с созданными данными, чтобы удалить тестовые данные

#### Замечания по E2E в CI

В CI задайте переменную окружения `CI=true`, чтобы включить:
- Повторы (2 попытки на тест)
- Один воркер (последовательное выполнение)
- Свежий dev-сервер (без повторного использования)

#### Артефакты E2E

Следующее создаётся во время прогонов тестов и исключено через gitignore:
- `.auth/` — сохранённое состояние аутентификации браузера
- `test-results/` — артефакты тестов (скриншоты, трассировки)
- `playwright-report/` — HTML-отчёт

## Документация

- Обновляйте документацию при любых новых функциях или изменениях
- Документируйте API соответствующими комментариями и примерами
- Поддерживайте README и другие руководства в актуальном состоянии

## Правила сообщества

- Будьте уважительны и инклюзивны во всех взаимодействиях
- Давайте конструктивную обратную связь по pull request
- Помогайте новым контрибьюторам начать работу
- Сообщайте о любом неподобающем поведении сопровождающим проекта

---

Спасибо за вклад в наш проект! Если у вас есть вопросы или нужна помощь, откройте issue или свяжитесь с сопровождающими.
