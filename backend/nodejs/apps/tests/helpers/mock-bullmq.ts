import sinon from 'sinon';

export interface MockQueue {
  add: sinon.SinonStub;
  addBulk: sinon.SinonStub;
  getJob: sinon.SinonStub;
  getJobs: sinon.SinonStub;
  getJobCounts: sinon.SinonStub;
  removeRepeatable: sinon.SinonStub;
  obliterate: sinon.SinonStub;
  pause: sinon.SinonStub;
  resume: sinon.SinonStub;
  close: sinon.SinonStub;
  getRepeatableJobs: sinon.SinonStub;
  drain: sinon.SinonStub;
  clean: sinon.SinonStub;
}

export interface MockWorker {
  on: sinon.SinonStub;
  close: sinon.SinonStub;
  pause: sinon.SinonStub;
  resume: sinon.SinonStub;
  isRunning: sinon.SinonStub;
}

export function createMockQueue(): MockQueue {
  return {
    add: sinon.stub().resolves({ id: 'mock-job-id', name: 'mock-job' }),
    addBulk: sinon.stub().resolves([]),
    getJob: sinon.stub().resolves(null),
    getJobs: sinon.stub().resolves([]),
    getJobCounts: sinon.stub().resolves({ waiting: 0, active: 0, completed: 0, failed: 0 }),
    removeRepeatable: sinon.stub().resolves(),
    obliterate: sinon.stub().resolves(),
    pause: sinon.stub().resolves(),
    resume: sinon.stub().resolves(),
    close: sinon.stub().resolves(),
    getRepeatableJobs: sinon.stub().resolves([]),
    drain: sinon.stub().resolves(),
    clean: sinon.stub().resolves([]),
  };
}

export function createMockWorker(): MockWorker {
  return {
    on: sinon.stub(),
    close: sinon.stub().resolves(),
    pause: sinon.stub().resolves(),
    resume: sinon.stub().resolves(),
    isRunning: sinon.stub().returns(true),
  };
}
