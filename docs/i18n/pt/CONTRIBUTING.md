# Contribuindo para o PipesHub Workplace AI

<div align="center">

**Translations:** [English](../../../CONTRIBUTING.md) · [Français](../fr/CONTRIBUTING.md) · [Deutsch](../de/CONTRIBUTING.md) · [简体中文](../zh-CN/CONTRIBUTING.md) · [日本語](../ja/CONTRIBUTING.md) · [Русский](../ru/CONTRIBUTING.md) · [עברית](../he/CONTRIBUTING.md) · [한국어](../ko/CONTRIBUTING.md) · [Español](../es/CONTRIBUTING.md) · **Português** · [Türkçe](../tr/CONTRIBUTING.md) · [Tiếng Việt](../vi/CONTRIBUTING.md) · [Italiano](../it/CONTRIBUTING.md)

</div>

Bem-vindo ao nosso projeto de código aberto! Ficamos felizes que você tenha interesse em contribuir. Este documento fornece diretrizes e instruções para ajudá-lo a começar como colaborador.

## 💻 Build de contribuição para desenvolvedores

## Índice
- Configurando o ambiente de desenvolvimento
- Arquitetura do projeto
- Fluxo de trabalho de contribuição
- Diretrizes de estilo de código
- Testes
- Documentação
- Diretrizes da comunidade

## Configurando o ambiente de desenvolvimento

### Dependências de sistema

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

### Dependências da aplicação
```bash
1. **Docker** - Install Docker for your platform
2. **Node.js** - Install Node.js(v22.15.0)
3. **Python 3.12** - Install as shown above
4. **Optional debugging tools:**
   - MongoDB Compass or Studio 3T
   - etcd-manager
```

### Iniciando os contêineres Docker necessários

**Redis:**
```bash
docker run -d --name redis --restart always -p 6379:6379 redis:bookworm
```

**Qdrant:** (a API Key deve corresponder ao .env)
```bash
docker run -p 6333:6333 -p 6334:6334 -e QDRANT__SERVICE__API_KEY=your_qdrant_secret_api_key qdrant/qdrant:v1.13.6
```

**Servidor ETCD:**


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

**ArangoDB:** (a senha deve corresponder ao .env)
```bash
docker run -e ARANGO_ROOT_PASSWORD=your_password -p 8529:8529 --name arango --restart always -d arangodb:3.12.4
```

**Neo4j Desktop (em vez do ArangoDB):** O PipesHub pode usar o **Neo4j** como banco de dados de grafos (`DATA_STORE=neo4j`) em vez do ArangoDB. Isso é útil se você preferir uma interface gráfica local e não quiser o contêiner do ArangoDB.

1. Instale o [Neo4j Desktop](https://neo4j.com/download/), crie um **DBMS local**, defina sua senha e **inicie-o**.
2. Deixe o listener Bolt padrão em **localhost:7687** (ou anote o host/porta exibidos no Desktop, caso os tenha alterado).
3. **Não** inicie o contêiner Docker do ArangoDB acima ao usar o Neo4j.
4. Em `backend/.env` (o modelo que você copia para `backend/nodejs/apps/.env` e `backend/python/.env`), defina pelo menos:
   ```bash
   DATA_STORE=neo4j
   NEO4J_URI=bolt://localhost:7687
   NEO4J_USERNAME=neo4j
   NEO4J_PASSWORD=<same password as your DBMS>
   NEO4J_DATABASE=neo4j
   ```
   Os serviços Python leem `DATA_STORE` e gravam `dataStoreType` no armazenamento KV (etcd/Redis) na inicialização; a API Node.js usa isso para verificações de integridade e trata `NEO4J_*` como a conexão Neo4j ativa.
5. Inicie o serviço **connectors** do Python (`python -m app.connectors_main`) antes ou junto com o restante da pilha para que os metadados de implantação permaneçam consistentes. Se você já fez o bootstrap no ArangoDB usando os mesmos dados do etcd, redefina o etcd ou a chave de implantação no armazenamento KV antes de trocar de backend de grafos para evitar estado inconsistente.

Para uma pilha completa em Docker com o Neo4j em vez do ArangoDB, consulte `docker-compose.build.neo4j.yml` em `deployment/docker-compose/` (documentado no `README.md` do repositório).

**MongoDB:** (a senha deve corresponder ao MONGO URI do .env)

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

### Iniciando o serviço de backend Node.js
```bash
cd backend/nodejs/apps
cp ../../env.template .env  # Create .env file from template
npm install
npm run dev
```

### Iniciando os serviços de backend Python
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

### Configurando o frontend
```bash
cd frontend
cp env.template .env  # Modify port if Node.js backend uses a different one
npm install
PORT=3001 npm run dev
```

Em seguida, abra o navegador na URL exibida (normalmente `http://localhost:3001` ao usar `PORT=3001`; o Next.js usa a porta 3000 por padrão se `PORT` não estiver definido).

## Arquitetura do projeto

Nosso projeto consiste em três componentes principais:

1. **Frontend**: Aplicação Next.js para a interface do usuário
2. **Backend Node.js**: Lida com as solicitações de API, autenticação e lógica de negócios
3. **Serviços Python**: Três microsserviços para:
   - Connectors: Lida com as conexões às fontes de dados
   - Indexing: Gerencia a indexação e o processamento de documentos
   - Query: Processa as solicitações de busca e recuperação

## Fluxo de trabalho de contribuição

1. **Faça um fork do repositório** para a sua conta do GitHub
2. **Clone o seu fork** para a sua máquina local
3. **Crie um novo branch** para a sua funcionalidade ou correção de bug:
   ```bash
   git checkout -b feature/your-feature-name
   ```
4. **Faça suas alterações** seguindo nossas diretrizes de estilo de código
5. **Teste suas alterações** minuciosamente
6. **Faça commit das suas alterações** com mensagens de commit significativas:
   ```bash
   git commit -m "Add feature: brief description of changes"
   ```
7. **Faça push do seu branch** para o seu fork do GitHub:
   ```bash
   git push origin feature/your-feature-name
   ```
8. **Abra um Pull Request** contra o nosso repositório principal
   - Forneça uma descrição clara das alterações
   - Referencie quaisquer issues relacionadas
   - Adicione capturas de tela, se aplicável

## Diretrizes de estilo de código

- **Python**: Siga as diretrizes da PEP 8
- **JavaScript/TypeScript**: Use o ESLint com a configuração do nosso projeto
- **CSS/SCSS**: Siga a convenção de nomenclatura BEM
- **Mensagens de commit**: Use o formato conventional commits

## Testes

- Escreva testes unitários para novas funcionalidades
- Garanta que todos os testes passem antes de enviar um PR
- Inclua testes de integração quando apropriado
- Documente os passos de teste manual para funcionalidades complexas

### Executando os testes unitários do Node.js

Os testes usam o **Mocha** como executor de testes e o **c8** para a cobertura de código. Os arquivos de teste ficam em `backend/nodejs/apps/tests/` e seguem a convenção de nomenclatura `*.test.ts`. Consulte [`backend/nodejs/apps/tests/README.md`](../../../backend/nodejs/apps/tests/README.md) para todos os detalhes.

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

### Executando os testes unitários do Python

Os testes usam o **pytest** e ficam em `backend/python/tests/`. Os arquivos de teste seguem a convenção de nomenclatura `test_*.py`. Consulte [`backend/python/tests/README.md`](../../../backend/python/tests/README.md) para todos os detalhes.

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

### Executando os testes E2E do frontend (Playwright)

O frontend (`frontend/`) usa o [Playwright](https://playwright.dev/) para testes de ponta a ponta. Os testes cobrem autenticação, navegação, configurações do espaço de trabalho, CRUD de entidades (usuários, grupos, equipes), chat e páginas da base de conhecimento. Os **detalhes oficiais de E2E** estão em [`frontend/tests/e2e/README.md`](../../../frontend/tests/e2e/README.md); o que segue é um resumo voltado a colaboradores.

#### Pré-requisitos

1. Instale as dependências (inclui `@playwright/test`):
   ```bash
   cd frontend
   npm install
   ```

2. Instale os navegadores do Playwright:
   ```bash
   npx playwright install chromium
   ```

3. Crie um arquivo `.env.test` a partir do modelo e preencha as credenciais de teste:
   ```bash
   cp .env.test.example .env.test
   ```

   Variáveis obrigatórias:
   | Variável | Descrição |
   |----------|-------------|
   | `TEST_USER_EMAIL` | E-mail de um usuário administrador existente |
   | `TEST_USER_PASSWORD` | Senha desse usuário |
   | `BASE_URL` | Onde o Playwright abre o app (padrão na configuração: `http://localhost:3001`) |
   | `NEXT_PUBLIC_API_BASE_URL` | URL do backend para chamadas de API (seeding/fixtures); usa `http://localhost:3000` por padrão nos fixtures se não definida |

#### Executando os testes E2E

Todos os comandos abaixo são executados a partir do diretório `frontend/`.

| Comando | Descrição |
|---------|-------------|
| `npm run test:e2e` | Executa todos os testes (inicia o servidor de desenvolvimento automaticamente) |
| `npm run test:e2e:ui` | Abre a interface do Playwright para depuração interativa |
| `npm run test:e2e:headed` | Executa os testes em um navegador visível |
| `npm run test:e2e:seed` | Gera dados de teste em massa (30 usuários, 30 grupos, 30 equipes) |
| `npm run test:e2e:cleanup` | Exclui todos os dados de teste gerados |
| `npm run test:e2e:users` | Executa apenas os testes relacionados a usuários |
| `npm run test:e2e:groups` | Executa apenas os testes relacionados a grupos |
| `npm run test:e2e:teams` | Executa apenas os testes relacionados a equipes |
| `npm run test:e2e:report` | Abre o relatório de testes HTML |
| `npm run test:e2e:coverage` | Executa todos os testes com cobertura de código V8 |
| `npm run test:e2e:coverage-report` | Abre o relatório HTML de cobertura |

#### Cobertura de código

Execute `npm run test:e2e:coverage` para coletar a cobertura de código V8. Os relatórios são gerados em `coverage/e2e/` nos formatos V8, LCOV e resumo de console. Abra o relatório HTML com `npm run test:e2e:coverage-report`.

#### Depuração e saída detalhada

Para observar a execução dos testes em um navegador visível e capturar rastreamentos completos (incluindo testes que passam):

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

| Flag | O que faz |
|------|-------------|
| `--headed` | Abre uma janela de navegador visível em vez de executar sem interface |
| `--trace on` | Registra um rastreamento para cada teste (por padrão, registra apenas na primeira nova tentativa) |
| `--slow-mo=N` | Adiciona uma pausa de N milissegundos entre cada ação do Playwright |
| `--video on` | Grava um vídeo de cada execução de teste |
| `--screenshot on` | Tira uma captura de tela após cada teste (não apenas em falhas) |

**Modo de interface interativa** (recomendado para depuração):

```bash
npm run test:e2e:ui
```

Isso abre a interface integrada do Playwright com um navegador ao vivo, uma linha do tempo de ações e instantâneos do DOM que você pode percorrer passo a passo.

**Visualizando rastreamentos e relatórios após uma execução:**

```bash
# Open the HTML report — click any test to see its trace
npx playwright show-report

# Open a specific trace file directly
npx playwright show-trace test-results/<test-folder>/trace.zip
```

#### Projetos de teste E2E

O Playwright está configurado com quatro projetos que são executados em ordem:

1. **setup** — Faz login pelo navegador e salva o estado de autenticação em `.auth/user.json`.
2. **seed** — Gera dados em massa usando uma combinação de interações de UI e chamadas de API. Depende de `setup`.
3. **authenticated** — Todos os testes de funcionalidades que usam o estado de autenticação salvo. Depende de `setup`.
4. **unauthenticated** — Testes da página de login executados sem autenticação salva.

#### Estrutura de diretórios de E2E

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

#### Escrevendo novos testes E2E

- Os **testes autenticados** ficam em uma pasta de funcionalidade sob `frontend/tests/e2e/` e importam de `@playwright/test`. Eles usam automaticamente o estado de autenticação salvo.
- Os **testes baseados em API** (seeding, cleanup) importam de `../fixtures/api-context.fixture` de forma relativa às outras specs em `tests/e2e/` (veja `seed/` e `setup/`).
- Os **helpers** em `tests/e2e/helpers/` fornecem funções reutilizáveis para interações comuns de UI (linhas de tabela, paginação, busca, formulários de barra lateral, entrada de tags).

Teste de exemplo:
```typescript
import { test, expect } from '@playwright/test';

test.describe('My Feature', () => {
  test('loads the page', async ({ page }) => {
    await page.goto('/workspace/my-feature/');
    await expect(page.locator('text="My Feature"')).toBeVisible();
  });
});
```

#### Convenções de dados de seed

- Os usuários gerados seguem o padrão `e2e-user-XXXX@e2etest.pipeshub.local`
- Os grupos gerados são nomeados `E2E Group XXX`
- As equipes geradas são nomeadas `E2E Team XXXX`
- Sempre execute `npm run test:e2e:cleanup` após execuções com dados gerados para remover os dados de teste

#### Notas de CI para E2E

No CI, defina a variável de ambiente `CI=true` para habilitar:
- Novas tentativas (2 tentativas por teste)
- Worker único (execução sequencial)
- Servidor de desenvolvimento novo (sem reutilização)

#### Artefatos de E2E

Os itens a seguir são gerados durante as execuções de teste e estão no gitignore:
- `.auth/` — Estado de autenticação do navegador salvo
- `test-results/` — Artefatos de teste (capturas de tela, rastreamentos)
- `playwright-report/` — Relatório HTML

## Documentação

- Atualize a documentação para quaisquer novas funcionalidades ou alterações
- Documente as APIs com comentários e exemplos apropriados
- Mantenha o README e outros guias atualizados

## Diretrizes da comunidade

- Seja respeitoso e inclusivo em todas as interações
- Forneça feedback construtivo nos pull requests
- Ajude os novos colaboradores a começar
- Reporte qualquer comportamento inadequado aos mantenedores do projeto

---

Obrigado por contribuir para o nosso projeto! Se você tiver qualquer dúvida ou precisar de ajuda, abra uma issue ou entre em contato com os mantenedores.
