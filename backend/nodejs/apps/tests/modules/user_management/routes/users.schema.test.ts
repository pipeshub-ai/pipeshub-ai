import 'reflect-metadata';
import { expect } from 'chai';
import {
  GetAllUsersListItemSchema,
  GetAllUsersResponseSchema,
  GetAllUsersBlockedListItemSchema,
  GetAllUsersBlockedResponseSchema,
} from '../../../../src/modules/user_management/validation/user-validators';

describe('GetAllUsers response schemas (Zod)', () => {
  const validListItem = {
    _id: '69cd0daf863a6899015af272',
    orgId: '69cd0daf863a6899015af270',
    fullName: 'Harshit',
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

    it('should passthrough extra lean fields (e.g. firstName)', () => {
      const result = GetAllUsersListItemSchema.safeParse({
        ...validListItem,
        firstName: 'H',
      });
      expect(result.success).to.be.true;
      if (result.success) {
        expect(result.data.firstName).to.equal('H');
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
    fullName: 'Harshit',
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
