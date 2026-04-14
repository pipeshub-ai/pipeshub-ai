import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { UserGroupController } from '../../../../src/modules/user_management/controller/userGroups.controller';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
import { UserGroups } from '../../../../src/modules/user_management/schema/userGroup.schema';
import { UserDisplayPicture } from '../../../../src/modules/user_management/schema/userDp.schema';

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

  describe('getAllUsers', () => {
    it('should return all non-deleted users in the org', async () => {
      const mockUsers = [
        { _id: 'u1', fullName: 'User One', orgId },
        { _id: 'u2', fullName: 'User Two', orgId },
      ];

      sinon.stub(Users, 'find').resolves(mockUsers as any);

      await controller.getAllUsers(req, res);

      expect(res.json.calledWith(mockUsers)).to.be.true;
    });
  });

  describe('createUserGroup', () => {
    it('should create a user group successfully', async () => {
      req.body = { name: 'Engineering', type: 'custom' };

      sinon.stub(UserGroups, 'findOne').resolves(null);

      const mockSavedGroup = {
        _id: 'g1',
        name: 'Engineering',
        type: 'custom',
        orgId,
        users: [],
      };

      sinon.stub(UserGroups.prototype, 'save').resolves(mockSavedGroup);

      await controller.createUserGroup(req, res);

      expect(res.status.calledWith(201)).to.be.true;
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
    it('should return groups and userDps map', async () => {
      const userId1 = new mongoose.Types.ObjectId();
      const userId2 = new mongoose.Types.ObjectId();
      const mockGroups = [
        { _id: 'g1', name: 'admin', type: 'admin', users: [userId1] },
        { _id: 'g2', name: 'everyone', type: 'everyone', users: [userId1, userId2] },
      ];

      sinon.stub(UserGroups, 'find').returns({
        lean: sinon.stub().returns({
          exec: sinon.stub().resolves(mockGroups),
        }),
      } as any);

      const mockDpDocs = [
        { userId: userId1, pic: 'base64data', mimeType: 'image/jpeg' },
      ];

      sinon.stub(UserDisplayPicture, 'find').returns({
        lean: sinon.stub().returns({
          exec: sinon.stub().resolves(mockDpDocs),
        }),
      } as any);

      await controller.getAllUserGroups(req, res);

      expect(res.status.calledWith(200)).to.be.true;
      const responseArg = res.json.firstCall.args[0];
      expect(responseArg).to.have.property('groups').that.deep.equals(mockGroups);
      expect(responseArg).to.have.property('userDps');
      expect(responseArg.userDps[userId1.toString()]).to.equal('data:image/jpeg;base64,base64data');
      expect(responseArg.userDps[userId2.toString()]).to.be.undefined;
    });

    it('should return empty userDps when no groups have users', async () => {
      const mockGroups = [
        { _id: 'g1', name: 'admin', type: 'admin', users: [] },
      ];

      sinon.stub(UserGroups, 'find').returns({
        lean: sinon.stub().returns({
          exec: sinon.stub().resolves(mockGroups),
        }),
      } as any);

      await controller.getAllUserGroups(req, res);

      expect(res.status.calledWith(200)).to.be.true;
      const responseArg = res.json.firstCall.args[0];
      expect(responseArg).to.have.property('groups').that.deep.equals(mockGroups);
      expect(responseArg).to.have.property('userDps').that.deep.equals({});
    });
  });

  describe('getUserGroupById', () => {
    it('should return a group by id', async () => {
      req.params.groupId = 'g1';
      const mockGroup = { _id: 'g1', name: 'admin', type: 'admin', orgId };

      sinon.stub(UserGroups, 'findOne').returns({
        lean: sinon.stub().returns({
          exec: sinon.stub().resolves(mockGroup),
        }),
      } as any);

      await controller.getUserGroupById(req, res);

      expect(res.json.calledWith(mockGroup)).to.be.true;
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
      req.params.id = 'g1';
      req.body = { name: 'New Name' };

      const mockGroup = {
        _id: 'g1',
        name: 'Old Name',
        type: 'custom',
        orgId,
        isDeleted: false,
        save: sinon.stub().resolves(),
      };

      sinon.stub(UserGroups, 'findOne').resolves(mockGroup as any);

      await controller.updateGroup(req, res);

      expect(mockGroup.name).to.equal('New Name');
      expect(mockGroup.save.calledOnce).to.be.true;
      expect(res.status.calledWith(200)).to.be.true;
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
      req.params.groupId = 'g1';

      const mockGroup = {
        _id: 'g1',
        type: 'custom',
        isDeleted: false,
        save: sinon.stub().resolves(),
      };

      sinon.stub(UserGroups, 'findOne').returns({
        exec: sinon.stub().resolves(mockGroup),
      } as any);

      await controller.deleteGroup(req, res);

      expect(mockGroup.isDeleted).to.be.true;
      expect(mockGroup.save.calledOnce).to.be.true;
      expect(res.status.calledWith(200)).to.be.true;
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
      req.params.groupId = 'g1';

      const mockGroup = {
        _id: 'g1',
        type: 'custom',
        isDeleted: false,
        deletedBy: undefined as string | undefined,
        save: sinon.stub().resolves(),
      };

      sinon.stub(UserGroups, 'findOne').returns({
        exec: sinon.stub().resolves(mockGroup),
      } as any);

      await controller.deleteGroup(req, res);

      expect(mockGroup.deletedBy).to.equal(req.user.userId);
    });
  });

  describe('addUsersToGroups', () => {
    it('should add users to groups', async () => {
      req.body = {
        userIds: ['u1', 'u2'],
        groupIds: ['g1', 'g2'],
      };

      sinon.stub(UserGroups, 'updateMany').resolves({
        modifiedCount: 2,
      } as any);

      await controller.addUsersToGroups(req, res);

      expect(res.status.calledWith(200)).to.be.true;
      expect(res.json.calledWith({ message: 'Users added to groups successfully' })).to.be.true;
    });

    it('should throw BadRequestError when userIds is empty', async () => {
      req.body = { userIds: [], groupIds: ['g1'] };

      try {
        await controller.addUsersToGroups(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('userIds array is required');
      }
    });

    it('should throw BadRequestError when groupIds is empty', async () => {
      req.body = { userIds: ['u1'], groupIds: [] };

      try {
        await controller.addUsersToGroups(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('groupIds array is required');
      }
    });

    it('should throw BadRequestError when userIds is missing', async () => {
      req.body = { groupIds: ['g1'] };

      try {
        await controller.addUsersToGroups(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('userIds array is required');
      }
    });

    it('should throw BadRequestError when no groups were modified', async () => {
      req.body = {
        userIds: ['u1'],
        groupIds: ['nonexistent'],
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
    it('should remove users from groups', async () => {
      req.body = {
        userIds: ['u1'],
        groupIds: ['g1'],
      };

      sinon.stub(UserGroups, 'updateMany').resolves({
        modifiedCount: 1,
      } as any);

      await controller.removeUsersFromGroups(req, res);

      expect(res.status.calledWith(200)).to.be.true;
      expect(res.json.calledWith({ message: 'Users removed from groups successfully' })).to.be.true;
    });

    it('should throw BadRequestError when userIds is empty', async () => {
      req.body = { userIds: [], groupIds: ['g1'] };

      try {
        await controller.removeUsersFromGroups(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('User IDs are required');
      }
    });

    it('should throw BadRequestError when groupIds is empty', async () => {
      req.body = { userIds: ['u1'], groupIds: [] };

      try {
        await controller.removeUsersFromGroups(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('Group IDs are required');
      }
    });

    it('should throw BadRequestError when no groups were modified', async () => {
      req.body = { userIds: ['u1'], groupIds: ['g1'] };

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
    it('should return users in a group', async () => {
      req.params.groupId = 'g1';

      sinon.stub(UserGroups, 'findOne').resolves({
        _id: 'g1',
        users: ['u1', 'u2'],
      } as any);

      await controller.getUsersInGroup(req, res);

      expect(res.status.calledWith(200)).to.be.true;
      expect(res.json.calledWith({ users: ['u1', 'u2'] })).to.be.true;
    });

    it('should throw NotFoundError when group not found', async () => {
      req.params.groupId = 'nonexistent';

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
      req.params.userId = 'u1';

      const mockGroups = [
        { name: 'admin', type: 'admin' },
        { name: 'everyone', type: 'everyone' },
      ];

      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves(mockGroups),
      } as any);

      await controller.getGroupsForUser(req, res);

      expect(res.status.calledWith(200)).to.be.true;
      expect(res.json.calledWith(mockGroups)).to.be.true;
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
      expect(res.json.calledWith(mockStats)).to.be.true;
    });
  });
});
