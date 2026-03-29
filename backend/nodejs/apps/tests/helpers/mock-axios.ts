import sinon from 'sinon';

export interface MockAxios {
  get: sinon.SinonStub;
  post: sinon.SinonStub;
  put: sinon.SinonStub;
  patch: sinon.SinonStub;
  delete: sinon.SinonStub;
  request: sinon.SinonStub;
  create: sinon.SinonStub;
}

export function createMockAxios(): MockAxios {
  const instance: MockAxios = {
    get: sinon.stub().resolves({ data: {}, status: 200 }),
    post: sinon.stub().resolves({ data: {}, status: 200 }),
    put: sinon.stub().resolves({ data: {}, status: 200 }),
    patch: sinon.stub().resolves({ data: {}, status: 200 }),
    delete: sinon.stub().resolves({ data: {}, status: 200 }),
    request: sinon.stub().resolves({ data: {}, status: 200 }),
    create: sinon.stub(),
  };
  // create returns a new mock instance
  instance.create.returns(instance);
  return instance;
}
