.PHONY: help install-deps-linux install-deps-mac install-deps-windows \
	docker-redis docker-qdrant docker-etcd docker-arango docker-mongo \
	docker-zookeeper docker-kafka docker-all start-containers stop-containers \
	setup-nodejs implode-setup-nodejs setup-python setup-python-deps \
	implode-setup-python run-connectors run-indexing run-query run-docling \
	setup-frontend implode-setup-frontend setup-all clean-all

# Color codes for output
BLUE := \033[0;34m
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m # No Color

help: ## Show this help message
	@echo "$(BLUE)PipesHub AI Setup Makefile$(NC)"
	@echo ""
	@echo "$(GREEN)System Dependencies:$(NC)"
	@echo "  make install-deps-linux   - Install dependencies on Linux"
	@echo "  make install-deps-mac     - Install dependencies on macOS"
	@echo "  make install-deps-windows - Show Windows installation instructions"
	@echo ""
	@echo "$(GREEN)Docker Containers:$(NC)"
	@echo "  make docker-all           - Start all Docker containers"
	@echo "  make docker-redis         - Start Redis container"
	@echo "  make docker-qdrant        - Start Qdrant container"
	@echo "  make docker-etcd          - Start ETCD container"
	@echo "  make docker-arango        - Start ArangoDB container"
	@echo "  make docker-mongo         - Start MongoDB container"
	@echo "  make docker-zookeeper     - Start Zookeeper container"
	@echo "  make docker-kafka         - Start Kafka container"
	@echo "  make stop-containers      - Stop all containers"
	@echo ""
	@echo "$(GREEN)Node.js Backend:$(NC)"
	@echo "  make setup-nodejs         - Setup Node.js backend (install deps)"
	@echo "  make implode-setup-nodejs - Remove Node.js setup"
	@echo "  make run-nodejs           - Run Node.js backend (manual: cd backend/nodejs/apps && npm run dev)"
	@echo ""
	@echo "$(GREEN)Python Backend:$(NC)"
	@echo "  make setup-python         - Setup Python backend (create venv, install deps)"
	@echo "  make setup-python-deps    - Install Python dependencies (requires venv)"
	@echo "  make implode-setup-python - Remove Python setup"
	@echo "  make run-connectors       - Run connectors service (requires venv)"
	@echo "  make run-indexing         - Run indexing service (requires venv)"
	@echo "  make run-query            - Run query service (requires venv)"
	@echo "  make run-docling          - Run docling service (requires venv)"
	@echo ""
	@echo "$(GREEN)Frontend:$(NC)"
	@echo "  make setup-frontend       - Setup frontend (install deps)"
	@echo "  make implode-setup-frontend - Remove frontend setup"
	@echo "  make run-frontend         - Run frontend (manual: cd frontend && npm run dev)"
	@echo ""
	@echo "$(GREEN)Complete Setup:$(NC)"
	@echo "  make setup-all            - Setup all services (Node.js, Python, Frontend)"
	@echo "  make clean-all            - Clean all setups"
	@echo ""
	@echo "$(GREEN)Manual Start Instructions:$(NC)"
	@echo "  Node.js:  cd backend/nodejs/apps && npm run dev"
	@echo "  Frontend: cd frontend伶          && npm run dev"
	@echo "  Python:   cd backend/python && source venv/bin/activate"

# ==============================================================================
# System Dependencies Installation
# ==============================================================================

install-deps-linux: ## Install system dependencies on Linux
	@echo "$(BLUE)Installing Linux dependencies...$(NC)"
	sudo apt update
	sudo apt install -y python3.10-venv
	sudo apt-get install -y libreoffice
	sudo apt install -y ocrmypdf tesseract-ocr ghostscript unpaper qpdf
	@echo "$(GREEN)Linux dependencies installed successfully!$(NC)"

install-deps-mac: ## Install system dependencies on macOS
	@echo "$(BLUE)Installing macOS dependencies...$(NC)"
	@echo "$(YELLOW)Installing Homebrew if not already installed...$(NC)"
	@bash -c "$$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" || echo "Homebrew already installed"
	brew install python@3.10
	brew install libreoffice
	brew install ocrmypdf ghostscript unpaper qpdf tesseract
	@echo "$(GREEN)macOS dependencies installed successfully!$(NC)"

install-deps-windows: ## Show Windows installation instructions
	@echo "$(YELLOW)Windows Installation Instructions:$(NC)"
	@echo "1. Install Python 3.10 from python.org"
	@echo "2. Install Tesseract OCR if you need to test OCR functionality"
	@echo "3. Consider using WSL2 for a Linux-like environment"
	@echo "4. Run: make install-deps-linux in WSL2"

# ==============================================================================
# Docker Container Management
# ==============================================================================

docker-redis: ## Start Redis container
	@echo "$(BLUE)Starting Redis container...$(NC)"
	@docker run -d --name redis --restart always -p 6379:6379 redis:bookworm 2>/dev/null || echo "$(YELLOW)Redis container already exists or running$(NC)"
	@echo "$(GREEN)Redis container started on port 6379$(NC)"

docker-qdrant: ## Start Qdrant container (API Key must match with .env)
	@echo "$(BLUE)Starting Qdrant container...$(NC)"
	@docker run -d --name qdrant --restart always -p 6333:6333 -p 6334:6334 \
		-e QDRANT__SERVICE__API_KEY=your_qdrant_secret_api_key \
		qdrant/qdrant:v1.13.6 2>/dev/null || echo "$(YELLOW)Qdrant container already exists or running$(NC)"
	@echo "$(GREEN)Qdrant container started on ports 6333, 6334$(NC)"
	@echo "$(YELLOW)NOTE: Make sure QDRANT__SERVICE__API_KEY matches your .env file$(NC)"

docker-etcd: ## Start ETCD container
	@echo "$(BLUE)Starting ETCD container...$(NC)"
	@docker run -d --name etcd-server --restart always \
		-p 2379:2379 -p 2380:2380 \
		quay.io/coreos/etcd:v3.5.17 /usr/local/bin/etcd \
		--name etcd0 \
		--data-dir /etcd-data \
		--listen-client-urls http://0.0.0.0:2379 \
		--advertise-client-urls http://0.0.0.0:2379 \
		--listen-peer-urls http://0.0.0.0:2380 2>/dev/null || echo "$(YELLOW)ETCD container already exists or running$(NC)"
	@echo "$(GREEN)ETCD container started on ports 2379, 2380$(NC)"

docker-arango: ## Start ArangoDB container (Password must match with .env)
	@echo "$(BLUE)Starting ArangoDB container...$(NC)"
	@docker run -e ARANGO_ROOT_PASSWORD=your_password -p 8529:8529 \
		--name arango --restart always -d arangodb:3.12.4 2>/dev/null || echo "$(YELLOW)ArangoDB container already exists or running$(NC)"
	@echo "$(GREEN)ArangoDB container started on port 8529$(NC)"
	@echo "$(YELLOW)NOTE: Make sure ARANGO_ROOT_PASSWORD matches your .env file$(NC)"

docker-mongo: ## Start MongoDB container
	@echo "$(BLUE)Starting MongoDB container...$(NC)"
	@docker run -d --name mongodb --restart always -p 27017:27017 \
		-e MONGO_INITDB_ROOT_USERNAME=admin \
		-e MONGO_INITDB_ROOT_PASSWORD=password \
		mongo:8.0.6 2>/dev/null || echo "$(YELLOW)MongoDB container already exists or running$(NC)"
	@echo "$(GREEN)MongoDB container started on port 27017$(NC)"
	@echo "$(YELLOW)NOTE: Make sure credentials match your .env file$(NC)"

docker-zookeeper: ## Start Zookeeper container
	@echo "$(BLUE)Starting Zookeeper container...$(NC)"
	@docker run -d --name zookeeper --restart always -p 2181:2181 \
		-e ZOOKEEPER_CLIENT_PORT=2181 \
		-e ZOOKEEPER_TICK_TIME=2000 \
		confluentinc/cp-zookeeper:7.9.0 2>/dev/null || echo "$(YELLOW)Zookeeper container already exists or running$(NC)"
	@echo "$(GREEN)Zookeeper container started on port 2181$(NC)"

docker-kafka: docker-zookeeper ## Start Kafka container (requires Zookeeper)
	@echo "$(BLUE)Waiting for Zookeeper to be ready...$(NC)"
	@sleep 3
	@docker run -d --name kafka --restart always --link zookeeper:zookeeper -p 9092:9092 \
		-e KAFKA_BROKER_ID=1 \
		-e KAFKA_ZOOKEEPER_CONNECT=zookeeper:2181 \
		-e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://localhost:9092 \
		-e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=PLAINTEXT:PLAINTEXT \
		-e KAFKA_INTER_BROKER_LISTENER_NAME=PLAINTEXT \
		-e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 \
		confluentinc/cp-kafka:7.9.0 2>/dev/null || echo "$(YELLOW)Kafka container already exists or running$(NC)"
	@echo "$(GREEN)Kafka container started on port 9092$(NC)"

docker-all: docker-redis docker-qdrant docker-etcd docker-arango docker-mongo docker-zookeeper docker-kafka ## Start all Docker containers
	@echo "$(GREEN)All Docker containers started successfully!$(NC)"
	@echo "$(YELLOW)Check running containers with: docker ps$(NC)"

start-containers: docker-all ## Alias for docker-all

stop-containers: ## Stop all containers
	@echo "$(YELLOW)Stopping all containers...$(NC)"
	@docker stop redis qdrant etcd-server arango mongodb zookeeper kafka 2>/dev/null || true
	@echo "$(GREEN)All containers stopped!$(NC)"

# ==============================================================================
# Node.js Backend Setup
# ==============================================================================

setup-nodejs: ## Setup Node.js backend
	@echo "$(BLUE)Setting up Node.js backend...$(NC)"
	@if [ ! -f backend/nodejs/apps/.env ]; then \
		echo "$(YELLOW)Creating .env file from template...$(NC)"; \
		cp backend/env.template backend/nodejs/apps/.env; \
	fi
	@echo "$(BLUE)Installing Node.js dependencies...$(NC)"
	cd backend/nodejs/apps && npm install
	@echo "$(GREEN)Node.js backend setup complete!$(NC)"
	@echo "$(YELLOW)To run: cd backend/nodejs/apps && npm run dev$(NC)"

implode-setup-nodejs: ## Remove Node.js setup
	@echo "$(YELLOW)Removing Node.js setup...$(NC)"
	rm -rf backend/nodejs/apps/node_modules
	rm -f backend/nodejs/apps/.env
	@echo "$(GREEN)Node.js setup removed!$(NC)"

run-nodejs: ## Run Node.js backend
	@echo "$(BLUE)Starting Node.js backend...$(NC)"
	@echo "$(YELLOW)Note: This will block the terminal$(NC)"
	cd backend/nodejs/apps && npm run dev

# ==============================================================================
# Python Backend Setup
# ==============================================================================

setup-python: ## Setup Python backend (create venv and install dependencies)
	@echo "$(BLUE)Setting up Python backend...$(NC)"
	@if [ ! -f backend/python/.env ]; then \
		echo "$(YELLOW)Creating .env file from template...$(NC)"; \
		cp backend/env.template backend/python/.env; \
	fi
	@if [ ! -d backend/python/venv ]; then \
		echo "$(BLUE)Creating Python virtual environment...$(NC)"; \
		cd backend/python && python3.10 -m venv venv; \
	fi
	@echo "$(BLUE)Installing Python dependencies...$(NC)"
	cd backend/python && source venv/bin/activate && pip install -e .
	@echo "$(BLUE)Installing additional language models...$(NC)"
	cd backend/python && source venv/bin/activate && python -m spacy download en_core_web_sm && python -c "import nltk; nltk.download('punkt')"
	@echo "$(GREEN)Python backend setup complete!$(NC)"
	@echo "$(YELLOW)To activate venv: cd backend/python && source venv/bin/activate$(NC)"
	@echo "$(YELLOW)To run services (in separate terminals):$(NC)"
	@echo "$(YELLOW)  - make run-connectors$(NC)"
	@echo "$(YELLOW)  - make run-indexing$(NC)"
	@echo "$(YELLOW)  - make run-query$(NC)"
	@echo "$(YELLOW)  - make run-docling$(NC)"

setup-python-deps: ## Install Python dependencies (requires existing venv)
	@echo "$(BLUE)Installing Python dependencies...$(NC)"
	@if [ ! -d backend/python/venv ]; then \
		echo "$(RED)Error: Virtual environment not found. Run 'make setup-python' first$(NC)"; \
		exit 1; \
	fi
	cd backend/python && source venv/bin/activate && pip install -e .

implode-setup-python: ## Remove Python setup
	@echo "$(YELLOW)Removing Python setup...$(NC)"
	rm -rf backend/python/venv
	rm -rf backend/python/app.egg-info
	rm -rf backend/python/*.egg-info
	rm -f backend/python/.env
	rm -f backend/python/celerybeat-schedule
	@echo "$(GREEN)Python setup removed!$(NC)"

run-connectors: ## Run Python connectors service
	@echo "$(BLUE)Starting connectors service...$(NC)"
	@echo "$(YELLOW)Note: This will block the terminal$(NC)"
	cd backend/python && source venv/bin/activate && python -m app.connectors_main

run-indexing: ## Run Python indexing service
	@echo "$(BLUE)Starting indexing service...$(NC)"
	@echo "$(YELLOW)Note: This will block the terminal$(NC)"
	cd backend/python && source venv/bin/activate && python -m app.indexing_main

run-query: ## Run Python query service
	@echo "$(BLUE)Starting query service...$(NC)"
	@echo "$(YELLOW)Note: This will block the terminal$(NC)"
	cd backend/python && source venv/bin/activate && python -m app.query_main

run-docling: ## Run Python docling service
	@echo "$(BLUE)Starting docling service...$(NC)"
	@echo "$(YELLOW)Note: This will block the terminal$(NC)"
	cd backend/python && source venv/bin/activate && python -m app.docling_main

# ==============================================================================
# Frontend Setup
# ==============================================================================

setup-frontend: ## Setup frontend
	@echo "$(BLUE)Setting up frontend...$(NC)"
	@if [ ! -f frontend/.env ]; then \
		echo "$(YELLOW)Creating .env file from template...$(NC)"; \
		cp frontend/env.template frontend/.env; \
	fi
	@echo "$(BLUE)Installing frontend dependencies...$(NC)"
	cd frontend && npm install
	@echo "$(GREEN)Frontend setup complete!$(NC)"
	@echo "$(YELLOW)To run: cd frontend && npm run dev$(NC)"

implode-setup-frontend: ## Remove frontend setup
	@echo "$(YELLOW)Removing frontend setup...$(NC)"
	rm -rf frontend/node_modules
	rm -f frontend/.env
	@echo "$(GREEN)Frontend setup removed!$(NC)"

run-frontend: ## Run frontend
	@echo "$(BLUE)Starting frontend...$(NC)"
	@echo "$(YELLOW)Note: This will block the terminal$(NC)"
	cd frontend && npm run dev

# ==============================================================================
# Complete Setup
# ==============================================================================

setup-all: setup-nodejs setup-python setup-frontend ## Setup all services
	@echo "$(GREEN)================================================$(NC)"
	@echo "$(GREEN)Setup complete!$(NC)"
	@echo "$(GREEN)================================================$(NC)"
	@echo ""
	@echo "$(BLUE)Next steps:$(NC)"
	@echo "1. Start Docker containers: make docker-all"
	@echo "2. Start Node.js backend: cd backend/nodejs/apps && npm run dev"
	@echo "3. Start Python services (in separate terminals):"
	@echo "   - make run-connectors"
	@echo "   - make run-indexing"
	@echo "   - make run-query"
	@echo "   - make run-docling"
	@echo "4. Start frontend: cd frontend && npm run dev"
	@echo ""
	@echo "$(YELLOW)Or use tmux/screen for managing multiple services!$(NC)"

clean-all: implode-setup-nodejs implode-setup-python implode-setup-frontend stop-containers ## Clean all setups
	@echo "$(GREEN)All setups cleaned!$(NC)"

