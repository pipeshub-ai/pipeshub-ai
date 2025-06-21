#!/bin/bash

CONFIG_FILE="config.yaml"
LOG_FILE="setup.log"
RETRY_LIMIT=3
RETRY_WAIT=3

# Function to check service health
check_service() {
    local name="$1"
    local host="$2"
    local port="$3"
    local retries=0

    echo "Checking $name at $host:$port..." | tee -a "$LOG_FILE"
    while ! nc -z "$host" "$port"; do
        ((retries++))
        if [ "$retries" -ge "$RETRY_LIMIT" ]; then
            echo "$name is not responding on $host:$port after $RETRY_LIMIT attempts." | tee -a "$LOG_FILE"
            return 1
        fi
        echo "Retrying $name ($retries/$RETRY_LIMIT)..." | tee -a "$LOG_FILE"
        sleep "$RETRY_WAIT"
    done
    echo "$name is up on $host:$port." | tee -a "$LOG_FILE"
    return 0
}

# Load services from app.config.yaml
SERVICES=(
    "zookeeper:localhost:2181"
    "kafka:localhost:9092"
    "etcd:localhost:2379"
    "qdrant:localhost:6333"
    "redis:localhost:6379"
    "arangodb:localhost:8529"
)

# Check external services
for service in "${SERVICES[@]}"; do
    IFS=":" read -r name host port <<< "$service"
    check_service "$name" "$host" "$port" || exit 1
done

# Prompt before starting internal services
echo "All external services are up. Start internal services? (y/n)" | tee -a "$LOG_FILE"
read -r response
if [[ "$response" =~ ^[Yy]$ ]]; then
    echo "Starting internal services..." | tee -a "$LOG_FILE"
    docker-compose up -d | tee -a "$LOG_FILE"
    echo "System is live! Frontend available at: http://localhost:3000" | tee -a "$LOG_FILE"
else
    echo "Aborting startup." | tee -a "$LOG_FILE"
    exit 1
fi
