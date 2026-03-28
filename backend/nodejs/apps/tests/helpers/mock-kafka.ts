import sinon from 'sinon';

export interface MockKafkaProducer {
  connect: sinon.SinonStub;
  disconnect: sinon.SinonStub;
  publish: sinon.SinonStub;
  publishBatch: sinon.SinonStub;
  isConnected: sinon.SinonStub;
  healthCheck: sinon.SinonStub;
  stop: sinon.SinonStub;
}

export interface MockKafkaConsumer {
  connect: sinon.SinonStub;
  disconnect: sinon.SinonStub;
  subscribe: sinon.SinonStub;
  consume: sinon.SinonStub;
  pause: sinon.SinonStub;
  resume: sinon.SinonStub;
  isConnected: sinon.SinonStub;
  healthCheck: sinon.SinonStub;
}

export function createMockKafkaProducer(): MockKafkaProducer {
  return {
    connect: sinon.stub().resolves(),
    disconnect: sinon.stub().resolves(),
    publish: sinon.stub().resolves(),
    publishBatch: sinon.stub().resolves(),
    isConnected: sinon.stub().returns(true),
    healthCheck: sinon.stub().resolves(true),
    stop: sinon.stub().resolves(),
  };
}

export function createMockKafkaConsumer(): MockKafkaConsumer {
  return {
    connect: sinon.stub().resolves(),
    disconnect: sinon.stub().resolves(),
    subscribe: sinon.stub().resolves(),
    consume: sinon.stub().resolves(),
    pause: sinon.stub(),
    resume: sinon.stub(),
    isConnected: sinon.stub().returns(true),
    healthCheck: sinon.stub().resolves(true),
  };
}
