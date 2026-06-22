import 'reflect-metadata';
import { expect } from 'chai';
import {
  createTeamSchema,
  deleteTeamSchema,
  getTeamSchema,
  getTeamUsersSchema,
  getUserTeamsQuerySchema,
  updateTeamSchema,
} from '../../../../src/modules/user_management/validators/teams.validators';

const VALID_TEAM_ID = '550e8400-e29b-41d4-a716-446655440000';
const VALID_ALL_TEAM_ID = 'all_507f1f77bcf86cd799439011';
const VALID_USER_ID = '507f1f77bcf86cd799439011';

describe('user_management/validators/teams.validators', () => {
  describe('createTeamSchema', () => {
    it('should accept minimal create body', () => {
      const result = createTeamSchema.safeParse({
        body: { name: 'Engineering' },
      });
      expect(result.success).to.be.true;
    });

    it('should accept userRoles', () => {
      const result = createTeamSchema.safeParse({
        body: {
          name: 'Engineering',
          userRoles: [{ userId: VALID_USER_ID, role: 'READER' }],
        },
      });
      expect(result.success).to.be.true;
    });

    it('should reject missing name', () => {
      const result = createTeamSchema.safeParse({ body: {} });
      expect(result.success).to.be.false;
    });

    it('should reject name over 100 characters', () => {
      const result = createTeamSchema.safeParse({
        body: { name: 'a'.repeat(101) },
      });
      expect(result.success).to.be.false;
    });

    it('should reject invalid mongo userId in userRoles', () => {
      const result = createTeamSchema.safeParse({
        body: {
          name: 'Team',
          userRoles: [{ userId: 'not-mongo', role: 'READER' }],
        },
      });
      expect(result.success).to.be.false;
    });

    it('should reject invalid role in userRoles', () => {
      const result = createTeamSchema.safeParse({
        body: {
          name: 'Team',
          userRoles: [{ userId: VALID_USER_ID, role: 'ADMIN' }],
        },
      });
      expect(result.success).to.be.false;
    });

    it('should strip legacy userIds and role from create body', () => {
      const result = createTeamSchema.safeParse({
        body: {
          name: 'Team',
          userIds: [VALID_USER_ID],
          role: 'WRITER',
        },
      });
      expect(result.success).to.be.true;
      if (result.success) {
        expect(result.data.body).to.deep.equal({ name: 'Team' });
      }
    });
  });

  describe('getUserTeamsQuerySchema', () => {
    it('should accept pagination query', () => {
      const result = getUserTeamsQuerySchema.safeParse({
        query: { page: '2', limit: '25' },
      });
      expect(result.success).to.be.true;
      if (result.success) {
        expect(result.data.query.page).to.equal(2);
        expect(result.data.query.limit).to.equal(25);
      }
    });

    it('should treat empty search as omitted', () => {
      for (const search of ['', '   ']) {
        const result = getUserTeamsQuerySchema.safeParse({
          query: { search },
        });
        expect(result.success).to.be.true;
        if (result.success) {
          expect(result.data.query.search).to.equal(undefined);
        }
      }
    });

    it('should reject XSS in search', () => {
      const result = getUserTeamsQuerySchema.safeParse({
        query: { search: '<script>alert(1)</script>' },
      });
      expect(result.success).to.be.false;
    });

    it('should reject limit over 100', () => {
      const result = getUserTeamsQuerySchema.safeParse({
        query: { limit: '101' },
      });
      expect(result.success).to.be.false;
    });

    it('should reject search over 1000 characters', () => {
      const result = getUserTeamsQuerySchema.safeParse({
        query: { search: 'a'.repeat(1001) },
      });
      expect(result.success).to.be.false;
    });

    it('should accept filter query params', () => {
      const result = getUserTeamsQuerySchema.safeParse({
        query: {
          page: '1',
          limit: '10',
          created_by: VALID_USER_ID,
          created_after: '1700000000000',
        },
      });
      expect(result.success).to.be.true;
    });

    it('should reject invalid created_by', () => {
      const result = getUserTeamsQuerySchema.safeParse({
        query: { created_by: 'bad-id' },
      });
      expect(result.success).to.be.false;
    });
  });

  describe('teamId params schemas', () => {
    it('getTeamSchema should accept UUID teamId', () => {
      const result = getTeamSchema.safeParse({
        params: { teamId: VALID_TEAM_ID },
      });
      expect(result.success).to.be.true;
    });

    it('getTeamSchema should accept system All team teamId', () => {
      const result = getTeamSchema.safeParse({
        params: { teamId: VALID_ALL_TEAM_ID },
      });
      expect(result.success).to.be.true;
    });

    it('getTeamSchema should reject invalid teamId', () => {
      const result = getTeamSchema.safeParse({
        params: { teamId: 'team1' },
      });
      expect(result.success).to.be.false;
    });

    it('getTeamUsersSchema should accept system All team teamId', () => {
      const result = getTeamUsersSchema.safeParse({
        params: { teamId: VALID_ALL_TEAM_ID },
        query: {},
      });
      expect(result.success).to.be.true;
    });

    it('deleteTeamSchema should accept UUID teamId', () => {
      const result = deleteTeamSchema.safeParse({
        params: { teamId: VALID_TEAM_ID },
      });
      expect(result.success).to.be.true;
    });
  });

  describe('updateTeamSchema', () => {
    it('should accept partial update body', () => {
      const result = updateTeamSchema.safeParse({
        params: { teamId: VALID_TEAM_ID },
        body: {
          name: 'Updated',
          updateUserRoles: [{ userId: VALID_USER_ID, role: 'WRITER' }],
        },
      });
      expect(result.success).to.be.true;
    });

    it('should reject invalid removeUserIds entry', () => {
      const result = updateTeamSchema.safeParse({
        params: { teamId: VALID_TEAM_ID },
        body: { removeUserIds: ['x'] },
      });
      expect(result.success).to.be.false;
    });

    it('should strip legacy addUserIds and role from update body', () => {
      const result = updateTeamSchema.safeParse({
        params: { teamId: VALID_TEAM_ID },
        body: {
          addUserIds: [VALID_USER_ID],
          role: 'READER',
        },
      });
      expect(result.success).to.be.true;
      if (result.success) {
        expect(result.data.body).to.deep.equal({});
      }
    });
  });

  describe('getTeamUsersSchema', () => {
    it('should accept params and query', () => {
      const result = getTeamUsersSchema.safeParse({
        params: { teamId: VALID_TEAM_ID },
        query: { page: '1', limit: '50', search: 'alice' },
      });
      expect(result.success).to.be.true;
    });
  });
});
