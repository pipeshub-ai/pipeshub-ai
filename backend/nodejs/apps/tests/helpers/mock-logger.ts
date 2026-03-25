import sinon from 'sinon';

export interface MockLogger {
  info: sinon.SinonStub;
  error: sinon.SinonStub;
  warn: sinon.SinonStub;
  debug: sinon.SinonStub;
  logRequest: sinon.SinonStub;
  updateDefaultMeta: sinon.SinonStub;
}

export function createMockLogger(): MockLogger {
  return {
    info: sinon.stub(),
    error: sinon.stub(),
    warn: sinon.stub(),
    debug: sinon.stub(),
    logRequest: sinon.stub(),
    updateDefaultMeta: sinon.stub(),
  };
}
