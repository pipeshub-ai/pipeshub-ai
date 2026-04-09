import 'reflect-metadata';
import { expect } from 'chai';
import {
  GetAllUsersListItemSchema,
  GetAllUsersResponseSchema,
  GetAllUsersBlockedListItemSchema,
  GetAllUsersBlockedResponseSchema,
  GetAllUsersWithGroupsResponseSchema,
  CreateResponseSchema,
  DeleteResponseSchema,
  DisplayPictureErrorResponseSchema,
  RemoveDisplayPictureResponseSchema,
  AdminCheckResponseSchema,
  HealthResponseSchema,
  InternalAdminResponseSchema,
  GetUserByIdResponseSchema,
  InternalLookupResponseSchema,
  CheckUserExistsByEmailResponseSchema,
  UpdateEmailResponseSchema,
  UpdatePutResponseSchema,
  UpdateFirstNameResponseSchema,
  UpdateFullNameResponseSchema,
  UpdateLastNameResponseSchema,
  UpdateDesignationResponseSchema,
  GetEmailByIdResponseSchema,
  GetEmailByIdValidationSchema,
  BulkInviteBodySchema,
  BulkInviteValidationSchema,
  InviteSentResponseSchema,
  BulkInviteResponseSchema,
  GraphListResponseSchema,
} from '../../../../src/modules/user_management/validation/user.schemas';

describe('GetAllUsers response schemas (Zod)', () => {
  const validListItem = {
    _id: '69cd0daf863a6899015af272',
    orgId: '69cd0daf863a6899015af270',
    fullName: 'John Doe',
    hasLoggedIn: true,
    isDeleted: false,
    createdAt: '2026-04-01T12:21:03.688Z',
    updatedAt: '2026-04-01T12:21:06.325Z',
    slug: 'user-1',
    __v: 0,
  };

  const validListItemNoFullName = {
    _id: '69cd0e4a863a6899015af331',
    orgId: '69cd0daf863a6899015af270',
    hasLoggedIn: false,
    isDeleted: false,
    createdAt: '2026-04-01T12:23:38.676Z',
    updatedAt: '2026-04-01T12:23:38.676Z',
    slug: 'user-2',
    __v: 0,
  };

  describe('GetAllUsersListItemSchema', () => {
    it('should accept a full user row', () => {
      const result = GetAllUsersListItemSchema.safeParse(validListItem);
      expect(result.success).to.be.true;
    });

    it('should accept when fullName is omitted', () => {
      const result = GetAllUsersListItemSchema.safeParse(validListItemNoFullName);
      expect(result.success).to.be.true;
    });

    it('should accept optional profile fields from lean (e.g. firstName)', () => {
      const result = GetAllUsersListItemSchema.safeParse({
        ...validListItem,
        firstName: 'H',
      });
      expect(result.success).to.be.true;
      if (result.success) {
        expect(result.data.firstName).to.equal('H');
      }
    });

    it('should strip keys not in the schema (default Zod object behavior)', () => {
      const result = GetAllUsersListItemSchema.safeParse({
        ...validListItem,
        notInSchema: 'x',
      });
      expect(result.success).to.be.true;
      if (result.success) {
        expect('notInSchema' in result.data).to.be.false;
      }
    });

    it('should reject when required fields are missing', () => {
      const result = GetAllUsersListItemSchema.safeParse({
        _id: '69cd0daf863a6899015af272',
        orgId: '69cd0daf863a6899015af270',
      });
      expect(result.success).to.be.false;
    });

    it('should reject invalid __v type', () => {
      const result = GetAllUsersListItemSchema.safeParse({
        ...validListItem,
        __v: '0',
      });
      expect(result.success).to.be.false;
    });
  });

  describe('GetAllUsersResponseSchema', () => {
    it('should accept an empty array', () => {
      const result = GetAllUsersResponseSchema.safeParse([]);
      expect(result.success).to.be.true;
    });

    it('should accept a list matching GET /users', () => {
      const result = GetAllUsersResponseSchema.safeParse([
        validListItem,
        validListItemNoFullName,
      ]);
      expect(result.success).to.be.true;
    });

    it('should reject when one element is invalid', () => {
      const result = GetAllUsersResponseSchema.safeParse([
        validListItem,
        { _id: 'bad' },
      ]);
      expect(result.success).to.be.false;
    });
  });

  const validBlockedItem = {
    _id: '69cd0daf863a6899015af272',
    email: 'user@example.com',
    orgId: '69cd0daf863a6899015af270',
    fullName: 'John Doe',
    hasLoggedIn: false,
    slug: 'user-1',
    createdAt: '2026-04-01T12:21:03.688Z',
    updatedAt: '2026-04-01T12:21:06.325Z',
  };

  describe('GetAllUsersBlockedListItemSchema', () => {
    it('should accept a blocked-user row', () => {
      const result = GetAllUsersBlockedListItemSchema.safeParse(validBlockedItem);
      expect(result.success).to.be.true;
    });

    it('should reject invalid email', () => {
      const result = GetAllUsersBlockedListItemSchema.safeParse({
        ...validBlockedItem,
        email: 'not-an-email',
      });
      expect(result.success).to.be.false;
    });

    it('should reject missing email', () => {
      const result = GetAllUsersBlockedListItemSchema.safeParse({
        _id: validBlockedItem._id,
        orgId: validBlockedItem.orgId,
        hasLoggedIn: false,
        slug: 'x',
        createdAt: validBlockedItem.createdAt,
        updatedAt: validBlockedItem.updatedAt,
      });
      expect(result.success).to.be.false;
    });
  });

  describe('GetAllUsersBlockedResponseSchema', () => {
    it('should accept an empty array', () => {
      const result = GetAllUsersBlockedResponseSchema.safeParse([]);
      expect(result.success).to.be.true;
    });

    it('should accept a blocked list', () => {
      const result = GetAllUsersBlockedResponseSchema.safeParse([validBlockedItem]);
      expect(result.success).to.be.true;
    });
  });
});

describe('GetAllUsersWithGroups response schemas (Zod)', () => {
  const adminGroup = { name: 'admin', type: 'admin' };
  const everyoneGroup = { name: 'everyone', type: 'everyone' };

  const userWithGroups = {
    _id: '69cfb0b9b4ebec89103cae94',
    orgId: '69cfb0b9b4ebec89103cae92',
    fullName: 'John Doe',
    hasLoggedIn: true,
    groups: [adminGroup, everyoneGroup],
  };

  const userNoFullName = {
    _id: '69cfb28cb4ebec89103caf81',
    orgId: '69cfb0b9b4ebec89103cae92',
    hasLoggedIn: false,
    groups: [everyoneGroup],
  };


  describe('GetAllUsersWithGroupsResponseSchema', () => {
    it('should accept an empty array', () => {
      const result = GetAllUsersWithGroupsResponseSchema.safeParse([]);
      expect(result.success).to.be.true;
    });

    it('should accept the full example payload', () => {
      const result = GetAllUsersWithGroupsResponseSchema.safeParse([
        userWithGroups,
        userNoFullName,
      ]);
      expect(result.success).to.be.true;
    });

    it('should reject when one element is missing required fields', () => {
      const result = GetAllUsersWithGroupsResponseSchema.safeParse([
        userWithGroups,
        { _id: '69cfb28cb4ebec89103caf81' },
      ]);
      expect(result.success).to.be.false;
    });

    it('should reject a non-array value', () => {
      const result = GetAllUsersWithGroupsResponseSchema.safeParse(userWithGroups);
      expect(result.success).to.be.false;
    });
  });
});

describe('GET /users/:id response schema (Zod)', () => {
  describe('GetUserByIdResponseSchema', () => {
    const userWithFullName = {
      _id: '69cfb0b9b4ebec89103cae94',
      orgId: '69cfb0b9b4ebec89103cae92',
      email: 'Rose@example.com',
      fullName: 'Rose Smith',
      hasLoggedIn: true,
      slug: 'user-1',
      isDeleted: false,
      createdAt: '2026-04-03T12:21:13.226Z',
      updatedAt: '2026-04-03T12:21:15.185Z',
      __v: 0,
    };

    const userWithoutFullName = {
      _id: '69cfb28cb4ebec89103caf81',
      orgId: '69cfb0b9b4ebec89103cae92',
      email: 'John@example.com',
      hasLoggedIn: false,
      slug: 'user-2',
      isDeleted: false,
      createdAt: '2026-04-03T12:29:00.434Z',
      updatedAt: '2026-04-03T12:29:00.434Z',
      __v: 0,
    };

    it('should accept a user document with fullName', () => {
      const result = GetUserByIdResponseSchema.safeParse(userWithFullName);
      expect(result.success).to.be.true;
    });

    it('should accept a user document without fullName', () => {
      const result = GetUserByIdResponseSchema.safeParse(userWithoutFullName);
      expect(result.success).to.be.true;
    });

    it('should accept when email is omitted (HIDE_EMAIL)', () => {
      const { email: _e, ...rest } = userWithFullName;
      const result = GetUserByIdResponseSchema.safeParse(rest);
      expect(result.success).to.be.true;
    });

    it('should reject when email is present but invalid', () => {
      const result = GetUserByIdResponseSchema.safeParse({
        ...userWithFullName,
        email: 'not-an-email',
      });
      expect(result.success).to.be.false;
    });

    it('should reject without _id or orgId', () => {
      const { _id: _i, ...withoutId } = userWithFullName;
      const { orgId: _o, ...withoutOrgId } = userWithFullName;
      expect(GetUserByIdResponseSchema.safeParse(withoutId).success).to.be.false;
      expect(GetUserByIdResponseSchema.safeParse(withoutOrgId).success).to.be.false;
    });

    it('should strip unknown keys (default object behavior)', () => {
      const result = GetUserByIdResponseSchema.safeParse({
        ...userWithFullName,
        extraField: 'x',
      });
      expect(result.success).to.be.true;
      if (result.success) {
        expect(result.data).to.deep.equal(userWithFullName);
      }
    });
  });

  describe('InternalLookupResponseSchema (GET /users/internal/:id)', () => {
    const internalUser = {
      _id: '69cfb0b9b4ebec89103cae94',
      orgId: '69cfb0b9b4ebec89103cae92',
      fullName: 'Jason Smith',
      email: 'jason@example.com',
      hasLoggedIn: true,
      isDeleted: false,
      createdAt: '2026-04-03T12:21:13.226Z',
      updatedAt: '2026-04-04T06:13:38.077Z',
      slug: 'user-1',
      __v: 0,
    };

    it('should accept internal lookup user payload', () => {
      expect(InternalLookupResponseSchema.safeParse(internalUser).success).to.be.true;
    });

    it('should reject when email is omitted', () => {
      const { email: _e, ...rest } = internalUser;
      expect(InternalLookupResponseSchema.safeParse(rest).success).to.be.false;
    });
  });

  describe('CheckUserExistsByEmailResponseSchema (GET /users/email/exists)', () => {
    const userDoc = {
      _id: '69cfb0b9b4ebec89103cae94',
      orgId: '69cfb0b9b4ebec89103cae92',
      fullName: 'John Doe',
      email: 'john@example.com',
      hasLoggedIn: true,
      isDeleted: false,
      createdAt: '2026-04-03T12:21:13.226Z',
      updatedAt: '2026-04-04T06:13:38.077Z',
      slug: 'user-1',
      __v: 0,
    };

    it('should accept an array of lean user documents', () => {
      expect(CheckUserExistsByEmailResponseSchema.safeParse([userDoc]).success).to.be.true;
    });

    it('should accept an empty array', () => {
      expect(CheckUserExistsByEmailResponseSchema.safeParse([]).success).to.be.true;
    });

    it('should reject when a row omits email', () => {
      const { email: _e, ...rest } = userDoc;
      expect(CheckUserExistsByEmailResponseSchema.safeParse([rest]).success).to.be.false;
    });
  });
});

describe('POST /users response schema (Zod)', () => {
  describe('CreateResponseSchema', () => {
    const createdUser = {
      orgId: '69cfb0b9b4ebec89103cae92',
      fullName: 'John Doe',
      email: 'john@example.com',
      hasLoggedIn: false,
      isDeleted: false,
      _id: '69d00f68262b53565bba38f7',
      createdAt: '2026-04-03T19:05:12.217Z',
      updatedAt: '2026-04-03T19:05:12.217Z',
      slug: 'user-4',
      __v: 0,
    };

    it('should accept the created-user payload shape', () => {
      const result = CreateResponseSchema.safeParse(createdUser);
      expect(result.success).to.be.true;
    });

    it('should reject when fullName is missing', () => {
      const { fullName: _f, ...noName } = createdUser;
      const result = CreateResponseSchema.safeParse(noName);
      expect(result.success).to.be.false;
    });

    it('should reject when email is missing', () => {
      const { email: _e, ...rest } = createdUser;
      const result = CreateResponseSchema.safeParse(rest);
      expect(result.success).to.be.false;
    });

    it('should reject invalid email', () => {
      const result = CreateResponseSchema.safeParse({
        ...createdUser,
        email: 'not-an-email',
      });
      expect(result.success).to.be.false;
    });
  });
});

describe('PATCH /users/:id name-field response schemas (Zod)', () => {
  const base = {
    _id: '69d00f68262b53565bba38f7',
    orgId: '69cfb0b9b4ebec89103cae92',
    fullName: 'John Doe',
    email: 'john@example.com',
    hasLoggedIn: false,
    isDeleted: false,
    createdAt: '2026-04-03T19:05:12.217Z',
    updatedAt: '2026-04-03T19:15:00.005Z',
    slug: 'user-4',
    __v: 0,
  };

  it('UpdateFullNameResponseSchema accepts PATCH fullname body', () => {
    expect(UpdateFullNameResponseSchema.safeParse(base).success).to.be.true;
  });

  it('UpdateFirstNameResponseSchema accepts PATCH firstName body', () => {
    const row = {
      ...base,
      updatedAt: '2026-04-03T19:18:32.765Z',
      firstName: 'John',
    };
    expect(UpdateFirstNameResponseSchema.safeParse(row).success).to.be.true;
  });

  it('UpdateLastNameResponseSchema accepts PATCH lastName body', () => {
    const row = {
      ...base,
      updatedAt: '2026-04-03T19:18:59.816Z',
      lastName: 'Doe',
    };
    expect(UpdateLastNameResponseSchema.safeParse(row).success).to.be.true;
  });

  it('UpdateFirstNameResponseSchema rejects without firstName', () => {
    expect(UpdateFirstNameResponseSchema.safeParse(base).success).to.be.false;
  });

  it('UpdateLastNameResponseSchema rejects without lastName', () => {
    expect(UpdateLastNameResponseSchema.safeParse(base).success).to.be.false;
  });

  it('UpdateDesignationResponseSchema accepts PATCH designation response', () => {
    const row = {
      ...base,
      email: 'jason@example.com',
      designation: 'ceo',
    };
    expect(UpdateDesignationResponseSchema.safeParse(row).success).to.be.true;
  });

  it('UpdateDesignationResponseSchema rejects without designation', () => {
    expect(UpdateDesignationResponseSchema.safeParse(base).success).to.be.false;
  });
});

describe('PATCH/PUT/DELETE user response schemas (Zod)', () => {
  const patchEmailExample = {
    _id: '69d00f68262b53565bba38f7',
    orgId: '69cfb0b9b4ebec89103cae92',
    fullName: 'John Doe',
    email: 'john@example.com',
    hasLoggedIn: false,
    isDeleted: false,
    createdAt: '2026-04-03T19:05:12.217Z',
    updatedAt: '2026-04-03T19:33:20.838Z',
    slug: 'user-4',
    __v: 0,
    firstName: 'John',
    lastName: 'Doe',
    designation: 'Engineer',
  };

  it('UpdateEmailResponseSchema accepts PATCH email response', () => {
    expect(UpdateEmailResponseSchema.safeParse(patchEmailExample).success).to.be
      .true;
  });

  it('UpdateEmailResponseSchema accepts without designation', () => {
    const { designation: _d, ...noDesignation } = patchEmailExample;
    expect(UpdateEmailResponseSchema.safeParse(noDesignation).success).to.be.true;
  });

  it('UpdateEmailResponseSchema accepts core only (no first/last/designation)', () => {
    const minimal = {
      _id: '69d00f68262b53565bba38f7',
      orgId: '69cfb0b9b4ebec89103cae92',
      fullName: 'John Doe',
      email: 'john@example.com',
      hasLoggedIn: false,
      isDeleted: false,
      createdAt: '2026-04-03T19:05:12.217Z',
      updatedAt: '2026-04-03T19:33:20.838Z',
      slug: 'user-4',
      __v: 0,
    };
    expect(UpdateEmailResponseSchema.safeParse(minimal).success).to.be.true;
  });

  it('UpdatePutResponseSchema accepts PUT user + meta', () => {
    const putExample = {
      ...patchEmailExample,
      hasLoggedIn: true,
      updatedAt: '2026-04-03T19:34:48.518Z',
      meta: { emailChangeMailStatus: 'notNeeded' as const },
    };
    expect(UpdatePutResponseSchema.safeParse(putExample).success).to.be.true;
  });

  it('UpdatePutResponseSchema accepts core + meta without profile fields', () => {
    const minimal = {
      _id: '69d00f68262b53565bba38f7',
      orgId: '69cfb0b9b4ebec89103cae92',
      fullName: 'John Doe',
      email: 'john@example.com',
      hasLoggedIn: true,
      isDeleted: false,
      createdAt: '2026-04-03T19:05:12.217Z',
      updatedAt: '2026-04-03T19:34:48.518Z',
      slug: 'user-4',
      __v: 0,
      meta: { emailChangeMailStatus: 'notNeeded' as const },
    };
    expect(UpdatePutResponseSchema.safeParse(minimal).success).to.be.true;
  });

  it('DeleteResponseSchema accepts delete message', () => {
    expect(
      DeleteResponseSchema.safeParse({
        message: 'User deleted successfully',
      }).success,
    ).to.be.true;
  });

  it('AdminCheckResponseSchema accepts admin check message', () => {
    expect(
      AdminCheckResponseSchema.safeParse({
        message: 'User has admin access',
      }).success,
    ).to.be.true;
  });

  it('DeleteResponseSchema rejects empty message', () => {
    expect(DeleteResponseSchema.safeParse({ message: '' }).success).to.be.false;
  });

  it('AdminCheckResponseSchema rejects empty message', () => {
    expect(AdminCheckResponseSchema.safeParse({ message: '' }).success).to.be
      .false;
  });
});

describe('User display picture JSON response schemas (GET/DELETE /users/dp)', () => {
  it('DisplayPictureErrorResponseSchema accepts error payload', () => {
    expect(
      DisplayPictureErrorResponseSchema.safeParse({
        errorMessage: 'User pic not found',
      }).success,
    ).to.be.true;
  });

  it('DisplayPictureErrorResponseSchema rejects empty errorMessage', () => {
    expect(DisplayPictureErrorResponseSchema.safeParse({ errorMessage: '' }).success).to.be
      .false;
  });

  it('RemoveDisplayPictureResponseSchema accepts cleared document', () => {
    expect(
      RemoveDisplayPictureResponseSchema.safeParse({
        _id: '507f1f77bcf86cd799439011',
        userId: '507f1f77bcf86cd799439011',
        orgId: '507f1f77bcf86cd799439012',
        pic: null,
        mimeType: null,
        __v: 0,
      }).success,
    ).to.be.true;
  });

  it('RemoveDisplayPictureResponseSchema rejects non-null pic', () => {
    expect(
      RemoveDisplayPictureResponseSchema.safeParse({
        _id: '507f1f77bcf86cd799439011',
        userId: '507f1f77bcf86cd799439011',
        orgId: '507f1f77bcf86cd799439012',
        pic: 'still-here',
        mimeType: null,
        __v: 0,
      }).success,
    ).to.be.false;
  });
});

describe('HealthResponseSchema', () => {
  it('should accept health payload', () => {
    const result = HealthResponseSchema.safeParse({
      status: 'healthy',
      timestamp: '2026-04-01T12:00:00.000Z',
    });
    expect(result.success).to.be.true;
  });

  it('should reject non-healthy status', () => {
    const result = HealthResponseSchema.safeParse({
      status: 'degraded',
      timestamp: '2026-04-01T12:00:00.000Z',
    });
    expect(result.success).to.be.false;
  });
});

describe('InternalAdminResponseSchema (GET /users/internal/admin-users)', () => {
  it('should accept admin user id list', () => {
    const result = InternalAdminResponseSchema.safeParse({
      adminUserIds: [
        '69cfb0b9b4ebec89103cae94',
        '69d0b62aa9ae4aae8766f756',
        '69d0b62aa9ae4aae8766f757',
      ],
    });
    expect(result.success).to.be.true;
  });

  it('should accept empty adminUserIds', () => {
    expect(InternalAdminResponseSchema.safeParse({ adminUserIds: [] }).success).to.be
      .true;
  });

  it('should reject when adminUserIds is missing', () => {
    expect(InternalAdminResponseSchema.safeParse({}).success).to.be.false;
  });

  it('should reject empty string id', () => {
    expect(
      InternalAdminResponseSchema.safeParse({
        adminUserIds: ['69cfb0b9b4ebec89103cae94', ''],
      }).success,
    ).to.be.false;
  });

  it('should reject id that is not 24 hex chars', () => {
    expect(
      InternalAdminResponseSchema.safeParse({
        adminUserIds: ['69cfb0b9b4ebec89103cae94', 'short'],
      }).success,
    ).to.be.false;
  });
});

describe('GraphListResponseSchema (GET /users/graph/list)', () => {
  const validPayload = {
    status: 'success' as const,
    message: 'Users fetched successfully',
    users: [
      {
        id: 'd4636eaa-bb18-4d7f-b34d-9b0557bfe29b',
        userId: '69d0b073fd9d9dff456b4cba',
        name: '',
        email: 'test@example.com',
        isActive: true,
        createdAtTimestamp: 1775284339784,
        updatedAtTimestamp: 1775284339784,
      },
    ],
    pagination: {
      page: 1,
      limit: 100,
      total: 3,
      pages: 1,
      hasNext: false,
      hasPrev: false,
    },
  };

  it('should accept full connector payload', () => {
    expect(GraphListResponseSchema.safeParse(validPayload).success).to.be.true;
  });

  it('should reject empty message', () => {
    expect(
      GraphListResponseSchema.safeParse({ ...validPayload, message: '' }).success,
    ).to.be.false;
  });

  it('should reject wrong status', () => {
    expect(
      GraphListResponseSchema.safeParse({
        ...validPayload,
        status: 'error',
      }).success,
    ).to.be.false;
  });

  it('should strip unknown keys on user items (default object behavior)', () => {
    const result = GraphListResponseSchema.safeParse({
      ...validPayload,
      users: [
        {
          ...validPayload.users[0],
          connectorExtra: 'omitted-from-output',
        },
      ],
    });
    expect(result.success).to.be.true;
    expect('connectorExtra' in result.data!.users[0]).to.be.false;
  });
});

describe('POST /users/bulk/invite and invite response schemas (Zod)', () => {
  const validGroupId = '69cfb0b9b4ebec89103cae98';

  it('BulkInviteBodySchema accepts emails and empty groupIds', () => {
    const result = BulkInviteBodySchema.safeParse({
      emails: ['test@example.com', 'hamil@example.com'],
      groupIds: [],
    });
    expect(result.success).to.be.true;
  });

  it('BulkInviteBodySchema accepts emails with groupIds', () => {
    const result = BulkInviteBodySchema.safeParse({
      emails: ['hello@world.com', 'okay@world.com'],
      groupIds: [validGroupId],
    });
    expect(result.success).to.be.true;
  });

  it('BulkInviteBodySchema rejects empty emails array', () => {
    const result = BulkInviteBodySchema.safeParse({
      emails: [],
      groupIds: [],
    });
    expect(result.success).to.be.false;
  });

  it('BulkInviteBodySchema rejects invalid group id', () => {
    const result = BulkInviteBodySchema.safeParse({
      emails: ['a@b.com'],
      groupIds: ['not-24-hex'],
    });
    expect(result.success).to.be.false;
  });

  it('BulkInviteBodySchema rejects unknown keys', () => {
    const result = BulkInviteBodySchema.safeParse({
      emails: ['a@b.com'],
      groupIds: [],
      extra: true,
    });
    expect(result.success).to.be.false;
  });

  it('BulkInviteValidationSchema wraps body', () => {
    const result = BulkInviteValidationSchema.safeParse({
      body: { emails: ['a@b.com'], groupIds: [validGroupId] },
      query: {},
      params: {},
      headers: {},
    });
    expect(result.success).to.be.true;
  });

  it('InviteSentResponseSchema accepts success message', () => {
    expect(
      InviteSentResponseSchema.safeParse({
        message: 'Invite sent successfully',
      }).success,
    ).to.be.true;
  });

  it('BulkInviteResponseSchema accepts message or errorMessage', () => {
    expect(
      BulkInviteResponseSchema.safeParse({
        message: 'Invite sent successfully',
      }).success,
    ).to.be.true;
    expect(
      BulkInviteResponseSchema.safeParse({
        errorMessage: 'All provided emails already have active accounts',
      }).success,
    ).to.be.true;
  });
});

describe('GET /users/:id/email schemas (Zod)', () => {
  describe('GetEmailByIdValidationSchema', () => {
    it('should accept a valid MongoDB id param', () => {
      const result = GetEmailByIdValidationSchema.safeParse({
        body: {},
        query: {},
        params: { id: '69cfb0b9b4ebec89103cae94' },
        headers: {},
      });
      expect(result.success).to.be.true;
    });

    it('should reject an invalid id param', () => {
      const result = GetEmailByIdValidationSchema.safeParse({
        body: {},
        query: {},
        params: { id: 'not-an-objectid' },
        headers: {},
      });
      expect(result.success).to.be.false;
    });
  });

  describe('GetEmailByIdResponseSchema', () => {
    it('should accept a valid email payload', () => {
      const result = GetEmailByIdResponseSchema.safeParse({
        email: 'user@example.com',
      });
      expect(result.success).to.be.true;
    });

    it('should reject an invalid email', () => {
      const result = GetEmailByIdResponseSchema.safeParse({
        email: 'not-an-email',
      });
      expect(result.success).to.be.false;
    });

    it('should reject missing email', () => {
      const result = GetEmailByIdResponseSchema.safeParse({});
      expect(result.success).to.be.false;
    });
  });
});
