/**
 * Global kafkajs mock — loaded via .mocharc.yaml `require` BEFORE any test file.
 *
 * Replaces the real kafkajs `Kafka` constructor (and the Producer/Consumer/Admin
 * it returns) with fake, always-resolving no-ops so nothing in the test suite
 * ever tries to open a real TCP connection to a Kafka broker.
 *
 * Individual test files that need finer control can still stub on top of this
 * (e.g. sinon.stub(BaseKafkaProducerConnection.prototype, 'connect') …); this
 * file simply acts as a safety net for every other test that transitively
 * imports kafkajs.
 */

class FakeKafkaProducer {
  async connect(): Promise<void> {}
  async disconnect(): Promise<void> {}
  async send(_args: any): Promise<any[]> { return []; }
  async sendBatch(_args: any): Promise<any[]> { return []; }
  on() { return this; }
  transaction() {
    return {
      send: async () => [],
      sendBatch: async () => [],
      commit: async () => {},
      abort: async () => {},
    };
  }
}

class FakeKafkaConsumer {
  async connect(): Promise<void> {}
  async disconnect(): Promise<void> {}
  async subscribe(_args: any): Promise<void> {}
  async run(_args: any): Promise<void> {}
  async stop(): Promise<void> {}
  async commitOffsets(_args: any): Promise<void> {}
  seek(_args: any): void {}
  pause(_topics: any): void {}
  resume(_topics: any): void {}
  paused(): any[] { return []; }
  on() { return this; }
}

class FakeKafkaAdmin {
  async connect(): Promise<void> {}
  async disconnect(): Promise<void> {}
  async listTopics(): Promise<string[]> { return []; }
  async createTopics(_args: any): Promise<boolean> { return true; }
  async deleteTopics(_args: any): Promise<void> {}
  async fetchTopicMetadata(_args: any): Promise<any> {
    return { topics: [] };
  }
  async describeCluster(): Promise<any> {
    return { brokers: [], controller: null, clusterId: 'fake' };
  }
}

class FakeKafka {
  constructor(_options?: any) {}
  producer(_options?: any) { return new FakeKafkaProducer(); }
  consumer(_options?: any) { return new FakeKafkaConsumer(); }
  admin(_options?: any) { return new FakeKafkaAdmin(); }
  logger() {
    return {
      info: () => {},
      error: () => {},
      warn: () => {},
      debug: () => {},
      setLogLevel: () => {},
    };
  }
}

const kafkajsPath = require.resolve('kafkajs');

try { require(kafkajsPath); } catch { /* ignore */ }

const cached = require.cache[kafkajsPath];
const fakeExports: any = {
  Kafka: FakeKafka,
  CompressionTypes: { None: 0, GZIP: 1, Snappy: 2, LZ4: 3, ZSTD: 4 },
  CompressionCodecs: {},
  logLevel: { NOTHING: 0, ERROR: 1, WARN: 2, INFO: 4, DEBUG: 5 },
  PartitionAssigners: { roundRobin: () => ({}), range: () => ({}) },
  AssignerProtocol: {},
  ResourceTypes: {},
  ResourceConfigTypes: {},
  AclResourceTypes: {},
  AclOperationTypes: {},
  AclPermissionTypes: {},
  AclResourcePatternTypes: {},
};
fakeExports.default = fakeExports;

if (cached) {
  cached.exports = fakeExports;
} else {
  require.cache[kafkajsPath] = {
    id: kafkajsPath,
    filename: kafkajsPath,
    loaded: true,
    exports: fakeExports,
    children: [],
    paths: [],
    parent: null,
  } as any;
}
