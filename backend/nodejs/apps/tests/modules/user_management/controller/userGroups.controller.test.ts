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
    it('should return paginated groups with userCount', async () => {
      const mockGroups = [
        { _id: 'g1', name: 'admin', type: 'admin', orgId, slug: 'g-1', isDeleted: false, users: ['u1', 'u2'], createdAt: '2026-01-01', updatedAt: '2026-01-01' },
        { _id: 'g2', name: 'everyone', type: 'everyone', orgId, slug: 'g-2', isDeleted: false, users: [], createdAt: '2026-01-01', updatedAt: '2026-01-01' },
      ];

      sinon.stub(UserGroups, 'find').returns({
        skip: sinon.stub().returns({
          limit: sinon.stub().returns({
            lean: sinon.stub().returns({
              exec: sinon.stub().resolves(mockGroups),
            }),
          }),
        }),
      } as any);
      sinon.stub(UserGroups, 'countDocuments').resolves(2);

      req.query = { page: '1', limit: '25' };
      await controller.getAllUserGroups(req, res);

      expect(res.status.calledWith(200)).to.be.true;
      const responseArg = res.json.firstCall.args[0];
      expect(responseArg).to.have.property('groups').that.is.an('array').with.lengthOf(2);
      expect(responseArg.groups[0]).to.have.property('userCount', 2);
      expect(responseArg.groups[1]).to.have.property('userCount', 0);
      expect(responseArg.groups[0]).to.not.have.property('users');
      expect(responseArg).to.have.property('pagination');
      expect(responseArg.pagination).to.deep.include({ page: 1, limit: 25, totalCount: 2 });
    });

    it('should filter groups by search query', async () => {
      sinon.stub(UserGroups, 'find').returns({
        skip: sinon.stub().returns({
          limit: sinon.stub().returns({
            lean: sinon.stub().returns({
              exec: sinon.stub().resolves([]),
            }),
          }),
        }),
      } as any);
      sinon.stub(UserGroups, 'countDocuments').resolves(0);

      req.query = { search: 'admin' };
      await controller.getAllUserGroups(req, res);

      expect(res.status.calledWith(200)).to.be.true;
      // Verify regex filter was applied
      const findCall = (UserGroups.find as sinon.SinonStub).firstCall.args[0];
      expect(findCall).to.have.property('name');
      expect(findCall.name).to.have.property('$regex', 'admin');
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
    it('should return paginated users with profilePicture', async () => {
      req.params.groupId = 'g1';
      req.query = { page: '1', limit: '25' };
      const userId1 = new mongoose.Types.ObjectId();

      sinon.stub(UserGroups, 'findOne').returns({
        lean: sinon.stub().returns({
          exec: sinon.stub().resolves({ _id: 'g1', users: [userId1] }),
        }),
      } as any);

      sinon.stub(Users, 'find').returns({
        select: sinon.stub().returns({
          skip: sinon.stub().returns({
            limit: sinon.stub().returns({
              lean: sinon.stub().returns({
                exec: sinon.stub().resolves([
                  { _id: userId1, fullName: 'Alice', email: 'alice@test.com' },
                ]),
              }),
            }),
          }),
        }),
      } as any);

      sinon.stub(Users, 'countDocuments').resolves(1);

      sinon.stub(UserDisplayPicture, 'find').returns({
        lean: sinon.stub().returns({
          exec: sinon.stub().resolves([
            { userId: userId1, pic: 'b64pic', mimeType: 'image/jpeg' },
          ]),
        }),
      } as any);

      await controller.getUsersInGroup(req, res);

      expect(res.status.calledWith(200)).to.be.true;
      const responseArg = res.json.firstCall.args[0];
      expect(responseArg).to.have.property('users').that.is.an('array').with.lengthOf(1);
      expect(responseArg.users[0]).to.deep.include({
        _id: userId1.toString(),
        fullName: 'Alice',
        email: 'alice@test.com',
        profilePicture: 'data:image/jpeg;base64,b64pic',
      });
      expect(responseArg).to.have.property('pagination');
      expect(responseArg.pagination).to.deep.include({ page: 1, totalCount: 1 });
    });

    it('should throw NotFoundError when group not found', async () => {
      req.params.groupId = 'nonexistent';

      sinon.stub(UserGroups, 'findOne').returns({
        lean: sinon.stub().returns({
          exec: sinon.stub().resolves(null),
        }),
      } as any);

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
