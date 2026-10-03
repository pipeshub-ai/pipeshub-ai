import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { UserGroupController } from '../../../../src/modules/user_management/controller/userGroups.controller';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
import { UserGroups } from '../../../../src/modules/user_management/schema/userGroup.schema';
import { UserDisplayPicture } from '../../../../src/modules/user_management/schema/userDp.schema';
import { BadRequestError } from '../../../../src/libs/errors/http.errors';

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
      query: {},
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
        expect(error.message).to.equal('Group name or type "admin", "everyone", or "standard" cannot be created');
      }
    });

    it('should throw BadRequestError when name is admin', async () => {
      req.body = { name: 'admin', type: 'custom' };

      try {
        await controller.createUserGroup(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('Group name or type "admin", "everyone", or "standard" cannot be created');
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

  describe('createUserGroup duplicate name check', () => {
    const otherOrgId = new mongoose.Types.ObjectId().toString();
    let stored: Array<{ name: string; orgId: mongoose.Types.ObjectId; isDeleted: boolean }>;

    // Answers findOne the way Mongo would: a key missing from the filter matches every org.
    const stubStore = () =>
      sinon.stub(UserGroups, 'findOne').callsFake(((filter: Record<string, unknown>) => {
        const cast = UserGroups.find().cast(UserGroups, { ...filter }) as Record<string, unknown>;
        const match = stored.find(
          (g) =>
            g.name === cast.name &&
            g.isDeleted === cast.isDeleted &&
            (!('orgId' in cast) || g.orgId.equals(cast.orgId as mongoose.Types.ObjectId)),
        );
        return Promise.resolve(match ?? null);
      }) as unknown as typeof UserGroups.findOne);

    beforeEach(() => {
      stored = [];
      sinon.stub(UserGroups.prototype, 'save').callsFake(function (this: { name: string; orgId: mongoose.Types.ObjectId }) {
        stored.push({ name: this.name, orgId: this.orgId, isDeleted: false });
        return Promise.resolve(this);
      } as unknown as typeof UserGroups.prototype.save);
    });

    it('lets two orgs each create a group with the same name', async () => {
      stubStore();
      stored.push({ name: 'Engineering', orgId: new mongoose.Types.ObjectId(otherOrgId), isDeleted: false });
      req.body = { name: 'Engineering', type: 'custom' };

      await controller.createUserGroup(req, res);

      expect(res.status.calledWith(201)).to.be.true;
      expect(stored.filter((g) => g.name === 'Engineering')).to.have.length(2);
    });

    it('still refuses a second group with the same name in the same org, without naming other orgs', async () => {
      stubStore();
      stored.push({ name: 'Engineering', orgId: new mongoose.Types.ObjectId(otherOrgId), isDeleted: false });
      req.body = { name: 'Engineering', type: 'custom' };
      await controller.createUserGroup(req, res);

      try {
        await controller.createUserGroup(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: unknown) {
        expect(error).to.be.instanceOf(Error);
        if (!(error instanceof Error)) throw error;
        expect(error.message).to.equal('Group already exists');
        expect(error.message).to.not.include(otherOrgId);
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

    it('escapes regex metacharacters in the group search query (OT-9)', async () => {
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

      req.query = { search: 'a(b).*' };
      await controller.getAllUserGroups(req, res);

      expect(res.status.calledWith(200)).to.be.true;
      const findCall = (UserGroups.find as sinon.SinonStub).firstCall.args[0];
      // Metacharacters are escaped, so the value is matched literally (no 500, no match-all).
      expect(findCall.name.$regex).to.equal('a\\(b\\)\\.\\*');
      const re = new RegExp(findCall.name.$regex, findCall.name.$options);
      expect(re.test('a(b).*')).to.be.true;
      expect(re.test('aXbYZ')).to.be.false;
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

  describe('updateGroup duplicate name check', () => {
    const otherOrgId = new mongoose.Types.ObjectId().toString();
    type StoredGroup = {
      _id: string;
      name: string;
      type: string;
      orgId: mongoose.Types.ObjectId;
      isDeleted: boolean;
      save: sinon.SinonStub;
    };
    let stored: StoredGroup[];

    const group = (id: string, name: string, org: string): StoredGroup => ({
      _id: id,
      name,
      type: 'custom',
      orgId: new mongoose.Types.ObjectId(org),
      isDeleted: false,
      save: sinon.stub().resolves(),
    });

    // Answers findOne the way Mongo would for the filters updateGroup sends.
    beforeEach(() => {
      stored = [];
      sinon.stub(UserGroups, 'findOne').callsFake(((filter: {
        _id: string | { $ne: string };
        name?: string;
        orgId: string;
        isDeleted: boolean;
      }) => {
        const orgFilter = new mongoose.Types.ObjectId(String(filter.orgId));
        const match = stored.find(
          (g) =>
            g.orgId.equals(orgFilter) &&
            g.isDeleted === filter.isDeleted &&
            (filter.name === undefined || g.name === filter.name) &&
            (typeof filter._id === 'string' ? g._id === filter._id : g._id !== filter._id.$ne),
        );
        return Promise.resolve(match ?? null);
      }) as unknown as typeof UserGroups.findOne);
    });

    it('refuses a rename to a name another group in the same org already has', async () => {
      stored.push(group('g1', 'Design', orgId), group('g2', 'Engineering', orgId));
      req.params.groupId = 'g1';
      req.body = { name: 'Engineering' };

      try {
        await controller.updateGroup(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: unknown) {
        expect(error).to.be.instanceOf(BadRequestError);
        if (!(error instanceof BadRequestError)) throw error;
        expect(error.message).to.equal('Group already exists');
        expect(error.statusCode).to.equal(400);
      }
      expect(stored[0]!.name).to.equal('Design');
      expect(stored[0]!.save.called).to.be.false;
    });

    it('allows a rename to a name used only in another org', async () => {
      stored.push(group('g1', 'Design', orgId), group('g2', 'Engineering', otherOrgId));
      req.params.groupId = 'g1';
      req.body = { name: 'Engineering' };

      await controller.updateGroup(req, res);

      expect(stored[0]!.name).to.equal('Engineering');
      expect(res.status.calledWith(200)).to.be.true;
    });

    it('allows saving a group with its own unchanged name', async () => {
      stored.push(group('g1', 'Design', orgId));
      req.params.groupId = 'g1';
      req.body = { name: 'Design' };

      await controller.updateGroup(req, res);

      expect(stored[0]!.save.calledOnce).to.be.true;
      expect(res.status.calledWith(200)).to.be.true;
    });

    it('refuses a rename to a reserved group name', async () => {
      stored.push(group('g1', 'Design', orgId));
      req.params.groupId = 'g1';
      req.body = { name: 'everyone' };

      try {
        await controller.updateGroup(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: unknown) {
        expect(error).to.be.instanceOf(BadRequestError);
        if (!(error instanceof BadRequestError)) throw error;
        expect(error.statusCode).to.equal(400);
      }
      expect(stored[0]!.name).to.equal('Design');
    });
  });

  describe('simultaneous requests and the unique name index', () => {
    type Committed = {
      _id: string;
      committedName: string;
      orgId: mongoose.Types.ObjectId;
      isDeleted: boolean;
    };
    let committed: Committed[];

    const duplicateKeyError = (name: string) =>
      Object.assign(new Error(`E11000 duplicate key error collection: userGroups index: orgId_1_name_1_active_unique dup key: { name: "${name}" }`), {
        code: 11000,
        keyPattern: { orgId: 1, name: 1 },
        keyValue: { name },
      });

    // Behaves like the partial unique index on (orgId, name) for active groups.
    const commit = (id: string, name: string, org: mongoose.Types.ObjectId): Promise<void> => {
      const clash = committed.find(
        (g) => g._id !== id && !g.isDeleted && g.orgId.equals(org) && g.committedName === name,
      );
      if (clash) return Promise.reject(duplicateKeyError(name));
      const existing = committed.find((g) => g._id === id);
      if (existing) existing.committedName = name;
      else committed.push({ _id: id, committedName: name, orgId: org, isDeleted: false });
      return Promise.resolve();
    };

    const newRes = () => ({
      status: sinon.stub().returnsThis(),
      json: sinon.stub().returnsThis(),
    });

    beforeEach(() => {
      committed = [];
    });

    it('lets one of two simultaneous creates with the same name win and gives the other the duplicate-name message', async () => {
      sinon.stub(UserGroups, 'findOne').callsFake(((filter: { name: string; orgId: string; isDeleted: boolean }) => {
        const org = new mongoose.Types.ObjectId(String(filter.orgId));
        const match = committed.find(
          (g) => g.orgId.equals(org) && g.isDeleted === filter.isDeleted && g.committedName === filter.name,
        );
        return Promise.resolve(match ?? null);
      }) as unknown as typeof UserGroups.findOne);
      sinon.stub(UserGroups.prototype, 'save').callsFake(function (this: { _id: mongoose.Types.ObjectId; name: string; orgId: mongoose.Types.ObjectId }) {
        return commit(String(this._id), this.name, this.orgId).then(() => this);
      } as unknown as typeof UserGroups.prototype.save);

      const reqA = { ...req, body: { name: 'Engineering', type: 'custom' } };
      const reqB = { ...req, body: { name: 'Engineering', type: 'custom' } };
      const resA = newRes();
      const resB = newRes();

      const results = await Promise.allSettled([
        controller.createUserGroup(reqA, resA as any),
        controller.createUserGroup(reqB, resB as any),
      ]);

      const fulfilled = results.filter((r) => r.status === 'fulfilled');
      const rejected = results.filter((r): r is PromiseRejectedResult => r.status === 'rejected');
      expect(fulfilled).to.have.length(1);
      expect(rejected).to.have.length(1);
      expect(rejected[0]!.reason).to.be.instanceOf(BadRequestError);
      expect(rejected[0]!.reason.message).to.equal('Group already exists');
      expect(rejected[0]!.reason.statusCode).to.equal(400);
      expect(committed.filter((g) => g.committedName === 'Engineering')).to.have.length(1);
    });

    it('lets one of two simultaneous renames to the same name win and leaves the other group unchanged', async () => {
      const org = new mongoose.Types.ObjectId(orgId);
      committed.push(
        { _id: 'g1', committedName: 'Design', orgId: org, isDeleted: false },
        { _id: 'g2', committedName: 'Ops', orgId: org, isDeleted: false },
      );
      // Each request loads its own copy of the group, as Mongoose does.
      const load = (id: string) => {
        const copy = {
          _id: id,
          name: committed.find((g) => g._id === id)!.committedName,
          type: 'custom',
          orgId: org,
          isDeleted: false,
          save: () => commit(id, copy.name, org).then(() => copy),
        };
        return copy;
      };
      sinon.stub(UserGroups, 'findOne').callsFake(((filter: {
        _id: string | { $ne: string };
        name?: string;
        isDeleted: boolean;
      }) => {
        if (typeof filter._id === 'string') return Promise.resolve(load(filter._id));
        const excluded = filter._id.$ne;
        const match = committed.find(
          (g) => g._id !== excluded && g.isDeleted === filter.isDeleted && g.committedName === filter.name,
        );
        return Promise.resolve(match ?? null);
      }) as unknown as typeof UserGroups.findOne);

      const results = await Promise.allSettled([
        controller.updateGroup({ ...req, params: { groupId: 'g1' }, body: { name: 'Engineering' } }, newRes() as any),
        controller.updateGroup({ ...req, params: { groupId: 'g2' }, body: { name: 'Engineering' } }, newRes() as any),
      ]);

      const rejected = results.filter((r): r is PromiseRejectedResult => r.status === 'rejected');
      expect(rejected).to.have.length(1);
      expect(rejected[0]!.reason).to.be.instanceOf(BadRequestError);
      expect(rejected[0]!.reason.message).to.equal('Group already exists');
      expect(committed.filter((g) => g.committedName === 'Engineering')).to.have.length(1);
      const loser = committed.find((g) => g.committedName !== 'Engineering')!;
      expect(loser.committedName).to.equal(loser._id === 'g1' ? 'Design' : 'Ops');
    });

    it('passes through a duplicate-key error on another index untouched', async () => {
      sinon.stub(UserGroups, 'findOne').resolves(null);
      const slugClash = Object.assign(new Error('E11000 duplicate key error index: slug_1'), {
        code: 11000,
        keyPattern: { slug: 1 },
      });
      sinon.stub(UserGroups.prototype, 'save').rejects(slugClash);
      req.body = { name: 'Engineering', type: 'custom' };

      try {
        await controller.createUserGroup(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: unknown) {
        expect(error).to.equal(slugClash);
      }
    });
  });

  describe('updateGroup', () => {
    it('should update group name', async () => {
      req.params.groupId = 'g1';
      req.body = { name: 'New Name' };

      const mockGroup = {
        _id: 'g1',
        name: 'Old Name',
        type: 'custom',
        orgId,
        isDeleted: false,
        save: sinon.stub().resolves(),
      };

      const findOne = sinon.stub(UserGroups, 'findOne');
      findOne.onFirstCall().resolves(mockGroup as any);
      findOne.onSecondCall().resolves(null);

      await controller.updateGroup(req, res);

      expect(mockGroup.name).to.equal('New Name');
      expect(mockGroup.save.calledOnce).to.be.true;
      expect(res.status.calledWith(200)).to.be.true;
    });

    it('should trim whitespace before updating group name', async () => {
      req.params.groupId = 'g1';
      req.body = { name: '  New Name  ' };

      const mockGroup = {
        _id: 'g1',
        name: 'Old Name',
        type: 'custom',
        orgId,
        isDeleted: false,
        save: sinon.stub().resolves(),
      };

      const findOne = sinon.stub(UserGroups, 'findOne');
      findOne.onFirstCall().resolves(mockGroup as any);
      findOne.onSecondCall().resolves(null);

      await controller.updateGroup(req, res);

      expect(mockGroup.name).to.equal('New Name');
      expect(mockGroup.save.calledOnce).to.be.true;
      expect(res.status.calledWith(200)).to.be.true;
    });

    it('should throw BadRequestError when name is missing', async () => {
      req.params.groupId = 'g1';
      req.body = {};

      try {
        await controller.updateGroup(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('New name is required');
      }
    });

    it('should throw BadRequestError when name is only whitespace', async () => {
      req.params.groupId = 'g1';
      req.body = { name: '   ' };

      try {
        await controller.updateGroup(req, res);
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.equal('New name is required');
      }
    });

    it('should throw NotFoundError when group not found', async () => {
      req.params.groupId = 'nonexistent';
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
      req.params.groupId = 'g1';
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
      req.params.groupId = 'g1';
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
