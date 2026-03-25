import sinon from 'sinon';

export interface MockTransport {
  sendMail: sinon.SinonStub;
  verify: sinon.SinonStub;
  close: sinon.SinonStub;
}

export function createMockTransport(): MockTransport {
  return {
    sendMail: sinon.stub().resolves({
      messageId: 'mock-message-id',
      accepted: ['test@example.com'],
      rejected: [],
    }),
    verify: sinon.stub().resolves(true),
    close: sinon.stub(),
  };
}
