/**
 * Global kafkajs mock — loaded via .mocharc.yaml `require` BEFORE any test file.
 *
 * Prevents real TCP connections to Kafka brokers in tests that indirectly call
 * Kafka admin/producer/consumer code (e.g. app bootstrap topic checks).
 */

class FakeAdmin {
  connect() { return Promise.resolve(); }
  disconnect() { return Promise.resolve(); }
  listTopics() { return Promise.resolve([] as string[]); }
  createTopics(_opts?: unknown) { return Promise.resolve(true); }
  fetchTopicMetadata(_opts?: unknown) { return Promise.resolve({ topics: [] }); }
}

class FakeProducer {
  connect() { return Promise.resolve(); }
  disconnect() { return Promise.resolve(); }
  send(_opts?: unknown) { return Promise.resolve(); }
}

class FakeConsumer {
  connect() { return Promise.resolve(); }
  disconnect() { return Promise.resolve(); }
  subscribe(_opts?: unknown) { return Promise.resolve(); }
  run(_opts?: unknown) { return Promise.resolve(); }
  pause(_topics?: unknown) {}
  resume(_topics?: unknown) {}
}

class FakeKafka {
  constructor(_config?: unknown) {}
  admin() { return new FakeAdmin(); }
  producer(_opts?: unknown) { return new FakeProducer(); }
  consumer(_opts?: unknown) { return new FakeConsumer(); }
}

const kafkajsPath = require.resolve('kafkajs');
try { require(kafkajsPath); } catch { /* ignore */ }

const cached = require.cache[kafkajsPath];
if (cached) {
  cached.exports = { Kafka: FakeKafka };
} else {
  require.cache[kafkajsPath] = {
    id: kafkajsPath,
    filename: kafkajsPath,
    loaded: true,
    exports: { Kafka: FakeKafka },
    children: [],
    paths: [],
    parent: null,
  } as any;
}

