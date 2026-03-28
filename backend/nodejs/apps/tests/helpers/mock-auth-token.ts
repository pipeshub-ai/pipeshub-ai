import sinon from 'sinon';

export interface MockAuthTokenService {
  verifyToken: sinon.SinonStub;
  verifyScopedToken: sinon.SinonStub;
  generateToken: sinon.SinonStub;
  generateScopedToken: sinon.SinonStub;
}

export function createMockAuthTokenService(): MockAuthTokenService {
  return {
    verifyToken: sinon.stub().resolves({
      userId: 'test-user-id',
      orgId: 'test-org-id',
      email: 'test@example.com',
      iat: Math.floor(Date.now() / 1000),
      exp: Math.floor(Date.now() / 1000) + 3600,
    }),
    verifyScopedToken: sinon.stub().resolves({
      userId: 'test-user-id',
      orgId: 'test-org-id',
      scope: 'token:refresh',
      iat: Math.floor(Date.now() / 1000),
      exp: Math.floor(Date.now() / 1000) + 3600,
    }),
    generateToken: sinon.stub().returns('mock-jwt-token'),
    generateScopedToken: sinon.stub().returns('mock-scoped-token'),
  };
}
