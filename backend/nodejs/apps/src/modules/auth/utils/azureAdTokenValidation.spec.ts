import { expect } from 'chai';
import { extractTenantIdFromToken } from './azureAdTokenValidation';

describe('extractTenantIdFromToken', () => {
  it('extracts tid from valid JWT payload', () => {
    // Create a mock JWT with tid claim
    // JWT structure: header.payload.signature (we only need valid base64 structure)
    const header = Buffer.from(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).toString('base64url');
    const payload = Buffer.from(JSON.stringify({
      tid: '12345678-1234-1234-1234-123456789abc',
      sub: 'user123',
      aud: 'api://default',
    })).toString('base64url');
    const signature = 'fake-signature';
    const token = `${header}.${payload}.${signature}`;

    const result = extractTenantIdFromToken(token);

    expect(result).to.equal('12345678-1234-1234-1234-123456789abc');
  });

  it('returns null when tid claim is missing', () => {
    // JWT without tid claim
    const header = Buffer.from(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).toString('base64url');
    const payload = Buffer.from(JSON.stringify({
      sub: 'user123',
      aud: 'api://default',
      email: 'user@example.com',
    })).toString('base64url');
    const signature = 'fake-signature';
    const token = `${header}.${payload}.${signature}`;

    const result = extractTenantIdFromToken(token);

    expect(result).to.be.null;
  });

  it('returns null for null token input', () => {
    const result = extractTenantIdFromToken(null as any);

    expect(result).to.be.null;
  });

  it('returns null for undefined token input', () => {
    const result = extractTenantIdFromToken(undefined as any);

    expect(result).to.be.null;
  });

  it('returns null for invalid JWT format - not base64', () => {
    const invalidToken = 'not.a.valid.jwt.token';

    const result = extractTenantIdFromToken(invalidToken);

    expect(result).to.be.null;
  });

  it('returns null for invalid JWT format - wrong structure', () => {
    // Only two parts instead of three
    const invalidToken = 'header.payload';

    const result = extractTenantIdFromToken(invalidToken);

    expect(result).to.be.null;
  });

  it('returns null for empty string token', () => {
    const result = extractTenantIdFromToken('');

    expect(result).to.be.null;
  });

  it('returns null for JWT with malformed payload', () => {
    const header = Buffer.from(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).toString('base64url');
    const payload = 'not-valid-json';
    const signature = 'fake-signature';
    const token = `${header}.${payload}.${signature}`;

    const result = extractTenantIdFromToken(token);

    expect(result).to.be.null;
  });
});
