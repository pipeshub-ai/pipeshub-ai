# Đóng góp cho PipesHub Workplace AI

<div align="center">

**Translations:** [English](../../../CONTRIBUTING.md) · [Français](../fr/CONTRIBUTING.md) · [Deutsch](../de/CONTRIBUTING.md) · [简体中文](../zh-CN/CONTRIBUTING.md) · [日本語](../ja/CONTRIBUTING.md) · [Русский](../ru/CONTRIBUTING.md) · [עברית](../he/CONTRIBUTING.md) · [한국어](../ko/CONTRIBUTING.md) · [Español](../es/CONTRIBUTING.md) · [Português](../pt/CONTRIBUTING.md) · [Türkçe](../tr/CONTRIBUTING.md) · **Tiếng Việt** · [Italiano](../it/CONTRIBUTING.md)

</div>

Chào mừng bạn đến với dự án mã nguồn mở của chúng tôi! Chúng tôi rất vui vì bạn quan tâm đến việc đóng góp. Tài liệu này cung cấp các hướng dẫn và chỉ dẫn giúp bạn bắt đầu với tư cách là người đóng góp.

## 💻 Bản dựng đóng góp cho nhà phát triển

## Mục lục
- Thiết lập môi trường phát triển
- Kiến trúc dự án
- Quy trình đóng góp
- Hướng dẫn phong cách mã
- Kiểm thử
- Tài liệu
- Hướng dẫn cộng đồng

## Thiết lập môi trường phát triển

### Phụ thuộc hệ thống

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

### Phụ thuộc ứng dụng
```bash
1. **Docker** - Install Docker for your platform
2. **Node.js** - Install Node.js(v22.15.0)
3. **Python 3.12** - Install as shown above
4. **Optional debugging tools:**
   - MongoDB Compass or Studio 3T
   - etcd-manager
```

### Khởi động các container Docker cần thiết

**Redis:**
```bash
docker run -d --name redis --restart always -p 6379:6379 redis:bookworm
```

**Qdrant:** (API Key phải khớp với .env)
```bash
docker run -p 6333:6333 -p 6334:6334 -e QDRANT__SERVICE__API_KEY=your_qdrant_secret_api_key qdrant/qdrant:v1.13.6
```

**Máy chủ ETCD:**


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

**ArangoDB:** (mật khẩu phải khớp với .env)
```bash
docker run -e ARANGO_ROOT_PASSWORD=your_password -p 8529:8529 --name arango --restart always -d arangodb:3.12.4
```

**Neo4j Desktop (thay cho ArangoDB):** PipesHub có thể dùng **Neo4j** làm cơ sở dữ liệu đồ thị (`DATA_STORE=neo4j`) thay cho ArangoDB. Điều này hữu ích nếu bạn thích một GUI cục bộ và không muốn dùng container ArangoDB.

1. Cài đặt [Neo4j Desktop](https://neo4j.com/download/), tạo một **DBMS cục bộ**, đặt mật khẩu cho nó và **Khởi động** nó.
2. Giữ trình lắng nghe Bolt mặc định ở **localhost:7687** (hoặc ghi lại host/port hiển thị trong Desktop nếu bạn đã thay đổi).
3. **Không** khởi động container Docker ArangoDB ở trên khi dùng Neo4j.
4. Trong `backend/.env` (mẫu mà bạn sao chép vào `backend/nodejs/apps/.env` và `backend/python/.env`), hãy đặt ít nhất:
   ```bash
   DATA_STORE=neo4j
   NEO4J_URI=bolt://localhost:7687
   NEO4J_USERNAME=neo4j
   NEO4J_PASSWORD=<same password as your DBMS>
   NEO4J_DATABASE=neo4j
   ```
   Các dịch vụ Python đọc `DATA_STORE` và ghi `dataStoreType` vào kho KV (etcd/Redis) khi khởi động; Node.js API dùng điều này cho việc kiểm tra tình trạng và xem `NEO4J_*` là kết nối Neo4j đang hoạt động.
5. Khởi động dịch vụ Python **connectors** (`python -m app.connectors_main`) trước hoặc cùng với phần còn lại của ngăn xếp để siêu dữ liệu triển khai vẫn nhất quán. Nếu bạn đã khởi tạo (bootstrap) đối với ArangoDB trên cùng dữ liệu etcd, hãy đặt lại etcd hoặc khóa triển khai trong kho KV trước khi chuyển backend đồ thị để tránh trạng thái không khớp.

Để có ngăn xếp đầy đủ trong Docker với Neo4j thay cho ArangoDB, xem `docker-compose.build.neo4j.yml` trong `deployment/docker-compose/` (được ghi chép trong `README.md` của kho lưu trữ).

**MongoDB:** (mật khẩu phải khớp với MONGO URI trong .env)

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

### Khởi động dịch vụ backend Node.js
```bash
cd backend/nodejs/apps
cp ../../env.template .env  # Create .env file from template
npm install
npm run dev
```

### Khởi động các dịch vụ backend Python
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

### Thiết lập frontend
```bash
cd frontend
cp env.template .env  # Modify port if Node.js backend uses a different one
npm install
PORT=3001 npm run dev
```

Sau đó mở trình duyệt của bạn tại URL được hiển thị (thường là `http://localhost:3001` khi dùng `PORT=3001`; Next.js mặc định dùng cổng 3000 nếu `PORT` không được đặt).

## Kiến trúc dự án

Dự án của chúng tôi gồm ba thành phần chính:

1. **Frontend**: Ứng dụng Next.js cho giao diện người dùng
2. **Backend Node.js**: Xử lý các yêu cầu API, xác thực và logic nghiệp vụ
3. **Dịch vụ Python**: Ba microservice cho:
   - Connectors: Xử lý các kết nối nguồn dữ liệu
   - Indexing: Quản lý việc lập chỉ mục và xử lý tài liệu
   - Query: Xử lý các yêu cầu tìm kiếm và truy xuất

## Quy trình đóng góp

1. **Fork kho lưu trữ** vào tài khoản GitHub của bạn
2. **Clone bản fork của bạn** về máy cục bộ
3. **Tạo một nhánh mới** cho tính năng hoặc bản sửa lỗi của bạn:
   ```bash
   git checkout -b feature/your-feature-name
   ```
4. **Thực hiện các thay đổi** theo hướng dẫn phong cách mã của chúng tôi
5. **Kiểm thử các thay đổi** một cách kỹ lưỡng
6. **Commit các thay đổi** với thông điệp commit có ý nghĩa:
   ```bash
   git commit -m "Add feature: brief description of changes"
   ```
7. **Push nhánh của bạn** lên bản fork GitHub của bạn:
   ```bash
   git push origin feature/your-feature-name
   ```
8. **Mở một Pull Request** đến kho lưu trữ chính của chúng tôi
   - Cung cấp mô tả rõ ràng về các thay đổi
   - Tham chiếu mọi issue liên quan
   - Thêm ảnh chụp màn hình nếu phù hợp

## Hướng dẫn phong cách mã

- **Python**: Tuân theo hướng dẫn PEP 8
- **JavaScript/TypeScript**: Sử dụng ESLint với cấu hình dự án của chúng tôi
- **CSS/SCSS**: Tuân theo quy ước đặt tên BEM
- **Thông điệp commit**: Sử dụng định dạng conventional commits

## Kiểm thử

- Viết kiểm thử đơn vị cho các tính năng mới
- Đảm bảo tất cả các kiểm thử đều vượt qua trước khi gửi PR
- Bao gồm kiểm thử tích hợp khi phù hợp
- Ghi lại các bước kiểm thử thủ công cho các tính năng phức tạp

### Chạy kiểm thử đơn vị Node.js

Các kiểm thử dùng **Mocha** làm trình chạy kiểm thử và **c8** cho độ phủ mã. Các tệp kiểm thử nằm trong `backend/nodejs/apps/tests/` và tuân theo quy ước đặt tên `*.test.ts`. Xem [`backend/nodejs/apps/tests/README.md`](../../../backend/nodejs/apps/tests/README.md) để biết đầy đủ chi tiết.

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

### Chạy kiểm thử đơn vị Python

Các kiểm thử dùng **pytest** và nằm trong `backend/python/tests/`. Các tệp kiểm thử tuân theo quy ước đặt tên `test_*.py`. Xem [`backend/python/tests/README.md`](../../../backend/python/tests/README.md) để biết đầy đủ chi tiết.

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

### Chạy kiểm thử E2E của frontend (Playwright)

Frontend (`frontend/`) dùng [Playwright](https://playwright.dev/) để kiểm thử đầu cuối. Các kiểm thử bao gồm xác thực, điều hướng, cài đặt không gian làm việc, CRUD thực thể (người dùng, nhóm, đội), trò chuyện và các trang cơ sở tri thức. **Chi tiết E2E chính thức** nằm trong [`frontend/tests/e2e/README.md`](../../../frontend/tests/e2e/README.md); phần dưới đây là bản tóm tắt hướng đến người đóng góp.

#### Điều kiện tiên quyết

1. Cài đặt các phụ thuộc (bao gồm `@playwright/test`):
   ```bash
   cd frontend
   npm install
   ```

2. Cài đặt trình duyệt Playwright:
   ```bash
   npx playwright install chromium
   ```

3. Tạo tệp `.env.test` từ mẫu và điền thông tin đăng nhập kiểm thử:
   ```bash
   cp .env.test.example .env.test
   ```

   Các biến bắt buộc:
   | Biến | Mô tả |
   |----------|-------------|
   | `TEST_USER_EMAIL` | Email của một người dùng quản trị hiện có |
   | `TEST_USER_PASSWORD` | Mật khẩu của người dùng đó |
   | `BASE_URL` | Nơi Playwright mở ứng dụng (mặc định trong cấu hình: `http://localhost:3001`) |
   | `NEXT_PUBLIC_API_BASE_URL` | URL backend cho các lệnh gọi API (seeding/fixtures); mặc định là `http://localhost:3000` trong fixtures nếu không được đặt |

#### Chạy kiểm thử E2E

Tất cả các lệnh dưới đây chạy từ thư mục `frontend/`.

| Lệnh | Mô tả |
|---------|-------------|
| `npm run test:e2e` | Chạy tất cả kiểm thử (tự động khởi động máy chủ phát triển) |
| `npm run test:e2e:ui` | Mở giao diện Playwright để gỡ lỗi tương tác |
| `npm run test:e2e:headed` | Chạy kiểm thử trong trình duyệt hiển thị |
| `npm run test:e2e:seed` | Tạo dữ liệu kiểm thử hàng loạt (30 người dùng, 30 nhóm, 30 đội) |
| `npm run test:e2e:cleanup` | Xóa tất cả dữ liệu kiểm thử đã tạo |
| `npm run test:e2e:users` | Chỉ chạy các kiểm thử liên quan đến người dùng |
| `npm run test:e2e:groups` | Chỉ chạy các kiểm thử liên quan đến nhóm |
| `npm run test:e2e:teams` | Chỉ chạy các kiểm thử liên quan đến đội |
| `npm run test:e2e:report` | Mở báo cáo kiểm thử HTML |
| `npm run test:e2e:coverage` | Chạy tất cả kiểm thử với độ phủ mã V8 |
| `npm run test:e2e:coverage-report` | Mở báo cáo độ phủ HTML |

#### Độ phủ mã

Chạy `npm run test:e2e:coverage` để thu thập độ phủ mã V8. Báo cáo được tạo trong `coverage/e2e/` ở các định dạng V8, LCOV và tóm tắt console. Mở báo cáo HTML bằng `npm run test:e2e:coverage-report`.

#### Gỡ lỗi và đầu ra chi tiết

Để quan sát quá trình thực thi kiểm thử trong trình duyệt hiển thị và chụp lại đầy đủ trace (bao gồm cả các kiểm thử vượt qua):

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

| Cờ | Tác dụng |
|------|-------------|
| `--headed` | Mở một cửa sổ trình duyệt hiển thị thay vì chạy ở chế độ headless |
| `--trace on` | Ghi lại trace cho mọi kiểm thử (mặc định chỉ ghi ở lần thử lại đầu tiên) |
| `--slow-mo=N` | Thêm khoảng dừng N mili-giây giữa mỗi hành động Playwright |
| `--video on` | Quay video của mọi lần chạy kiểm thử |
| `--screenshot on` | Chụp ảnh màn hình sau mỗi kiểm thử (không chỉ khi thất bại) |

**Chế độ UI tương tác** (khuyến nghị để gỡ lỗi):

```bash
npm run test:e2e:ui
```

Lệnh này mở giao diện tích hợp của Playwright với một trình duyệt trực tiếp, dòng thời gian hành động và các ảnh chụp DOM mà bạn có thể xem từng bước.

**Xem trace và báo cáo sau một lần chạy:**

```bash
# Open the HTML report — click any test to see its trace
npx playwright show-report

# Open a specific trace file directly
npx playwright show-trace test-results/<test-folder>/trace.zip
```

#### Các project kiểm thử E2E

Playwright được cấu hình với bốn project chạy theo thứ tự:

1. **setup** — Đăng nhập qua trình duyệt và lưu trạng thái xác thực vào `.auth/user.json`.
2. **seed** — Tạo dữ liệu hàng loạt bằng cách kết hợp tương tác UI và lệnh gọi API. Phụ thuộc vào `setup`.
3. **authenticated** — Tất cả các kiểm thử tính năng dùng trạng thái xác thực đã lưu. Phụ thuộc vào `setup`.
4. **unauthenticated** — Các kiểm thử trang đăng nhập chạy mà không có xác thực đã lưu.

#### Cấu trúc thư mục E2E

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

#### Viết kiểm thử E2E mới

- Các **kiểm thử có xác thực** được đặt trong một thư mục tính năng dưới `frontend/tests/e2e/` và import từ `@playwright/test`. Chúng tự động dùng trạng thái xác thực đã lưu.
- Các **kiểm thử dựa trên API** (seeding, cleanup) import từ `../fixtures/api-context.fixture` tương đối so với các spec khác trong `tests/e2e/` (xem `seed/` và `setup/`).
- Các **helper** trong `tests/e2e/helpers/` cung cấp các hàm có thể tái sử dụng cho các tương tác UI phổ biến (hàng bảng, phân trang, tìm kiếm, biểu mẫu thanh bên, nhập thẻ).

Ví dụ kiểm thử:
```typescript
import { test, expect } from '@playwright/test';

test.describe('My Feature', () => {
  test('loads the page', async ({ page }) => {
    await page.goto('/workspace/my-feature/');
    await expect(page.locator('text="My Feature"')).toBeVisible();
  });
});
```

#### Quy ước dữ liệu seed

- Người dùng được tạo theo mẫu `e2e-user-XXXX@e2etest.pipeshub.local`
- Các nhóm được tạo có tên `E2E Group XXX`
- Các đội được tạo có tên `E2E Team XXXX`
- Luôn chạy `npm run test:e2e:cleanup` sau các lần chạy có dữ liệu seed để xóa dữ liệu kiểm thử

#### Ghi chú CI cho E2E

Trong CI, đặt biến môi trường `CI=true` để bật:
- Thử lại (2 lần thử mỗi kiểm thử)
- Một worker duy nhất (thực thi tuần tự)
- Máy chủ phát triển mới (không tái sử dụng)

#### Tạo phẩm (artifact) E2E

Những thứ sau được tạo ra trong các lần chạy kiểm thử và được gitignore:
- `.auth/` — Trạng thái xác thực trình duyệt đã lưu
- `test-results/` — Tạo phẩm kiểm thử (ảnh chụp màn hình, trace)
- `playwright-report/` — Báo cáo HTML

## Tài liệu

- Cập nhật tài liệu cho mọi tính năng hoặc thay đổi mới
- Ghi tài liệu cho các API bằng các bình luận và ví dụ phù hợp
- Giữ README và các hướng dẫn khác luôn cập nhật

## Hướng dẫn cộng đồng

- Tôn trọng và hòa nhập trong mọi tương tác
- Đưa ra phản hồi mang tính xây dựng trên các pull request
- Giúp những người đóng góp mới bắt đầu
- Báo cáo mọi hành vi không phù hợp cho những người bảo trì dự án

---

Cảm ơn bạn đã đóng góp cho dự án của chúng tôi! Nếu bạn có bất kỳ câu hỏi nào hoặc cần trợ giúp, vui lòng mở một issue hoặc liên hệ với những người bảo trì.
