import { at, oid } from '../roundtrip';

export const ORG_A = oid();
export const ORG_B = oid();
export const ADMIN_A = oid();
export const MEMBER_A = oid();
export const SERVICE_A = oid();
export const USER_B = oid();
export const GONE_B = oid();
export const OLD_B = oid();

export const identityDocs = (): Record<string, unknown[]> => ({
  org: [
    {
      _id: ORG_A,
      slug: 'org-1001',
      registeredName: 'Acme Ltd',
      shortName: 'Acme',
      domain: 'acme.example',
      contactEmail: 'admin@acme.example',
      accountType: 'business',
      permanentAddress: { addressLine1: '1 Main St', city: 'Springfield' },
      onBoardingStatus: 'configured',
      isDeleted: false,
      createdAt: at('2026-01-02T03:04:05.678Z'),
      updatedAt: at('2026-01-03T03:04:05.678Z'),
      __v: 0,
    },
    {
      _id: ORG_B,
      slug: 'beta-labs',
      domain: 'beta.example',
      contactEmail: 'o@beta.example',
      accountType: 'individual',
      createdAt: at('2026-02-02T00:00:00.000Z'),
      updatedAt: at('2026-02-02T00:00:00.000Z'),
      __v: 2,
    },
  ],
  'org-logos': [
      { _id: oid(), orgId: ORG_A, logo: 'iVBORw0KGgo=', mimeType: 'image/png', __v: 0 },
      { _id: oid(), orgId: ORG_B, logo: null, __v: 1 },
    ],
  users: [
    {
      _id: ADMIN_A, slug: 'user-1001', orgId: ORG_A, fullName: 'Ada Admin', firstName: 'Ada', lastName: 'Admin', email: 'ada@acme.example',
      hasLoggedIn: true, kind: 'human', isDisabled: false, role: 'admin', isDeleted: false,
      address: { addressLine1: '1 Main St', city: 'Springfield', country: 'US' },
      createdAt: at('2026-01-02T03:04:05.678Z'), updatedAt: at('2026-01-05T00:00:00.000Z'), __v: 0,
    },
    {
      _id: MEMBER_A, slug: 'user-1002', orgId: ORG_A, fullName: 'Zoë\u0000 Ünï 山田', email: 'Zoe@Acme.example', mobile: '+1 555 0100', designation: 'Engineer',
      createdAt: at('2026-01-03T00:00:00.000Z'), updatedAt: at('2026-01-03T00:00:00.000Z'), __v: 3,
    },
    {
      _id: SERVICE_A, slug: 'user-1003', orgId: ORG_A, fullName: 'ci bot', email: 'ci@acme.example', kind: 'service', description: 'builds', isDisabled: true,
      restoreOpId: 'op-1', role: 'member', hasLoggedIn: false, isDeleted: false,
      createdAt: at('2026-01-04T00:00:00.000Z'), updatedAt: at('2026-01-04T00:00:00.000Z'), __v: 0,
    },
    {
      _id: USER_B, slug: 'user-1004', orgId: ORG_B, fullName: 'Zoe B', email: 'zoe@acme.example', kind: 'human', role: 'admin', hasLoggedIn: true, isDeleted: false, isDisabled: false,
      createdAt: at('2026-02-03T00:00:00.000Z'), updatedAt: at('2026-02-03T00:00:00.000Z'), __v: 0,
    },
    {
      _id: GONE_B, slug: 'user-1005', orgId: ORG_B, email: 'gone@beta.example', kind: 'human', role: 'member', hasLoggedIn: false, isDisabled: false,
      isDeleted: true, deletedBy: String(USER_B), createdAt: at('2026-02-04T00:00:00.000Z'), updatedAt: at('2026-02-05T00:00:00.000Z'), __v: 1,
    },
  ],
  userGroups: [
    {
      _id: oid(), slug: 'usergroup-1001', name: 'admin', type: 'admin', orgId: ORG_A, users: [ADMIN_A, ADMIN_A, MEMBER_A], isDeleted: false,
      createdAt: at('2026-01-02T03:04:05.700Z'), updatedAt: at('2026-01-02T03:04:05.700Z'), __v: 2,
    },
    {
      _id: oid(), slug: 'usergroup-1002', name: 'everyone', type: 'everyone', orgId: ORG_A, users: [MEMBER_A, ADMIN_A, SERVICE_A], isDeleted: false,
      createdAt: at('2026-01-02T03:04:05.710Z'), updatedAt: at('2026-01-02T03:04:05.710Z'), __v: 0,
    },
    {
      _id: oid(), slug: 'usergroup-1003', name: 'Empty \u2603', type: 'custom', orgId: ORG_B, users: [],
      createdAt: at('2026-02-02T00:00:00.000Z'), updatedAt: at('2026-02-02T00:00:00.000Z'), __v: 0,
    },
    {
      _id: oid(), slug: 'usergroup-1004', name: 'old', type: 'standard', orgId: ORG_B, users: [USER_B], isDeleted: true, deletedBy: USER_B,
      createdAt: at('1969-12-31T23:59:59.500Z'), updatedAt: at('2026-02-02T00:00:00.000Z'), __v: 1,
    },
  ],
  userCredentials: [
    {
      _id: oid(), userId: String(ADMIN_A), orgId: String(ORG_A), ipAddress: '10.0.0.1', hashedPassword: '$2b$10$adminhash', forceNewPasswordGeneration: false,
      wrongCredentialCount: 2, isBlocked: true, blockExpiresAt: at('2026-03-01T00:00:00.000Z'), isDeleted: false,
      createdAt: at('2026-01-02T03:04:05.678Z'), updatedAt: at('2026-01-06T00:00:00.000Z'), __v: 0,
    },
    {
      _id: oid(), userId: String(MEMBER_A), orgId: String(ORG_A), ipAddress: '10.0.0.2', hashedPassword: '$2b$10$oldhash', otpValidity: 1767225600000, hashedOTP: 'otp1',
      createdAt: at('2026-01-03T00:00:00.000Z'), updatedAt: at('2026-01-03T00:00:00.000Z'), __v: 0,
    },
    {
      _id: oid(), userId: String(MEMBER_A), orgId: String(ORG_A), ipAddress: '10.0.0.7', hashedPassword: '$2b$10$newerhash', wrongCredentialCount: 1,
      createdAt: at('2026-01-20T00:00:00.000Z'), updatedAt: at('2026-01-20T00:00:00.000Z'), __v: 0,
    },
    {
      _id: oid(), userId: String(USER_B), orgId: String(ORG_B), ipAddress: '10.0.0.3', hashedPassword: '$2b$10$newhash', forceNewPasswordGeneration: true, wrongCredentialCount: 0,
      isBlocked: false, isDeleted: false, createdAt: at('2026-02-03T00:00:00.000Z'), updatedAt: at('2026-02-10T00:00:00.000Z'), __v: 0,
    },
    {
      _id: oid(), userId: String(SERVICE_A), orgId: String(ORG_A), ipAddress: '10.0.0.4', hashedPassword: 'x', isDeleted: true,
      createdAt: at('2026-01-04T00:00:00.000Z'), updatedAt: at('2026-01-04T00:00:00.000Z'), __v: 0,
    },
    {
      _id: oid(), userId: String(oid()), orgId: String(ORG_A), ipAddress: '10.0.0.5', hashedPassword: 'orphan',
      createdAt: at('2026-01-04T00:00:00.000Z'), updatedAt: at('2026-01-04T00:00:00.000Z'), __v: 0,
    },
    {
      _id: oid(), userId: String(GONE_B), orgId: String(ORG_B), ipAddress: '10.0.0.6', hashedPassword: 'removed-user',
      createdAt: at('2026-02-04T00:00:00.000Z'), updatedAt: at('2026-02-04T00:00:00.000Z'), __v: 0,
    },
  ],
  orgAuthConfig: [
    {
      _id: oid(), orgId: ORG_A, authSteps: [{ order: 1, allowedMethods: [{ type: 'password' }, { type: 'google' }] }, { order: 2, allowedMethods: [{ type: 'otp' }] }],
      isDeleted: false, createdAt: at('2026-01-02T03:04:05.800Z'), updatedAt: at('2026-01-02T03:04:05.800Z'), __v: 0,
    },
    {
      _id: oid(), orgId: ORG_B, domain: 'beta.example', authSteps: [{ order: 1, allowedMethods: [{ type: 'samlSso' }] }],
      createdAt: at('2026-02-02T00:00:00.000Z'), updatedAt: at('2026-02-02T00:00:00.000Z'), __v: 4,
    },
  ],
  userActivities: [
    {
      _id: oid(), email: 'ada@acme.example', userId: ADMIN_A, orgId: ORG_A, activityType: 'login', loginMode: 'PASSWORD', ipAddress: '10.0.0.1', isDeleted: false,
      createdAt: at('2026-01-06T00:00:00.000Z'), updatedAt: at('2026-01-06T00:00:00.000Z'), __v: 0,
    },
    {
      _id: oid(), orgId: ORG_B, userId: USER_B, activityType: 'logout', ipAddress: '10.0.0.3',
      createdAt: at('2026-02-11T00:00:00.000Z'), updatedAt: at('2026-02-11T00:00:00.000Z'), __v: 0,
    },
    {
      _id: oid(), email: 'nobody@example.com', activityType: 'wrong otp', ipAddress: '10.9.9.9', createdAt: at('2026-02-12T00:00:00.000Z'), updatedAt: at('2026-02-12T00:00:00.000Z'), __v: 0,
    },
  ],
  usedPasswordResetLinks: [
    {
      _id: oid(), linkHash: 'a'.repeat(64), userId: String(ADMIN_A), orgId: String(ORG_A), expiresAt: at('2026-01-07T00:00:00.000Z'),
      createdAt: at('2026-01-06T00:00:00.000Z'), updatedAt: at('2026-01-06T00:00:00.000Z'), __v: 0,
    },
  ],
  'user-dps': [
    { _id: oid(), userId: ADMIN_A, orgId: ORG_A, pic: 'aGVsbG8=', picUrl: 'https://cdn.example/a.png', mimeType: 'image/png', __v: 0 },
    { _id: oid(), userId: USER_B, orgId: ORG_B, pic: null, __v: 2 },
  ],
  counters: [
    { _id: String(oid()), name: 'Org', seq: 1002 },
    { _id: String(oid()), name: 'User', seq: 1005 },
    { _id: String(oid()), name: 'UserGroup', seq: 1004 },
    { _id: String(oid()), name: 'OAuthApp', seq: 1001 },
  ],
});
