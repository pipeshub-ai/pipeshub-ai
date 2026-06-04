# PipesHub Workplace AI への貢献

<div align="center">

**Translations:** [English](../../../CONTRIBUTING.md) · [Français](../fr/CONTRIBUTING.md) · [Deutsch](../de/CONTRIBUTING.md) · [简体中文](../zh-CN/CONTRIBUTING.md) · **日本語** · [Русский](../ru/CONTRIBUTING.md) · [עברית](../he/CONTRIBUTING.md) · [한국어](../ko/CONTRIBUTING.md) · [Español](../es/CONTRIBUTING.md) · [Português](../pt/CONTRIBUTING.md) · [Türkçe](../tr/CONTRIBUTING.md) · [Tiếng Việt](../vi/CONTRIBUTING.md) · [Italiano](../it/CONTRIBUTING.md)

</div>

私たちのオープンソースプロジェクトへようこそ！ あなたが貢献に関心を持ってくださっていることを嬉しく思います。本ドキュメントは、貢献者として始めるためのガイドラインと手順を提供します。

## 💻 開発者向け貢献ビルド

## 目次
- 開発環境のセットアップ
- プロジェクトアーキテクチャ
- 貢献ワークフロー
- コードスタイルガイドライン
- テスト
- ドキュメント
- コミュニティガイドライン

## 開発環境のセットアップ

### システム依存関係

#### Linux
```bash
sudo apt update
sudo apt install python3.12-venv
sudo apt-get install libreoffice
```

#### Mac
```bash
# Install Homebrew if not already installed
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"


# Install required packages
brew install python@3.12
brew install libreoffice
```

#### Windows
```bash
- Install Python 3.12
- Consider using WSL2 for a Linux-like environment
```

### アプリケーション依存関係
```bash
1. **Docker** - Install Docker for your platform
2. **Node.js** - Install Node.js(v22.15.0)
3. **Python 3.12** - Install as shown above
4. **Optional debugging tools:**
   - MongoDB Compass or Studio 3T
   - etcd-manager
```

### 必要な Docker コンテナの起動

**Redis：**
```bash
docker run -d --name redis --restart always -p 6379:6379 redis:bookworm
```

**Qdrant：**（API Key は .env と一致している必要があります）
```bash
docker run -p 6333:6333 -p 6334:6334 -e QDRANT__SERVICE__API_KEY=your_qdrant_secret_api_key qdrant/qdrant:v1.13.6
```

**ETCD サーバー：**


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

**ArangoDB：**（パスワードは .env と一致している必要があります）
```bash
docker run -e ARANGO_ROOT_PASSWORD=your_password -p 8529:8529 --name arango --restart always -d arangodb:3.12.4
```

**Neo4j Desktop（ArangoDB の代わりに）：** PipesHub は ArangoDB の代わりに **Neo4j** をグラフデータベースとして使用できます（`DATA_STORE=neo4j`）。ローカルの GUI を使いたい場合や、ArangoDB コンテナを動かしたくない場合に便利です。

1. [Neo4j Desktop](https://neo4j.com/download/) をインストールし、**ローカル DBMS** を作成してパスワードを設定し、**起動**します。
2. デフォルトの Bolt リスナーを **localhost:7687** のままにします（変更した場合は Desktop に表示されるホスト/ポートを控えておきます）。
3. Neo4j を使う場合は、上記の ArangoDB Docker コンテナを**起動しない**でください。
4. `backend/.env`（`backend/nodejs/apps/.env` と `backend/python/.env` にコピーするテンプレート）で、少なくとも次を設定します：
   ```bash
   DATA_STORE=neo4j
   NEO4J_URI=bolt://localhost:7687
   NEO4J_USERNAME=neo4j
   NEO4J_PASSWORD=<same password as your DBMS>
   NEO4J_DATABASE=neo4j
   ```
   Python サービスは起動時に `DATA_STORE` を読み取り、`dataStoreType` を KV ストア（etcd/Redis）に書き込みます。Node.js API はそれをヘルスチェックに使用し、`NEO4J_*` を実際の Neo4j 接続として扱います。
5. デプロイメントのメタデータの整合性を保つため、残りのスタックの前または同時に **connectors** Python サービス（`python -m app.connectors_main`）を起動してください。同じ etcd データに対してすでに ArangoDB でブートストラップ済みの場合は、グラフバックエンドを切り替える前に etcd または KV ストアのデプロイメントキーをリセットして、状態の不一致を回避してください。

ArangoDB の代わりに Neo4j を使う Docker でのフルスタックについては、`deployment/docker-compose/` 内の `docker-compose.build.neo4j.yml` を参照してください（リポジトリの `README.md` に記載されています）。

**MongoDB：**（パスワードは .env の MONGO URI と一致している必要があります）

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

### Node.js バックエンドサービスの起動
```bash
cd backend/nodejs/apps
cp ../../env.template .env  # Create .env file from template
npm install
npm run dev
```

### Python バックエンドサービスの起動
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

### フロントエンドのセットアップ
```bash
cd frontend
cp env.template .env  # Modify port if Node.js backend uses a different one
npm install
PORT=3001 npm run dev
```

その後、ブラウザで表示された URL を開きます（`PORT=3001` を使用する場合は通常 `http://localhost:3001`。`PORT` を設定しない場合、Next.js はデフォルトでポート 3000 を使用します）。

## プロジェクトアーキテクチャ

私たちのプロジェクトは、3 つの主要なコンポーネントで構成されています：

1. **フロントエンド**：ユーザーインターフェース向けの Next.js アプリケーション
2. **Node.js バックエンド**：API リクエスト、認証、ビジネスロジックを処理
3. **Python サービス**：次の 3 つのマイクロサービス：
   - Connectors：データソース接続を処理
   - Indexing：ドキュメントのインデックス作成と処理を管理
   - Query：検索および取得リクエストを処理

## 貢献ワークフロー

1. **リポジトリをフォーク**して自分の GitHub アカウントへ
2. フォークを**ローカルマシンにクローン**
3. 機能やバグ修正のために**新しいブランチを作成**：
   ```bash
   git checkout -b feature/your-feature-name
   ```
4. 私たちのコードスタイルガイドラインに従って**変更を加える**
5. 変更を入念に**テストする**
6. 意味のあるコミットメッセージで**変更をコミット**：
   ```bash
   git commit -m "Add feature: brief description of changes"
   ```
7. ブランチを自分の GitHub フォークへ**プッシュ**：
   ```bash
   git push origin feature/your-feature-name
   ```
8. 私たちのメインリポジトリに対して**プルリクエストを作成**
   - 変更内容を明確に説明する
   - 関連する issue を参照する
   - 該当する場合はスクリーンショットを追加する

## コードスタイルガイドライン

- **Python**：PEP 8 ガイドラインに従う
- **JavaScript/TypeScript**：私たちのプロジェクト設定で ESLint を使用する
- **CSS/SCSS**：BEM 命名規則に従う
- **コミットメッセージ**：conventional commits 形式を使用する

## テスト

- 新機能には単体テストを書く
- PR を提出する前にすべてのテストが通ることを確認する
- 適切な場合は統合テストを含める
- 複雑な機能については手動テストの手順を文書化する

### Node.js 単体テストの実行

テストはテストランナーとして **Mocha**、コードカバレッジに **c8** を使用します。テストファイルは `backend/nodejs/apps/tests/` にあり、`*.test.ts` の命名規則に従います。詳細は [`backend/nodejs/apps/tests/README.md`](../../../backend/nodejs/apps/tests/README.md) を参照してください。

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

### Python 単体テストの実行

テストは **pytest** を使用し、`backend/python/tests/` にあります。テストファイルは `test_*.py` の命名規則に従います。詳細は [`backend/python/tests/README.md`](../../../backend/python/tests/README.md) を参照してください。

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

### フロントエンド E2E テストの実行（Playwright）

フロントエンド（`frontend/`）はエンドツーエンドテストに [Playwright](https://playwright.dev/) を使用します。テストは認証、ナビゲーション、ワークスペース設定、エンティティの CRUD（ユーザー、グループ、チーム）、チャット、ナレッジベースのページをカバーします。**信頼できる E2E の詳細**は [`frontend/tests/e2e/README.md`](../../../frontend/tests/e2e/README.md) にあります。以下は貢献者向けの要約です。

#### 前提条件

1. 依存関係をインストール（`@playwright/test` を含む）：
   ```bash
   cd frontend
   npm install
   ```

2. Playwright のブラウザをインストール：
   ```bash
   npx playwright install chromium
   ```

3. テンプレートから `.env.test` ファイルを作成し、テスト用の資格情報を入力：
   ```bash
   cp .env.test.example .env.test
   ```

   必須の変数：
   | 変数 | 説明 |
   |----------|-------------|
   | `TEST_USER_EMAIL` | 既存の管理者ユーザーのメールアドレス |
   | `TEST_USER_PASSWORD` | そのユーザーのパスワード |
   | `BASE_URL` | Playwright がアプリを開く場所（設定のデフォルト：`http://localhost:3001`） |
   | `NEXT_PUBLIC_API_BASE_URL` | API 呼び出し（シーディング/フィクスチャ）用のバックエンド URL。未設定の場合、フィクスチャでは `http://localhost:3000` がデフォルト |

#### E2E テストの実行

以下のコマンドはすべて `frontend/` ディレクトリから実行します。

| コマンド | 説明 |
|---------|-------------|
| `npm run test:e2e` | すべてのテストを実行（開発サーバーを自動起動） |
| `npm run test:e2e:ui` | インタラクティブなデバッグ用に Playwright UI を開く |
| `npm run test:e2e:headed` | 可視ブラウザでテストを実行 |
| `npm run test:e2e:seed` | バルクのテストデータをシード（30 ユーザー、30 グループ、30 チーム） |
| `npm run test:e2e:cleanup` | シードしたすべてのテストデータを削除 |
| `npm run test:e2e:users` | ユーザー関連のテストのみ実行 |
| `npm run test:e2e:groups` | グループ関連のテストのみ実行 |
| `npm run test:e2e:teams` | チーム関連のテストのみ実行 |
| `npm run test:e2e:report` | HTML テストレポートを開く |
| `npm run test:e2e:coverage` | V8 コードカバレッジ付きで全テストを実行 |
| `npm run test:e2e:coverage-report` | カバレッジ HTML レポートを開く |

#### コードカバレッジ

`npm run test:e2e:coverage` を実行して V8 コードカバレッジを収集します。レポートは `coverage/e2e/` に V8、LCOV、コンソールサマリーの各形式で生成されます。`npm run test:e2e:coverage-report` で HTML レポートを開きます。

#### デバッグと詳細出力

可視ブラウザでテストの実行を観察し、完全なトレース（合格したテストを含む）を取得するには：

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

| フラグ | 効果 |
|------|-------------|
| `--headed` | ヘッドレス実行の代わりに可視ブラウザウィンドウを開く |
| `--trace on` | すべてのテストでトレースを記録（デフォルトは最初の再試行時のみ記録） |
| `--slow-mo=N` | 各 Playwright 操作の間に N ミリ秒の一時停止を追加 |
| `--video on` | 各テスト実行の動画を録画 |
| `--screenshot on` | 各テスト後にスクリーンショットを撮影（失敗時だけでなく） |

**インタラクティブ UI モード**（デバッグに推奨）：

```bash
npm run test:e2e:ui
```

これにより、ライブブラウザ、操作のタイムライン、ステップ実行できる DOM スナップショットを備えた Playwright 組み込みの UI が開きます。

**実行後のトレースとレポートの表示：**

```bash
# Open the HTML report — click any test to see its trace
npx playwright show-report

# Open a specific trace file directly
npx playwright show-trace test-results/<test-folder>/trace.zip
```

#### E2E テストプロジェクト

Playwright は順番に実行される 4 つのプロジェクトで構成されています：

1. **setup** — ブラウザ経由でログインし、認証状態を `.auth/user.json` に保存します。
2. **seed** — UI 操作と API 呼び出しを組み合わせてバルクデータをシードします。`setup` に依存します。
3. **authenticated** — 保存された認証状態を使用するすべての機能テスト。`setup` に依存します。
4. **unauthenticated** — 保存された認証なしで実行されるログインページのテスト。

#### E2E ディレクトリ構造

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

#### 新しい E2E テストの作成

- **認証テスト**は `frontend/tests/e2e/` 配下の機能フォルダーに置き、`@playwright/test` からインポートします。これらは保存された認証状態を自動的に使用します。
- **API ベースのテスト**（シーディング、クリーンアップ）は、`tests/e2e/` 内の他のスペックからの相対パスで `../fixtures/api-context.fixture` をインポートします（`seed/` と `setup/` を参照）。
- `tests/e2e/helpers/` 内の**ヘルパー**は、一般的な UI 操作（テーブル行、ページネーション、検索、サイドバーフォーム、タグ入力）のための再利用可能な関数を提供します。

テストの例：
```typescript
import { test, expect } from '@playwright/test';

test.describe('My Feature', () => {
  test('loads the page', async ({ page }) => {
    await page.goto('/workspace/my-feature/');
    await expect(page.locator('text="My Feature"')).toBeVisible();
  });
});
```

#### シードデータの規約

- シードされたユーザーは `e2e-user-XXXX@e2etest.pipeshub.local` のパターンに従います
- シードされたグループは `E2E Group XXX` という名前です
- シードされたチームは `E2E Team XXXX` という名前です
- シードを用いたテスト実行の後は、必ず `npm run test:e2e:cleanup` を実行してテストデータを削除してください

#### E2E CI に関する注意

CI では、環境変数 `CI=true` を設定して次を有効にします：
- 再試行（テストごとに 2 回）
- 単一ワーカー（逐次実行）
- 新しい開発サーバー（再利用しない）

#### E2E 成果物

以下はテスト実行中に生成され、gitignore されています：
- `.auth/` — 保存されたブラウザの認証状態
- `test-results/` — テスト成果物（スクリーンショット、トレース）
- `playwright-report/` — HTML レポート

## ドキュメント

- 新機能や変更があればドキュメントを更新する
- 適切なコメントと例で API を文書化する
- README やその他のガイドを最新に保つ

## コミュニティガイドライン

- すべてのやり取りで敬意を払い、包摂的であること
- プルリクエストには建設的なフィードバックを提供すること
- 新しい貢献者の立ち上げを支援すること
- 不適切な行為があればプロジェクトの保守担当者へ報告すること

---

私たちのプロジェクトへの貢献に感謝します！ ご質問がある場合や助けが必要な場合は、issue を作成するか、保守担当者にご連絡ください。
