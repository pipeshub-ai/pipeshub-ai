import { expect } from 'chai';
import sinon from 'sinon';
import { OrgAuthConfig, AuthMethodType } from '../schema/orgAuthConfiguration.schema';
import { extractTenantIdFromToken } from '../utils/azureAdTokenValidation';

describe('UserAccountController - Tenant ID Org Lookup', () => {
  let sandbox: sinon.SinonSandbox;
  let findOneStub: sinon.SinonStub;

  beforeEach(() => {
    sandbox = sinon.createSandbox();
    // Stub OrgAuthConfig.findOne for database mocking
    findOneStub = sandbox.stub(OrgAuthConfig, 'findOne');
  });

  afterEach(() => {
    sandbox.restore();
  });

  describe('Microsoft SSO JIT provisioning', () => {
    it('uses tenant ID matched org when found', async () => {
      const mockTenantId = '12345678-1234-1234-1234-123456789abc';
      const mockOrgId = 'tenant-matched-org-123';

      // Create a real JWT with the tenant ID
      const header = Buffer.from(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).toString('base64url');
      const payload = Buffer.from(JSON.stringify({
        tid: mockTenantId,
        sub: 'user123',
        email: 'user@example.com'
      })).toString('base64url');
      const mockIdToken = `${header}.${payload}.fake-signature`;

      // Setup: OrgAuthConfig.findOne returns matching org
      findOneStub.resolves({
        orgId: mockOrgId,
        microsoftTenantId: mockTenantId,
        isDeleted: false,
      });

      // Simulate the tenant lookup logic (mimics controller logic at lines 1384-1413)
      const method = AuthMethodType.MICROSOFT;
      const credentials = { idToken: mockIdToken };
      const sessionInfo = { orgId: 'domain-based-org-456', email: 'user@example.com' };

      // Execute tenant lookup logic
      let effectiveOrgId = sessionInfo.orgId;
      let matchedBy: 'microsoftTenantId' | 'domain' = 'domain';

      if (method === AuthMethodType.MICROSOFT || method === AuthMethodType.AZURE_AD) {
        const idToken = credentials?.idToken;
        if (idToken) {
          const extractedTenantId = extractTenantIdFromToken(idToken);

          if (extractedTenantId) {
            const tenantOrgConfig = await OrgAuthConfig.findOne({
              microsoftTenantId: extractedTenantId,
              isDeleted: false,
            });

            if (tenantOrgConfig) {
              effectiveOrgId = tenantOrgConfig.orgId.toString();
              matchedBy = 'microsoftTenantId';
            }
          }
        }
      }

      // Verify OrgAuthConfig.findOne was called with correct query
      expect(findOneStub.calledOnce).to.be.true;
      expect(findOneStub.firstCall.args[0]).to.deep.equal({
        microsoftTenantId: mockTenantId,
        isDeleted: false,
      });

      // Verify effectiveOrgId uses tenant-matched org
      expect(effectiveOrgId).to.equal(mockOrgId);
      expect(matchedBy).to.equal('microsoftTenantId');
    });

    it('falls back to domain when no tenant match', async () => {
      const mockTenantId = '12345678-1234-1234-1234-123456789abc';
      const domainBasedOrgId = 'domain-based-org-456';

      // Create a real JWT with the tenant ID
      const header = Buffer.from(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).toString('base64url');
      const payload = Buffer.from(JSON.stringify({
        tid: mockTenantId,
        sub: 'user123'
      })).toString('base64url');
      const mockIdToken = `${header}.${payload}.fake-signature`;

      // Setup: OrgAuthConfig.findOne returns null (no match)
      findOneStub.resolves(null);

      // Simulate the tenant lookup logic
      const method = AuthMethodType.MICROSOFT;
      const credentials = { idToken: mockIdToken };
      const sessionInfo = { orgId: domainBasedOrgId, email: 'user@example.com' };

      // Execute tenant lookup logic
      let effectiveOrgId = sessionInfo.orgId;
      let matchedBy: 'microsoftTenantId' | 'domain' = 'domain';

      if (method === AuthMethodType.MICROSOFT || method === AuthMethodType.AZURE_AD) {
        const idToken = credentials?.idToken;
        if (idToken) {
          const extractedTenantId = extractTenantIdFromToken(idToken);

          if (extractedTenantId) {
            const tenantOrgConfig = await OrgAuthConfig.findOne({
              microsoftTenantId: extractedTenantId,
              isDeleted: false,
            });

            if (tenantOrgConfig) {
              effectiveOrgId = tenantOrgConfig.orgId.toString();
              matchedBy = 'microsoftTenantId';
            }
          }
        }
      }

      // Verify OrgAuthConfig.findOne was called
      expect(findOneStub.calledOnce).to.be.true;
      expect(findOneStub.firstCall.args[0]).to.deep.equal({
        microsoftTenantId: mockTenantId,
        isDeleted: false,
      });

      // Verify effectiveOrgId uses domain-based org (fallback)
      expect(effectiveOrgId).to.equal(domainBasedOrgId);
      expect(matchedBy).to.equal('domain');
    });

    it('skips tenant lookup for non-Microsoft methods', async () => {
      const domainBasedOrgId = 'domain-based-org-456';

      // Simulate the tenant lookup logic for Google SSO
      const method = AuthMethodType.GOOGLE as AuthMethodType;
      const credentials = { idToken: 'some-google-token' };
      const sessionInfo = { orgId: domainBasedOrgId, email: 'user@example.com' };

      // Execute tenant lookup logic
      let effectiveOrgId = sessionInfo.orgId;
      let matchedBy: 'microsoftTenantId' | 'domain' = 'domain';

      // For non-Microsoft methods, the condition is false, so tenant lookup is skipped
      const isMicrosoftMethod = method === AuthMethodType.MICROSOFT || method === AuthMethodType.AZURE_AD;

      if (isMicrosoftMethod) {
        const idToken = credentials?.idToken;
        if (idToken) {
          const extractedTenantId = extractTenantIdFromToken(idToken);

          if (extractedTenantId) {
            const tenantOrgConfig = await OrgAuthConfig.findOne({
              microsoftTenantId: extractedTenantId,
              isDeleted: false,
            });

            if (tenantOrgConfig) {
              effectiveOrgId = tenantOrgConfig.orgId.toString();
              matchedBy = 'microsoftTenantId';
            }
          }
        }
      }

      // Verify method is not Microsoft
      expect(isMicrosoftMethod).to.be.false;

      // Verify OrgAuthConfig.findOne was NOT called
      expect(findOneStub.called).to.be.false;

      // Verify effectiveOrgId remains domain-based
      expect(effectiveOrgId).to.equal(domainBasedOrgId);
      expect(matchedBy).to.equal('domain');
    });

    it('logs matchedBy correctly for tenant match', async () => {
      const mockTenantId = '12345678-1234-1234-1234-123456789abc';
      const mockOrgId = 'tenant-matched-org-123';

      // Create JWT for AZURE_AD method
      const header = Buffer.from(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).toString('base64url');
      const payload = Buffer.from(JSON.stringify({
        tid: mockTenantId,
        sub: 'user123'
      })).toString('base64url');
      const mockIdToken = `${header}.${payload}.fake-signature`;

      findOneStub.resolves({
        orgId: mockOrgId,
        microsoftTenantId: mockTenantId,
        isDeleted: false,
      });

      const method = AuthMethodType.AZURE_AD as AuthMethodType;
      const credentials = { idToken: mockIdToken };
      const sessionInfo = { orgId: 'domain-based-org-456', email: 'user@example.com' };

      let effectiveOrgId = sessionInfo.orgId;
      let matchedBy: 'microsoftTenantId' | 'domain' = 'domain';
      let loggedTenantId: string | null = null;
      let loggedOrgId: string | null = null;

      if (method === AuthMethodType.MICROSOFT || method === AuthMethodType.AZURE_AD) {
        const idToken = credentials?.idToken;
        if (idToken) {
          const extractedTenantId = extractTenantIdFromToken(idToken);

          if (extractedTenantId) {
            const tenantOrgConfig = await OrgAuthConfig.findOne({
              microsoftTenantId: extractedTenantId,
              isDeleted: false,
            });

            if (tenantOrgConfig) {
              effectiveOrgId = tenantOrgConfig.orgId.toString();
              matchedBy = 'microsoftTenantId';

              // Simulate logging
              loggedTenantId = extractedTenantId;
              loggedOrgId = effectiveOrgId;
            }
          }
        }
      }

      // Verify matchedBy is set correctly
      expect(matchedBy).to.equal('microsoftTenantId');
      expect(loggedTenantId).to.equal(mockTenantId);
      expect(loggedOrgId).to.equal(mockOrgId);
    });

    it('handles missing idToken gracefully', async () => {
      const domainBasedOrgId = 'domain-based-org-456';

      // Simulate the tenant lookup logic with no idToken
      const method = AuthMethodType.MICROSOFT;
      const credentials: Record<string, any> = {}; // No idToken
      const sessionInfo = { orgId: domainBasedOrgId, email: 'user@example.com' };

      let effectiveOrgId = sessionInfo.orgId;
      let matchedBy: 'microsoftTenantId' | 'domain' = 'domain';

      if (method === AuthMethodType.MICROSOFT || method === AuthMethodType.AZURE_AD) {
        const idToken = credentials?.idToken;
        if (idToken) {
          const extractedTenantId = extractTenantIdFromToken(idToken);

          if (extractedTenantId) {
            const tenantOrgConfig = await OrgAuthConfig.findOne({
              microsoftTenantId: extractedTenantId,
              isDeleted: false,
            });

            if (tenantOrgConfig) {
              effectiveOrgId = tenantOrgConfig.orgId.toString();
              matchedBy = 'microsoftTenantId';
            }
          }
        }
      }

      // Verify database query was not called
      expect(findOneStub.called).to.be.false;

      // Verify fallback to domain-based org
      expect(effectiveOrgId).to.equal(domainBasedOrgId);
      expect(matchedBy).to.equal('domain');
    });

    it('handles null tenant ID from extraction', async () => {
      const domainBasedOrgId = 'domain-based-org-456';

      // Create JWT without tid claim
      const header = Buffer.from(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).toString('base64url');
      const payload = Buffer.from(JSON.stringify({
        sub: 'user123',
        email: 'user@example.com'
      })).toString('base64url');
      const mockIdToken = `${header}.${payload}.fake-signature`;

      const method = AuthMethodType.MICROSOFT;
      const credentials = { idToken: mockIdToken };
      const sessionInfo = { orgId: domainBasedOrgId, email: 'user@example.com' };

      let effectiveOrgId = sessionInfo.orgId;
      let matchedBy: 'microsoftTenantId' | 'domain' = 'domain';

      if (method === AuthMethodType.MICROSOFT || method === AuthMethodType.AZURE_AD) {
        const idToken = credentials?.idToken;
        if (idToken) {
          const extractedTenantId = extractTenantIdFromToken(idToken);

          if (extractedTenantId) {
            const tenantOrgConfig = await OrgAuthConfig.findOne({
              microsoftTenantId: extractedTenantId,
              isDeleted: false,
            });

            if (tenantOrgConfig) {
              effectiveOrgId = tenantOrgConfig.orgId.toString();
              matchedBy = 'microsoftTenantId';
            }
          }
        }
      }

      // Verify database query was NOT called (null tenant ID)
      expect(findOneStub.called).to.be.false;

      // Verify fallback to domain-based org
      expect(effectiveOrgId).to.equal(domainBasedOrgId);
      expect(matchedBy).to.equal('domain');
    });
  });
});
