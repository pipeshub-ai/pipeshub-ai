#!/bin/bash
set -euo pipefail

echo "Starting Redis..."
docker run -d --name redis --restart always -p 127.0.0.1:6379:6379 redis:bookworm

echo "Starting Qdrant..."
docker run -d --restart always -p 127.0.0.1:6333:6333 -p 127.0.0.1:6334:6334 -e QDRANT__SERVICE__API_KEY="${QDRANT_API_KEY:-your_qdrant_secret_api_key}" --name qdrant qdrant/qdrant:v1.15

echo "Starting ETCD..."
docker run -d --name etcd-server --restart always -p 127.0.0.1:2379:2379 -p 127.0.0.1:2380:2380 quay.io/coreos/etcd:v3.5.17 /usr/local/bin/etcd \
  --name etcd0 \
  --data-dir /etcd-data \
  --listen-client-urls http://0.0.0.0:2379 \
  --advertise-client-urls http://127.0.0.1:2379 \
  --listen-peer-urls http://0.0.0.0:2380 \
  --initial-advertise-peer-urls http://127.0.0.1:2380 \
  --initial-cluster etcd0=http://127.0.0.1:2380

echo "Starting ArangoDB..."
docker run -d -e ARANGO_ROOT_PASSWORD="${ARANGO_ROOT_PASSWORD:-your_password}" -p 127.0.0.1:8529:8529 --name arango --restart always arangodb:3.12.4

echo "Starting MongoDB..."
docker run -d --name mongodb --restart always -p 127.0.0.1:27017:27017 \
  -e MONGO_INITDB_ROOT_USERNAME="${MONGO_INITDB_ROOT_USERNAME:-admin}" \
  -e MONGO_INITDB_ROOT_PASSWORD="${MONGO_INITDB_ROOT_PASSWORD:-password}" \
  mongo:8.0.17

echo "Starting Zookeeper..."
docker run -d --name zookeeper --restart always -p 127.0.0.1:2181:2181 \
  -e ZOOKEEPER_CLIENT_PORT=2181 \
  -e ZOOKEEPER_TICK_TIME=2000 \
  confluentinc/cp-zookeeper:7.9.0

echo "Starting Kafka..."
docker run -d --name kafka --restart always --link zookeeper:zookeeper -p 127.0.0.1:9092:9092 \
  -e KAFKA_BROKER_ID=1 \
  -e KAFKA_ZOOKEEPER_CONNECT=zookeeper:2181 \
  -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://localhost:9092 \
  -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=PLAINTEXT:PLAINTEXT \
  -e KAFKA_INTER_BROKER_LISTENER_NAME=PLAINTEXT \
  -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 \
  confluentinc/cp-kafka:7.9.0

echo "Waiting for services to become healthy..."

check_port() {
  local port=$1
  local name=$2
  local retries=30
  local wait=2

  if ! command -v nc >/dev/null 2>&1; then
    echo "ERROR: nc (netcat) is required but not installed." >&2
    exit 1
  fi

  echo -n "Waiting for $name on port $port..."
  while ! nc -z 127.0.0.1 $port >/dev/null 2>&1; do
    retries=$((retries - 1))
    if [ $retries -eq 0 ]; then
      echo " FAILED"
      docker ps
      exit 1
    fi
    sleep $wait
  done
  echo " OK"
}

check_port 6379 "Redis"
check_port 6333 "Qdrant"
check_port 2379 "ETCD"
check_port 8529 "ArangoDB"
check_port 27017 "MongoDB"
check_port 2181 "Zookeeper"
check_port 9092 "Kafka"

echo "All services started."
docker ps
