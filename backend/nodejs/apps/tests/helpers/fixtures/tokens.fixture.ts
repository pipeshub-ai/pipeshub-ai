export const TEST_JWT_SECRET = 'test-jwt-secret-key-for-unit-tests-32chars!';
export const TEST_SCOPED_JWT_SECRET = 'test-scoped-jwt-secret-for-tests-32chars!';

export const VALID_TOKEN_PAYLOAD = {
  userId: 'test-user-id',
  orgId: 'test-org-id',
  email: 'test@example.com',
  firstName: 'Test',
  lastName: 'User',
  fullName: 'Test User',
  accountType: 'BUSINESS',
  role: 'admin',
  iat: Math.floor(Date.now() / 1000),
  exp: Math.floor(Date.now() / 1000) + 3600,
};

export const EXPIRED_TOKEN_PAYLOAD = {
  ...VALID_TOKEN_PAYLOAD,
  iat: Math.floor(Date.now() / 1000) - 7200,
  exp: Math.floor(Date.now() / 1000) - 3600,
};

export const SCOPED_TOKEN_PAYLOAD = {
  userId: 'test-user-id',
  orgId: 'test-org-id',
  scope: 'token:refresh',
  iat: Math.floor(Date.now() / 1000),
  exp: Math.floor(Date.now() / 1000) + 600,
};

export const OAUTH_TOKEN_PAYLOAD = {
  userId: 'test-user-id',
  orgId: 'test-org-id',
  clientId: 'test-client-id',
  grantType: 'authorization_code',
  scopes: ['read', 'write'],
  tokenType: 'oauth',
  iat: Math.floor(Date.now() / 1000),
  exp: Math.floor(Date.now() / 1000) + 3600,
};
