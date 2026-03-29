import sinon, { SinonStub } from 'sinon';
import { Response, NextFunction } from 'express';

export interface MockResponse {
  status: SinonStub;
  json: SinonStub;
  send: SinonStub;
  setHeader: SinonStub;
  getHeader: SinonStub;
  writeHead: SinonStub;
  end: SinonStub;
  headersSent: boolean;
  cookie: SinonStub;
  clearCookie: SinonStub;
  redirect: SinonStub;
  type: SinonStub;
  sendFile: SinonStub;
  download: SinonStub;
  contentType: SinonStub;
}

export function createMockRequest(overrides: Record<string, any> = {}): any {
  const req = {
    headers: {},
    body: {},
    params: {},
    query: {},
    path: '/test',
    method: 'GET',
    ip: '127.0.0.1',
    protocol: 'http',
    originalUrl: '/api/v1/test',
    baseUrl: '/api/v1',
    get: sinon.stub().returns(undefined),
    header: sinon.stub().returns(undefined),
    user: undefined,
    context: undefined,
    tokenPayload: undefined,
    file: undefined,
    files: undefined,
    ...overrides,
  };
  return req;
}

export function createMockResponse(): MockResponse {
  const res: MockResponse = {
    status: sinon.stub(),
    json: sinon.stub(),
    send: sinon.stub(),
    setHeader: sinon.stub(),
    getHeader: sinon.stub(),
    writeHead: sinon.stub(),
    end: sinon.stub(),
    headersSent: false,
    cookie: sinon.stub(),
    clearCookie: sinon.stub(),
    redirect: sinon.stub(),
    type: sinon.stub(),
    sendFile: sinon.stub(),
    download: sinon.stub(),
    contentType: sinon.stub(),
  };
  // Chain: res.status(200).json({}) should work
  res.status.returns(res);
  res.json.returns(res);
  res.send.returns(res);
  res.setHeader.returns(res);
  res.writeHead.returns(res);
  res.type.returns(res);
  res.contentType.returns(res);
  return res;
}

export function createMockNext(): SinonStub {
  return sinon.stub() as SinonStub;
}

export function createAuthenticatedRequest(
  userId: string = 'test-user-id',
  orgId: string = 'test-org-id',
  email: string = 'test@example.com',
  overrides: Record<string, any> = {},
): any {
  return createMockRequest({
    user: {
      userId,
      orgId,
      email,
      firstName: 'Test',
      lastName: 'User',
      fullName: 'Test User',
      accountType: 'BUSINESS',
      role: 'admin',
      iat: Math.floor(Date.now() / 1000),
      exp: Math.floor(Date.now() / 1000) + 3600,
      ...overrides.user,
    },
    ...overrides,
  });
}
