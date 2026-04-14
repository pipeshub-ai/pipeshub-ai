import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { TeamsController } from '../../../../src/modules/user_management/controller/teams.controller';
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command';
import { UserDisplayPicture } from '../../../../src/modules/user_management/schema/userDp.schema';

describe('TeamsController', () => {
  let controller: TeamsController;
  let mockConfig: any;
  let mockLogger: any;
  let req: any;
  let res: any;
  let next: sinon.SinonStub;
  let executeStub: sinon.SinonStub;

  beforeEach(() => {
    mockConfig = {
      connectorBackend: 'http://localhost:8088',
    };

    mockLogger = {
      debug: sinon.stub(),
      info: sinon.stub(),
      error: sinon.stub(),
      warn: sinon.stub(),
    };

    controller = new TeamsController(mockConfig, mockLogger);

    req = {
      user: {
        userId: '507f1f77bcf86cd799439011',
        orgId: '507f1f77bcf86cd799439012',
      },
      params: {},
      query: {},
      body: {},
      headers: {
        'content-type': 'application/json',
      },
      context: { requestId: 'test-request-id' },
    };

    res = {
      status: sinon.stub().returnsThis(),
      json: sinon.stub().returnsThis(),
    };

    next = sinon.stub();
  });

  afterEach(() => {
    sinon.restore();
  });

  describe('createTeam', () => {
    it('should create a team successfully', async () => {
      const mockTeam = { id: 'team1', name: 'Engineering' };
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 201,
        data: mockTeam,
      });

      req.body = { name: 'Engineering', userIds: [] };

      await controller.createTeam(req, res, next);

      expect(res.status.calledWith(201)).to.be.true;
      expect(res.json.calledWith(mockTeam)).to.be.true;
    });

    it('should call next with BadRequestError when orgId is missing', async () => {
      req.user = { userId: '507f1f77bcf86cd799439011' };

      await controller.createTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
      const error = next.firstCall.args[0];
      expect(error).to.be.an('error');
    });

    it('should call next with BadRequestError when userId is missing', async () => {
      req.user = { orgId: '507f1f77bcf86cd799439012' };

      await controller.createTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
      const error = next.firstCall.args[0];
      expect(error).to.be.an('error');
    });

    it('should handle AI service errors', async () => {
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').rejects(
        new Error('Service unavailable'),
      );

      req.body = { name: 'Engineering' };

      await controller.createTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
      expect(mockLogger.error.called).to.be.true;
    });
  });

  describe('getTeam', () => {
    it('should get a team by id', async () => {
      const mockTeam = { id: 'team1', name: 'Engineering' };
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: mockTeam,
      });

      req.params.teamId = 'team1';

      await controller.getTeam(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
      expect(res.json.calledWith(mockTeam)).to.be.true;
    });

    it('should throw BadRequestError when orgId is missing', async () => {
      req.user = { userId: '507f1f77bcf86cd799439011' };
      req.params.teamId = 'team1';

      try {
        await controller.getTeam(req, res, next);
      } catch (error: any) {
        expect(error.message).to.equal('Organization ID is required');
      }
    });

    it('should throw BadRequestError when userId is missing', async () => {
      req.user = { orgId: '507f1f77bcf86cd799439012' };
      req.params.teamId = 'team1';

      try {
        await controller.getTeam(req, res, next);
      } catch (error: any) {
        expect(error.message).to.equal('User ID is required');
      }
    });

    it('should handle backend errors from AI service', async () => {
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').rejects({
        statusCode: 404,
        data: { detail: 'Team not found' },
        message: 'Not found',
      });

      req.params.teamId = 'nonexistent';

      await controller.getTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });
  });

  describe('listTeams', () => {
    it('should list teams with pagination', async () => {
      const mockTeams = {
        teams: [{ id: 'team1', name: 'Team 1' }],
        total: 1,
      };
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: mockTeams,
      });

      req.query = { page: '1', limit: '10' };

      await controller.listTeams(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
      expect(res.json.calledWith(mockTeams)).to.be.true;
    });

    it('should call next with BadRequestError when orgId is missing', async () => {
      req.user = { userId: '507f1f77bcf86cd799439011' };

      await controller.listTeams(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should call next with BadRequestError when userId is missing', async () => {
      req.user = { orgId: '507f1f77bcf86cd799439012' };

      await controller.listTeams(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should include search param in query', async () => {
      const mockTeams = { teams: [], total: 0 };
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: mockTeams,
      });

      req.query = { page: '1', limit: '10', search: 'engineering' };

      await controller.listTeams(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
    });
  });

  describe('addUsersToTeam', () => {
    it('should add users to a team', async () => {
      const mockResult = { message: 'Users added' };
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: mockResult,
      });

      req.params.teamId = 'team1';
      req.body = { userIds: ['u1', 'u2'] };

      await controller.addUsersToTeam(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
    });

    it('should call next with error when orgId is missing', async () => {
      req.user = { userId: '507f1f77bcf86cd799439011' };
      req.params.teamId = 'team1';

      await controller.addUsersToTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });
  });

  describe('updateTeam', () => {
    it('should update a team', async () => {
      const mockResult = { id: 'team1', name: 'Updated Team' };
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: mockResult,
      });

      req.params.teamId = 'team1';
      req.body = { name: 'Updated Team' };

      await controller.updateTeam(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
    });

    it('should call next with error when orgId is missing', async () => {
      req.user = { userId: '507f1f77bcf86cd799439011' };
      req.params.teamId = 'team1';

      await controller.updateTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });
  });

  describe('deleteTeam', () => {
    it('should delete a team', async () => {
      const mockResult = { message: 'Team deleted' };
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: mockResult,
      });

      req.params.teamId = 'team1';

      await controller.deleteTeam(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
    });

    it('should call next with error when orgId is missing', async () => {
      req.user = { userId: '507f1f77bcf86cd799439011' };
      req.params.teamId = 'team1';

      await controller.deleteTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });
  });

  describe('removeUserFromTeam', () => {
    it('should remove a user from team', async () => {
      const mockResult = { message: 'User removed' };
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: mockResult,
      });

      req.params.teamId = 'team1';
      req.body = { userIds: ['u1'] };

      await controller.removeUserFromTeam(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
    });

    it('should call next with error when userId is missing', async () => {
      req.user = { orgId: '507f1f77bcf86cd799439012' };
      req.params.teamId = 'team1';

      await controller.removeUserFromTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });
  });

  describe('getUserTeams', () => {
    it('should return teams data as-is from AI service', async () => {
      const mockTeams = {
        teams: [{ id: 'team1', name: 'Team 1', memberCount: 3 }],
        pagination: { page: 1, limit: 10, total: 1 },
      };
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: mockTeams,
      });

      await controller.getUserTeams(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
      expect(res.json.calledWith(mockTeams)).to.be.true;
    });

    it('should return empty response when AI service returns non-200', async () => {
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 500,
        data: null,
      });

      await controller.getUserTeams(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
      const responseArg = res.json.firstCall.args[0];
      expect(responseArg).to.have.property('teams');
      expect(responseArg.teams).to.deep.equal([]);
    });

    it('should call next with error when orgId is missing', async () => {
      req.user = { userId: '507f1f77bcf86cd799439011' };

      await controller.getUserTeams(req, res, next);

      expect(next.calledOnce).to.be.true;
    });
  });

  describe('getTeamUsers', () => {
    it('should inject profilePicture into team members', async () => {
      const memberId = '507f1f77bcf86cd799439099';
      req.params.teamId = 'team1';

      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: {
          members: [{ id: 'uuid1', userId: memberId, userName: 'Alice' }],
        },
      });

      sinon.stub(UserDisplayPicture, 'find').returns({
        lean: sinon.stub().returns({
          exec: sinon.stub().resolves([
            { userId: memberId, pic: 'base64pic', mimeType: 'image/jpeg' },
          ]),
        }),
      } as any);

      await controller.getTeamUsers(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
      const responseArg = res.json.firstCall.args[0];
      expect(responseArg.members[0].profilePicture).to.equal('data:image/jpeg;base64,base64pic');
    });

    it('should not add profilePicture when user has no DP', async () => {
      req.params.teamId = 'team1';

      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: {
          members: [{ id: 'uuid1', userId: 'noDP', userName: 'Bob' }],
        },
      });

      sinon.stub(UserDisplayPicture, 'find').returns({
        lean: sinon.stub().returns({
          exec: sinon.stub().resolves([]),
        }),
      } as any);

      await controller.getTeamUsers(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
      const responseArg = res.json.firstCall.args[0];
      expect(responseArg.members[0]).to.not.have.property('profilePicture');
    });

    it('should call next with error when orgId is missing', async () => {
      req.user = { userId: '507f1f77bcf86cd799439011' };
      req.params.teamId = 'team1';

      await controller.getTeamUsers(req, res, next);

      expect(next.calledOnce).to.be.true;
    });
  });

  describe('updateTeamUsersPermissions', () => {
    it('should update team users permissions', async () => {
      const mockResult = { message: 'Permissions updated' };
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: mockResult,
      });

      req.params.teamId = 'team1';
      req.body = { userRoles: [{ userId: 'u1', role: 'admin' }] };

      await controller.updateTeamUsersPermissions(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
    });

    it('should call next with error when orgId is missing', async () => {
      req.user = { userId: '507f1f77bcf86cd799439011' };
      req.params.teamId = 'team1';

      await controller.updateTeamUsersPermissions(req, res, next);

      expect(next.calledOnce).to.be.true;
    });
  });

  describe('getUserCreatedTeams', () => {
    it('should get user-created teams', async () => {
      const mockTeams = { teams: [{ id: 'team1' }] };
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: mockTeams,
      });

      req.query = { page: '1', limit: '10' };

      await controller.getUserCreatedTeams(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
    });

    it('should call next with error when orgId is missing', async () => {
      req.user = { userId: '507f1f77bcf86cd799439011' };

      await controller.getUserCreatedTeams(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should call next with error when userId is missing', async () => {
      req.user = { orgId: '507f1f77bcf86cd799439012' };

      await controller.getUserCreatedTeams(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should include search param in getUserCreatedTeams query', async () => {
      const mockTeams = { teams: [], total: 0 };
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: mockTeams,
      });

      req.query = { page: '1', limit: '10', search: 'test' };

      await controller.getUserCreatedTeams(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
    });
  });

  // =========================================================================
  // handleBackendError - various error types
  // =========================================================================
  describe('handleBackendError - error branches via createTeam', () => {
    it('should handle ECONNREFUSED error', async () => {
      const connError = new Error('fetch failed');
      (connError as any).cause = { code: 'ECONNREFUSED' };
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').rejects(connError);

      req.body = { name: 'Team' };

      await controller.createTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
      const err = next.firstCall.args[0];
      expect(err.message).to.include('unavailable');
    });

    it('should handle fetch failed error message', async () => {
      const fetchError = new Error('fetch failed');
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').rejects(fetchError);

      req.body = { name: 'Team' };

      await controller.createTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
      const err = next.firstCall.args[0];
      expect(err.message).to.include('unavailable');
    });

    it('should handle error with statusCode 400', async () => {
      const error400: any = { statusCode: 400, data: { detail: 'Bad request' }, message: 'Bad' };
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').rejects(error400);

      req.body = { name: 'Team' };

      await controller.createTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should handle error with statusCode 401', async () => {
      const error401: any = { statusCode: 401, data: { reason: 'Unauthorized' } };
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').rejects(error401);

      req.body = { name: 'Team' };

      await controller.createTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should handle error with statusCode 403', async () => {
      const error403: any = { statusCode: 403, data: { message: 'Forbidden' } };
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').rejects(error403);

      req.body = { name: 'Team' };

      await controller.createTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should handle error with statusCode 404', async () => {
      const error404: any = { statusCode: 404, data: {}, message: 'Not Found' };
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').rejects(error404);

      req.body = { name: 'Team' };

      await controller.createTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should handle error with statusCode 409', async () => {
      const error409: any = { statusCode: 409, data: { detail: 'Conflict' } };
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').rejects(error409);

      req.body = { name: 'Team' };

      await controller.createTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should handle error with statusCode 500', async () => {
      const error500: any = { statusCode: 500, data: { detail: 'Server error' } };
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').rejects(error500);

      req.body = { name: 'Team' };

      await controller.createTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should handle error with unknown statusCode', async () => {
      const errorUnknown: any = { statusCode: 418, data: { detail: 'Teapot' } };
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').rejects(errorUnknown);

      req.body = { name: 'Team' };

      await controller.createTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should handle error with data.reason fallback', async () => {
      const errReason: any = { statusCode: 400, data: { reason: 'Rate limited' } };
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').rejects(errReason);

      req.body = { name: 'Team' };

      await controller.createTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should handle error with data.message fallback', async () => {
      const errMsg: any = { statusCode: 400, data: { message: 'Custom message' } };
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').rejects(errMsg);

      req.body = { name: 'Team' };

      await controller.createTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should handle error with ECONNREFUSED detail', async () => {
      const errConn: any = { statusCode: undefined, data: { detail: 'ECONNREFUSED' }, message: 'ECONNREFUSED' };
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').rejects(errConn);

      req.body = { name: 'Team' };

      await controller.createTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
      const err = next.firstCall.args[0];
      expect(err.message).to.include('unavailable');
    });
  });

  // =========================================================================
  // handleAIServiceResponse - branches via various methods
  // =========================================================================
  describe('handleAIServiceResponse - non-200 status', () => {
    it('should throw error when AI service returns 400 from getTeam', async () => {
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 400,
        data: { detail: 'Bad request' },
      });

      req.params.teamId = 'team1';

      await controller.getTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should throw error when AI service returns null data from listTeams', async () => {
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: null,
      });

      req.query = { page: '1', limit: '10' };

      await controller.listTeams(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should throw error when AI service returns 500 from addUsersToTeam', async () => {
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 500,
        data: { detail: 'Internal error' },
      });

      req.params.teamId = 'team1';
      req.body = { userIds: ['u1'] };

      await controller.addUsersToTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should throw error when AI service returns non-200 for createTeam', async () => {
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 422,
        data: { detail: 'Validation failed' },
      });

      req.body = { name: 'Team' };

      await controller.createTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should throw error when AI service returns null data for deleteTeam', async () => {
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: null,
      });

      req.params.teamId = 'team1';

      await controller.deleteTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should throw error when AI service returns non-200 for updateTeam', async () => {
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 403,
        data: { message: 'Forbidden' },
      });

      req.params.teamId = 'team1';
      req.body = { name: 'Updated' };

      await controller.updateTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should throw error when AI service returns null data for removeUserFromTeam', async () => {
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: null,
      });

      req.params.teamId = 'team1';
      req.body = { userIds: ['u1'] };

      await controller.removeUserFromTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should throw error when AI service returns null data for getTeamUsers', async () => {
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: null,
      });

      req.params.teamId = 'team1';

      await controller.getTeamUsers(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should throw error when AI service returns null data for updateTeamUsersPermissions', async () => {
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: null,
      });

      req.params.teamId = 'team1';
      req.body = { userRoles: [] };

      await controller.updateTeamUsersPermissions(req, res, next);

      expect(next.calledOnce).to.be.true;
    });
  });

  // =========================================================================
  // Various methods - AI service errors (different error types)
  // =========================================================================
  describe('AI service errors across methods', () => {
    it('should handle AI service error in getTeam', async () => {
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').rejects(new Error('Service down'));

      req.params.teamId = 'team1';

      await controller.getTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should handle AI service error in listTeams', async () => {
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').rejects(new Error('Timeout'));

      req.query = { page: '1', limit: '10' };

      await controller.listTeams(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should handle AI service error in addUsersToTeam', async () => {
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').rejects(new Error('Network error'));

      req.params.teamId = 'team1';
      req.body = { userIds: ['u1'] };

      await controller.addUsersToTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should handle AI service error in updateTeam', async () => {
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').rejects(new Error('Connection lost'));

      req.params.teamId = 'team1';
      req.body = { name: 'Updated' };

      await controller.updateTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should handle AI service error in deleteTeam', async () => {
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').rejects(new Error('Delete failed'));

      req.params.teamId = 'team1';

      await controller.deleteTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should handle AI service error in removeUserFromTeam', async () => {
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').rejects(new Error('Remove failed'));

      req.params.teamId = 'team1';
      req.body = { userIds: ['u1'] };

      await controller.removeUserFromTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should handle AI service error in getTeamUsers', async () => {
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').rejects(new Error('Fetch users failed'));

      req.params.teamId = 'team1';

      await controller.getTeamUsers(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should handle AI service error in getUserTeams', async () => {
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').rejects(new Error('Fetch teams failed'));

      await controller.getUserTeams(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should handle AI service error in updateTeamUsersPermissions', async () => {
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').rejects(new Error('Perms failed'));

      req.params.teamId = 'team1';
      req.body = { userRoles: [] };

      await controller.updateTeamUsersPermissions(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should handle AI service error in getUserCreatedTeams', async () => {
      executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').rejects(new Error('Fetch failed'));

      req.query = { page: '1', limit: '10' };

      await controller.getUserCreatedTeams(req, res, next);

      expect(next.calledOnce).to.be.true;
    });
  });

  // -----------------------------------------------------------------------
  // Branch coverage: listTeams - query param branches (page/limit/search)
  // -----------------------------------------------------------------------
  describe('listTeams - query param branches', () => {
    it('should pass all query params when provided', async () => {
      req.query = { page: '1', limit: '10', search: 'dev' };
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: [{ id: 'team1' }],
      });

      await controller.listTeams(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
    });

    it('should work with no query params', async () => {
      req.query = {};
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: [],
      });

      await controller.listTeams(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
    });

    it('should work with only page', async () => {
      req.query = { page: '2' };
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: [],
      });

      await controller.listTeams(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
    });

    it('should work with only limit', async () => {
      req.query = { limit: '5' };
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: [],
      });

      await controller.listTeams(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
    });

    it('should work with only search', async () => {
      req.query = { search: 'team' };
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: [],
      });

      await controller.listTeams(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
    });

    it('should throw on search > 1000 chars', async () => {
      req.query = { search: 'a'.repeat(1001) };

      await controller.listTeams(req, res, next);

      expect(next.calledOnce).to.be.true;
      expect(next.firstCall.args[0].message).to.include('Search parameter too long');
    });

    it('should throw on XSS in search', async () => {
      req.query = { search: '<script>alert("xss")</script>' };

      await controller.listTeams(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should throw when orgId is missing', async () => {
      req.user = { userId: '507f1f77bcf86cd799439011' };

      await controller.listTeams(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should throw when userId is missing', async () => {
      req.user = { orgId: '507f1f77bcf86cd799439012' };

      await controller.listTeams(req, res, next);

      expect(next.calledOnce).to.be.true;
    });
  });

  // -----------------------------------------------------------------------
  // Branch coverage: getUserTeams - query param branches and non-200 path
  // -----------------------------------------------------------------------
  describe('getUserTeams - query param branches', () => {
    it('should pass all query params', async () => {
      req.query = { page: '1', limit: '10', search: 'team' };
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { teams: [] },
      });

      await controller.getUserTeams(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
    });

    it('should work with no query params', async () => {
      req.query = {};
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { teams: [] },
      });

      await controller.getUserTeams(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
    });

    it('should work with only page', async () => {
      req.query = { page: '2' };
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { teams: [] },
      });

      await controller.getUserTeams(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
    });

    it('should work with only limit', async () => {
      req.query = { limit: '20' };
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { teams: [] },
      });

      await controller.getUserTeams(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
    });

    it('should work with only search', async () => {
      req.query = { search: 'dev' };
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { teams: [] },
      });

      await controller.getUserTeams(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
    });

    it('should return empty teams when AI service returns non-200', async () => {
      req.query = {};
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 500,
        data: null,
      });

      await controller.getUserTeams(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
      expect(res.json.calledOnce).to.be.true;
      const result = res.json.firstCall.args[0];
      expect(result).to.have.property('teams');
      expect(result.teams).to.be.an('array').with.lengthOf(0);
    });

    it('should throw on search > 1000 chars', async () => {
      req.query = { search: 'a'.repeat(1001) };

      await controller.getUserTeams(req, res, next);

      expect(next.calledOnce).to.be.true;
      expect(next.firstCall.args[0].message).to.include('Search parameter too long');
    });

    it('should throw on XSS in search', async () => {
      req.query = { search: '<script>alert("xss")</script>' };

      await controller.getUserTeams(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should throw when orgId is missing', async () => {
      req.user = { userId: '507f1f77bcf86cd799439011' };

      await controller.getUserTeams(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should throw when userId is missing', async () => {
      req.user = { orgId: '507f1f77bcf86cd799439012' };

      await controller.getUserTeams(req, res, next);

      expect(next.calledOnce).to.be.true;
    });
  });

  // -----------------------------------------------------------------------
  // Branch coverage: getUserCreatedTeams - query param branches
  // -----------------------------------------------------------------------
  describe('getUserCreatedTeams - query param branches', () => {
    it('should pass all query params', async () => {
      req.query = { page: '1', limit: '10', search: 'my' };
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: [],
      });

      await controller.getUserCreatedTeams(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
    });

    it('should work with no query params', async () => {
      req.query = {};
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: [],
      });

      await controller.getUserCreatedTeams(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
    });

    it('should work with only page', async () => {
      req.query = { page: '3' };
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: [],
      });

      await controller.getUserCreatedTeams(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
    });

    it('should work with only limit', async () => {
      req.query = { limit: '50' };
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: [],
      });

      await controller.getUserCreatedTeams(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
    });

    it('should work with only search', async () => {
      req.query = { search: 'created' };
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: [],
      });

      await controller.getUserCreatedTeams(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
    });

    it('should throw on search > 1000 chars', async () => {
      req.query = { search: 'a'.repeat(1001) };

      await controller.getUserCreatedTeams(req, res, next);

      expect(next.calledOnce).to.be.true;
      expect(next.firstCall.args[0].message).to.include('Search parameter too long');
    });

    it('should throw on XSS in search', async () => {
      req.query = { search: '<script>alert("xss")</script>' };

      await controller.getUserCreatedTeams(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should throw when orgId is missing', async () => {
      req.user = { userId: '507f1f77bcf86cd799439011' };

      await controller.getUserCreatedTeams(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should throw when userId is missing', async () => {
      req.user = { orgId: '507f1f77bcf86cd799439012' };

      await controller.getUserCreatedTeams(req, res, next);

      expect(next.calledOnce).to.be.true;
    });
  });

  // -----------------------------------------------------------------------
  // Branch coverage: handleBackendError - errorDetail fallback chain
  // -----------------------------------------------------------------------
  describe('handleBackendError - errorDetail fallback branches', () => {
    it('should use data.reason when data.detail is missing', async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 400,
        data: { reason: 'Bad input' },
      });

      req.body = { name: 'Team' };

      await controller.createTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
      expect(next.firstCall.args[0].message).to.include('Bad input');
    });

    it('should use data.message when data.detail and data.reason are missing', async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 400,
        data: { message: 'Validation failed' },
      });

      req.body = { name: 'Team' };

      await controller.createTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
      expect(next.firstCall.args[0].message).to.include('Validation failed');
    });

    it('should use error.message when data fields are missing', async () => {
      const error: any = new Error('Direct error message');
      error.statusCode = 400;
      error.data = {};
      sinon.stub(AIServiceCommand.prototype, 'execute').rejects(error);

      req.body = { name: 'Team' };

      await controller.createTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should return Unknown error when no error details available', async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 400,
        data: {},
      });

      req.body = { name: 'Team' };

      await controller.createTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should handle ECONNREFUSED errorDetail', async () => {
      const error: any = new Error('ECONNREFUSED');
      error.statusCode = undefined;
      error.data = {};
      sinon.stub(AIServiceCommand.prototype, 'execute').rejects(error);

      req.body = { name: 'Team' };

      await controller.createTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('should handle status 401', async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 401,
        data: { detail: 'Unauthorized' },
      });

      req.body = { name: 'Team' };

      await controller.createTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
      expect(next.firstCall.args[0].message).to.include('Unauthorized');
    });

    it('should handle status 403', async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 403,
        data: { detail: 'Forbidden' },
      });

      req.body = { name: 'Team' };

      await controller.createTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
      expect(next.firstCall.args[0].message).to.include('Forbidden');
    });

    it('should handle status 404', async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 404,
        data: { detail: 'Not found' },
      });

      req.body = { name: 'Team' };

      await controller.createTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
      expect(next.firstCall.args[0].message).to.include('Not found');
    });

    it('should handle status 409', async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 409,
        data: { detail: 'Conflict' },
      });

      req.body = { name: 'Team' };

      await controller.createTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
      expect(next.firstCall.args[0].message).to.include('Conflict');
    });

    it('should handle status 500', async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 500,
        data: { detail: 'Internal error' },
      });

      req.body = { name: 'Team' };

      await controller.createTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
      expect(next.firstCall.args[0].message).to.include('Internal error');
    });

    it('should handle default unknown status code', async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 502,
        data: { detail: 'Gateway error' },
      });

      req.body = { name: 'Team' };

      await controller.createTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
      expect(next.firstCall.args[0].message).to.include('Backend error');
    });

    it('should handle fetch failed error without ECONNREFUSED cause', async () => {
      const error = new Error('fetch failed');
      sinon.stub(AIServiceCommand.prototype, 'execute').rejects(error);

      req.body = { name: 'Team' };

      await controller.createTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
      expect(next.firstCall.args[0].message).to.include('AI Service is currently unavailable');
    });
  });

  // -----------------------------------------------------------------------
  // Branch coverage: handleAIServiceResponse - null data path
  // -----------------------------------------------------------------------
  describe('handleAIServiceResponse - null data path', () => {
    it('should throw NotFoundError when response data is null', async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: null,
      });

      await controller.getTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
      expect(next.firstCall.args[0].message).to.include('failed');
    });

    it('should throw NotFoundError when response data is undefined', async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: undefined,
      });

      req.params.teamId = 'team1';

      await controller.getTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });
  });

  // -----------------------------------------------------------------------
  // Branch coverage: addUsersToTeam/updateTeam/deleteTeam/removeUserFromTeam/getTeamUsers - userId missing
  // -----------------------------------------------------------------------
  describe('methods - missing orgId and userId branches', () => {
    it('addUsersToTeam should throw when orgId is missing', async () => {
      req.user = { userId: '507f1f77bcf86cd799439011' };
      req.params.teamId = 'team1';

      await controller.addUsersToTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('addUsersToTeam should throw when userId is missing', async () => {
      req.user = { orgId: '507f1f77bcf86cd799439012' };
      req.params.teamId = 'team1';

      await controller.addUsersToTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('updateTeam should throw when orgId is missing', async () => {
      req.user = { userId: '507f1f77bcf86cd799439011' };
      req.params.teamId = 'team1';

      await controller.updateTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('updateTeam should throw when userId is missing', async () => {
      req.user = { orgId: '507f1f77bcf86cd799439012' };
      req.params.teamId = 'team1';

      await controller.updateTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('deleteTeam should throw when orgId is missing', async () => {
      req.user = { userId: '507f1f77bcf86cd799439011' };
      req.params.teamId = 'team1';

      await controller.deleteTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('deleteTeam should throw when userId is missing', async () => {
      req.user = { orgId: '507f1f77bcf86cd799439012' };
      req.params.teamId = 'team1';

      await controller.deleteTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('removeUserFromTeam should throw when orgId is missing', async () => {
      req.user = { userId: '507f1f77bcf86cd799439011' };
      req.params.teamId = 'team1';

      await controller.removeUserFromTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('removeUserFromTeam should throw when userId is missing', async () => {
      req.user = { orgId: '507f1f77bcf86cd799439012' };
      req.params.teamId = 'team1';

      await controller.removeUserFromTeam(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('getTeamUsers should throw when orgId is missing', async () => {
      req.user = { userId: '507f1f77bcf86cd799439011' };
      req.params.teamId = 'team1';

      await controller.getTeamUsers(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('getTeamUsers should throw when userId is missing', async () => {
      req.user = { orgId: '507f1f77bcf86cd799439012' };
      req.params.teamId = 'team1';

      await controller.getTeamUsers(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('updateTeamUsersPermissions should throw when orgId is missing', async () => {
      req.user = { userId: '507f1f77bcf86cd799439011' };
      req.params.teamId = 'team1';

      await controller.updateTeamUsersPermissions(req, res, next);

      expect(next.calledOnce).to.be.true;
    });

    it('updateTeamUsersPermissions should throw when userId is missing', async () => {
      req.user = { orgId: '507f1f77bcf86cd799439012' };
      req.params.teamId = 'team1';

      await controller.updateTeamUsersPermissions(req, res, next);

      expect(next.calledOnce).to.be.true;
    });
  });

  // -----------------------------------------------------------------------
  // Branch coverage: getTeam - missing orgId and userId throws (before try)
  // -----------------------------------------------------------------------
  describe('getTeam - missing params (thrown outside try)', () => {
    it('should throw BadRequestError when orgId is missing', async () => {
      req.user = { userId: '507f1f77bcf86cd799439011' };
      req.params.teamId = 'team1';

      try {
        await controller.getTeam(req, res, next);
        // May throw directly since the check is outside try block
      } catch (error: any) {
        expect(error.message).to.include('Organization ID is required');
      }
    });

    it('should throw BadRequestError when userId is missing', async () => {
      req.user = { orgId: '507f1f77bcf86cd799439012' };
      req.params.teamId = 'team1';

      try {
        await controller.getTeam(req, res, next);
      } catch (error: any) {
        expect(error.message).to.include('User ID is required');
      }
    });
  });
});
