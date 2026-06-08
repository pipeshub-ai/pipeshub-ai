# Contribuer à PipesHub Workplace AI

<div align="center">

**Translations:** [English](../../../CONTRIBUTING.md) · **Français** · [Deutsch](../de/CONTRIBUTING.md) · [简体中文](../zh-CN/CONTRIBUTING.md) · [日本語](../ja/CONTRIBUTING.md) · [Русский](../ru/CONTRIBUTING.md) · [עברית](../he/CONTRIBUTING.md) · [한국어](../ko/CONTRIBUTING.md) · [Español](../es/CONTRIBUTING.md) · [Português](../pt/CONTRIBUTING.md) · [Türkçe](../tr/CONTRIBUTING.md) · [Tiếng Việt](../vi/CONTRIBUTING.md) · [Italiano](../it/CONTRIBUTING.md)

</div>

Bienvenue dans notre projet open source ! Nous sommes ravis que vous souhaitiez contribuer. Ce document fournit des directives et des instructions pour vous aider à démarrer en tant que contributeur.

## 💻 Build de contribution pour développeurs

## Table des matières
- Configurer l'environnement de développement
- Architecture du projet
- Workflow de contribution
- Directives de style de code
- Tests
- Documentation
- Directives de la communauté

## Configurer l'environnement de développement

### Dépendances système

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

### Dépendances de l'application
```bash
1. **Docker** - Install Docker for your platform
2. **Node.js** - Install Node.js(v22.15.0)
3. **Python 3.12** - Install as shown above
4. **Optional debugging tools:**
   - MongoDB Compass or Studio 3T
   - etcd-manager
```

### Démarrer les conteneurs Docker requis

**Redis :**
```bash
docker run -d --name redis --restart always -p 6379:6379 redis:bookworm
```

**Qdrant :** (l'API Key doit correspondre à .env)
```bash
docker run -p 6333:6333 -p 6334:6334 -e QDRANT__SERVICE__API_KEY=your_qdrant_secret_api_key qdrant/qdrant:v1.13.6
```

**Serveur ETCD :**


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

**ArangoDB :** (le mot de passe doit correspondre à .env)
```bash
docker run -e ARANGO_ROOT_PASSWORD=your_password -p 8529:8529 --name arango --restart always -d arangodb:3.12.4
```

**Neo4j Desktop (au lieu d'ArangoDB) :** PipesHub peut utiliser **Neo4j** comme base de données orientée graphe (`DATA_STORE=neo4j`) à la place d'ArangoDB. C'est utile si vous préférez une interface graphique locale et ne voulez pas du conteneur ArangoDB.

1. Installez [Neo4j Desktop](https://neo4j.com/download/), créez un **DBMS local**, définissez son mot de passe et **démarrez-le**.
2. Laissez l'écouteur Bolt par défaut sur **localhost:7687** (ou notez l'hôte/port affiché dans Desktop si vous les avez modifiés).
3. **Ne démarrez pas** le conteneur Docker ArangoDB ci-dessus lorsque vous utilisez Neo4j.
4. Dans `backend/.env` (le modèle que vous copiez dans `backend/nodejs/apps/.env` et `backend/python/.env`), définissez au moins :
   ```bash
   DATA_STORE=neo4j
   NEO4J_URI=bolt://localhost:7687
   NEO4J_USERNAME=neo4j
   NEO4J_PASSWORD=<same password as your DBMS>
   NEO4J_DATABASE=neo4j
   ```
   Les services Python lisent `DATA_STORE` et écrivent `dataStoreType` dans le magasin KV (etcd/Redis) au démarrage ; l'API Node.js l'utilise pour les contrôles de santé et traite `NEO4J_*` comme la connexion Neo4j active.
5. Démarrez le service Python **connectors** (`python -m app.connectors_main`) avant ou avec le reste de la pile afin que les métadonnées de déploiement restent cohérentes. Si vous avez déjà initialisé sur ArangoDB avec les mêmes données etcd, réinitialisez etcd ou la clé de déploiement dans le magasin KV avant de changer de backend de graphe pour éviter un état incohérent.

Pour une pile complète sous Docker avec Neo4j au lieu d'ArangoDB, voir `docker-compose.build.neo4j.yml` dans `deployment/docker-compose/` (documenté dans le `README.md` du dépôt).

**MongoDB :** (le mot de passe doit correspondre au MONGO URI de .env)

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

**Zookeeper :**

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


**Apache Kafka :**

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

### Démarrer le service backend Node.js
```bash
cd backend/nodejs/apps
cp ../../env.template .env  # Create .env file from template
npm install
npm run dev
```

### Démarrer les services backend Python
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

### Configurer le frontend
```bash
cd frontend
cp env.template .env  # Modify port if Node.js backend uses a different one
npm install
PORT=3001 npm run dev
```

Ouvrez ensuite votre navigateur à l'URL affichée (généralement `http://localhost:3001` lorsque vous utilisez `PORT=3001` ; Next.js utilise le port 3000 par défaut si `PORT` n'est pas défini).

## Architecture du projet

Notre projet se compose de trois composants principaux :

1. **Frontend** : Application Next.js pour l'interface utilisateur
2. **Backend Node.js** : Gère les requêtes API, l'authentification et la logique métier
3. **Services Python** : Trois microservices pour :
   - Connectors : Gère les connexions aux sources de données
   - Indexing : Gère l'indexation et le traitement des documents
   - Query : Traite les requêtes de recherche et de récupération

## Workflow de contribution

1. **Forkez le dépôt** sur votre compte GitHub
2. **Clonez votre fork** sur votre machine locale
3. **Créez une nouvelle branche** pour votre fonctionnalité ou correction de bug :
   ```bash
   git checkout -b feature/your-feature-name
   ```
4. **Effectuez vos modifications** en suivant nos directives de style de code
5. **Testez vos modifications** de manière approfondie
6. **Validez vos modifications** avec des messages de commit explicites :
   ```bash
   git commit -m "Add feature: brief description of changes"
   ```
7. **Poussez votre branche** vers votre fork GitHub :
   ```bash
   git push origin feature/your-feature-name
   ```
8. **Ouvrez une Pull Request** vers notre dépôt principal
   - Fournissez une description claire des modifications
   - Référencez les issues associées
   - Ajoutez des captures d'écran le cas échéant

## Directives de style de code

- **Python** : Suivez les directives PEP 8
- **JavaScript/TypeScript** : Utilisez ESLint avec la configuration de notre projet
- **CSS/SCSS** : Suivez la convention de nommage BEM
- **Messages de commit** : Utilisez le format conventional commits

## Tests

- Écrivez des tests unitaires pour les nouvelles fonctionnalités
- Assurez-vous que tous les tests passent avant de soumettre une PR
- Incluez des tests d'intégration le cas échéant
- Documentez les étapes de test manuel pour les fonctionnalités complexes

### Exécuter les tests unitaires Node.js

Les tests utilisent **Mocha** comme exécuteur de tests et **c8** pour la couverture de code. Les fichiers de test se trouvent dans `backend/nodejs/apps/tests/` et suivent la convention de nommage `*.test.ts`. Consultez [`backend/nodejs/apps/tests/README.md`](../../../backend/nodejs/apps/tests/README.md) pour tous les détails.

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

### Exécuter les tests unitaires Python

Les tests utilisent **pytest** et se trouvent dans `backend/python/tests/`. Les fichiers de test suivent la convention de nommage `test_*.py`. Consultez [`backend/python/tests/README.md`](../../../backend/python/tests/README.md) pour tous les détails.

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

### Exécuter les tests E2E du frontend (Playwright)

Le frontend (`frontend/`) utilise [Playwright](https://playwright.dev/) pour les tests de bout en bout. Les tests couvrent l'authentification, la navigation, les paramètres de l'espace de travail, le CRUD des entités (utilisateurs, groupes, équipes), le chat et les pages de la base de connaissances. Les **détails E2E faisant autorité** se trouvent dans [`frontend/tests/e2e/README.md`](../../../frontend/tests/e2e/README.md) ; ce qui suit est un résumé destiné aux contributeurs.

#### Prérequis

1. Installez les dépendances (inclut `@playwright/test`) :
   ```bash
   cd frontend
   npm install
   ```

2. Installez les navigateurs Playwright :
   ```bash
   npx playwright install chromium
   ```

3. Créez un fichier `.env.test` à partir du modèle et renseignez les identifiants de test :
   ```bash
   cp .env.test.example .env.test
   ```

   Variables requises :
   | Variable | Description |
   |----------|-------------|
   | `TEST_USER_EMAIL` | E-mail d'un utilisateur administrateur existant |
   | `TEST_USER_PASSWORD` | Mot de passe de cet utilisateur |
   | `BASE_URL` | Où Playwright ouvre l'application (par défaut dans la config : `http://localhost:3001`) |
   | `NEXT_PUBLIC_API_BASE_URL` | URL du backend pour les appels API (seeding/fixtures) ; par défaut `http://localhost:3000` dans les fixtures si non définie |

#### Exécuter les tests E2E

Toutes les commandes ci-dessous s'exécutent depuis le répertoire `frontend/`.

| Commande | Description |
|---------|-------------|
| `npm run test:e2e` | Exécute tous les tests (démarre automatiquement le serveur de développement) |
| `npm run test:e2e:ui` | Ouvre l'interface Playwright pour le débogage interactif |
| `npm run test:e2e:headed` | Exécute les tests dans un navigateur visible |
| `npm run test:e2e:seed` | Génère des données de test en masse (30 utilisateurs, 30 groupes, 30 équipes) |
| `npm run test:e2e:cleanup` | Supprime toutes les données de test générées |
| `npm run test:e2e:users` | Exécute uniquement les tests liés aux utilisateurs |
| `npm run test:e2e:groups` | Exécute uniquement les tests liés aux groupes |
| `npm run test:e2e:teams` | Exécute uniquement les tests liés aux équipes |
| `npm run test:e2e:report` | Ouvre le rapport de tests HTML |
| `npm run test:e2e:coverage` | Exécute tous les tests avec la couverture de code V8 |
| `npm run test:e2e:coverage-report` | Ouvre le rapport de couverture HTML |

#### Couverture de code

Exécutez `npm run test:e2e:coverage` pour collecter la couverture de code V8. Les rapports sont générés dans `coverage/e2e/` aux formats V8, LCOV et résumé console. Ouvrez le rapport HTML avec `npm run test:e2e:coverage-report`.

#### Débogage et sortie détaillée

Pour observer l'exécution des tests dans un navigateur visible et capturer des traces complètes (y compris les tests réussis) :

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

| Option | Ce qu'elle fait |
|------|-------------|
| `--headed` | Ouvre une fenêtre de navigateur visible au lieu de s'exécuter sans interface |
| `--trace on` | Enregistre une trace pour chaque test (par défaut, uniquement lors de la première nouvelle tentative) |
| `--slow-mo=N` | Ajoute une pause de N millisecondes entre chaque action Playwright |
| `--video on` | Enregistre une vidéo de chaque exécution de test |
| `--screenshot on` | Prend une capture d'écran après chaque test (pas seulement en cas d'échec) |

**Mode interface interactive** (recommandé pour le débogage) :

```bash
npm run test:e2e:ui
```

Cela ouvre l'interface intégrée de Playwright avec un navigateur en direct, une chronologie des actions et des instantanés du DOM que vous pouvez parcourir étape par étape.

**Afficher les traces et rapports après une exécution :**

```bash
# Open the HTML report — click any test to see its trace
npx playwright show-report

# Open a specific trace file directly
npx playwright show-trace test-results/<test-folder>/trace.zip
```

#### Projets de tests E2E

Playwright est configuré avec quatre projets exécutés dans l'ordre :

1. **setup** — Se connecte via le navigateur et enregistre l'état d'authentification dans `.auth/user.json`.
2. **seed** — Génère des données en masse en combinant des interactions d'interface et des appels API. Dépend de `setup`.
3. **authenticated** — Tous les tests de fonctionnalités qui utilisent l'état d'authentification enregistré. Dépend de `setup`.
4. **unauthenticated** — Tests de la page de connexion exécutés sans authentification enregistrée.

#### Structure des répertoires E2E

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

#### Écrire de nouveaux tests E2E

- Les **tests authentifiés** vont dans un dossier de fonctionnalité sous `frontend/tests/e2e/` et importent depuis `@playwright/test`. Ils utilisent automatiquement l'état d'authentification enregistré.
- Les **tests basés sur l'API** (seeding, cleanup) importent depuis `../fixtures/api-context.fixture` de manière relative aux autres specs dans `tests/e2e/` (voir `seed/` et `setup/`).
- Les **helpers** dans `tests/e2e/helpers/` fournissent des fonctions réutilisables pour les interactions d'interface courantes (lignes de tableau, pagination, recherche, formulaires de barre latérale, saisie de tags).

Exemple de test :
```typescript
import { test, expect } from '@playwright/test';

test.describe('My Feature', () => {
  test('loads the page', async ({ page }) => {
    await page.goto('/workspace/my-feature/');
    await expect(page.locator('text="My Feature"')).toBeVisible();
  });
});
```

#### Conventions des données de seed

- Les utilisateurs générés suivent le modèle `e2e-user-XXXX@e2etest.pipeshub.local`
- Les groupes générés sont nommés `E2E Group XXX`
- Les équipes générées sont nommées `E2E Team XXXX`
- Exécutez toujours `npm run test:e2e:cleanup` après des exécutions avec données générées pour supprimer les données de test

#### Notes CI pour E2E

En CI, définissez la variable d'environnement `CI=true` pour activer :
- Les nouvelles tentatives (2 essais par test)
- Un worker unique (exécution séquentielle)
- Un serveur de développement neuf (pas de réutilisation)

#### Artefacts E2E

Les éléments suivants sont générés pendant les exécutions de test et sont ignorés par git :
- `.auth/` — État d'authentification du navigateur enregistré
- `test-results/` — Artefacts de test (captures d'écran, traces)
- `playwright-report/` — Rapport HTML

## Documentation

- Mettez à jour la documentation pour toute nouvelle fonctionnalité ou modification
- Documentez les API avec des commentaires et des exemples appropriés
- Maintenez le README et les autres guides à jour

## Directives de la communauté

- Soyez respectueux et inclusif dans toutes les interactions
- Fournissez des retours constructifs sur les pull requests
- Aidez les nouveaux contributeurs à démarrer
- Signalez tout comportement inapproprié aux mainteneurs du projet

---

Merci de contribuer à notre projet ! Si vous avez des questions ou besoin d'aide, ouvrez une issue ou contactez les mainteneurs.
