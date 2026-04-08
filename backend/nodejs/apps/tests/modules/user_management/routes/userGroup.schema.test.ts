import 'reflect-metadata';
import { expect } from 'chai';
import {
  AddUsersToGroupsResponseSchema,
  AddUsersToGroupsValidationSchema,
  CreateUserGroupResponseSchema,
  GetAllUserGroupsResponseSchema,
  GetGroupStatisticsResponseSchema,
  GetGroupsForUserResponseSchema,
  GetUsersInGroupResponseSchema,
  RemoveUsersFromGroupsResponseSchema,
  RemoveUsersFromGroupsValidationSchema,
  UserGroupDocumentResponseSchema,
} from '../../../../src/modules/user_management/schemas/userGroup.schemas';

describe('CreateUserGroupResponseSchema (Zod)', () => {
  const valid = {
    name: 'Test grp',
    type: 'custom',
    orgId: '69cd0daf863a6899015af270',
    users: [],
    isDeleted: false,
    _id: '69cd17ffbea35d8fcaed7701',
    createdAt: '2026-04-01T13:05:03.062Z',
    updatedAt: '2026-04-01T13:05:03.062Z',
    slug: 'usergroup-4',
    __v: 0,
  };

  it('should accept POST /user-groups success body', () => {
    const result = CreateUserGroupResponseSchema.safeParse(valid);
    expect(result.success).to.be.true;
  });

  it('should accept users as id strings', () => {
    const result = CreateUserGroupResponseSchema.safeParse({
      ...valid,
      users: ['507f1f77bcf86cd799439011'],
    });
    expect(result.success).to.be.true;
  });

  it('should passthrough extra fields', () => {
    const result = CreateUserGroupResponseSchema.safeParse({
      ...valid,
      deletedBy: null,
    });
    expect(result.success).to.be.true;
  });

  it('should reject invalid type enum', () => {
    const result = CreateUserGroupResponseSchema.safeParse({
      ...valid,
      type: 'invalid',
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing slug', () => {
    const { slug, ...rest } = valid;
    const result = CreateUserGroupResponseSchema.safeParse(rest);
    expect(result.success).to.be.false;
  });
});

describe('UserGroupDocumentResponseSchema (Zod)', () => {
  it('should match single-group GET/PUT/DELETE shape (alias of create schema)', () => {
    const doc = {
      name: 'admin',
      type: 'admin' as const,
      orgId: '69cd0daf863a6899015af270',
      users: ['69cd0daf863a6899015af272'],
      isDeleted: false,
      _id: '69cd0daf863a6899015af274',
      createdAt: '2026-04-01T12:21:03.665Z',
      updatedAt: '2026-04-01T12:21:03.665Z',
      slug: 'usergroup-1',
      __v: 0,
    };
    expect(UserGroupDocumentResponseSchema.safeParse(doc).success).to.be.true;
    expect(CreateUserGroupResponseSchema.safeParse(doc).success).to.be.true;
  });
});

describe('GetAllUserGroupsResponseSchema (Zod)', () => {
  const item = {
    _id: '69cd0daf863a6899015af274',
    name: 'admin',
    type: 'admin' as const,
    orgId: '69cd0daf863a6899015af270',
    users: ['69cd0daf863a6899015af272'],
    isDeleted: false,
    createdAt: '2026-04-01T12:21:03.665Z',
    updatedAt: '2026-04-01T12:21:03.665Z',
    slug: 'usergroup-1',
    __v: 0,
  };

  it('should accept GET /user-groups array body', () => {
    const result = GetAllUserGroupsResponseSchema.safeParse([item]);
    expect(result.success).to.be.true;
  });

  it('should accept empty array', () => {
    const result = GetAllUserGroupsResponseSchema.safeParse([]);
    expect(result.success).to.be.true;
  });

  it('should reject when one element is invalid', () => {
    const result = GetAllUserGroupsResponseSchema.safeParse([item, { _id: 'bad' }]);
    expect(result.success).to.be.false;
  });
});

describe('AddUsersToGroups / RemoveUsersFromGroups (Zod)', () => {
  const validBody = {
    userIds: ['69cd0daf863a6899015af272', '69cd0e4a863a6899015af331'],
    groupIds: ['69cd17ffbea35d8fcaed7701'],
  };

  it('AddUsersToGroupsValidationSchema should accept valid body', () => {
    const result = AddUsersToGroupsValidationSchema.safeParse({
      body: validBody,
      query: {},
      params: {},
      headers: {},
    });
    expect(result.success).to.be.true;
  });

  it('RemoveUsersFromGroupsValidationSchema should accept same body shape', () => {
    const result = RemoveUsersFromGroupsValidationSchema.safeParse({
      body: validBody,
      query: {},
      params: {},
      headers: {},
    });
    expect(result.success).to.be.true;
  });

  it('should reject invalid ObjectId in userIds', () => {
    const result = AddUsersToGroupsValidationSchema.safeParse({
      body: { ...validBody, userIds: ['not-valid'] },
      query: {},
      params: {},
      headers: {},
    });
    expect(result.success).to.be.false;
  });

  it('AddUsersToGroupsResponseSchema', () => {
    expect(
      AddUsersToGroupsResponseSchema.safeParse({
        message: 'Users added to groups successfully',
      }).success,
    ).to.be.true;
  });

  it('RemoveUsersFromGroupsResponseSchema', () => {
    expect(
      RemoveUsersFromGroupsResponseSchema.safeParse({
        message: 'Users removed from groups successfully',
      }).success,
    ).to.be.true;
  });
});

describe('GetUsersInGroupResponseSchema / GetGroupsForUserResponseSchema (Zod)', () => {
  it('GetUsersInGroupResponseSchema should accept users id list', () => {
    const result = GetUsersInGroupResponseSchema.safeParse({
      users: [
        '69cd0daf863a6899015af272',
        '69cd0e4a863a6899015af331',
        '69cd0ee6a9a42375da641267',
      ],
    });
    expect(result.success).to.be.true;
  });

  it('GetUsersInGroupResponseSchema should accept empty users', () => {
    expect(GetUsersInGroupResponseSchema.safeParse({ users: [] }).success).to.be
      .true;
  });

  it('GetUsersInGroupResponseSchema should reject invalid id', () => {
    expect(
      GetUsersInGroupResponseSchema.safeParse({ users: ['bad'] }).success,
    ).to.be.false;
  });

  it('GetGroupsForUserResponseSchema should accept group summaries', () => {
    const result = GetGroupsForUserResponseSchema.safeParse([
      {
        _id: '69cd0daf863a6899015af274',
        name: 'admin',
        type: 'admin',
      },
      {
        _id: '69cd0daf863a6899015af275',
        name: 'everyone',
        type: 'everyone',
      },
      {
        _id: '69cd17ffbea35d8fcaed7701',
        name: 'Test grp',
        type: 'custom',
      },
    ]);
    expect(result.success).to.be.true;
  });

  it('GetGroupsForUserResponseSchema should reject invalid type', () => {
    const result = GetGroupsForUserResponseSchema.safeParse([
      { _id: '69cd0daf863a6899015af274', name: 'x', type: 'invalid' },
    ]);
    expect(result.success).to.be.false;
  });
});

describe('GetGroupStatisticsResponseSchema (Zod)', () => {
  const sample = [
    {
      _id: 'everyone',
      count: 1,
      totalUsers: 3,
      avgUsers: 3,
    },
    {
      _id: 'admin',
      count: 1,
      totalUsers: 1,
      avgUsers: 1,
    },
    {
      _id: 'standard',
      count: 1,
      totalUsers: 0,
      avgUsers: 0,
    },
    {
      _id: 'Test grp',
      count: 1,
      totalUsers: 3,
      avgUsers: 3,
    },
  ];

  it('should accept GET /stats/list body', () => {
    expect(GetGroupStatisticsResponseSchema.safeParse(sample).success).to.be.true;
  });

  it('should accept empty array', () => {
    expect(GetGroupStatisticsResponseSchema.safeParse([]).success).to.be.true;
  });

  it('should reject row with wrong types', () => {
    expect(
      GetGroupStatisticsResponseSchema.safeParse([
        { _id: 'x', count: '1', totalUsers: 1, avgUsers: 1 },
      ]).success,
    ).to.be.false;
  });
});
