import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { UserGroupController } from '../../../../src/modules/user_management/controller/userGroups.controller';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
import { UserGroups } from '../../../../src/modules/user_management/schema/userGroup.schema';
describe('UserGroupController', () => {
  let controller: UserGroupController;
  let req: any;
  let res: any;
  const orgId = new mongoose.Types.ObjectId().toString();

  beforeEach(() => {
    controller = new UserGroupController();

    req = {
      user: {
        userId: new mongoose.Types.ObjectId().toString(),
        orgId: orgId,
      },
      params: {},
      body: {},
    };

    res = {
      status: sinon.stub().returnsThis(),
      json: sinon.stub().returnsThis(),
    };
  });

  afterEach(() => {
    sinon.restore();
  });


  describe('createUserGroup', () => {
    it('should create a user group successfully', async () => {
      req.body = { name: 'Engineering', type: 'custom' };

      sinon.stub(UserGroups, 'findOne').resolves(null);

      const mockSavedGroupPlain = {
        _id: '69cd17ffbea35d8fcaed7701',
        name: 'Engineering',
        type: 'custom' as const,
        orgId,
        users: [] as string[],
        isDeleted: false,
        createdAt: '2026-04-01T13:05:03.062Z',
        updatedAt: '2026-04-01T13:05:03.062Z',
        slug: 'usergroup-1',
        __v: 0,
      };

      const mockSavedGroup = {
        ...mockSavedGroupPlain,
        toJSON: () => mockSavedGroupPlain,
      };

      sinon.stub(UserGroups.prototype, 'save').resolves(mockSavedGroup);

      await controller.createUserGroup(req, res);

      expect(res.status.calledWith(201)).to.be.true;
      expect(res.json.calledOnce).to.be.true;
      expect(res.json.firstCall.args[0]).to.deep.equal(mockSavedGroupPlain);
    });

    it('should log and continue when saved document fails response schema', async () => {
      req.body = { name: 'Bad', type: 'custom' };

      sinon.stub(UserGroups, 'findOne').resolves(null);

      const badPlain = {
        _id: '69cd17ffbea35d8fcaed7701',
        name: 'Bad',
        type: 'custom',
        orgId,
        users: [],
      };
      sinon.stub(UserGroups.prototype, 'save').resolves({
        ...badPlain,
        toJSON: () => badPlain,
      } as any);

      await controller.createUserGroup(req, res);
      expect(res.json.called).to.be.true;
    });

    it('should throw BadRequestError when name is missing', async () => {
      req.body = { type: 'custom' };

      try {
        await controller.createUserGroup(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('name(Name of the Group) is required');
      }
    });

    it('should throw BadRequestError when type is missing', async () => {
      req.body = { name: 'Engineering' };

      try {
        await controller.createUserGroup(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('type(Type of the Group) is required');
      }
    });

    it('should throw BadRequestError when trying to create admin group', async () => {
      req.body = { name: 'admin', type: 'admin' };

      try {
        await controller.createUserGroup(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('this type of group cannot be created');
      }
    });

    it('should throw BadRequestError when name is admin', async () => {
      req.body = { name: 'admin', type: 'custom' };

      try {
        await controller.createUserGroup(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('this type of group cannot be created');
      }
    });

    it('should throw BadRequestError for unknown group type', async () => {
      req.body = { name: 'MyGroup', type: 'unknownType' };

      try {
        await controller.createUserGroup(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('type(Type of the Group) unknown');
      }
    });

    it('should throw BadRequestError when group with same name exists', async () => {
      req.body = { name: 'Existing Group', type: 'custom' };

      sinon.stub(UserGroups, 'findOne').resolves({
        name: 'Existing Group',
      } as any);

      try {
        await controller.createUserGroup(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('Group already exists');
      }
    });
  });

  describe('getAllUserGroups', () => {
    it('should return all non-deleted groups for the org', async () => {
      const mockGroups = [
        {
          _id: '69cd0daf863a6899015af274',
          name: 'admin',
          type: 'admin',
          orgId,
          users: ['69cd0daf863a6899015af272'],
          isDeleted: false,
          createdAt: '2026-04-01T12:21:03.665Z',
          updatedAt: '2026-04-01T12:21:03.665Z',
          slug: 'usergroup-1',
          __v: 0,
        },
        {
          _id: '69cd0daf863a6899015af275',
          name: 'everyone',
          type: 'everyone',
          orgId,
          users: [
            '69cd0daf863a6899015af272',
            '69cd0e4a863a6899015af331',
            '69cd0ee6a9a42375da641267',
          ],
          isDeleted: false,
          createdAt: '2026-04-01T12:21:03.676Z',
          updatedAt: '2026-04-01T12:26:14.444Z',
          slug: 'usergroup-2',
          __v: 0,
        },
      ];

      sinon.stub(UserGroups, 'find').returns({
        lean: sinon.stub().returns({
          exec: sinon.stub().resolves(mockGroups),
        }),
      } as any);

      await controller.getAllUserGroups(req, res);

      expect(res.status.calledWith(200)).to.be.true;
      expect(res.json.calledOnce).to.be.true;
      expect(res.json.firstCall.args[0]).to.deep.equal(mockGroups);
    });

    it('should log and continue when list fails response schema', async () => {
      const badGroups = [{ _id: 'x', name: 'incomplete' }];

      sinon.stub(UserGroups, 'find').returns({
        lean: sinon.stub().returns({
          exec: sinon.stub().resolves(badGroups),
        }),
      } as any);

      await controller.getAllUserGroups(req, res);
      expect(res.json.called).to.be.true;
    });
  });

  describe('getUserGroupById', () => {
    it('should return a group by id', async () => {
      req.params.groupId = '69cd0daf863a6899015af274';
      const mockGroup = {
        _id: '69cd0daf863a6899015af274',
        name: 'admin',
        type: 'admin',
        orgId,
        users: ['69cd0daf863a6899015af272'],
        isDeleted: false,
        createdAt: '2026-04-01T12:21:03.665Z',
        updatedAt: '2026-04-01T12:21:03.665Z',
        slug: 'usergroup-1',
        __v: 0,
      };

      sinon.stub(UserGroups, 'findOne').returns({
        lean: sinon.stub().returns({
          exec: sinon.stub().resolves(mockGroup),
        }),
      } as any);

      await controller.getUserGroupById(req, res);

      expect(res.status.calledWith(200)).to.be.true;
      expect(res.json.calledOnce).to.be.true;
      expect(res.json.firstCall.args[0]).to.deep.equal(mockGroup);
    });

    it('should log and continue when group document fails response schema', async () => {
      req.params.groupId = '69cd0daf863a6899015af274';

      sinon.stub(UserGroups, 'findOne').returns({
        lean: sinon.stub().returns({
          exec: sinon.stub().resolves({ _id: 'x', name: 'bad' }),
        }),
      } as any);

      await controller.getUserGroupById(req, res);
      expect(res.json.called).to.be.true;
    });

    it('should throw NotFoundError when group not found', async () => {
      req.params.groupId = 'nonexistent';

      sinon.stub(UserGroups, 'findOne').returns({
        lean: sinon.stub().returns({
          exec: sinon.stub().resolves(null),
        }),
      } as any);

      try {
        await controller.getUserGroupById(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('UserGroup not found');
      }
    });
  });

  describe('updateGroup', () => {
    it('should update group name', async () => {
      req.params.id = '507f1f77bcf86cd799439011';
      req.body = { name: 'New Name' };

      const mockGroup = {
        _id: '507f1f77bcf86cd799439011',
        name: 'Old Name',
        type: 'custom',
        orgId,
        users: [],
        isDeleted: false,
        createdAt: '2026-04-01T12:21:03.665Z',
        updatedAt: '2026-04-01T12:21:03.665Z',
        slug: 'usergroup-custom',
        __v: 0,
        save: sinon.stub().resolves(),
        toJSON(this: typeof mockGroup) {
          return {
            _id: this._id,
            name: this.name,
            type: this.type,
            orgId: this.orgId,
            users: this.users,
            isDeleted: this.isDeleted,
            createdAt: this.createdAt,
            updatedAt: this.updatedAt,
            slug: this.slug,
            __v: this.__v,
          };
        },
      };

      sinon.stub(UserGroups, 'findOne').resolves(mockGroup as any);

      await controller.updateGroup(req, res);

      expect(mockGroup.name).to.equal('New Name');
      expect(mockGroup.save.calledOnce).to.be.true;
      expect(res.status.calledWith(200)).to.be.true;
      expect(res.json.firstCall.args[0]).to.deep.equal({
        _id: '507f1f77bcf86cd799439011',
        name: 'New Name',
        type: 'custom',
        orgId,
        users: [],
        isDeleted: false,
        createdAt: '2026-04-01T12:21:03.665Z',
        updatedAt: '2026-04-01T12:21:03.665Z',
        slug: 'usergroup-custom',
        __v: 0,
      });
    });

    it('should log and continue when updated document fails response schema', async () => {
      req.params.id = '507f1f77bcf86cd799439011';
      req.body = { name: 'New Name' };

      const mockGroup = {
        _id: '507f1f77bcf86cd799439011',
        name: 'Old Name',
        type: 'custom',
        orgId,
        save: sinon.stub().resolves(),
        toJSON(this: typeof mockGroup) {
          return {
            _id: this._id,
            name: this.name,
            type: this.type,
            orgId: this.orgId,
          };
        },
      };

      sinon.stub(UserGroups, 'findOne').resolves(mockGroup as any);

      await controller.updateGroup(req, res);
      expect(res.json.called).to.be.true;
    });

    it('should throw BadRequestError when name is missing', async () => {
      req.params.id = 'g1';
      req.body = {};

      try {
        await controller.updateGroup(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('New name is required');
      }
    });

    it('should throw NotFoundError when group not found', async () => {
      req.params.id = 'nonexistent';
      req.body = { name: 'New Name' };

      sinon.stub(UserGroups, 'findOne').resolves(null);

      try {
        await controller.updateGroup(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('User group not found');
      }
    });

    it('should throw ForbiddenError when updating admin group', async () => {
      req.params.id = 'g1';
      req.body = { name: 'New Name' };

      sinon.stub(UserGroups, 'findOne').resolves({
        type: 'admin',
        isDeleted: false,
      } as any);

      try {
        await controller.updateGroup(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('Not Allowed');
      }
    });

    it('should throw ForbiddenError when updating everyone group', async () => {
      req.params.id = 'g1';
      req.body = { name: 'New Name' };

      sinon.stub(UserGroups, 'findOne').resolves({
        type: 'everyone',
        isDeleted: false,
      } as any);

      try {
        await controller.updateGroup(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('Not Allowed');
      }
    });
  });

  describe('deleteGroup', () => {
    it('should delete a custom group', async () => {
      req.params.groupId = '507f1f77bcf86cd799439011';

      const mockGroup = {
        _id: '507f1f77bcf86cd799439011',
        name: 'Custom',
        type: 'custom',
        orgId,
        users: [],
        isDeleted: false,
        createdAt: '2026-04-01T12:21:03.665Z',
        updatedAt: '2026-04-01T12:21:03.665Z',
        slug: 'usergroup-custom',
        __v: 0,
        save: sinon.stub().resolves(),
        toJSON(this: typeof mockGroup) {
          return {
            _id: this._id,
            name: this.name,
            type: this.type,
            orgId: this.orgId,
            users: this.users,
            isDeleted: this.isDeleted,
            deletedBy: this.deletedBy,
            createdAt: this.createdAt,
            updatedAt: this.updatedAt,
            slug: this.slug,
            __v: this.__v,
          };
        },
      };

      sinon.stub(UserGroups, 'findOne').returns({
        exec: sinon.stub().resolves(mockGroup),
      } as any);

      await controller.deleteGroup(req, res);

      expect(mockGroup.isDeleted).to.be.true;
      expect(mockGroup.save.calledOnce).to.be.true;
      expect(res.status.calledWith(200)).to.be.true;
      expect(res.json.firstCall.args[0]).to.deep.equal({
        _id: '507f1f77bcf86cd799439011',
        name: 'Custom',
        type: 'custom',
        orgId,
        users: [],
        isDeleted: true,
        createdAt: '2026-04-01T12:21:03.665Z',
        updatedAt: '2026-04-01T12:21:03.665Z',
        slug: 'usergroup-custom',
        __v: 0,
        deletedBy: req.user.userId,
      });
    });

    it('should throw NotFoundError when group not found', async () => {
      req.params.groupId = 'nonexistent';

      sinon.stub(UserGroups, 'findOne').returns({
        exec: sinon.stub().resolves(null),
      } as any);

      try {
        await controller.deleteGroup(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('User group not found');
      }
    });

    it('should throw ForbiddenError when deleting non-custom group', async () => {
      req.params.groupId = 'g1';

      sinon.stub(UserGroups, 'findOne').returns({
        exec: sinon.stub().resolves({
          type: 'admin',
          isDeleted: false,
        }),
      } as any);

      try {
        await controller.deleteGroup(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('Only custom groups can be deleted');
      }
    });

    it('should set deletedBy to current user', async () => {
      req.params.groupId = '507f1f77bcf86cd799439011';

      const mockGroup = {
        _id: '507f1f77bcf86cd799439011',
        name: 'Custom',
        type: 'custom',
        orgId,
        users: [],
        isDeleted: false,
        deletedBy: undefined as string | undefined,
        createdAt: '2026-04-01T12:21:03.665Z',
        updatedAt: '2026-04-01T12:21:03.665Z',
        slug: 'usergroup-custom',
        __v: 0,
        save: sinon.stub().resolves(),
        toJSON(this: typeof mockGroup) {
          return {
            _id: this._id,
            name: this.name,
            type: this.type,
            orgId: this.orgId,
            users: this.users,
            isDeleted: this.isDeleted,
            deletedBy: this.deletedBy,
            createdAt: this.createdAt,
            updatedAt: this.updatedAt,
            slug: this.slug,
            __v: this.__v,
          };
        },
      };

      sinon.stub(UserGroups, 'findOne').returns({
        exec: sinon.stub().resolves(mockGroup),
      } as any);

      await controller.deleteGroup(req, res);

      expect(mockGroup.deletedBy).to.equal(req.user.userId);
    });

    it('should log and continue when deleted document fails response schema', async () => {
      req.params.groupId = '507f1f77bcf86cd799439011';

      const mockGroup = {
        _id: '507f1f77bcf86cd799439011',
        type: 'custom',
        isDeleted: false,
        save: sinon.stub().resolves(),
        toJSON(this: typeof mockGroup) {
          return {
            _id: this._id,
            type: this.type,
            isDeleted: this.isDeleted,
          };
        },
      };

      sinon.stub(UserGroups, 'findOne').returns({
        exec: sinon.stub().resolves(mockGroup),
      } as any);

      await controller.deleteGroup(req, res);
      expect(res.json.called).to.be.true;
    });
  });

  describe('addUsersToGroups', () => {
    const uid1 = '69cd0daf863a6899015af272';
    const uid2 = '69cd0e4a863a6899015af331';
    const gid1 = '69cd17ffbea35d8fcaed7701';
    const gid2 = '507f1f77bcf86cd799439012';

    it('should add users to groups', async () => {
      req.body = {
        userIds: [uid1, uid2],
        groupIds: [gid1, gid2],
      };

      sinon.stub(UserGroups, 'updateMany').resolves({
        modifiedCount: 2,
      } as any);

      await controller.addUsersToGroups(req, res);

      expect(res.status.calledWith(200)).to.be.true;
      expect(res.json.calledWith({ message: 'Users added to groups successfully' })).to.be.true;
    });

    it('should throw BadRequestError when userIds is empty', async () => {
      req.body = { userIds: [], groupIds: [gid1] };

      try {
        await controller.addUsersToGroups(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('userIds array is required');
      }
    });

    it('should throw BadRequestError when groupIds is empty', async () => {
      req.body = { userIds: [uid1], groupIds: [] };

      try {
        await controller.addUsersToGroups(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('groupIds array is required');
      }
    });

    it('should throw BadRequestError when userIds is missing', async () => {
      req.body = { groupIds: [gid1] };

      try {
        await controller.addUsersToGroups(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('userIds array is required');
      }
    });

    it('should throw BadRequestError when no groups were modified', async () => {
      req.body = {
        userIds: [uid1],
        groupIds: ['507f1f77bcf86cd799439099'],
      };

      sinon.stub(UserGroups, 'updateMany').resolves({
        modifiedCount: 0,
      } as any);

      try {
        await controller.addUsersToGroups(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('No groups found or updated');
      }
    });
  });

  describe('removeUsersFromGroups', () => {
    const uid1 = '69cd0e4a863a6899015af331';
    const gid1 = '69cd17ffbea35d8fcaed7701';

    it('should remove users from groups', async () => {
      req.body = {
        userIds: [uid1],
        groupIds: [gid1],
      };

      sinon.stub(UserGroups, 'updateMany').resolves({
        modifiedCount: 1,
      } as any);

      await controller.removeUsersFromGroups(req, res);

      expect(res.status.calledWith(200)).to.be.true;
      expect(res.json.calledWith({ message: 'Users removed from groups successfully' })).to.be.true;
    });

    it('should throw BadRequestError when userIds is empty', async () => {
      req.body = { userIds: [], groupIds: [gid1] };

      try {
        await controller.removeUsersFromGroups(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('User IDs are required');
      }
    });

    it('should throw BadRequestError when groupIds is empty', async () => {
      req.body = { userIds: [uid1], groupIds: [] };

      try {
        await controller.removeUsersFromGroups(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('Group IDs are required');
      }
    });

    it('should throw BadRequestError when no groups were modified', async () => {
      req.body = {
        userIds: [uid1],
        groupIds: ['507f1f77bcf86cd799439099'],
      };

      sinon.stub(UserGroups, 'updateMany').resolves({
        modifiedCount: 0,
      } as any);

      try {
        await controller.removeUsersFromGroups(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('No groups found or updated');
      }
    });
  });

  describe('getUsersInGroup', () => {
    const gid = '507f1f77bcf86cd799439011';
    const uid1 = '69cd0daf863a6899015af272';
    const uid2 = '69cd0e4a863a6899015af331';

    it('should return users in a group', async () => {
      req.params.groupId = gid;

      sinon.stub(UserGroups, 'findOne').resolves({
        _id: gid,
        users: [uid1, uid2],
      } as any);

      await controller.getUsersInGroup(req, res);

      expect(res.status.calledWith(200)).to.be.true;
      expect(res.json.calledWith({ users: [uid1, uid2] })).to.be.true;
    });

    it('should throw NotFoundError when group not found', async () => {
      req.params.groupId = '507f1f77bcf86cd799439099';

      sinon.stub(UserGroups, 'findOne').resolves(null);

      try {
        await controller.getUsersInGroup(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('Group not found');
      }
    });
  });

  describe('getGroupsForUser', () => {
    it('should return groups for a user', async () => {
      req.params.userId = '69cd0daf863a6899015af272';

      const mockGroups = [
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
      ];

      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().returns({
          lean: sinon.stub().returns({
            exec: sinon.stub().resolves(mockGroups),
          }),
        }),
      } as any);

      await controller.getGroupsForUser(req, res);

      expect(res.status.calledWith(200)).to.be.true;
      expect(res.json.calledOnce).to.be.true;
      expect(res.json.firstCall.args[0]).to.deep.equal(mockGroups);
    });
  });

  describe('getGroupStatistics', () => {
    it('should return group statistics', async () => {
      const mockStats = [
        { _id: 'admin', count: 1, totalUsers: 2, avgUsers: 2 },
        { _id: 'everyone', count: 1, totalUsers: 5, avgUsers: 5 },
      ];

      sinon.stub(UserGroups, 'aggregate').resolves(mockStats);

      await controller.getGroupStatistics(req, res);

      expect(res.status.calledWith(200)).to.be.true;
      expect(res.json.calledOnce).to.be.true;
      expect(res.json.firstCall.args[0]).to.deep.equal(mockStats);
    });
  });
});
