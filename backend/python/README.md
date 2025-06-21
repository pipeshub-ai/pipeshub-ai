NGROK:

```
ngrok http --url=gradually-amused-guppy.ngrok-free.app 8088
```

Zookeeper and Kafka:

```
docker run -d \
   --name zookeeper \
   --restart always \
   -p 2181:2181 \
   -e ZOOKEEPER_CLIENT_PORT=2181 \
   -e ZOOKEEPER_TICK_TIME=2000 \
   confluentinc/cp-zookeeper:latest

 docker run -d \
   --name kafka \
   --restart always \
   --link zookeeper:zookeeper \
   -p 9092:9092 \
   -e KAFKA_BROKER_ID=1 \
   -e KAFKA_ZOOKEEPER_CONNECT=zookeeper:2181 \
   -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://localhost:9092 \
   -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=PLAINTEXT:PLAINTEXT \
   -e KAFKA_INTER_BROKER_LISTENER_NAME=PLAINTEXT \
   -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 \
   confluentinc/cp-kafka:latest
```

Create a topic:

```
docker exec -it kafka /usr/bin/kafka-topics --create --bootstrap-server localhost:9092 --topic record-events --partitions 1 --replication-factor 1
```

ETCD:

```
docker run -d --name etcd-server \
  -p 2379:2379 -p 2380:2380 \
  quay.io/coreos/etcd:v3.5.17 \
  /usr/local/bin/etcd \
  --name etcd0 \
  --data-dir /etcd-data \
  --listen-client-urls http://0.0.0.0:2379 \
  --advertise-client-urls http://0.0.0.0:2379 \
  --listen-peer-urls http://0.0.0.0:2380
```

Qdrant:

```
docker run -p 6333:6333 -p 6334:6334 qdrant/qdrant
```

Redis:

```
docker run --name some-redis -d -p 6379:6379 redis
```

ArangoDB:

```
docker run -d --name arangodb -p 8529:8529 -e ARANGO_ROOT_PASSWORD=your_password arangodb/arangodb:latest

```
