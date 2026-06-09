# 为 PipesHub Workplace AI 做贡献

<div align="center">

**Translations:** [English](../../../CONTRIBUTING.md) · [Français](../fr/CONTRIBUTING.md) · [Deutsch](../de/CONTRIBUTING.md) · **简体中文** · [日本語](../ja/CONTRIBUTING.md) · [Русский](../ru/CONTRIBUTING.md) · [עברית](../he/CONTRIBUTING.md) · [한국어](../ko/CONTRIBUTING.md) · [Español](../es/CONTRIBUTING.md) · [Português](../pt/CONTRIBUTING.md) · [Türkçe](../tr/CONTRIBUTING.md) · [Tiếng Việt](../vi/CONTRIBUTING.md) · [Italiano](../it/CONTRIBUTING.md)

</div>

欢迎来到我们的开源项目！我们很高兴你有兴趣参与贡献。本文档提供了帮助你作为贡献者起步的指南和说明。

## 💻 开发者贡献构建

## 目录
- 搭建开发环境
- 项目架构
- 贡献工作流
- 代码风格指南
- 测试
- 文档
- 社区准则

## 搭建开发环境

### 系统依赖

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

### 应用依赖
```bash
1. **Docker** - Install Docker for your platform
2. **Node.js** - Install Node.js(v22.15.0)
3. **Python 3.12** - Install as shown above
4. **Optional debugging tools:**
   - MongoDB Compass or Studio 3T
   - etcd-manager
```

### 启动所需的 Docker 容器

**Redis：**
```bash
docker run -d --name redis --restart always -p 6379:6379 redis:bookworm
```

**Qdrant：**（API Key 必须与 .env 一致）
```bash
docker run -p 6333:6333 -p 6334:6334 -e QDRANT__SERVICE__API_KEY=your_qdrant_secret_api_key qdrant/qdrant:v1.13.6
```

**ETCD 服务器：**


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

**ArangoDB：**（密码必须与 .env 一致）
```bash
docker run -e ARANGO_ROOT_PASSWORD=your_password -p 8529:8529 --name arango --restart always -d arangodb:3.12.4
```

**Neo4j Desktop（作为 ArangoDB 的替代）：** PipesHub 可以使用 **Neo4j** 作为图数据库（`DATA_STORE=neo4j`）来替代 ArangoDB。如果你更喜欢本地 GUI 并且不想运行 ArangoDB 容器，这会很有用。

1. 安装 [Neo4j Desktop](https://neo4j.com/download/)，创建一个**本地 DBMS**，设置其密码，并**启动**它。
2. 保持默认的 Bolt 监听器在 **localhost:7687**（如果你做了更改，请记下 Desktop 中显示的主机/端口）。
3. 使用 Neo4j 时，**不要**启动上面的 ArangoDB Docker 容器。
4. 在 `backend/.env`（你复制到 `backend/nodejs/apps/.env` 和 `backend/python/.env` 的模板）中，至少设置：
   ```bash
   DATA_STORE=neo4j
   NEO4J_URI=bolt://localhost:7687
   NEO4J_USERNAME=neo4j
   NEO4J_PASSWORD=<same password as your DBMS>
   NEO4J_DATABASE=neo4j
   ```
   Python 服务会读取 `DATA_STORE`，并在启动时将 `dataStoreType` 写入 KV 存储（etcd/Redis）；Node.js API 会据此进行健康检查，并将 `NEO4J_*` 视为实时的 Neo4j 连接。
5. 在其余技术栈之前或同时启动 **connectors** Python 服务（`python -m app.connectors_main`），以保持部署元数据的一致性。如果你已经在相同的 etcd 数据上针对 ArangoDB 进行了初始化，请在切换图后端之前重置 etcd 或 KV 存储中的部署键，以避免状态不匹配。

如需在 Docker 中运行使用 Neo4j（而非 ArangoDB）的完整技术栈，请参阅 `deployment/docker-compose/` 中的 `docker-compose.build.neo4j.yml`（在仓库的 `README.md` 中有说明）。

**MongoDB：**（密码必须与 .env 的 MONGO URI 一致）

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

**Zookeeper：**

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


**Apache Kafka：**

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

### 启动 Node.js 后端服务
```bash
cd backend/nodejs/apps
cp ../../env.template .env  # Create .env file from template
npm install
npm run dev
```

### 启动 Python 后端服务
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

### 设置前端
```bash
cd frontend
cp env.template .env  # Modify port if Node.js backend uses a different one
npm install
PORT=3001 npm run dev
```

然后在浏览器中打开显示的 URL（使用 `PORT=3001` 时通常为 `http://localhost:3001`；如果未设置 `PORT`，Next.js 默认使用 3000 端口）。

## 项目架构

我们的项目由三个主要组件构成：

1. **前端（Frontend）**：用于用户界面的 Next.js 应用
2. **Node.js 后端**：处理 API 请求、身份验证和业务逻辑
3. **Python 服务**：三个微服务，分别用于：
   - Connectors（连接器）：处理数据源连接
   - Indexing（索引）：管理文档索引与处理
   - Query（查询）：处理搜索与检索请求

## 贡献工作流

1. **Fork 仓库** 到你的 GitHub 账户
2. **克隆你的 fork** 到本地机器
3. 为你的功能或缺陷修复**创建一个新分支**：
   ```bash
   git checkout -b feature/your-feature-name
   ```
4. 按照我们的代码风格指南**进行更改**
5. 充分**测试你的更改**
6. 使用有意义的提交信息**提交你的更改**：
   ```bash
   git commit -m "Add feature: brief description of changes"
   ```
7. 将你的分支**推送**到你的 GitHub fork：
   ```bash
   git push origin feature/your-feature-name
   ```
8. 针对我们的主仓库**发起 Pull Request**
   - 提供清晰的更改说明
   - 引用任何相关的 issue
   - 如适用，添加截图

## 代码风格指南

- **Python**：遵循 PEP 8 指南
- **JavaScript/TypeScript**：使用 ESLint 并采用我们的项目配置
- **CSS/SCSS**：遵循 BEM 命名约定
- **提交信息**：使用 conventional commits 格式

## 测试

- 为新功能编写单元测试
- 在提交 PR 前确保所有测试通过
- 在适当之处包含集成测试
- 为复杂功能记录手动测试步骤

### 运行 Node.js 单元测试

测试以 **Mocha** 作为测试运行器，并使用 **c8** 进行代码覆盖率统计。测试文件位于 `backend/nodejs/apps/tests/`，遵循 `*.test.ts` 命名约定。完整细节参见 [`backend/nodejs/apps/tests/README.md`](../../../backend/nodejs/apps/tests/README.md)。

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

### 运行 Python 单元测试

测试使用 **pytest**，位于 `backend/python/tests/`。测试文件遵循 `test_*.py` 命名约定。完整细节参见 [`backend/python/tests/README.md`](../../../backend/python/tests/README.md)。

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

### 运行前端 E2E 测试（Playwright）

前端（`frontend/`）使用 [Playwright](https://playwright.dev/) 进行端到端测试。测试覆盖身份验证、导航、工作区设置、实体 CRUD（用户、组、团队）、聊天和知识库页面。**权威的 E2E 细节**位于 [`frontend/tests/e2e/README.md`](../../../frontend/tests/e2e/README.md)；以下是面向贡献者的摘要。

#### 前置条件

1. 安装依赖（包含 `@playwright/test`）：
   ```bash
   cd frontend
   npm install
   ```

2. 安装 Playwright 浏览器：
   ```bash
   npx playwright install chromium
   ```

3. 从模板创建 `.env.test` 文件并填入测试凭据：
   ```bash
   cp .env.test.example .env.test
   ```

   必需的变量：
   | 变量 | 说明 |
   |----------|-------------|
   | `TEST_USER_EMAIL` | 现有管理员用户的邮箱 |
   | `TEST_USER_PASSWORD` | 该用户的密码 |
   | `BASE_URL` | Playwright 打开应用的地址（配置中的默认值：`http://localhost:3001`） |
   | `NEXT_PUBLIC_API_BASE_URL` | 用于 API 调用（种子数据/夹具）的后端 URL；未设置时，夹具中默认为 `http://localhost:3000` |

#### 运行 E2E 测试

以下所有命令均在 `frontend/` 目录中运行。

| 命令 | 说明 |
|---------|-------------|
| `npm run test:e2e` | 运行所有测试（自动启动开发服务器） |
| `npm run test:e2e:ui` | 打开 Playwright UI 进行交互式调试 |
| `npm run test:e2e:headed` | 在可见浏览器中运行测试 |
| `npm run test:e2e:seed` | 批量植入测试数据（30 个用户、30 个组、30 个团队） |
| `npm run test:e2e:cleanup` | 删除所有植入的测试数据 |
| `npm run test:e2e:users` | 仅运行与用户相关的测试 |
| `npm run test:e2e:groups` | 仅运行与组相关的测试 |
| `npm run test:e2e:teams` | 仅运行与团队相关的测试 |
| `npm run test:e2e:report` | 打开 HTML 测试报告 |
| `npm run test:e2e:coverage` | 使用 V8 代码覆盖率运行所有测试 |
| `npm run test:e2e:coverage-report` | 打开覆盖率 HTML 报告 |

#### 代码覆盖率

运行 `npm run test:e2e:coverage` 以收集 V8 代码覆盖率。报告将生成在 `coverage/e2e/` 中，包含 V8、LCOV 和控制台摘要格式。使用 `npm run test:e2e:coverage-report` 打开 HTML 报告。

#### 调试与详细输出

要在可见浏览器中观察测试执行并捕获完整跟踪（包括通过的测试）：

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

| 标志 | 作用 |
|------|-------------|
| `--headed` | 打开可见的浏览器窗口，而不是无头运行 |
| `--trace on` | 为每个测试记录跟踪（默认仅在首次重试时记录） |
| `--slow-mo=N` | 在每个 Playwright 操作之间增加 N 毫秒的暂停 |
| `--video on` | 录制每次测试运行的视频 |
| `--screenshot on` | 在每个测试后截图（不仅仅是失败时） |

**交互式 UI 模式**（推荐用于调试）：

```bash
npm run test:e2e:ui
```

这会打开 Playwright 内置的 UI，带有实时浏览器、操作时间线和可逐步查看的 DOM 快照。

**在运行后查看跟踪和报告：**

```bash
# Open the HTML report — click any test to see its trace
npx playwright show-report

# Open a specific trace file directly
npx playwright show-trace test-results/<test-folder>/trace.zip
```

#### E2E 测试项目（Projects）

Playwright 配置了四个按顺序运行的项目：

1. **setup** —— 通过浏览器登录并将认证状态保存到 `.auth/user.json`。
2. **seed** —— 使用 UI 交互和 API 调用的组合来植入批量数据。依赖于 `setup`。
3. **authenticated** —— 所有使用已保存认证状态的功能测试。依赖于 `setup`。
4. **unauthenticated** —— 在没有已保存认证的情况下运行的登录页测试。

#### E2E 目录结构

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

#### 编写新的 E2E 测试

- **认证测试**放在 `frontend/tests/e2e/` 下的功能文件夹中，并从 `@playwright/test` 导入。它们会自动使用已保存的认证状态。
- **基于 API 的测试**（植入数据、清理）从相对于 `tests/e2e/` 中其他规范的 `../fixtures/api-context.fixture` 导入（参见 `seed/` 和 `setup/`）。
- `tests/e2e/helpers/` 中的**辅助函数**为常见 UI 交互（表格行、分页、搜索、侧边栏表单、标签输入）提供可复用的函数。

示例测试：
```typescript
import { test, expect } from '@playwright/test';

test.describe('My Feature', () => {
  test('loads the page', async ({ page }) => {
    await page.goto('/workspace/my-feature/');
    await expect(page.locator('text="My Feature"')).toBeVisible();
  });
});
```

#### 种子数据约定

- 植入的用户遵循 `e2e-user-XXXX@e2etest.pipeshub.local` 的模式
- 植入的组命名为 `E2E Group XXX`
- 植入的团队命名为 `E2E Team XXXX`
- 在植入数据的测试运行后，务必运行 `npm run test:e2e:cleanup` 以删除测试数据

#### E2E CI 说明

在 CI 中，设置环境变量 `CI=true` 以启用：
- 重试（每个测试 2 次尝试）
- 单 worker（顺序执行）
- 全新的开发服务器（不复用）

#### E2E 产物

以下内容在测试运行期间生成，并已被 gitignore：
- `.auth/` —— 已保存的浏览器认证状态
- `test-results/` —— 测试产物（截图、跟踪）
- `playwright-report/` —— HTML 报告

## 文档

- 为任何新功能或更改更新文档
- 用恰当的注释和示例为 API 编写文档
- 保持 README 和其他指南为最新

## 社区准则

- 在所有互动中保持尊重与包容
- 在 pull request 上提供建设性反馈
- 帮助新贡献者起步
- 向项目维护者举报任何不当行为

---

感谢你为我们的项目做出贡献！如果你有任何问题或需要帮助，请提交 issue 或联系维护者。
