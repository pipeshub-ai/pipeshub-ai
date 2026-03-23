export const TEST_USER = {
  _id: 'test-user-id',
  userId: 'test-user-id',
  email: 'test@example.com',
  firstName: 'Test',
  lastName: 'User',
  fullName: 'Test User',
  orgId: 'test-org-id',
  accountType: 'BUSINESS',
  role: 'admin',
  isActive: true,
  createdAt: new Date('2025-01-01T00:00:00Z'),
  updatedAt: new Date('2025-01-01T00:00:00Z'),
};

export const TEST_USER_2 = {
  _id: 'test-user-id-2',
  userId: 'test-user-id-2',
  email: 'user2@example.com',
  firstName: 'Jane',
  lastName: 'Doe',
  fullName: 'Jane Doe',
  orgId: 'test-org-id',
  accountType: 'BUSINESS',
  role: 'member',
  isActive: true,
  createdAt: new Date('2025-01-02T00:00:00Z'),
  updatedAt: new Date('2025-01-02T00:00:00Z'),
};

export const TEST_INDIVIDUAL_USER = {
  _id: 'individual-user-id',
  userId: 'individual-user-id',
  email: 'individual@example.com',
  firstName: 'Solo',
  lastName: 'User',
  fullName: 'Solo User',
  orgId: 'individual-org-id',
  accountType: 'INDIVIDUAL',
  role: 'admin',
  isActive: true,
  createdAt: new Date('2025-01-03T00:00:00Z'),
  updatedAt: new Date('2025-01-03T00:00:00Z'),
};

export const TEST_INACTIVE_USER = {
  ...TEST_USER,
  _id: 'inactive-user-id',
  userId: 'inactive-user-id',
  email: 'inactive@example.com',
  isActive: false,
};
