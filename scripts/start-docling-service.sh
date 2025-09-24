#!/bin/bash

# Script to start the main service with Docling service included

set -e

echo "🚀 Starting PipeHub AI with Docling Service"

# Get the script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Change to project root
cd "$PROJECT_ROOT"

echo "📁 Working directory: $(pwd)"

# Build the main Docker image (which now includes Docling service)
echo "🔨 Building main Docker image with Docling service..."
docker build -t pipeshub-ai:latest .

if [ $? -eq 0 ]; then
    echo "✅ Main image with Docling service built successfully"
else
    echo "❌ Failed to build main image"
    exit 1
fi

# Start the service using docker-compose
echo "🚀 Starting main service with Docling service included..."
docker-compose -f deployment/docker-compose/docker-compose.dev.yml up -d pipeshub-ai

if [ $? -eq 0 ]; then
    echo "✅ Main service with Docling started successfully"
    echo "🌐 Services available at:"
    echo "   - Frontend: http://localhost:3000"
    echo "   - Query Service: http://localhost:8001"
    echo "   - Connector Service: http://localhost:8088"
    echo "   - Indexing Service: http://localhost:8091"
    echo "   - Docling Service: http://localhost:8081"
    echo "🔍 Docling Health check: http://localhost:8081/health"
    echo "📊 View logs: docker-compose -f deployment/docker-compose/docker-compose.dev.yml logs -f pipeshub-ai"
else
    echo "❌ Failed to start main service"
    exit 1
fi

echo "🎉 PipeHub AI with Docling service setup complete!"
