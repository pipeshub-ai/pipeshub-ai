# Contributing to PipesHub Workplace AI 

Welcome to our open source project! We're excited that you're interested in contributing. This document provides guidelines and instructions to help you get started as a contributor.

## 💻 Developer Contribution Build

## Table of Contents
- [Setting Up the Development Environment](#setting-up-the-development-environment)
- [Project Architecture](#project-architecture)
- [Contribution Workflow](#contribution-workflow)
- [Code Style Guidelines](#code-style-guidelines)
- [Testing](#testing)
- [Documentation](#documentation)
- [Community Guidelines](#community-guidelines)

## Setting Up the Development Environment

### System Dependencies

#### Linux
```bash
sudo apt update
sudo apt install python3.10-venv
sudo apt-get install libreoffice
sudo apt install ocrmypdf tesseract-ocr ghostscript unpaper qpdf
```

#### Mac
```bash
# Install Homebrew if not already installed
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"


# Install required packages
brew install python@3.10
brew install libreoffice
brew install ocrmypdf ghostscript unpaper qpdf

# Install optional packages
brew install tesseract
```

#### Windows
```bash
- Install Python 3.10
- Install Tesseract OCR if you need to test OCR functionality
- Consider using WSL2 for a Linux-like environment
```

### Application Dependencies
```bash
1. **Docker** - Install Docker for your platform
2. **Node.js** - Install Node.js(v22.15.0)
3. **Python 3.10** - Install as shown above
4. **Optional debugging tools:**
   - MongoDB Compass or Studio 3T
   - etcd-manager
```

### Starting Required Docker Containers

**Redis:**
```bash
docker run -d --name redis --restart always -p 6379:6379 redis:bookworm
```

**Qdrant:** (API Key must match with .env)
```bash
docker run -p 6333:6333 -p 6334:6334 -e QDRANT__SERVICE__API_KEY=your_qdrant_secret_api_key qdrant/qdrant:v1.13.6
```

**ETCD Server:**


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

**ArangoDB:** (Password must match with .env)
```bash
docker run -e ARANGO_ROOT_PASSWORD=your_password -p 8529:8529 --name arango --restart always -d arangodb:3.12.4
```

**MongoDB:** (Password must match with .env MONGO URI)

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

### Starting Node.js Backend Service
```bash
cd backend/nodejs/apps
cp ../../env.template .env  # Create .env file from template
npm install
npm run dev
```

### Starting Python Backend Services
```bash
cd backend/python
cp ../env.template .env
# Create and activate virtual environment
python3.10 -m venv venv
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

### Setting Up Frontend
```bash
cd frontend
cp env.template .env  # Modify port if Node.js backend uses a different one
npm install
npm run dev
```

Then open your browser to the displayed URL (typically http://localhost:3000).

## Project Architecture

Our project consists of three main components:

1. **Frontend**: React/Next.js application for the user interface
2. **Node.js Backend**: Handles API requests, authentication, and business logic
3. **Python Services**: Three microservices for:
   - Connectors: Handles data source connections
   - Indexing: Manages document indexing and processing
   - Query: Processes search and retrieval requests

## Contribution Workflow

1. **Fork the repository** to your GitHub account
2. **Clone your fork** to your local machine
3. **Create a new branch** for your feature or bug fix:
   ```bash
   git checkout -b feature/your-feature-name
   ```
4. **Make your changes** following our code style guidelines
5. **Test your changes** thoroughly
6. **Commit your changes** with meaningful commit messages:
   ```bash
   git commit -m "Add feature: brief description of changes"
   ```
7. **Push your branch** to your GitHub fork:
   ```bash
   git push origin feature/your-feature-name
   ```
8. **Open a Pull Request** against our main repository
   - Provide a clear description of the changes
   - Reference any related issues
   - Add screenshots if applicable

## Code Style Guidelines

- **Python**: Follow PEP 8 guidelines
- **JavaScript/TypeScript**: Use ESLint with our project configuration
- **CSS/SCSS**: Follow BEM naming convention
- **Commit Messages**: Use the conventional commits format

## Testing

- Write unit tests for new features
- Ensure all tests pass before submitting a PR
- Include integration tests where appropriate
- Document manual testing steps for complex features

## Documentation

- Update documentation for any new features or changes
- Document APIs with appropriate comments and examples
- Keep README and other guides up to date

## Community Guidelines

- Be respectful and inclusive in all interactions
- Provide constructive feedback on pull requests
- Help new contributors get started
- Report any inappropriate behavior to the project maintainers

---

Thank you for contributing to our project! If you have any questions or need help, please open an issue or reach out to the maintainers.
