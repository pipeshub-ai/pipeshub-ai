import sinon from 'sinon';

export interface MockRedisService {
  get: sinon.SinonStub;
  set: sinon.SinonStub;
  delete: sinon.SinonStub;
  exists: sinon.SinonStub;
  increment: sinon.SinonStub;
  setHash: sinon.SinonStub;
  getHash: sinon.SinonStub;
  getAllHash: sinon.SinonStub;
  deleteHash: sinon.SinonStub;
  acquireLock: sinon.SinonStub;
  releaseLock: sinon.SinonStub;
  isConnected: sinon.SinonStub;
  disconnect: sinon.SinonStub;
}

export function createMockRedisService(): MockRedisService {
  return {
    get: sinon.stub().resolves(null),
    set: sinon.stub().resolves(),
    delete: sinon.stub().resolves(),
    exists: sinon.stub().resolves(false),
    increment: sinon.stub().resolves(1),
    setHash: sinon.stub().resolves(),
    getHash: sinon.stub().resolves(null),
    getAllHash: sinon.stub().resolves(null),
    deleteHash: sinon.stub().resolves(),
    acquireLock: sinon.stub().resolves('mock-lock-token'),
    releaseLock: sinon.stub().resolves(true),
    isConnected: sinon.stub().returns(true),
    disconnect: sinon.stub().resolves(),
  };
}
