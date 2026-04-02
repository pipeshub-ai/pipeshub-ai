import sinon from 'sinon';

export interface MockKeyValueStoreService {
  connect: sinon.SinonStub;
  disconnect: sinon.SinonStub;
  isConnected: sinon.SinonStub;
  healthCheck: sinon.SinonStub;
  createKey: sinon.SinonStub;
  updateValue: sinon.SinonStub;
  getKey: sinon.SinonStub;
  deleteKey: sinon.SinonStub;
  getAllKeys: sinon.SinonStub;
  watchKey: sinon.SinonStub;
  listKeysInDirectory: sinon.SinonStub;
  compareAndSet: sinon.SinonStub;
  publishCacheInvalidation: sinon.SinonStub;
  get: sinon.SinonStub;
  set: sinon.SinonStub;
  delete: sinon.SinonStub;
}

export function createMockKeyValueStoreService(): MockKeyValueStoreService {
  return {
    connect: sinon.stub().resolves(),
    disconnect: sinon.stub().resolves(),
    isConnected: sinon.stub().returns(true),
    healthCheck: sinon.stub().resolves(true),
    createKey: sinon.stub().resolves(),
    updateValue: sinon.stub().resolves(),
    getKey: sinon.stub().resolves(null),
    deleteKey: sinon.stub().resolves(),
    getAllKeys: sinon.stub().resolves([]),
    watchKey: sinon.stub().resolves(),
    listKeysInDirectory: sinon.stub().resolves([]),
    compareAndSet: sinon.stub().resolves(true),
    publishCacheInvalidation: sinon.stub().resolves(),
    get: sinon.stub().resolves(null),
    set: sinon.stub().resolves(),
    delete: sinon.stub().resolves(),
  };
}
