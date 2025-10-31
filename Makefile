.PHONY: help install-deps-linux install-deps-mac install-deps-windows \
	docker-redis docker-qdrant docker-etcd docker-arango docker-mongo \
	docker-zookeeper docker-kafka docker-all start-containers stop-containers clean-docker stop-and-clean-volumes \
	setup-nodejs implode-setup-nodejs setup-python setup-python-deps \
	implode-setup-python connectors indexing query docling \
	setup-frontend implode-setup-frontend clean-env setup-all clean-all \
	dev-docker-menu prod-docker-menu dev-docker-up dev-docker-down dev-docker-stop dev-docker-clean dev-docker-hard-clean \
	prod-docker-up prod-docker-down prod-docker-stop prod-docker-clean prod-docker-hard-clean \
	check-and-kill-ports

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
	@echo "$(GREEN)Docker Deployments:$(NC)"
	@echo "  make dev-docker-menu      - Show development deployment commands"
	@echo "  make prod-docker-menu     - Show production deployment commands"
	@echo ""
	@echo "$(GREEN)Individual Docker Containers:$(NC)"
	@echo "  make docker-all           - Start all Docker containers"
	@echo "  make docker-redis         - Start Redis container"
	@echo "  make docker-qdrant        - Start Qdrant container"
	@echo "  make docker-etcd          - Start ETCD container"
	@echo "  make docker-arango        - Start ArangoDB container"
	@echo "  make docker-mongo         - Start MongoDB container"
	@echo "  make docker-zookeeper     - Start Zookeeper container"
	@echo "  make docker-kafka         - Start Kafka container"
	@echo "  make stop-containers      - Stop all containers"
	@echo "  make stop-and-clean-volumes - Stop containers and clean volumes"
	@echo ""
	@echo "$(GREEN)Node.js Backend:$(NC)"
	@echo "  make setup-nodejs         - Setup Node.js backend (install deps)"
	@echo "  make implode-setup-nodejs - Remove Node.js setup"
	@echo "  make nodejs           - Run Node.js backend (manual: cd backend/nodejs/apps && npm run dev)"
	@echo ""
	@echo "$(GREEN)Python Backend:$(NC)"
	@echo "  make setup-python         - Setup Python backend (create venv, install deps)"
	@echo "  make setup-python-deps    - Install Python dependencies (requires venv)"
	@echo "  make implode-setup-python - Remove Python setup"
	@echo "  make connectors       - Run connectors service (requires venv)"
	@echo "  make indexing         - Run indexing service (requires venv)"
	@echo "  make query            - Run query service (requires venv)"
	@echo "  make docling          - Run docling service (requires venv)"
	@echo ""
	@echo "$(GREEN)Frontend:$(NC)"
	@echo "  make setup-frontend       - Setup frontend (install deps)"
	@echo "  make implode-setup-frontend - Remove frontend setup"
	@echo "  make frontend         - Run frontend (manual: cd frontend && npm run dev)"
	@echo ""
	@echo "$(GREEN)Docker Cleanup:$(NC)"
	@echo "  make clean-docker         - Remove containers, prune volumes and images"
	@echo ""
	@echo "$(GREEN)Environment Cleanup:$(NC)"
	@echo "  make clean-env            - Remove all .env files"
	@echo ""
	@echo "$(GREEN)Complete Setup:$(NC)"
	@echo "  make setup-all            - Setup all services (Node.js, Python, Frontend)"
	@echo "  make clean-all            - Clean all setups (will ask about .env files)"
	@echo ""
	@echo "$(GREEN)Manual Start Instructions:$(NC)"
	@echo "  Node.js:  cd backend/nodejs/apps && npm run dev"
	@echo "  Frontend: cd frontend          && npm run dev"
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

stop-and-clean-volumes: ## Stop containers and clean volumes
	@echo "$(YELLOW)Stopping all containers...$(NC)"
	@docker stop redis qdrant etcd-server arango mongodb zookeeper kafka 2>/dev/null || true
	@echo "$(YELLOW)Removing containers...$(NC)"
	@docker rm redis qdrant etcd-server arango mongodb zookeeper kafka 2>/dev/null || true
	@echo "$(YELLOW)Pruning unused volumes...$(NC)"
	@docker volume prune -f
	@echo "$(GREEN)Containers stopped and volumes cleaned!$(NC)"

clean-docker: ## Remove containers, prune volumes and images - cleans everything
	@echo "$(RED)Cleaning Docker...$(NC)"
	@echo "$(YELLOW)Stopping all containers...$(NC)"
	@docker stop redis qdrant etcd-server arango mongodb zookeeper kafka 2>/dev/null || true
	@echo "$(YELLOW)Removing containers...$(NC)"
	@docker rm redis qdrant etcd-server arango mongodb zookeeper kafka 2>/dev/null || true
	@echo "$(YELLOW)Pruning unused volumes...$(NC)"
	@docker volume prune -f
	@echo "$(YELLOW)Pruning unused images...$(NC)"
	@docker image prune -f
	@echo "$(GREEN)Docker cleanup complete!$(NC)"

# ==============================================================================
# Node.js Backend Setup
# ==============================================================================

setup-nodejs: ## Setup Node.js backend
	@echo "$(BLUE)Setting up Node.js backend...$(NC)"
	@if [ ! -f backend/nodejs/apps/.env ]; then \
		echo "$(YELLOW)Creating .env file from template...$(NC)"; \
		cp backend/env.template backend/nodejs/apps/.env; \
	else \
		echo "$(BLUE).env file already exists. Do you want to replace it with template? (y/N)$(NC)"; \
		read -p "> " answer && if [ "$$answer" = "y" ] || [ "$$answer" = "Y" ]; then \
			cp backend/env.template backend/nodejs/apps/.env; \
			echo "$(GREEN).env file replaced!$(NC)"; \
		else \
			echo "$(YELLOW)Keeping existing .env file$(NC)"; \
		fi; \
	fi
	@echo "$(BLUE)Installing Node.js dependencies...$(NC)"
	cd backend/nodejs/apps && npm install
	@echo "$(GREEN)Node.js backend setup complete!$(NC)"
	@echo "$(YELLOW)To run: cd backend/nodejs/apps && npm run dev$(NC)"

implode-setup-nodejs: ## Remove Node.js setup
	@echo "$(YELLOW)Removing Node.js setup...$(NC)"
	rm -rf backend/nodejs/apps/node_modules
	@echo "$(GREEN)Node.js setup removed!$(NC)"
nodejs: ## Run Node.js backend
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
	else \
		echo "$(BLUE).env file already exists. Do you want to replace it with template? (y/N)$(NC)"; \
		read -p "> " answer && if [ "$$answer" = "y" ] || [ "$$answer" = "Y" ]; then \
			cp backend/env.template backend/python/.env; \
			echo "$(GREEN).env file replaced!$(NC)"; \
		else \
			echo "$(YELLOW)Keeping existing .env file$(NC)"; \
		fi; \
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
	@echo "$(YELLOW)  - make connectors$(NC)"
	@echo "$(YELLOW)  - make indexing$(NC)"
	@echo "$(YELLOW)  - make query$(NC)"
	@echo "$(YELLOW)  - make docling$(NC)"

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
	rm -f backend/python/celerybeat-schedule
	@echo "$(GREEN)Python setup removed!$(NC)"

connectors: ## Run Python connectors service
	@echo "$(BLUE)Starting connectors service...$(NC)"
	@echo "$(YELLOW)Note: This will block the terminal$(NC)"
	cd backend/python && source venv/bin/activate && python -m app.connectors_main

indexing: ## Run Python indexing service
	@echo "$(BLUE)Starting indexing service...$(NC)"
	@echo "$(YELLOW)Note: This will block the terminal$(NC)"
	cd backend/python && source venv/bin/activate && python -m app.indexing_main
query: ## Run Python query service
	@echo "$(BLUE)Starting query service...$(NC)"
	@echo "$(YELLOW)Note: This will block the terminal$(NC)"
	cd backend/python && source venv/bin/activate && python -m app.query_main
docling: ## Run Python docling service
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
	else \
		echo "$(BLUE).env file already exists. Do you want to replace it with template? (y/N)$(NC)"; \
		read -p "> " answer && if [ "$$answer" = "y" ] || [ "$$answer" = "Y" ]; then \
			cp frontend/env.template frontend/.env; \
			echo "$(GREEN).env file replaced!$(NC)"; \
		else \
			echo "$(YELLOW)Keeping existing .env file$(NC)"; \
		fi; \
	fi
	@echo "$(BLUE)Installing frontend dependencies...$(NC)"
	cd frontend && npm install
	@echo "$(GREEN)Frontend setup complete!$(NC)"
	@echo "$(YELLOW)To run: cd frontend && npm run dev$(NC)"

implode-setup-frontend: ## Remove frontend setup
	@echo "$(YELLOW)Removing frontend setup...$(NC)"
	rm -rf frontend/node_modules
	@echo "$(GREEN)Frontend setup removed!$(NC)"

clean-env: ## Remove all .env files
	@echo "$(YELLOW)Removing .env files...$(NC)"
	rm -f backend/nodejs/apps/.env
	rm -f backend/python/.env
	rm -f frontend/.env
	@echo "$(GREEN).env files removed!$(NC)"

frontend: ## Run frontend
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
	@echo "   - make connectors"
	@echo "   - make indexing"
	@echo "   - make query"
	@echo "   - make docling"
	@echo "4. Start frontend: cd frontend && npm run dev"
	@echo ""
	@echo "$(YELLOW)Or use tmux/screen for managing multiple services!$(NC)"

clean-all: ## Clean all setups with hard Docker cleanup
	@echo "$(RED)⚠️  WARNING: This will perform a HARD CLEAN removing all volumes and images!$(NC)"
	@read -p "Are you sure you want to continue? (y/N) " answer && if [ "$$answer" != "y" ] && [ "$$answer" != "Y" ]; then \
		echo "$(YELLOW)Aborted$(NC)"; \
		exit 1; \
	fi
	@echo "$(BLUE)Cleaning all setups...$(NC)"
	@echo "$(YELLOW)Removing Node.js setup...$(NC)"
	rm -rf backend/nodejs/apps/node_modules
	@echo "$(YELLOW)Removing Python setup...$(NC)"
	rm -rf backend/python/venv
	rm -rf backend/python/app.egg-info
	rm -rf backend/python/*.egg-info
	rm -f backend/python/celerybeat-schedule
	@echo "$(YELLOW)Removing frontend setup...$(NC)"
	rm -rf frontend/node_modules
	@echo "$(YELLOW)Stopping all containers...$(NC)"
	@docker stop redis qdrant etcd-server arango mongodb zookeeper kafka 2>/dev/null || true
	@echo "$(YELLOW)Stopping docker-compose containers...$(NC)"
	@cd deployment/docker-compose && docker compose -f docker-compose.dev.yml -p pipeshub-ai down -v 2>/dev/null || true
	@cd deployment/docker-compose && docker compose -f docker-compose.prod.yml -p pipeshub-ai down -v 2>/dev/null || true
	@echo "$(YELLOW)Removing all containers...$(NC)"
	@docker rm redis qdrant etcd-server arango mongodb zookeeper kafka 2>/dev/null || true
	@docker container prune -f
	@echo "$(YELLOW)Removing all volumes...$(NC)"
	@VOLUMES=$$(docker volume ls -q 2>/dev/null); \
	if [ ! -z "$$VOLUMES" ]; then \
		echo "$$VOLUMES" | xargs docker volume rm 2>/dev/null || true; \
	fi
	@echo "$(YELLOW)Pruning unused volumes...$(NC)"
	@docker volume prune -f
	@echo "$(YELLOW)Removing all images...$(NC)"
	@docker image prune -af
	@echo "$(BLUE)Do you want to clean .env files? (y/N)$(NC)"
	@read -p "> " answer && if [ "$$answer" = "y" ] || [ "$$answer" = "Y" ]; then \
		rm -f backend/nodejs/apps/.env backend/python/.env frontend/.env; \
		echo "$(GREEN).env files cleaned!$(NC)"; \
	fi
	@echo "$(GREEN)All setups cleaned!$(NC)"

# ==============================================================================
# Port Management Functions (Cross-Platform Compatible)
# ==============================================================================
# Supports: macOS (Darwin), Linux, Windows (Git Bash/WSL/MSYS2/Cygwin)
# Uses: lsof/ps/kill on Unix-like systems, netstat/tasklist/taskkill on Windows

check-and-kill-ports: ## Check for running processes on required ports and ask to kill them
	@echo "$(BLUE)Checking for running processes on required ports...$(NC)"
	@PORTS_TO_CHECK="8000 8091 8081 8088"; \
	PROCESSES_FOUND=""; \
	OS_TYPE=$$(uname -s 2>/dev/null || echo "Unknown"); \
	if [ "$$OS_TYPE" = "Darwin" ] || [ "$$OS_TYPE" = "Linux" ]; then \
		for port in $$PORTS_TO_CHECK; do \
			PID=$$(lsof -ti:$$port 2>/dev/null); \
			if [ ! -z "$$PID" ]; then \
				if [ "$$OS_TYPE" = "Darwin" ]; then \
					PROCESS_INFO=$$(ps -p $$PID -o pid,ppid,command 2>/dev/null | tail -n +2); \
				else \
					PROCESS_INFO=$$(ps -p $$PID -o pid,ppid,cmd --no-headers 2>/dev/null); \
				fi; \
				if [ ! -z "$$PROCESS_INFO" ]; then \
					PROCESSES_FOUND="$$PROCESSES_FOUND\nPort $$port: PID $$PID - $$PROCESS_INFO"; \
				fi; \
			fi; \
		done; \
	elif echo "$$OS_TYPE" | grep -q "MINGW\|CYGWIN\|MSYS"; then \
		for port in $$PORTS_TO_CHECK; do \
			PID=$$(netstat -ano | grep ":$$port " | awk '{print $$5}' | head -1 2>/dev/null); \
			if [ ! -z "$$PID" ] && [ "$$PID" != "0" ]; then \
				PROCESS_INFO=$$(tasklist /FI "PID eq $$PID" /FO CSV 2>/dev/null | tail -n +2); \
				if [ ! -z "$$PROCESS_INFO" ]; then \
					PROCESSES_FOUND="$$PROCESSES_FOUND\nPort $$port: PID $$PID - $$PROCESS_INFO"; \
				fi; \
			fi; \
		done; \
	else \
		echo "$(YELLOW)Warning: Unsupported operating system ($$OS_TYPE). Port checking may not work correctly.$(NC)"; \
		echo "$(YELLOW)Please manually check for processes on ports: $$PORTS_TO_CHECK$(NC)"; \
	fi; \
	if [ ! -z "$$PROCESSES_FOUND" ]; then \
		echo "$(YELLOW)Found running processes on required ports:$(NC)"; \
		echo "$$PROCESSES_FOUND"; \
		echo ""; \
		echo "$(RED)These processes may conflict with Docker deployment.$(NC)"; \
		read -p "Do you want to kill these processes? (y/N) " answer; \
		if [ "$$answer" = "y" ] || [ "$$answer" = "Y" ]; then \
			echo "$(YELLOW)Killing processes...$(NC)"; \
			if [ "$$OS_TYPE" = "Darwin" ] || [ "$$OS_TYPE" = "Linux" ]; then \
				for port in $$PORTS_TO_CHECK; do \
					PID=$$(lsof -ti:$$port 2>/dev/null); \
					if [ ! -z "$$PID" ]; then \
						echo "$(YELLOW)Killing process on port $$port (PID: $$PID)$(NC)"; \
						kill -9 $$PID 2>/dev/null || true; \
					fi; \
				done; \
			elif echo "$$OS_TYPE" | grep -q "MINGW\|CYGWIN\|MSYS"; then \
				for port in $$PORTS_TO_CHECK; do \
					PID=$$(netstat -ano | grep ":$$port " | awk '{print $$5}' | head -1 2>/dev/null); \
					if [ ! -z "$$PID" ] && [ "$$PID" != "0" ]; then \
						echo "$(YELLOW)Killing process on port $$port (PID: $$PID)$(NC)"; \
						taskkill /PID $$PID /F 2>/dev/null || true; \
					fi; \
				done; \
			fi; \
			echo "$(GREEN)Processes killed successfully!$(NC)"; \
		else \
			echo "$(YELLOW)Keeping existing processes. Deployment may fail if ports are in use.$(NC)"; \
		fi; \
	else \
		echo "$(GREEN)No conflicting processes found on required ports.$(NC)"; \
	fi

# ==============================================================================
# Development Docker Deployment
# ==============================================================================

dev-docker-menu: ## Show development deployment commands
	@echo "$(BLUE)===============================================$(NC)"
	@echo "$(BLUE)Development Docker Deployment Commands$(NC)"
	@echo "$(BLUE)===============================================$(NC)"
	@echo ""
	@echo "$(GREEN)  make dev-docker-up        - Start development deployment with build$(NC)"
	@echo "$(GREEN)  make dev-docker-down      - Stop and remove containers$(NC)"
	@echo "$(GREEN)  make dev-docker-stop      - Stop containers only$(NC)"
	@echo "$(GREEN)  make dev-docker-clean     - Stop and remove containers$(NC)"
	@echo "$(GREEN)  make dev-docker-hard-clean - Stop, remove containers and clean volumes$(NC)"
	@echo ""
	@echo "$(YELLOW)Note: Ensure environment variables are set in deployment/docker-compose/.env$(NC)"
	@echo "$(YELLOW)      Refer to deployment/docker-compose/env.template for required variables$(NC)"
	@echo ""

dev-docker-up: check-and-kill-ports ## Start development deployment with build
	@echo "$(BLUE)Starting development deployment with build...$(NC)"
	@if [ ! -f deployment/docker-compose/.env ]; then \
		echo "$(YELLOW)Warning: .env file not found in deployment/docker-compose/$(NC)"; \
		echo "$(YELLOW)Please copy deployment/docker-compose/env.template to deployment/docker-compose/.env$(NC)"; \
		echo "$(YELLOW)and set your environment variables before starting the deployment$(NC)"; \
		echo ""; \
		read -p "Continue anyway? (y/N) " answer && if [ "$$answer" != "y" ] && [ "$$answer" != "Y" ]; then \
			echo "$(RED)Aborted$(NC)"; \
			exit 1; \
		fi; \
	fi
	@cd deployment/docker-compose && docker compose -f docker-compose.dev.yml -p pipeshub-ai up --build -d
	@echo "$(GREEN)Development deployment started!$(NC)"
	@echo "$(YELLOW)View logs with: cd deployment/docker-compose && docker compose -f docker-compose.dev.yml -p pipeshub-ai logs -f$(NC)"

dev-docker-down: ## Stop and remove development containers
	@echo "$(YELLOW)Stopping development deployment...$(NC)"
	@cd deployment/docker-compose && docker compose -f docker-compose.dev.yml -p pipeshub-ai down
	@echo "$(GREEN)Development deployment stopped and removed!$(NC)"

dev-docker-stop: ## Stop development containers only
	@echo "$(YELLOW)Stopping development containers...$(NC)"
	@cd deployment/docker-compose && docker compose -f docker-compose.dev.yml -p pipeshub-ai stop
	@echo "$(GREEN)Development containers stopped!$(NC)"

dev-docker-clean: ## Stop and remove development containers
	@echo "$(YELLOW)Stopping and removing development containers...$(NC)"
	@cd deployment/docker-compose && docker compose -f docker-compose.dev.yml -p pipeshub-ai down
	@echo "$(GREEN)Development containers cleaned!$(NC)"

dev-docker-hard-clean: ## Stop, remove containers and clean volumes
	@echo "$(RED)⚠️  WARNING: This will remove all data volumes!$(NC)"
	@read -p "Are you sure you want to continue? (y/N) " answer && if [ "$$answer" != "y" ] && [ "$$answer" != "Y" ]; then \
		echo "$(YELLOW)Aborted$(NC)"; \
		exit 1; \
	fi
	@echo "$(YELLOW)Stopping and removing development containers with volumes...$(NC)"
	@cd deployment/docker-compose && docker compose -f docker-compose.dev.yml -p pipeshub-ai down -v
	@echo "$(GREEN)Development deployment and volumes cleaned!$(NC)"

# ==============================================================================
# Production Docker Deployment
# ==============================================================================

prod-docker-menu: ## Show production deployment commands
	@echo "$(BLUE)===============================================$(NC)"
	@echo "$(BLUE)Production Docker Deployment Commands$(NC)"
	@echo "$(BLUE)===============================================$(NC)"
	@echo ""
	@echo "$(GREEN)  make prod-docker-up       - Start production deployment$(NC)"
	@echo "$(GREEN)  make prod-docker-down     - Stop and remove containers$(NC)"
	@echo "$(GREEN)  make prod-docker-stop     - Stop containers only$(NC)"
	@echo "$(GREEN)  make prod-docker-clean    - Stop and remove containers$(NC)"
	@echo "$(GREEN)  make prod-docker-hard-clean - Stop, remove containers and clean volumes$(NC)"
	@echo ""
	@echo "$(YELLOW)⚠️  IMPORTANT: Set environment variables before deploying to production$(NC)"
	@echo "$(YELLOW)   - Copy deployment/docker-compose/env.template to deployment/docker-compose/.env$(NC)"
	@echo "$(YELLOW)   - Update secrets, passwords, and public URLs$(NC)"
	@echo "$(YELLOW)   - Set CONNECTOR_PUBLIC_BACKEND and FRONTEND_PUBLIC_URL for webhooks$(NC)"
	@echo ""

prod-docker-up: check-and-kill-ports ## Start production deployment
	@echo "$(BLUE)Starting production deployment...$(NC)"
	@if [ ! -f deployment/docker-compose/.env ]; then \
		echo "$(RED)ERROR: .env file not found in deployment/docker-compose/$(NC)"; \
		echo "$(YELLOW)Please copy deployment/docker-compose/env.template to deployment/docker-compose/.env$(NC)"; \
		echo "$(YELLOW)and configure your production environment variables$(NC)"; \
		echo ""; \
		read -p "Continue anyway? (y/N) " answer && if [ "$$answer" != "y" ] && [ "$$answer" != "Y" ]; then \
			echo "$(RED)Aborted$(NC)"; \
			exit 1; \
		fi; \
	fi
	@cd deployment/docker-compose && docker compose -f docker-compose.prod.yml -p pipeshub-ai up -d
	@echo "$(GREEN)Production deployment started!$(NC)"
	@echo "$(YELLOW)View logs with: cd deployment/docker-compose && docker compose -f docker-compose.prod.yml -p pipeshub-ai logs -f$(NC)"

prod-docker-down: ## Stop and remove production containers
	@echo "$(YELLOW)Stopping production deployment...$(NC)"
	@cd deployment/docker-compose && docker compose -f docker-compose.prod.yml -p pipeshub-ai down
	@echo "$(GREEN)Production deployment stopped and removed!$(NC)"

prod-docker-stop: ## Stop production containers only
	@echo "$(YELLOW)Stopping production containers...$(NC)"
	@cd deployment/docker-compose && docker compose -f docker-compose.prod.yml -p pipeshub-ai stop
	@echo "$(GREEN)Production containers stopped!$(NC)"

prod-docker-clean: ## Stop and remove production containers
	@echo "$(YELLOW)Stopping and removing production containers...$(NC)"
	@cd deployment/docker-compose && docker compose -f docker-compose.prod.yml -p pipeshub-ai down
	@echo "$(GREEN)Production containers cleaned!$(NC)"

prod-docker-hard-clean: ## Stop, remove containers and clean volumes
	@echo "$(RED)⚠️  WARNING: This will remove all data volumes including production data!$(NC)"
	@read -p "Are you absolutely sure? Type 'yes' to continue: " answer && if [ "$$answer" != "yes" ]; then \
		echo "$(YELLOW)Aborted$(NC)"; \
		exit 1; \
	fi
	@echo "$(YELLOW)Stopping and removing production containers with volumes...$(NC)"
	@cd deployment/docker-compose && docker compose -f docker-compose.prod.yml -p pipeshub-ai down -v
	@echo "$(GREEN)Production deployment and volumes cleaned!$(NC)"



