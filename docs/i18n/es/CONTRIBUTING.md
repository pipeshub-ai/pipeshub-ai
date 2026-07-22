# Contribuir a PipesHub Workplace AI

<div align="center">

**Translations:** [English](../../../CONTRIBUTING.md) · [Français](../fr/CONTRIBUTING.md) · [Deutsch](../de/CONTRIBUTING.md) · [简体中文](../zh-CN/CONTRIBUTING.md) · [日本語](../ja/CONTRIBUTING.md) · [Русский](../ru/CONTRIBUTING.md) · [עברית](../he/CONTRIBUTING.md) · [한국어](../ko/CONTRIBUTING.md) · **Español** · [Português](../pt/CONTRIBUTING.md) · [Türkçe](../tr/CONTRIBUTING.md) · [Tiếng Việt](../vi/CONTRIBUTING.md) · [Italiano](../it/CONTRIBUTING.md)

</div>

¡Bienvenido a nuestro proyecto de código abierto! Nos alegra que te interese contribuir. Este documento ofrece directrices e instrucciones para ayudarte a empezar como colaborador.

## 💻 Compilación de contribución para desarrolladores

## Índice
- Configurar el entorno de desarrollo
- Arquitectura del proyecto
- Flujo de trabajo de contribución
- Directrices de estilo de código
- Pruebas
- Documentación
- Directrices de la comunidad

## Configurar el entorno de desarrollo

### Dependencias del sistema

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

### Dependencias de la aplicación
```bash
1. **Docker** - Install Docker for your platform
2. **Node.js** - Install Node.js(v22.15.0)
3. **Python 3.12** - Install as shown above
4. **Optional debugging tools:**
   - MongoDB Compass or Studio 3T
   - etcd-manager
```

### Iniciar los contenedores Docker necesarios

**Redis:**
```bash
docker run -d --name redis --restart always -p 6379:6379 redis:bookworm
```

**Qdrant:** (la API Key debe coincidir con .env)
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

**ArangoDB:** (la contraseña debe coincidir con .env)
```bash
docker run -e ARANGO_ROOT_PASSWORD=your_password -p 8529:8529 --name arango --restart always -d arangodb:3.12.4
```

**Neo4j Desktop (en lugar de ArangoDB):** PipesHub puede usar **Neo4j** como base de datos de grafos (`DATA_STORE=neo4j`) en lugar de ArangoDB. Esto es útil si prefieres una interfaz gráfica local y no quieres el contenedor de ArangoDB.

1. Instala [Neo4j Desktop](https://neo4j.com/download/), crea un **DBMS local**, establece su contraseña e **inícialo**.
2. Deja el receptor Bolt por defecto en **localhost:7687** (o anota el host/puerto que se muestra en Desktop si lo cambiaste).
3. **No** inicies el contenedor Docker de ArangoDB anterior cuando uses Neo4j.
4. En `backend/.env` (la plantilla que copias en `backend/nodejs/apps/.env` y `backend/python/.env`), establece al menos:
   ```bash
   DATA_STORE=neo4j
   NEO4J_URI=bolt://localhost:7687
   NEO4J_USERNAME=neo4j
   NEO4J_PASSWORD=<same password as your DBMS>
   NEO4J_DATABASE=neo4j
   ```
   Los servicios de Python leen `DATA_STORE` y escriben `dataStoreType` en el almacén KV (etcd/Redis) al iniciar; la API de Node.js lo usa para las comprobaciones de salud y trata `NEO4J_*` como la conexión Neo4j activa.
5. Inicia el servicio **connectors** de Python (`python -m app.connectors_main`) antes o junto con el resto de la pila para que los metadatos de despliegue se mantengan coherentes. Si ya inicializaste contra ArangoDB con los mismos datos de etcd, reinicia etcd o la clave de despliegue en el almacén KV antes de cambiar de backend de grafos para evitar un estado inconsistente.

Para una pila completa en Docker con Neo4j en lugar de ArangoDB, consulta `docker-compose.build.neo4j.yml` en `deployment/docker-compose/` (documentado en el `README.md` del repositorio).

**MongoDB:** (la contraseña debe coincidir con el MONGO URI de .env)

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

### Iniciar el servicio de backend Node.js
```bash
cd backend/nodejs/apps
cp ../../env.template .env  # Create .env file from template
npm install
npm run dev
```

### Iniciar los servicios de backend Python
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

### Configurar el frontend
```bash
cd frontend
cp env.template .env  # Modify port if Node.js backend uses a different one
npm install
PORT=3001 npm run dev
```

Luego abre tu navegador en la URL que se muestra (normalmente `http://localhost:3001` al usar `PORT=3001`; Next.js usa el puerto 3000 por defecto si `PORT` no está definido).

## Arquitectura del proyecto

Nuestro proyecto consta de tres componentes principales:

1. **Frontend**: Aplicación Next.js para la interfaz de usuario
2. **Backend Node.js**: Gestiona las solicitudes a la API, la autenticación y la lógica de negocio
3. **Servicios Python**: Tres microservicios para:
   - Connectors: Gestiona las conexiones a las fuentes de datos
   - Indexing: Administra la indexación y el procesamiento de documentos
   - Query: Procesa las solicitudes de búsqueda y recuperación

## Flujo de trabajo de contribución

1. **Haz un fork del repositorio** a tu cuenta de GitHub
2. **Clona tu fork** en tu máquina local
3. **Crea una nueva rama** para tu funcionalidad o corrección de errores:
   ```bash
   git checkout -b feature/your-feature-name
   ```
4. **Realiza tus cambios** siguiendo nuestras directrices de estilo de código
5. **Prueba tus cambios** a fondo
6. **Confirma tus cambios** con mensajes de commit significativos:
   ```bash
   git commit -m "Add feature: brief description of changes"
   ```
7. **Empuja tu rama** a tu fork de GitHub:
   ```bash
   git push origin feature/your-feature-name
   ```
8. **Abre un Pull Request** contra nuestro repositorio principal
   - Proporciona una descripción clara de los cambios
   - Referencia cualquier issue relacionado
   - Añade capturas de pantalla si corresponde

## Directrices de estilo de código

- **Python**: Sigue las directrices de PEP 8
- **JavaScript/TypeScript**: Usa ESLint con la configuración de nuestro proyecto
- **CSS/SCSS**: Sigue la convención de nomenclatura BEM
- **Mensajes de commit**: Usa el formato de conventional commits

## Pruebas

- Escribe pruebas unitarias para las nuevas funcionalidades
- Asegúrate de que todas las pruebas pasen antes de enviar un PR
- Incluye pruebas de integración cuando sea apropiado
- Documenta los pasos de prueba manual para funcionalidades complejas

### Ejecutar las pruebas unitarias de Node.js

Las pruebas usan **Mocha** como ejecutor de pruebas y **c8** para la cobertura de código. Los archivos de prueba están en `backend/nodejs/apps/tests/` y siguen la convención de nomenclatura `*.test.ts`. Consulta [`backend/nodejs/apps/tests/README.md`](../../../backend/nodejs/apps/tests/README.md) para todos los detalles.

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

### Ejecutar las pruebas unitarias de Python

Las pruebas usan **pytest** y están en `backend/python/tests/`. Los archivos de prueba siguen la convención de nomenclatura `test_*.py`. Consulta [`backend/python/tests/README.md`](../../../backend/python/tests/README.md) para todos los detalles.

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

### Ejecutar las pruebas E2E del frontend (Playwright)

El frontend (`frontend/`) usa [Playwright](https://playwright.dev/) para las pruebas de extremo a extremo. Las pruebas cubren la autenticación, la navegación, la configuración del espacio de trabajo, el CRUD de entidades (usuarios, grupos, equipos), el chat y las páginas de la base de conocimiento. Los **detalles autorizados de E2E** están en [`frontend/tests/e2e/README.md`](../../../frontend/tests/e2e/README.md); lo siguiente es un resumen orientado a colaboradores.

#### Requisitos previos

1. Instala las dependencias (incluye `@playwright/test`):
   ```bash
   cd frontend
   npm install
   ```

2. Instala los navegadores de Playwright:
   ```bash
   npx playwright install chromium
   ```

3. Crea un archivo `.env.test` a partir de la plantilla y completa las credenciales de prueba:
   ```bash
   cp .env.test.example .env.test
   ```

   Variables requeridas:
   | Variable | Descripción |
   |----------|-------------|
   | `TEST_USER_EMAIL` | Correo de un usuario administrador existente |
   | `TEST_USER_PASSWORD` | Contraseña de ese usuario |
   | `BASE_URL` | Dónde abre Playwright la app (predeterminado en la configuración: `http://localhost:3001`) |
   | `NEXT_PUBLIC_API_BASE_URL` | URL del backend para las llamadas a la API (seeding/fixtures); por defecto `http://localhost:3000` en los fixtures si no se define |

#### Ejecutar las pruebas E2E

Todos los comandos siguientes se ejecutan desde el directorio `frontend/`.

| Comando | Descripción |
|---------|-------------|
| `npm run test:e2e` | Ejecuta todas las pruebas (inicia el servidor de desarrollo automáticamente) |
| `npm run test:e2e:ui` | Abre la interfaz de Playwright para depuración interactiva |
| `npm run test:e2e:headed` | Ejecuta las pruebas en un navegador visible |
| `npm run test:e2e:seed` | Genera datos de prueba en masa (30 usuarios, 30 grupos, 30 equipos) |
| `npm run test:e2e:cleanup` | Elimina todos los datos de prueba generados |
| `npm run test:e2e:users` | Ejecuta solo las pruebas relacionadas con usuarios |
| `npm run test:e2e:groups` | Ejecuta solo las pruebas relacionadas con grupos |
| `npm run test:e2e:teams` | Ejecuta solo las pruebas relacionadas con equipos |
| `npm run test:e2e:report` | Abre el informe de pruebas HTML |
| `npm run test:e2e:coverage` | Ejecuta todas las pruebas con cobertura de código V8 |
| `npm run test:e2e:coverage-report` | Abre el informe HTML de cobertura |

#### Cobertura de código

Ejecuta `npm run test:e2e:coverage` para recopilar la cobertura de código V8. Los informes se generan en `coverage/e2e/` en formatos V8, LCOV y resumen de consola. Abre el informe HTML con `npm run test:e2e:coverage-report`.

#### Depuración y salida detallada

Para observar la ejecución de las pruebas en un navegador visible y capturar trazas completas (incluidas las pruebas que pasan):

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

| Indicador | Qué hace |
|------|-------------|
| `--headed` | Abre una ventana de navegador visible en lugar de ejecutarse sin interfaz |
| `--trace on` | Registra una traza para cada prueba (por defecto solo registra en el primer reintento) |
| `--slow-mo=N` | Añade una pausa de N milisegundos entre cada acción de Playwright |
| `--video on` | Graba un video de cada ejecución de prueba |
| `--screenshot on` | Toma una captura de pantalla después de cada prueba (no solo en los fallos) |

**Modo de interfaz interactiva** (recomendado para depurar):

```bash
npm run test:e2e:ui
```

Esto abre la interfaz integrada de Playwright con un navegador en vivo, una línea de tiempo de acciones e instantáneas del DOM que puedes recorrer paso a paso.

**Ver trazas e informes después de una ejecución:**

```bash
# Open the HTML report — click any test to see its trace
npx playwright show-report

# Open a specific trace file directly
npx playwright show-trace test-results/<test-folder>/trace.zip
```

#### Proyectos de pruebas E2E

Playwright está configurado con cuatro proyectos que se ejecutan en orden:

1. **setup** — Inicia sesión a través del navegador y guarda el estado de autenticación en `.auth/user.json`.
2. **seed** — Genera datos en masa usando una combinación de interacciones de UI y llamadas a la API. Depende de `setup`.
3. **authenticated** — Todas las pruebas de funcionalidades que usan el estado de autenticación guardado. Depende de `setup`.
4. **unauthenticated** — Pruebas de la página de inicio de sesión que se ejecutan sin autenticación guardada.

#### Estructura de directorios de E2E

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

#### Escribir nuevas pruebas E2E

- Las **pruebas autenticadas** van en una carpeta de funcionalidad bajo `frontend/tests/e2e/` e importan desde `@playwright/test`. Usan automáticamente el estado de autenticación guardado.
- Las **pruebas basadas en API** (seeding, cleanup) importan desde `../fixtures/api-context.fixture` de forma relativa a otras specs en `tests/e2e/` (ver `seed/` y `setup/`).
- Los **helpers** en `tests/e2e/helpers/` proporcionan funciones reutilizables para interacciones comunes de UI (filas de tablas, paginación, búsqueda, formularios de barra lateral, entrada de etiquetas).

Prueba de ejemplo:
```typescript
import { test, expect } from '@playwright/test';

test.describe('My Feature', () => {
  test('loads the page', async ({ page }) => {
    await page.goto('/workspace/my-feature/');
    await expect(page.locator('text="My Feature"')).toBeVisible();
  });
});
```

#### Convenciones de datos de prueba

- Los usuarios generados siguen el patrón `e2e-user-XXXX@e2etest.pipeshub.local`
- Los grupos generados se nombran `E2E Group XXX`
- Los equipos generados se nombran `E2E Team XXXX`
- Ejecuta siempre `npm run test:e2e:cleanup` después de ejecuciones con datos generados para eliminar los datos de prueba

#### Notas de CI para E2E

En CI, establece la variable de entorno `CI=true` para habilitar:
- Reintentos (2 intentos por prueba)
- Un único worker (ejecución secuencial)
- Servidor de desarrollo nuevo (sin reutilización)

#### Artefactos de E2E

Lo siguiente se genera durante las ejecuciones de prueba y está en gitignore:
- `.auth/` — Estado de autenticación del navegador guardado
- `test-results/` — Artefactos de prueba (capturas de pantalla, trazas)
- `playwright-report/` — Informe HTML

## Documentación

- Actualiza la documentación ante cualquier nueva funcionalidad o cambio
- Documenta las API con comentarios y ejemplos apropiados
- Mantén el README y otras guías actualizados

## Directrices de la comunidad

- Sé respetuoso e inclusivo en todas las interacciones
- Ofrece comentarios constructivos en los pull requests
- Ayuda a los nuevos colaboradores a empezar
- Reporta cualquier comportamiento inapropiado a los mantenedores del proyecto

---

¡Gracias por contribuir a nuestro proyecto! Si tienes alguna pregunta o necesitas ayuda, abre un issue o ponte en contacto con los mantenedores.
