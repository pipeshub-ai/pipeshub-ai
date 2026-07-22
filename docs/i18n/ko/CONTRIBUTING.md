# PipesHub Workplace AI에 기여하기

<div align="center">

**Translations:** [English](../../../CONTRIBUTING.md) · [Français](../fr/CONTRIBUTING.md) · [Deutsch](../de/CONTRIBUTING.md) · [简体中文](../zh-CN/CONTRIBUTING.md) · [日本語](../ja/CONTRIBUTING.md) · [Русский](../ru/CONTRIBUTING.md) · [עברית](../he/CONTRIBUTING.md) · **한국어** · [Español](../es/CONTRIBUTING.md) · [Português](../pt/CONTRIBUTING.md) · [Türkçe](../tr/CONTRIBUTING.md) · [Tiếng Việt](../vi/CONTRIBUTING.md) · [Italiano](../it/CONTRIBUTING.md)

</div>

저희 오픈소스 프로젝트에 오신 것을 환영합니다! 기여에 관심을 가져주셔서 기쁩니다. 이 문서는 기여자로서 시작하는 데 도움이 되는 지침과 안내를 제공합니다.

## 💻 개발자 기여 빌드

## 목차
- 개발 환경 설정
- 프로젝트 아키텍처
- 기여 워크플로
- 코드 스타일 지침
- 테스트
- 문서
- 커뮤니티 지침

## 개발 환경 설정

### 시스템 의존성

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

### 애플리케이션 의존성
```bash
1. **Docker** - Install Docker for your platform
2. **Node.js** - Install Node.js(v22.15.0)
3. **Python 3.12** - Install as shown above
4. **Optional debugging tools:**
   - MongoDB Compass or Studio 3T
   - etcd-manager
```

### 필요한 Docker 컨테이너 시작

**Redis:**
```bash
docker run -d --name redis --restart always -p 6379:6379 redis:bookworm
```

**Qdrant:** (API Key는 .env와 일치해야 합니다)
```bash
docker run -p 6333:6333 -p 6334:6334 -e QDRANT__SERVICE__API_KEY=your_qdrant_secret_api_key qdrant/qdrant:v1.13.6
```

**ETCD 서버:**


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

**ArangoDB:** (비밀번호는 .env와 일치해야 합니다)
```bash
docker run -e ARANGO_ROOT_PASSWORD=your_password -p 8529:8529 --name arango --restart always -d arangodb:3.12.4
```

**Neo4j Desktop (ArangoDB 대신):** PipesHub는 ArangoDB 대신 **Neo4j**를 그래프 데이터베이스(`DATA_STORE=neo4j`)로 사용할 수 있습니다. 로컬 GUI를 선호하고 ArangoDB 컨테이너를 사용하고 싶지 않은 경우에 유용합니다.

1. [Neo4j Desktop](https://neo4j.com/download/)을 설치하고 **로컬 DBMS**를 생성한 뒤 비밀번호를 설정하고 **시작**합니다.
2. 기본 Bolt 리스너를 **localhost:7687**로 둡니다(변경한 경우 Desktop에 표시되는 호스트/포트를 기록해 두세요).
3. Neo4j를 사용할 때는 위의 ArangoDB Docker 컨테이너를 **시작하지 마세요**.
4. `backend/.env`(`backend/nodejs/apps/.env` 및 `backend/python/.env`로 복사하는 템플릿)에서 최소한 다음을 설정합니다:
   ```bash
   DATA_STORE=neo4j
   NEO4J_URI=bolt://localhost:7687
   NEO4J_USERNAME=neo4j
   NEO4J_PASSWORD=<same password as your DBMS>
   NEO4J_DATABASE=neo4j
   ```
   Python 서비스는 시작 시 `DATA_STORE`를 읽어 `dataStoreType`을 KV 스토어(etcd/Redis)에 기록합니다. Node.js API는 이를 상태 점검에 사용하며 `NEO4J_*`를 실제 Neo4j 연결로 취급합니다.
5. 배포 메타데이터의 일관성을 유지하기 위해 나머지 스택보다 먼저 또는 함께 **connectors** Python 서비스(`python -m app.connectors_main`)를 시작하세요. 동일한 etcd 데이터에서 이미 ArangoDB로 부트스트랩한 경우, 그래프 백엔드를 전환하기 전에 etcd 또는 KV 스토어의 배포 키를 재설정하여 상태 불일치를 방지하세요.

ArangoDB 대신 Neo4j를 사용하는 Docker 전체 스택은 `deployment/docker-compose/`의 `docker-compose.build.neo4j.yml`을 참조하세요(저장소 `README.md`에 설명되어 있습니다).

**MongoDB:** (비밀번호는 .env의 MONGO URI와 일치해야 합니다)

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

### Node.js 백엔드 서비스 시작
```bash
cd backend/nodejs/apps
cp ../../env.template .env  # Create .env file from template
npm install
npm run dev
```

### Python 백엔드 서비스 시작
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

### 프런트엔드 설정
```bash
cd frontend
cp env.template .env  # Modify port if Node.js backend uses a different one
npm install
PORT=3001 npm run dev
```

그런 다음 브라우저에서 표시된 URL을 엽니다(`PORT=3001`을 사용할 때는 일반적으로 `http://localhost:3001`이며, `PORT`가 설정되지 않으면 Next.js는 기본적으로 포트 3000을 사용합니다).

## 프로젝트 아키텍처

저희 프로젝트는 세 가지 주요 구성 요소로 이루어져 있습니다:

1. **프런트엔드**: 사용자 인터페이스를 위한 Next.js 애플리케이션
2. **Node.js 백엔드**: API 요청, 인증, 비즈니스 로직을 처리
3. **Python 서비스**: 다음을 위한 세 개의 마이크로서비스:
   - Connectors: 데이터 소스 연결 처리
   - Indexing: 문서 인덱싱 및 처리 관리
   - Query: 검색 및 검색 요청 처리

## 기여 워크플로

1. 저장소를 자신의 GitHub 계정으로 **포크**합니다
2. 포크를 로컬 머신으로 **클론**합니다
3. 기능 또는 버그 수정을 위해 **새 브랜치를 생성**합니다:
   ```bash
   git checkout -b feature/your-feature-name
   ```
4. 저희 코드 스타일 지침에 따라 **변경 사항을 작성**합니다
5. 변경 사항을 철저히 **테스트**합니다
6. 의미 있는 커밋 메시지로 **변경 사항을 커밋**합니다:
   ```bash
   git commit -m "Add feature: brief description of changes"
   ```
7. 브랜치를 자신의 GitHub 포크로 **푸시**합니다:
   ```bash
   git push origin feature/your-feature-name
   ```
8. 저희 메인 저장소를 대상으로 **풀 리퀘스트를 엽니다**
   - 변경 사항에 대한 명확한 설명을 제공합니다
   - 관련된 이슈를 참조합니다
   - 해당하는 경우 스크린샷을 추가합니다

## 코드 스타일 지침

- **Python**: PEP 8 지침을 따릅니다
- **JavaScript/TypeScript**: 저희 프로젝트 구성으로 ESLint를 사용합니다
- **CSS/SCSS**: BEM 명명 규칙을 따릅니다
- **커밋 메시지**: conventional commits 형식을 사용합니다

## 테스트

- 새로운 기능에 대한 단위 테스트를 작성합니다
- PR을 제출하기 전에 모든 테스트가 통과하는지 확인합니다
- 적절한 경우 통합 테스트를 포함합니다
- 복잡한 기능에 대해서는 수동 테스트 단계를 문서화합니다

### Node.js 단위 테스트 실행

테스트는 테스트 러너로 **Mocha**를, 코드 커버리지에 **c8**을 사용합니다. 테스트 파일은 `backend/nodejs/apps/tests/`에 있으며 `*.test.ts` 명명 규칙을 따릅니다. 자세한 내용은 [`backend/nodejs/apps/tests/README.md`](../../../backend/nodejs/apps/tests/README.md)를 참조하세요.

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

### Python 단위 테스트 실행

테스트는 **pytest**를 사용하며 `backend/python/tests/`에 있습니다. 테스트 파일은 `test_*.py` 명명 규칙을 따릅니다. 자세한 내용은 [`backend/python/tests/README.md`](../../../backend/python/tests/README.md)를 참조하세요.

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

### 프런트엔드 E2E 테스트 실행 (Playwright)

프런트엔드(`frontend/`)는 엔드투엔드 테스트에 [Playwright](https://playwright.dev/)를 사용합니다. 테스트는 인증, 내비게이션, 워크스페이스 설정, 엔티티 CRUD(사용자, 그룹, 팀), 채팅 및 지식 베이스 페이지를 다룹니다. **권위 있는 E2E 세부 정보**는 [`frontend/tests/e2e/README.md`](../../../frontend/tests/e2e/README.md)에 있으며, 아래는 기여자 중심의 요약입니다.

#### 전제 조건

1. 의존성을 설치합니다(`@playwright/test` 포함):
   ```bash
   cd frontend
   npm install
   ```

2. Playwright 브라우저를 설치합니다:
   ```bash
   npx playwright install chromium
   ```

3. 템플릿에서 `.env.test` 파일을 만들고 테스트 자격 증명을 입력합니다:
   ```bash
   cp .env.test.example .env.test
   ```

   필수 변수:
   | 변수 | 설명 |
   |----------|-------------|
   | `TEST_USER_EMAIL` | 기존 관리자 사용자의 이메일 |
   | `TEST_USER_PASSWORD` | 해당 사용자의 비밀번호 |
   | `BASE_URL` | Playwright가 앱을 여는 위치(구성 기본값: `http://localhost:3001`) |
   | `NEXT_PUBLIC_API_BASE_URL` | API 호출(시딩/픽스처)을 위한 백엔드 URL. 설정하지 않으면 픽스처에서 기본값은 `http://localhost:3000` |

#### E2E 테스트 실행

아래의 모든 명령은 `frontend/` 디렉터리에서 실행합니다.

| 명령 | 설명 |
|---------|-------------|
| `npm run test:e2e` | 모든 테스트 실행(개발 서버 자동 시작) |
| `npm run test:e2e:ui` | 대화형 디버깅을 위해 Playwright UI 열기 |
| `npm run test:e2e:headed` | 보이는 브라우저에서 테스트 실행 |
| `npm run test:e2e:seed` | 대량 테스트 데이터 시드(사용자 30, 그룹 30, 팀 30) |
| `npm run test:e2e:cleanup` | 시드된 모든 테스트 데이터 삭제 |
| `npm run test:e2e:users` | 사용자 관련 테스트만 실행 |
| `npm run test:e2e:groups` | 그룹 관련 테스트만 실행 |
| `npm run test:e2e:teams` | 팀 관련 테스트만 실행 |
| `npm run test:e2e:report` | HTML 테스트 리포트 열기 |
| `npm run test:e2e:coverage` | V8 코드 커버리지로 모든 테스트 실행 |
| `npm run test:e2e:coverage-report` | 커버리지 HTML 리포트 열기 |

#### 코드 커버리지

`npm run test:e2e:coverage`를 실행하여 V8 코드 커버리지를 수집합니다. 리포트는 V8, LCOV, 콘솔 요약 형식으로 `coverage/e2e/`에 생성됩니다. `npm run test:e2e:coverage-report`로 HTML 리포트를 엽니다.

#### 디버깅 및 상세 출력

보이는 브라우저에서 테스트 실행을 관찰하고 전체 추적(통과한 테스트 포함)을 캡처하려면:

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

| 플래그 | 동작 |
|------|-------------|
| `--headed` | 헤드리스 실행 대신 보이는 브라우저 창을 엽니다 |
| `--trace on` | 모든 테스트에 대해 추적을 기록합니다(기본값은 첫 재시도 시에만 기록) |
| `--slow-mo=N` | 각 Playwright 동작 사이에 N밀리초의 일시 정지를 추가합니다 |
| `--video on` | 모든 테스트 실행의 동영상을 녹화합니다 |
| `--screenshot on` | 모든 테스트 후 스크린샷을 찍습니다(실패 시뿐만 아니라) |

**대화형 UI 모드**(디버깅에 권장):

```bash
npm run test:e2e:ui
```

이렇게 하면 라이브 브라우저, 동작 타임라인, 단계별로 살펴볼 수 있는 DOM 스냅샷이 포함된 Playwright의 내장 UI가 열립니다.

**실행 후 추적 및 리포트 보기:**

```bash
# Open the HTML report — click any test to see its trace
npx playwright show-report

# Open a specific trace file directly
npx playwright show-trace test-results/<test-folder>/trace.zip
```

#### E2E 테스트 프로젝트

Playwright는 순서대로 실행되는 네 개의 프로젝트로 구성됩니다:

1. **setup** — 브라우저를 통해 로그인하고 인증 상태를 `.auth/user.json`에 저장합니다.
2. **seed** — UI 상호작용과 API 호출을 혼합하여 대량 데이터를 시드합니다. `setup`에 의존합니다.
3. **authenticated** — 저장된 인증 상태를 사용하는 모든 기능 테스트. `setup`에 의존합니다.
4. **unauthenticated** — 저장된 인증 없이 실행되는 로그인 페이지 테스트.

#### E2E 디렉터리 구조

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

#### 새 E2E 테스트 작성

- **인증 테스트**는 `frontend/tests/e2e/` 아래의 기능 폴더에 두고 `@playwright/test`에서 가져옵니다. 이들은 저장된 인증 상태를 자동으로 사용합니다.
- **API 기반 테스트**(시딩, 정리)는 `tests/e2e/`의 다른 스펙을 기준으로 한 상대 경로로 `../fixtures/api-context.fixture`에서 가져옵니다(`seed/` 및 `setup/` 참조).
- `tests/e2e/helpers/`의 **헬퍼**는 일반적인 UI 상호작용(테이블 행, 페이지네이션, 검색, 사이드바 폼, 태그 입력)을 위한 재사용 가능한 함수를 제공합니다.

테스트 예시:
```typescript
import { test, expect } from '@playwright/test';

test.describe('My Feature', () => {
  test('loads the page', async ({ page }) => {
    await page.goto('/workspace/my-feature/');
    await expect(page.locator('text="My Feature"')).toBeVisible();
  });
});
```

#### 시드 데이터 규약

- 시드된 사용자는 `e2e-user-XXXX@e2etest.pipeshub.local` 패턴을 따릅니다
- 시드된 그룹은 `E2E Group XXX`로 명명됩니다
- 시드된 팀은 `E2E Team XXXX`로 명명됩니다
- 시드를 사용한 테스트 실행 후에는 항상 `npm run test:e2e:cleanup`을 실행하여 테스트 데이터를 제거하세요

#### E2E CI 참고 사항

CI에서는 환경 변수 `CI=true`를 설정하여 다음을 활성화합니다:
- 재시도(테스트당 2회)
- 단일 워커(순차 실행)
- 새 개발 서버(재사용 안 함)

#### E2E 아티팩트

다음은 테스트 실행 중에 생성되며 gitignore됩니다:
- `.auth/` — 저장된 브라우저 인증 상태
- `test-results/` — 테스트 아티팩트(스크린샷, 추적)
- `playwright-report/` — HTML 리포트

## 문서

- 새로운 기능이나 변경 사항이 있으면 문서를 업데이트하세요
- 적절한 주석과 예제로 API를 문서화하세요
- README 및 기타 가이드를 최신 상태로 유지하세요

## 커뮤니티 지침

- 모든 상호작용에서 존중하고 포용적인 태도를 유지하세요
- 풀 리퀘스트에 건설적인 피드백을 제공하세요
- 새로운 기여자가 시작할 수 있도록 도와주세요
- 부적절한 행동은 프로젝트 유지 관리자에게 신고하세요

---

저희 프로젝트에 기여해 주셔서 감사합니다! 질문이 있거나 도움이 필요하면 이슈를 열거나 유지 관리자에게 연락해 주세요.
