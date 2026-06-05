# PipesHub Workplace AI'a Katkıda Bulunma

<div align="center">

**Translations:** [English](../../../CONTRIBUTING.md) · [Français](../fr/CONTRIBUTING.md) · [Deutsch](../de/CONTRIBUTING.md) · [简体中文](../zh-CN/CONTRIBUTING.md) · [日本語](../ja/CONTRIBUTING.md) · [Русский](../ru/CONTRIBUTING.md) · [עברית](../he/CONTRIBUTING.md) · [한국어](../ko/CONTRIBUTING.md) · [Español](../es/CONTRIBUTING.md) · [Português](../pt/CONTRIBUTING.md) · **Türkçe** · [Tiếng Việt](../vi/CONTRIBUTING.md) · [Italiano](../it/CONTRIBUTING.md)

</div>

Açık kaynak projemize hoş geldiniz! Katkıda bulunmakla ilgilendiğiniz için mutluyuz. Bu belge, bir katkıda bulunan olarak başlamanıza yardımcı olacak yönergeler ve talimatlar sunar.

## 💻 Geliştirici Katkı Derlemesi

## İçindekiler
- Geliştirme Ortamını Kurma
- Proje Mimarisi
- Katkı İş Akışı
- Kod Stili Yönergeleri
- Test
- Dokümantasyon
- Topluluk Yönergeleri

## Geliştirme Ortamını Kurma

### Sistem Bağımlılıkları

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

### Uygulama Bağımlılıkları
```bash
1. **Docker** - Install Docker for your platform
2. **Node.js** - Install Node.js(v22.15.0)
3. **Python 3.12** - Install as shown above
4. **Optional debugging tools:**
   - MongoDB Compass or Studio 3T
   - etcd-manager
```

### Gerekli Docker Konteynerlerini Başlatma

**Redis:**
```bash
docker run -d --name redis --restart always -p 6379:6379 redis:bookworm
```

**Qdrant:** (API Key, .env ile eşleşmelidir)
```bash
docker run -p 6333:6333 -p 6334:6334 -e QDRANT__SERVICE__API_KEY=your_qdrant_secret_api_key qdrant/qdrant:v1.13.6
```

**ETCD Sunucusu:**


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

**ArangoDB:** (Parola, .env ile eşleşmelidir)
```bash
docker run -e ARANGO_ROOT_PASSWORD=your_password -p 8529:8529 --name arango --restart always -d arangodb:3.12.4
```

**Neo4j Desktop (ArangoDB yerine):** PipesHub, ArangoDB yerine grafik veritabanı olarak **Neo4j** kullanabilir (`DATA_STORE=neo4j`). Yerel bir GUI tercih ediyorsanız ve ArangoDB konteynerini istemiyorsanız bu kullanışlıdır.

1. [Neo4j Desktop](https://neo4j.com/download/) kurun, bir **yerel DBMS** oluşturun, parolasını ayarlayın ve **başlatın**.
2. Varsayılan Bolt dinleyicisini **localhost:7687** üzerinde bırakın (değiştirdiyseniz Desktop'ta gösterilen ana bilgisayar/portu not edin).
3. Neo4j kullanırken yukarıdaki ArangoDB Docker konteynerini **başlatmayın**.
4. `backend/.env` dosyasında (`backend/nodejs/apps/.env` ve `backend/python/.env` içine kopyaladığınız şablon) en azından şunları ayarlayın:
   ```bash
   DATA_STORE=neo4j
   NEO4J_URI=bolt://localhost:7687
   NEO4J_USERNAME=neo4j
   NEO4J_PASSWORD=<same password as your DBMS>
   NEO4J_DATABASE=neo4j
   ```
   Python hizmetleri başlangıçta `DATA_STORE`'u okur ve `dataStoreType`'ı KV deposuna (etcd/Redis) yazar; Node.js API bunu sağlık kontrolleri için kullanır ve `NEO4J_*`'ı canlı Neo4j bağlantısı olarak ele alır.
5. Dağıtım meta verilerinin tutarlı kalması için **connectors** Python hizmetini (`python -m app.connectors_main`) yığının geri kalanından önce veya onunla birlikte başlatın. Aynı etcd verileri üzerinde halihazırda ArangoDB'ye karşı önyükleme yaptıysanız, tutarsız durumdan kaçınmak için grafik arka ucunu değiştirmeden önce etcd'yi veya KV deposundaki dağıtım anahtarını sıfırlayın.

ArangoDB yerine Neo4j ile Docker'da tam yığın için `deployment/docker-compose/` içindeki `docker-compose.build.neo4j.yml` dosyasına bakın (depo `README.md` dosyasında belgelenmiştir).

**MongoDB:** (Parola, .env'deki MONGO URI ile eşleşmelidir)

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

### Node.js Arka Uç Hizmetini Başlatma
```bash
cd backend/nodejs/apps
cp ../../env.template .env  # Create .env file from template
npm install
npm run dev
```

### Python Arka Uç Hizmetlerini Başlatma
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

### Ön Ucu Kurma
```bash
cd frontend
cp env.template .env  # Modify port if Node.js backend uses a different one
npm install
PORT=3001 npm run dev
```

Ardından tarayıcınızda gösterilen URL'yi açın (`PORT=3001` kullanırken genellikle `http://localhost:3001`; `PORT` ayarlanmamışsa Next.js varsayılan olarak 3000 portunu kullanır).

## Proje Mimarisi

Projemiz üç ana bileşenden oluşur:

1. **Ön Uç (Frontend)**: Kullanıcı arayüzü için Next.js uygulaması
2. **Node.js Arka Ucu**: API isteklerini, kimlik doğrulamayı ve iş mantığını işler
3. **Python Hizmetleri**: Şunlar için üç mikro hizmet:
   - Connectors: Veri kaynağı bağlantılarını işler
   - Indexing: Belge indeksleme ve işlemeyi yönetir
   - Query: Arama ve geri getirme isteklerini işler

## Katkı İş Akışı

1. Depoyu GitHub hesabınıza **fork edin**
2. Fork'unuzu yerel makinenize **klonlayın**
3. Özelliğiniz veya hata düzeltmeniz için **yeni bir dal oluşturun**:
   ```bash
   git checkout -b feature/your-feature-name
   ```
4. Kod stili yönergelerimizi izleyerek **değişikliklerinizi yapın**
5. Değişikliklerinizi iyice **test edin**
6. Anlamlı commit mesajlarıyla **değişikliklerinizi commit'leyin**:
   ```bash
   git commit -m "Add feature: brief description of changes"
   ```
7. Dalınızı GitHub fork'unuza **push'layın**:
   ```bash
   git push origin feature/your-feature-name
   ```
8. Ana depomuza karşı bir **Pull Request açın**
   - Değişikliklerin net bir açıklamasını sağlayın
   - İlgili issue'lara referans verin
   - Uygunsa ekran görüntüleri ekleyin

## Kod Stili Yönergeleri

- **Python**: PEP 8 yönergelerini izleyin
- **JavaScript/TypeScript**: Proje yapılandırmamızla ESLint kullanın
- **CSS/SCSS**: BEM adlandırma kuralını izleyin
- **Commit Mesajları**: conventional commits biçimini kullanın

## Test

- Yeni özellikler için birim testleri yazın
- Bir PR göndermeden önce tüm testlerin geçtiğinden emin olun
- Uygun olduğunda entegrasyon testleri ekleyin
- Karmaşık özellikler için manuel test adımlarını belgeleyin

### Node.js Birim Testlerini Çalıştırma

Testler, test çalıştırıcısı olarak **Mocha** ve kod kapsamı için **c8** kullanır. Test dosyaları `backend/nodejs/apps/tests/` içinde yer alır ve `*.test.ts` adlandırma kuralını izler. Tüm ayrıntılar için [`backend/nodejs/apps/tests/README.md`](../../../backend/nodejs/apps/tests/README.md) dosyasına bakın.

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

### Python Birim Testlerini Çalıştırma

Testler **pytest** kullanır ve `backend/python/tests/` içinde yer alır. Test dosyaları `test_*.py` adlandırma kuralını izler. Tüm ayrıntılar için [`backend/python/tests/README.md`](../../../backend/python/tests/README.md) dosyasına bakın.

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

### Ön Uç E2E Testlerini Çalıştırma (Playwright)

Ön uç (`frontend/`), uçtan uca test için [Playwright](https://playwright.dev/) kullanır. Testler kimlik doğrulama, gezinme, çalışma alanı ayarları, varlık CRUD (kullanıcılar, gruplar, ekipler), sohbet ve bilgi tabanı sayfalarını kapsar. **Yetkili E2E ayrıntıları** [`frontend/tests/e2e/README.md`](../../../frontend/tests/e2e/README.md) içindedir; aşağıdakiler katkıda bulunanlara yönelik bir özettir.

#### Ön Koşullar

1. Bağımlılıkları kurun (`@playwright/test` dahildir):
   ```bash
   cd frontend
   npm install
   ```

2. Playwright tarayıcılarını kurun:
   ```bash
   npx playwright install chromium
   ```

3. Şablondan bir `.env.test` dosyası oluşturun ve test kimlik bilgilerini doldurun:
   ```bash
   cp .env.test.example .env.test
   ```

   Gerekli değişkenler:
   | Değişken | Açıklama |
   |----------|-------------|
   | `TEST_USER_EMAIL` | Mevcut bir yönetici kullanıcının e-postası |
   | `TEST_USER_PASSWORD` | O kullanıcının parolası |
   | `BASE_URL` | Playwright'ın uygulamayı açtığı yer (yapılandırmada varsayılan: `http://localhost:3001`) |
   | `NEXT_PUBLIC_API_BASE_URL` | API çağrıları (seeding/fixtures) için arka uç URL'si; ayarlanmadığında fixture'larda varsayılan `http://localhost:3000` |

#### E2E Testlerini Çalıştırma

Aşağıdaki tüm komutlar `frontend/` dizininden çalıştırılır.

| Komut | Açıklama |
|---------|-------------|
| `npm run test:e2e` | Tüm testleri çalıştırır (geliştirme sunucusunu otomatik başlatır) |
| `npm run test:e2e:ui` | Etkileşimli hata ayıklama için Playwright UI'sini açar |
| `npm run test:e2e:headed` | Testleri görünür bir tarayıcıda çalıştırır |
| `npm run test:e2e:seed` | Toplu test verisi oluşturur (30 kullanıcı, 30 grup, 30 ekip) |
| `npm run test:e2e:cleanup` | Oluşturulan tüm test verilerini siler |
| `npm run test:e2e:users` | Yalnızca kullanıcıyla ilgili testleri çalıştırır |
| `npm run test:e2e:groups` | Yalnızca grupla ilgili testleri çalıştırır |
| `npm run test:e2e:teams` | Yalnızca ekiple ilgili testleri çalıştırır |
| `npm run test:e2e:report` | HTML test raporunu açar |
| `npm run test:e2e:coverage` | Tüm testleri V8 kod kapsamıyla çalıştırır |
| `npm run test:e2e:coverage-report` | Kapsam HTML raporunu açar |

#### Kod Kapsamı

V8 kod kapsamını toplamak için `npm run test:e2e:coverage` komutunu çalıştırın. Raporlar `coverage/e2e/` içinde V8, LCOV ve konsol özeti biçimlerinde oluşturulur. HTML raporunu `npm run test:e2e:coverage-report` ile açın.

#### Hata Ayıklama ve Ayrıntılı Çıktı

Test yürütmesini görünür bir tarayıcıda izlemek ve tam izleri (geçen testler dahil) yakalamak için:

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

| Bayrak | Ne yapar |
|------|-------------|
| `--headed` | Başsız çalışmak yerine görünür bir tarayıcı penceresi açar |
| `--trace on` | Her test için bir iz kaydeder (varsayılan olarak yalnızca ilk yeniden denemede kaydeder) |
| `--slow-mo=N` | Her Playwright eylemi arasına N milisaniyelik bir duraklama ekler |
| `--video on` | Her test çalıştırmasının videosunu kaydeder |
| `--screenshot on` | Her testten sonra ekran görüntüsü alır (yalnızca hatalarda değil) |

**Etkileşimli UI modu** (hata ayıklama için önerilir):

```bash
npm run test:e2e:ui
```

Bu, canlı bir tarayıcı, eylem zaman çizelgesi ve adım adım inceleyebileceğiniz DOM anlık görüntüleri içeren Playwright'ın yerleşik UI'sini açar.

**Bir çalıştırmadan sonra izleri ve raporları görüntüleme:**

```bash
# Open the HTML report — click any test to see its trace
npx playwright show-report

# Open a specific trace file directly
npx playwright show-trace test-results/<test-folder>/trace.zip
```

#### E2E Test Projeleri

Playwright, sırayla çalışan dört projeyle yapılandırılmıştır:

1. **setup** — Tarayıcı üzerinden oturum açar ve kimlik doğrulama durumunu `.auth/user.json` dosyasına kaydeder.
2. **seed** — UI etkileşimleri ve API çağrılarının bir karışımını kullanarak toplu veri oluşturur. `setup`'a bağlıdır.
3. **authenticated** — Kaydedilen kimlik doğrulama durumunu kullanan tüm özellik testleri. `setup`'a bağlıdır.
4. **unauthenticated** — Kaydedilmiş kimlik doğrulama olmadan çalışan oturum açma sayfası testleri.

#### E2E Dizin Yapısı

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

#### Yeni E2E Testleri Yazma

- **Kimlik doğrulamalı testler**, `frontend/tests/e2e/` altında bir özellik klasörüne yerleştirilir ve `@playwright/test`'ten içe aktarır. Kaydedilen kimlik doğrulama durumunu otomatik olarak kullanırlar.
- **API tabanlı testler** (seeding, cleanup), `tests/e2e/` içindeki diğer spec'lere göre `../fixtures/api-context.fixture`'tan içe aktarır (`seed/` ve `setup/`'a bakın).
- `tests/e2e/helpers/` içindeki **yardımcılar**, yaygın UI etkileşimleri (tablo satırları, sayfalandırma, arama, kenar çubuğu formları, etiket girişi) için yeniden kullanılabilir işlevler sağlar.

Örnek test:
```typescript
import { test, expect } from '@playwright/test';

test.describe('My Feature', () => {
  test('loads the page', async ({ page }) => {
    await page.goto('/workspace/my-feature/');
    await expect(page.locator('text="My Feature"')).toBeVisible();
  });
});
```

#### Seed Verisi Kuralları

- Oluşturulan kullanıcılar `e2e-user-XXXX@e2etest.pipeshub.local` desenini izler
- Oluşturulan gruplar `E2E Group XXX` olarak adlandırılır
- Oluşturulan ekipler `E2E Team XXXX` olarak adlandırılır
- Test verilerini kaldırmak için seed kullanılan test çalıştırmalarından sonra her zaman `npm run test:e2e:cleanup` çalıştırın

#### E2E CI Notları

CI'de, şunları etkinleştirmek için `CI=true` ortam değişkenini ayarlayın:
- Yeniden denemeler (test başına 2 deneme)
- Tek worker (sıralı yürütme)
- Yeni geliştirme sunucusu (yeniden kullanım yok)

#### E2E Yapıtları

Aşağıdakiler test çalıştırmaları sırasında oluşturulur ve gitignore edilir:
- `.auth/` — Kaydedilen tarayıcı kimlik doğrulama durumu
- `test-results/` — Test yapıtları (ekran görüntüleri, izler)
- `playwright-report/` — HTML raporu

## Dokümantasyon

- Yeni özellikler veya değişiklikler için dokümantasyonu güncelleyin
- API'leri uygun yorumlar ve örneklerle belgeleyin
- README'yi ve diğer kılavuzları güncel tutun

## Topluluk Yönergeleri

- Tüm etkileşimlerde saygılı ve kapsayıcı olun
- Pull request'lerde yapıcı geri bildirim sağlayın
- Yeni katkıda bulunanların başlamasına yardımcı olun
- Uygunsuz davranışları proje bakım sorumlularına bildirin

---

Projemize katkıda bulunduğunuz için teşekkür ederiz! Herhangi bir sorunuz olursa veya yardıma ihtiyaç duyarsanız, lütfen bir issue açın veya bakım sorumlularıyla iletişime geçin.
