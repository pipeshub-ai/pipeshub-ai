export const TEST_ORG = {
  _id: 'test-org-id',
  orgId: 'test-org-id',
  orgName: 'Test Organization',
  domain: 'example.com',
  accountType: 'BUSINESS',
  isActive: true,
  createdAt: new Date('2025-01-01T00:00:00Z'),
  updatedAt: new Date('2025-01-01T00:00:00Z'),
};

export const TEST_INDIVIDUAL_ORG = {
  _id: 'individual-org-id',
  orgId: 'individual-org-id',
  orgName: 'Individual Org',
  domain: 'individual.com',
  accountType: 'INDIVIDUAL',
  isActive: true,
  createdAt: new Date('2025-01-01T00:00:00Z'),
  updatedAt: new Date('2025-01-01T00:00:00Z'),
};

export const TEST_INACTIVE_ORG = {
  ...TEST_ORG,
  _id: 'inactive-org-id',
  orgId: 'inactive-org-id',
  isActive: false,
};
