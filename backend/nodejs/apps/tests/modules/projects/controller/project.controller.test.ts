import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import {
  createProject,
  listProjects,
  getProjectById,
  updateProject,
  deleteProject,
  archiveProject,
  unarchiveProject,
  pinProject,
  unpinProject,
  getProjectConversations,
  listProjectMembers,
  upsertProjectMembers,
  removeProjectMember,
  ensureProjectKnowledgeBase,
} from '../../../../src/modules/projects/controller/project.controller'
import { ProjectService } from '../../../../src/modules/projects/services/project.service'
import { ProjectKnowledgeBaseService } from '../../../../src/modules/projects/services/project-kb.service'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import { IAMServiceCommand } from '../../../../src/libs/commands/iam/iam.service.command'
import { BadRequestError, ForbiddenError, NotFoundError } from '../../../../src/libs/errors/http.errors'

const VALID_OID = 'aaaaaaaaaaaaaaaaaaaaaaaa'
const VALID_OID2 = 'bbbbbbbbbbbbbbbbbbbbbbbb'
const PROJECT_ID = 'cccccccccccccccccccccccc'

function createMockAppConfig(): any {
  return {
    aiBackend: 'http://localhost:8000',
    connectorBackend: 'http://localhost:8088',
    jwtSecret: 'test-jwt-secret',
    scopedJwtSecret: 'test-scoped-secret',
    cmBackend: 'http://localhost:3001',
    iamBackend: 'http://localhost:3001',
    frontendUrl: 'http://localhost:3000',
  }
}

function createMockRequest(overrides: Record<string, any> = {}): any {
  return {
    headers: { authorization: 'Bearer test-token' },
    body: {},
    params: {},
    query: {},
    user: { userId: VALID_OID, orgId: VALID_OID2, email: 'test@test.com', fullName: 'Test User' },
    ...overrides,
  }
}

function createMockResponse(): any {
  const res: any = {
    status: sinon.stub(),
    json: sinon.stub(),
  }
  res.status.returns(res)
  res.json.returns(res)
  return res
}

function createMockNext(): sinon.SinonStub {
  return sinon.stub()
}

function makeProjectDoc(overrides: Record<string, any> = {}): any {
  const base = {
    orgId: new mongoose.Types.ObjectId(VALID_OID2),
    userId: new mongoose.Types.ObjectId(VALID_OID),
    name: 'Q3 Plan',
    tools: [] as string[],
    linkedKnowledgeBaseId: null,
    members: [] as any[],
    visibility: 'private',
    chatSharing: 'private',
    isPinned: false,
    isArchived: false,
    isDeleted: false,
    lastActivityAt: Date.now(),
    ...overrides,
  }
  return {
    ...base,
    toObject: () => ({ ...base }),
  }
}

/** `resolveCallerTeamIds` (team-membership.ts) hits this same class for `entity/user/teams` — stub it so route handlers under test never issue a real HTTP call. */
function stubNoTeamMemberships(): sinon.SinonStub {
  return sinon
    .stub(AIServiceCommand.prototype, 'execute')
    .resolves({ statusCode: 200, data: { teams: [] } } as any)
}

describe('project.controller', () => {
  // Inside the describe: at file level, mocha applies it to every file in a serial (--no-parallel) run.
  afterEach(() => {
    sinon.restore()
  })

  describe('createProject', () => {
    it('creates a project and returns 201', async () => {
      const project = makeProjectDoc({ name: 'New Project' })
      sinon.stub(ProjectService, 'create').resolves(project)

      const req = createMockRequest({ body: { name: 'New Project' } })
      const res = createMockResponse()
      const next = createMockNext()

      await createProject(req, res, next)

      expect(next.called).to.be.false
      expect(res.status.calledWith(201)).to.be.true
      const body = res.json.firstCall.args[0]
      expect(body.project).to.deep.include({ ...project.toObject(), role: 'owner' })
    })

    it('forwards service errors to next', async () => {
      const error = new Error('validation failed')
      sinon.stub(ProjectService, 'create').rejects(error)

      const req = createMockRequest({ body: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await createProject(req, res, next)

      expect(next.calledWith(error)).to.be.true
      expect(res.status.called).to.be.false
    })
  })

  describe('listProjects', () => {
    it('paginates and returns totalPages computed from totalCount/limit', async () => {
      stubNoTeamMemberships()
      const rows = [makeProjectDoc()]
      sinon.stub(ProjectService, 'list').resolves({ projects: rows as any, totalCount: 25 })

      const req = createMockRequest({
        query: { page: 1, limit: 10, scope: 'mine', includeArchived: false },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await listProjects(createMockAppConfig())(req, res, next)

      expect(next.called).to.be.false
      expect(res.status.calledWith(200)).to.be.true
      const body = res.json.firstCall.args[0]
      expect(body.projects).to.equal(rows)
      expect(body.pagination).to.deep.equal({ page: 1, limit: 10, totalCount: 25, totalPages: 3 })
    })

    it('passes the exact archive filter through to ProjectService.list', async () => {
      stubNoTeamMemberships()
      const listStub = sinon.stub(ProjectService, 'list').resolves({
        projects: [],
        totalCount: 0,
      })

      const req = createMockRequest({
        query: {
          page: 1,
          limit: 10,
          scope: 'all',
          includeArchived: false,
          isArchived: true,
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await listProjects(createMockAppConfig())(req, res, next)

      expect(next.called).to.be.false
      expect(listStub.firstCall.args[2]).to.deep.include({
        includeArchived: false,
        isArchived: true,
      })
    })

    it('forwards service errors to next', async () => {
      stubNoTeamMemberships()
      const error = new Error('db down')
      sinon.stub(ProjectService, 'list').rejects(error)

      const req = createMockRequest({ query: { page: 1, limit: 10, scope: 'mine', includeArchived: false } })
      const res = createMockResponse()
      const next = createMockNext()

      await listProjects(createMockAppConfig())(req, res, next)

      expect(next.calledWith(error)).to.be.true
    })

    it('passes the resolved caller team ids through to ProjectService.list', async () => {
      sinon
        .stub(AIServiceCommand.prototype, 'execute')
        .resolves({ statusCode: 200, data: { teams: [{ id: 'team-1' }] } } as any)
      const listStub = sinon.stub(ProjectService, 'list').resolves({ projects: [], totalCount: 0 })

      const req = createMockRequest({
        query: { page: 1, limit: 10, scope: 'all', includeArchived: false },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await listProjects(createMockAppConfig())(req, res, next)

      expect(listStub.firstCall.args[3]).to.deep.equal(['team-1'])
    })
  })

  describe('getProjectById', () => {
    it('merges the computed role onto the plain project object', async () => {
      stubNoTeamMemberships()
      const project = makeProjectDoc({ name: 'Shared Project' })
      sinon.stub(ProjectService, 'assertAccess').resolves({ role: 'editor', project })

      const req = createMockRequest({ params: { projectId: PROJECT_ID } })
      const res = createMockResponse()
      const next = createMockNext()

      await getProjectById(createMockAppConfig())(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      const body = res.json.firstCall.args[0]
      expect(body.project.role).to.equal('editor')
      expect(body.project.name).to.equal('Shared Project')
    })

    it('forwards NotFoundError/ForbiddenError from assertAccess to next', async () => {
      stubNoTeamMemberships()
      const error = new Error('Project not found')
      sinon.stub(ProjectService, 'assertAccess').rejects(error)

      const req = createMockRequest({ params: { projectId: PROJECT_ID } })
      const res = createMockResponse()
      const next = createMockNext()

      await getProjectById(createMockAppConfig())(req, res, next)

      expect(next.calledWith(error)).to.be.true
    })
  })

  describe('updateProject', () => {
    it('applies the patch and returns the updated project with role', async () => {
      stubNoTeamMemberships()
      const updated = makeProjectDoc({ name: 'Renamed' })
      sinon.stub(ProjectService, 'update').resolves(updated)
      sinon.stub(ProjectService, 'computeRole').returns('editor')

      const req = createMockRequest({ params: { projectId: PROJECT_ID }, body: { name: 'Renamed' } })
      const res = createMockResponse()
      const next = createMockNext()

      await updateProject(createMockAppConfig())(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      const body = res.json.firstCall.args[0]
      expect(body.project).to.deep.include({ ...updated.toObject(), role: 'editor' })
    })

    it('grants the org team KB permission when visibility changes to org', async () => {
      stubNoTeamMemberships()
      const updated = makeProjectDoc({ visibility: 'org', linkedKnowledgeBaseId: 'kb-1' })
      sinon.stub(ProjectService, 'update').resolves(updated)
      const syncStub = sinon.stub(ProjectKnowledgeBaseService, 'syncMemberPermissions').resolves()
      const revokeStub = sinon.stub(ProjectKnowledgeBaseService, 'revokeOrgVisibility').resolves()

      const req = createMockRequest({
        params: { projectId: PROJECT_ID },
        body: { visibility: 'org' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await updateProject(createMockAppConfig())(req, res, next)

      expect(syncStub.calledWith(sinon.match.any, sinon.match.any, updated)).to.be.true
      expect(revokeStub.called).to.be.false
    })

    it('revokes the org team KB permission when visibility changes to private', async () => {
      stubNoTeamMemberships()
      const updated = makeProjectDoc({ visibility: 'private', linkedKnowledgeBaseId: 'kb-1' })
      sinon.stub(ProjectService, 'update').resolves(updated)
      const syncStub = sinon.stub(ProjectKnowledgeBaseService, 'syncMemberPermissions').resolves()
      const revokeStub = sinon.stub(ProjectKnowledgeBaseService, 'revokeOrgVisibility').resolves()

      const req = createMockRequest({
        params: { projectId: PROJECT_ID },
        body: { visibility: 'private' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await updateProject(createMockAppConfig())(req, res, next)

      expect(revokeStub.calledWith(sinon.match.any, sinon.match.any, updated)).to.be.true
      expect(syncStub.called).to.be.false
    })

    it('skips visibility sync when the patch does not touch visibility', async () => {
      stubNoTeamMemberships()
      const updated = makeProjectDoc({ linkedKnowledgeBaseId: 'kb-1' })
      sinon.stub(ProjectService, 'update').resolves(updated)
      const syncStub = sinon.stub(ProjectKnowledgeBaseService, 'syncMemberPermissions').resolves()
      const revokeStub = sinon.stub(ProjectKnowledgeBaseService, 'revokeOrgVisibility').resolves()

      const req = createMockRequest({ params: { projectId: PROJECT_ID }, body: { name: 'Renamed' } })
      const res = createMockResponse()
      const next = createMockNext()

      await updateProject(createMockAppConfig())(req, res, next)

      expect(syncStub.called).to.be.false
      expect(revokeStub.called).to.be.false
    })

    it('skips visibility sync when the project has no linked KB yet', async () => {
      stubNoTeamMemberships()
      const updated = makeProjectDoc({ visibility: 'org', linkedKnowledgeBaseId: null })
      sinon.stub(ProjectService, 'update').resolves(updated)
      const syncStub = sinon.stub(ProjectKnowledgeBaseService, 'syncMemberPermissions').resolves()
      const revokeStub = sinon.stub(ProjectKnowledgeBaseService, 'revokeOrgVisibility').resolves()

      const req = createMockRequest({ params: { projectId: PROJECT_ID }, body: { visibility: 'org' } })
      const res = createMockResponse()
      const next = createMockNext()

      await updateProject(createMockAppConfig())(req, res, next)

      expect(syncStub.called).to.be.false
      expect(revokeStub.called).to.be.false
      expect(res.status.calledWith(200)).to.be.true
    })

    it('forwards a service error to next without syncing or responding', async () => {
      stubNoTeamMemberships()
      const error = new ForbiddenError('Only the project owner can change sharing settings')
      sinon.stub(ProjectService, 'update').rejects(error)
      const syncStub = sinon.stub(ProjectKnowledgeBaseService, 'syncMemberPermissions').resolves()

      const req = createMockRequest({ params: { projectId: PROJECT_ID }, body: { visibility: 'org' } })
      const res = createMockResponse()
      const next = createMockNext()

      await updateProject(createMockAppConfig())(req, res, next)

      expect(next.calledOnceWith(error)).to.be.true
      expect(syncStub.called).to.be.false
      expect(res.status.called).to.be.false
    })

    it('reports a failed org-visibility revoke instead of answering 200 with a stale KB edge', async () => {
      stubNoTeamMemberships()
      const updated = makeProjectDoc({ visibility: 'private', linkedKnowledgeBaseId: 'kb-1' })
      sinon.stub(ProjectService, 'update').resolves(updated)
      const error = new Error('KB service unavailable')
      sinon.stub(ProjectKnowledgeBaseService, 'revokeOrgVisibility').rejects(error)

      const req = createMockRequest({ params: { projectId: PROJECT_ID }, body: { visibility: 'private' } })
      const res = createMockResponse()
      const next = createMockNext()

      await updateProject(createMockAppConfig())(req, res, next)

      expect(next.calledOnceWith(error)).to.be.true
      expect(res.status.called).to.be.false
    })

    it('passes the resolved caller team ids to both the update and the role computation', async () => {
      sinon
        .stub(AIServiceCommand.prototype, 'execute')
        .resolves({ statusCode: 200, data: { teams: [{ id: 'team-1' }] } } as any)
      const updated = makeProjectDoc()
      const updateStub = sinon.stub(ProjectService, 'update').resolves(updated)
      const roleStub = sinon.stub(ProjectService, 'computeRole').returns('editor')

      const req = createMockRequest({ params: { projectId: PROJECT_ID }, body: { name: 'Renamed' } })

      await updateProject(createMockAppConfig())(req, createMockResponse(), createMockNext())

      expect(updateStub.firstCall.args[4]).to.deep.equal(['team-1'])
      expect(roleStub.firstCall.args).to.deep.equal([updated, VALID_OID, VALID_OID2, ['team-1']])
    })
  })

  describe('deleteProject', () => {
    it('answers a repeated delete by the owner with success, touching nothing', async () => {
      sinon.stub(ProjectService, 'isDeletedByOwner').resolves(true)
      const assertAccessStub = sinon.stub(ProjectService, 'assertAccess')
      const deleteLinkedKbStub = sinon.stub(ProjectKnowledgeBaseService, 'deleteLinkedKb').resolves()
      const softDeleteStub = sinon.stub(ProjectService, 'softDelete').resolves()

      const req = createMockRequest({ params: { projectId: PROJECT_ID } })
      const res = createMockResponse()
      const next = createMockNext()

      await deleteProject(createMockAppConfig())(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.calledWith({ message: 'Project deleted successfully' })).to.be.true
      expect(assertAccessStub.called).to.be.false
      expect(deleteLinkedKbStub.called).to.be.false
      expect(softDeleteStub.called).to.be.false
      expect(next.called).to.be.false
    })

    it('answers success when another delete lands between the two lookups', async () => {
      const isDeletedStub = sinon.stub(ProjectService, 'isDeletedByOwner')
      isDeletedStub.onFirstCall().resolves(false)
      isDeletedStub.onSecondCall().resolves(true)
      sinon.stub(ProjectService, 'assertAccess').rejects(new NotFoundError('Project not found'))
      const deleteLinkedKbStub = sinon.stub(ProjectKnowledgeBaseService, 'deleteLinkedKb').resolves()
      const softDeleteStub = sinon.stub(ProjectService, 'softDelete').resolves()

      const req = createMockRequest({ params: { projectId: PROJECT_ID } })
      const res = createMockResponse()
      const next = createMockNext()

      await deleteProject(createMockAppConfig())(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(next.called).to.be.false
      expect(deleteLinkedKbStub.called).to.be.false
      expect(softDeleteStub.called).to.be.false
    })

    it('still answers 404 when the project is gone and was not deleted by the caller', async () => {
      sinon.stub(ProjectService, 'isDeletedByOwner').resolves(false)
      const error = new NotFoundError('Project not found')
      sinon.stub(ProjectService, 'assertAccess').rejects(error)

      const req = createMockRequest({ params: { projectId: PROJECT_ID } })
      const res = createMockResponse()
      const next = createMockNext()

      await deleteProject(createMockAppConfig())(req, res, next)

      expect(next.calledWith(error)).to.be.true
      expect(res.status.called).to.be.false
    })

    it('deletes the linked KB before soft-deleting and returns a success message', async () => {
      const project = makeProjectDoc({ linkedKnowledgeBaseId: 'kb-1' })
      sinon.stub(ProjectService, 'isDeletedByOwner').resolves(false)
      sinon.stub(ProjectService, 'assertAccess').resolves({ role: 'owner', project })
      const deleteLinkedKbStub = sinon.stub(ProjectKnowledgeBaseService, 'deleteLinkedKb').resolves()
      const softDeleteStub = sinon.stub(ProjectService, 'softDelete').resolves()

      const req = createMockRequest({ params: { projectId: PROJECT_ID } })
      const res = createMockResponse()
      const next = createMockNext()

      await deleteProject(createMockAppConfig())(req, res, next)

      expect(deleteLinkedKbStub.calledBefore(softDeleteStub)).to.be.true
      expect(deleteLinkedKbStub.firstCall.args[2]).to.equal(project)
      expect(softDeleteStub.calledWith(VALID_OID2, VALID_OID, PROJECT_ID)).to.be.true
      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.calledWith({ message: 'Project deleted successfully' })).to.be.true
    })

    it('forwards ForbiddenError when the caller is not the owner, without touching the KB or Mongo', async () => {
      const project = makeProjectDoc()
      sinon.stub(ProjectService, 'isDeletedByOwner').resolves(false)
      sinon.stub(ProjectService, 'assertAccess').resolves({ role: 'editor', project })
      const deleteLinkedKbStub = sinon.stub(ProjectKnowledgeBaseService, 'deleteLinkedKb').resolves()
      const softDeleteStub = sinon.stub(ProjectService, 'softDelete').resolves()

      const req = createMockRequest({ params: { projectId: PROJECT_ID } })
      const res = createMockResponse()
      const next = createMockNext()

      await deleteProject(createMockAppConfig())(req, res, next)

      expect(next.called).to.be.true
      expect(next.firstCall.args[0].message).to.include('Only the project owner')
      expect(deleteLinkedKbStub.called).to.be.false
      expect(softDeleteStub.called).to.be.false
    })

    it('propagates a KB deletion failure instead of soft-deleting anyway', async () => {
      const project = makeProjectDoc({ linkedKnowledgeBaseId: 'kb-1' })
      sinon.stub(ProjectService, 'isDeletedByOwner').resolves(false)
      sinon.stub(ProjectService, 'assertAccess').resolves({ role: 'owner', project })
      const error = new Error('connector service unavailable')
      sinon.stub(ProjectKnowledgeBaseService, 'deleteLinkedKb').rejects(error)
      const softDeleteStub = sinon.stub(ProjectService, 'softDelete').resolves()

      const req = createMockRequest({ params: { projectId: PROJECT_ID } })
      const res = createMockResponse()
      const next = createMockNext()

      await deleteProject(createMockAppConfig())(req, res, next)

      expect(next.calledWith(error)).to.be.true
      expect(softDeleteStub.called).to.be.false
    })
  })

  describe('archive / unarchive / pin / unpin', () => {
    it('archiveProject calls setArchived(true) and includes role', async () => {
      stubNoTeamMemberships()
      const project = makeProjectDoc({ isArchived: true })
      const stub = sinon.stub(ProjectService, 'setArchived').resolves(project)
      sinon.stub(ProjectService, 'computeRole').returns('owner')

      const req = createMockRequest({ params: { projectId: PROJECT_ID } })
      const res = createMockResponse()
      const next = createMockNext()

      await archiveProject(createMockAppConfig())(req, res, next)

      expect(stub.calledWith(VALID_OID2, VALID_OID, PROJECT_ID, true)).to.be.true
      const body = res.json.firstCall.args[0]
      expect(body.project).to.deep.include({ ...project.toObject(), role: 'owner' })
    })

    it('unarchiveProject calls setArchived(false)', async () => {
      stubNoTeamMemberships()
      const project = makeProjectDoc({ isArchived: false })
      const stub = sinon.stub(ProjectService, 'setArchived').resolves(project)

      const req = createMockRequest({ params: { projectId: PROJECT_ID } })
      const res = createMockResponse()
      const next = createMockNext()

      await unarchiveProject(createMockAppConfig())(req, res, next)

      expect(stub.calledWith(VALID_OID2, VALID_OID, PROJECT_ID, false)).to.be.true
    })

    it('pinProject calls setPinned(true)', async () => {
      stubNoTeamMemberships()
      const project = makeProjectDoc({ isPinned: true })
      const stub = sinon.stub(ProjectService, 'setPinned').resolves(project)

      const req = createMockRequest({ params: { projectId: PROJECT_ID } })
      const res = createMockResponse()
      const next = createMockNext()

      await pinProject(createMockAppConfig())(req, res, next)

      expect(stub.calledWith(VALID_OID2, VALID_OID, PROJECT_ID, true)).to.be.true
    })

    it('unpinProject calls setPinned(false)', async () => {
      stubNoTeamMemberships()
      const project = makeProjectDoc({ isPinned: false })
      const stub = sinon.stub(ProjectService, 'setPinned').resolves(project)

      const req = createMockRequest({ params: { projectId: PROJECT_ID } })
      const res = createMockResponse()
      const next = createMockNext()

      await unpinProject(createMockAppConfig())(req, res, next)

      expect(stub.calledWith(VALID_OID2, VALID_OID, PROJECT_ID, false)).to.be.true
    })

    const toggles = [
      { name: 'archiveProject', handler: archiveProject, method: 'setArchived' as const },
      { name: 'unarchiveProject', handler: unarchiveProject, method: 'setArchived' as const },
      { name: 'pinProject', handler: pinProject, method: 'setPinned' as const },
      { name: 'unpinProject', handler: unpinProject, method: 'setPinned' as const },
    ]

    for (const { name, handler, method } of toggles) {
      it(`${name} answers 200 with the caller's role computed from their team memberships`, async () => {
        sinon
          .stub(AIServiceCommand.prototype, 'execute')
          .resolves({ statusCode: 200, data: { teams: [{ id: 'team-1' }] } } as any)
        const project = makeProjectDoc()
        const serviceStub = sinon.stub(ProjectService, method).resolves(project)
        const roleStub = sinon.stub(ProjectService, 'computeRole').returns('editor')

        const req = createMockRequest({ params: { projectId: PROJECT_ID } })
        const res = createMockResponse()
        const next = createMockNext()

        await handler(createMockAppConfig())(req, res, next)

        expect(next.called).to.be.false
        expect(serviceStub.firstCall.args[4]).to.deep.equal(['team-1'])
        expect(roleStub.firstCall.args[3]).to.deep.equal(['team-1'])
        expect(res.status.calledOnceWith(200)).to.be.true
        expect(res.json.firstCall.args[0].project.role).to.equal('editor')
      })

      it(`${name} forwards a viewer's ForbiddenError to next without responding`, async () => {
        stubNoTeamMemberships()
        const error = new ForbiddenError('This action needs the editor role on the project')
        sinon.stub(ProjectService, method).rejects(error)
        const roleStub = sinon.stub(ProjectService, 'computeRole')

        const req = createMockRequest({ params: { projectId: PROJECT_ID } })
        const res = createMockResponse()
        const next = createMockNext()

        await handler(createMockAppConfig())(req, res, next)

        expect(next.calledOnceWith(error)).to.be.true
        expect(roleStub.called).to.be.false
        expect(res.status.called).to.be.false
      })
    }
  })

  describe('getProjectConversations', () => {
    function makeFindChain(result: any[]) {
      return {
        sort: sinon.stub().returnsThis(),
        skip: sinon.stub().returnsThis(),
        limit: sinon.stub().returnsThis(),
        select: sinon.stub().returnsThis(),
        lean: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves(result),
      }
    }

    it('asserts at least viewer access before querying sessions', async () => {
      stubNoTeamMemberships()
      const assertAccessStub = sinon
        .stub(ProjectService, 'assertAccess')
        .resolves({ role: 'viewer', project: makeProjectDoc() })
      sinon.stub(ChatSession, 'find').returns(makeFindChain([]) as any)
      sinon.stub(ChatSession, 'countDocuments').resolves(0)

      const req = createMockRequest({ params: { projectId: PROJECT_ID }, query: { page: 1, limit: 20 } })
      const res = createMockResponse()
      const next = createMockNext()

      await getProjectConversations(createMockAppConfig())(req, res, next)

      expect(assertAccessStub.calledWith(VALID_OID2, VALID_OID, PROJECT_ID, 'viewer')).to.be.true
      expect(res.status.calledWith(200)).to.be.true
      const body = res.json.firstCall.args[0]
      expect(body.conversations).to.deep.equal([])
      expect(body.pagination).to.deep.equal({ page: 1, limit: 20, totalCount: 0, totalPages: 0 })
    })

    it('scopes the query to own rows OR project-visible rows within the project', async () => {
      stubNoTeamMemberships()
      sinon.stub(ProjectService, 'assertAccess').resolves({ role: 'viewer', project: makeProjectDoc() })
      const findStub = sinon.stub(ChatSession, 'find').returns(makeFindChain([]) as any)
      sinon.stub(ChatSession, 'countDocuments').resolves(0)

      const req = createMockRequest({ params: { projectId: PROJECT_ID }, query: { page: 1, limit: 20 } })
      const res = createMockResponse()
      const next = createMockNext()

      await getProjectConversations(createMockAppConfig())(req, res, next)

      const filter = findStub.firstCall.args[0] as any
      expect(filter.projectId.toString()).to.equal(PROJECT_ID)
      expect(filter.isDeleted).to.equal(false)
      expect(filter.$or).to.deep.equal([
        { userId: new mongoose.Types.ObjectId(VALID_OID) },
        { projectVisibility: 'project' },
      ])
    })

    it('rejects with the access error when the caller has no access', async () => {
      stubNoTeamMemberships()
      const error = new Error('Project not found')
      sinon.stub(ProjectService, 'assertAccess').rejects(error)

      const req = createMockRequest({ params: { projectId: PROJECT_ID }, query: { page: 1, limit: 20 } })
      const res = createMockResponse()
      const next = createMockNext()

      await getProjectConversations(createMockAppConfig())(req, res, next)

      expect(next.calledWith(error)).to.be.true
    })
  })

  describe('listProjectMembers', () => {
    it('returns the member list', async () => {
      stubNoTeamMemberships()
      const members = [{ principalType: 'user', principalId: VALID_OID, role: 'viewer' }]
      sinon.stub(ProjectService, 'listMembers').resolves(members as any)

      const req = createMockRequest({ params: { projectId: PROJECT_ID } })
      const res = createMockResponse()
      const next = createMockNext()

      await listProjectMembers(createMockAppConfig())(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.calledWith({ members })).to.be.true
    })

    it('looks the members up as the caller, including their team memberships', async () => {
      sinon
        .stub(AIServiceCommand.prototype, 'execute')
        .resolves({ statusCode: 200, data: { teams: [{ id: 'team-1' }] } } as any)
      const listStub = sinon.stub(ProjectService, 'listMembers').resolves([])

      const req = createMockRequest({ params: { projectId: PROJECT_ID } })

      await listProjectMembers(createMockAppConfig())(req, createMockResponse(), createMockNext())

      expect(listStub.firstCall.args).to.deep.equal([VALID_OID2, VALID_OID, PROJECT_ID, ['team-1']])
    })

    it('forwards NotFoundError for a caller who cannot see the project, without responding', async () => {
      stubNoTeamMemberships()
      const error = new NotFoundError('Project not found')
      sinon.stub(ProjectService, 'listMembers').rejects(error)

      const req = createMockRequest({ params: { projectId: PROJECT_ID } })
      const res = createMockResponse()
      const next = createMockNext()

      await listProjectMembers(createMockAppConfig())(req, res, next)

      expect(next.calledOnceWith(error)).to.be.true
      expect(res.status.called).to.be.false
    })
  })

  describe('upsertProjectMembers', () => {
    it('rejects when a user member does not exist in IAM', async () => {
      sinon.stub(IAMServiceCommand.prototype, 'execute').resolves({ statusCode: 404, data: null } as any)

      const handler = upsertProjectMembers(createMockAppConfig())
      const req = createMockRequest({
        params: { projectId: PROJECT_ID },
        body: { members: [{ principalId: VALID_OID2, role: 'viewer' }] },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0].message).to.include('User not found')
    })

    it('rejects when a team member does not exist in the graph', async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 404, data: null } as any)

      const handler = upsertProjectMembers(createMockAppConfig())
      const req = createMockRequest({
        params: { projectId: PROJECT_ID },
        body: { members: [{ principalId: VALID_OID2, principalType: 'team', role: 'viewer' }] },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0].message).to.include('Team not found')
    })

    it('upserts a validated user member and returns the updated member list', async () => {
      sinon.stub(IAMServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: { _id: VALID_OID2 } } as any)
      const updated = makeProjectDoc({
        members: [{ principalType: 'user', principalId: new mongoose.Types.ObjectId(VALID_OID2), role: 'viewer' }],
      })
      const upsertStub = sinon.stub(ProjectService, 'upsertMembers').resolves(updated)

      const handler = upsertProjectMembers(createMockAppConfig())
      const members = [{ principalId: VALID_OID2, role: 'viewer' as const }]
      const req = createMockRequest({
        params: { projectId: PROJECT_ID },
        body: { members },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(upsertStub.calledWith(VALID_OID2, VALID_OID, PROJECT_ID, members)).to.be.true
      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.calledWith({ members: updated.members })).to.be.true
    })

    it('upserts a validated team member without an IAM user lookup', async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: {} } as any)
      const iamStub = sinon.stub(IAMServiceCommand.prototype, 'execute')
      const updated = makeProjectDoc({
        members: [{ principalType: 'team', principalId: new mongoose.Types.ObjectId(VALID_OID2), role: 'editor' }],
      })
      const upsertStub = sinon.stub(ProjectService, 'upsertMembers').resolves(updated)

      const handler = upsertProjectMembers(createMockAppConfig())
      const members = [{ principalId: VALID_OID2, principalType: 'team' as const, role: 'editor' as const }]
      const req = createMockRequest({
        params: { projectId: PROJECT_ID },
        body: { members },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(iamStub.called).to.be.false
      expect(upsertStub.calledWith(VALID_OID2, VALID_OID, PROJECT_ID, members)).to.be.true
      expect(res.status.calledWith(200)).to.be.true
    })

    it('syncs KB permissions when the project has a linked KB', async () => {
      sinon.stub(IAMServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: { _id: VALID_OID2 } } as any)
      const updated = makeProjectDoc({
        linkedKnowledgeBaseId: 'kb-1',
        members: [{ principalType: 'user', principalId: new mongoose.Types.ObjectId(VALID_OID2), role: 'viewer' }],
      })
      sinon.stub(ProjectService, 'upsertMembers').resolves(updated)
      const syncStub = sinon.stub(ProjectKnowledgeBaseService, 'syncMemberPermissions').resolves()

      const handler = upsertProjectMembers(createMockAppConfig())
      const req = createMockRequest({
        params: { projectId: PROJECT_ID },
        body: { members: [{ principalId: VALID_OID2, role: 'viewer' as const }] },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(syncStub.calledWith(sinon.match.any, sinon.match.any, updated)).to.be.true
      expect(res.status.calledWith(200)).to.be.true
    })

    it('skips the KB sync call when the project has no linked KB', async () => {
      sinon.stub(IAMServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: { _id: VALID_OID2 } } as any)
      const updated = makeProjectDoc({
        members: [{ principalType: 'user', principalId: new mongoose.Types.ObjectId(VALID_OID2), role: 'viewer' }],
      })
      sinon.stub(ProjectService, 'upsertMembers').resolves(updated)
      const syncStub = sinon.stub(ProjectKnowledgeBaseService, 'syncMemberPermissions').resolves()

      const handler = upsertProjectMembers(createMockAppConfig())
      const req = createMockRequest({
        params: { projectId: PROJECT_ID },
        body: { members: [{ principalId: VALID_OID2, role: 'viewer' as const }] },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(syncStub.called).to.be.false
    })

    it('forwards ForbiddenError from the service (non-owner caller) to next', async () => {
      sinon.stub(IAMServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: {} } as any)
      const error = new Error('Only the project owner can manage members')
      sinon.stub(ProjectService, 'upsertMembers').rejects(error)

      const handler = upsertProjectMembers(createMockAppConfig())
      const req = createMockRequest({
        params: { projectId: PROJECT_ID },
        body: { members: [{ principalId: VALID_OID2, role: 'viewer' }] },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledWith(error)).to.be.true
    })

    it('rejects a user member when the IAM lookup itself throws, without upserting', async () => {
      sinon.stub(IAMServiceCommand.prototype, 'execute').rejects(new Error('connect ECONNREFUSED'))
      const upsertStub = sinon.stub(ProjectService, 'upsertMembers')

      const req = createMockRequest({
        params: { projectId: PROJECT_ID },
        body: { members: [{ principalId: VALID_OID2, role: 'viewer' }] },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await upsertProjectMembers(createMockAppConfig())(req, res, next)

      const forwarded = next.firstCall.args[0]
      expect(forwarded).to.be.instanceOf(BadRequestError)
      expect(forwarded.message).to.equal(`User not found: ${VALID_OID2}`)
      expect(upsertStub.called).to.be.false
    })

    it('rejects a team member when the team lookup itself throws, without upserting', async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').rejects(new Error('connect ECONNREFUSED'))
      const upsertStub = sinon.stub(ProjectService, 'upsertMembers')

      const req = createMockRequest({
        params: { projectId: PROJECT_ID },
        body: { members: [{ principalId: VALID_OID2, principalType: 'team', role: 'viewer' }] },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await upsertProjectMembers(createMockAppConfig())(req, res, next)

      const forwarded = next.firstCall.args[0]
      expect(forwarded).to.be.instanceOf(BadRequestError)
      expect(forwarded.message).to.equal(`Team not found: ${VALID_OID2}`)
      expect(upsertStub.called).to.be.false
    })

    it('upserts nobody when one member of a mixed batch fails validation', async () => {
      sinon.stub(IAMServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: {} } as any)
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 404, data: null } as any)
      const upsertStub = sinon.stub(ProjectService, 'upsertMembers')

      const req = createMockRequest({
        params: { projectId: PROJECT_ID },
        body: {
          members: [
            { principalId: VALID_OID, role: 'editor' },
            { principalId: VALID_OID2, principalType: 'team', role: 'viewer' },
          ],
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await upsertProjectMembers(createMockAppConfig())(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0].message).to.equal(`Team not found: ${VALID_OID2}`)
      expect(upsertStub.called).to.be.false
      expect(res.status.called).to.be.false
    })

    it('reports a failed KB permission sync instead of answering 200', async () => {
      sinon.stub(IAMServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: {} } as any)
      sinon.stub(ProjectService, 'upsertMembers').resolves(makeProjectDoc({ linkedKnowledgeBaseId: 'kb-1' }))
      const error = new Error('KB service unavailable')
      sinon.stub(ProjectKnowledgeBaseService, 'syncMemberPermissions').rejects(error)

      const req = createMockRequest({
        params: { projectId: PROJECT_ID },
        body: { members: [{ principalId: VALID_OID2, role: 'viewer' }] },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await upsertProjectMembers(createMockAppConfig())(req, res, next)

      expect(next.calledOnceWith(error)).to.be.true
      expect(res.status.called).to.be.false
    })
  })

  describe('removeProjectMember', () => {
    it('removes a user member by default and returns the updated member list', async () => {
      const updated = makeProjectDoc({ members: [] })
      const removeMemberStub = sinon.stub(ProjectService, 'removeMember').resolves(updated)

      const req = createMockRequest({ params: { projectId: PROJECT_ID, memberUserId: VALID_OID2 } })
      const res = createMockResponse()
      const next = createMockNext()

      await removeProjectMember(createMockAppConfig())(req, res, next)

      expect(removeMemberStub.calledWith(VALID_OID2, VALID_OID, PROJECT_ID, VALID_OID2, 'user')).to.be.true
      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.calledWith({ members: updated.members })).to.be.true
    })

    it('removes a team member when principalType=team is passed as a query param', async () => {
      const updated = makeProjectDoc({ members: [] })
      const removeMemberStub = sinon.stub(ProjectService, 'removeMember').resolves(updated)

      const req = createMockRequest({
        params: { projectId: PROJECT_ID, memberUserId: VALID_OID2 },
        query: { principalType: 'team' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await removeProjectMember(createMockAppConfig())(req, res, next)

      expect(removeMemberStub.calledWith(VALID_OID2, VALID_OID, PROJECT_ID, VALID_OID2, 'team')).to.be.true
    })

    it('revokes the KB permission when the project has a linked KB', async () => {
      const updated = makeProjectDoc({ members: [], linkedKnowledgeBaseId: 'kb-1' })
      sinon.stub(ProjectService, 'removeMember').resolves(updated)
      const revokeStub = sinon.stub(ProjectKnowledgeBaseService, 'revokePrincipalPermission').resolves()

      const req = createMockRequest({
        params: { projectId: PROJECT_ID, memberUserId: VALID_OID2 },
        query: { principalType: 'team' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await removeProjectMember(createMockAppConfig())(req, res, next)

      expect(
        revokeStub.calledWith(sinon.match.any, sinon.match.any, updated, VALID_OID2, 'team'),
      ).to.be.true
      expect(res.status.calledWith(200)).to.be.true
    })

    it('skips the KB revoke call when the project has no linked KB', async () => {
      const updated = makeProjectDoc({ members: [] })
      sinon.stub(ProjectService, 'removeMember').resolves(updated)
      const revokeStub = sinon.stub(ProjectKnowledgeBaseService, 'revokePrincipalPermission').resolves()

      const req = createMockRequest({ params: { projectId: PROJECT_ID, memberUserId: VALID_OID2 } })
      const res = createMockResponse()
      const next = createMockNext()

      await removeProjectMember(createMockAppConfig())(req, res, next)

      expect(revokeStub.called).to.be.false
    })

    it('forwards ForbiddenError (non-owner) to next', async () => {
      const error = new Error('Only the project owner can manage members')
      sinon.stub(ProjectService, 'removeMember').rejects(error)

      const req = createMockRequest({ params: { projectId: PROJECT_ID, memberUserId: VALID_OID2 } })
      const res = createMockResponse()
      const next = createMockNext()

      await removeProjectMember(createMockAppConfig())(req, res, next)

      expect(next.calledWith(error)).to.be.true
    })

    it('reports a failed KB revoke instead of answering 200 while the member still holds KB access', async () => {
      sinon.stub(ProjectService, 'removeMember').resolves(makeProjectDoc({ linkedKnowledgeBaseId: 'kb-1' }))
      const error = new Error('KB service unavailable')
      sinon.stub(ProjectKnowledgeBaseService, 'revokePrincipalPermission').rejects(error)

      const req = createMockRequest({ params: { projectId: PROJECT_ID, memberUserId: VALID_OID2 } })
      const res = createMockResponse()
      const next = createMockNext()

      await removeProjectMember(createMockAppConfig())(req, res, next)

      expect(next.calledOnceWith(error)).to.be.true
      expect(res.status.called).to.be.false
    })
  })

  describe('ensureProjectKnowledgeBase', () => {
    it('asserts editor access and returns the linked KB id', async () => {
      stubNoTeamMemberships()
      const project = makeProjectDoc()
      sinon.stub(ProjectService, 'assertAccess').resolves({ role: 'editor', project })
      const ensureStub = sinon
        .stub(ProjectKnowledgeBaseService, 'ensureLinkedKb')
        .resolves('kb-42')

      const req = createMockRequest({ params: { projectId: PROJECT_ID } })
      const res = createMockResponse()
      const next = createMockNext()

      await ensureProjectKnowledgeBase(createMockAppConfig())(req, res, next)

      expect(ensureStub.calledWith(sinon.match.any, sinon.match.any, VALID_OID2, PROJECT_ID)).to.be.true
      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.calledWith({ kbId: 'kb-42' })).to.be.true
    })

    it('forwards NotFoundError from assertAccess (viewer-only caller) to next', async () => {
      stubNoTeamMemberships()
      const error = new Error('Project not found')
      sinon.stub(ProjectService, 'assertAccess').rejects(error)
      const ensureStub = sinon.stub(ProjectKnowledgeBaseService, 'ensureLinkedKb').resolves('kb-42')

      const req = createMockRequest({ params: { projectId: PROJECT_ID } })
      const res = createMockResponse()
      const next = createMockNext()

      await ensureProjectKnowledgeBase(createMockAppConfig())(req, res, next)

      expect(next.calledWith(error)).to.be.true
      expect(ensureStub.called).to.be.false
    })

    it('grants editor access through a team membership', async () => {
      sinon
        .stub(AIServiceCommand.prototype, 'execute')
        .resolves({ statusCode: 200, data: { teams: [{ id: 'team-1' }] } } as any)
      const assertAccessStub = sinon
        .stub(ProjectService, 'assertAccess')
        .resolves({ role: 'editor', project: makeProjectDoc() })
      sinon.stub(ProjectKnowledgeBaseService, 'ensureLinkedKb').resolves('kb-42')

      const req = createMockRequest({ params: { projectId: PROJECT_ID } })

      await ensureProjectKnowledgeBase(createMockAppConfig())(req, createMockResponse(), createMockNext())

      expect(assertAccessStub.firstCall.args).to.deep.equal([VALID_OID2, VALID_OID, PROJECT_ID, 'editor', ['team-1']])
    })

    it('forwards a KB creation failure to next without responding', async () => {
      stubNoTeamMemberships()
      sinon.stub(ProjectService, 'assertAccess').resolves({ role: 'editor', project: makeProjectDoc() })
      const error = new Error('Knowledge base creation did not return an id')
      sinon.stub(ProjectKnowledgeBaseService, 'ensureLinkedKb').rejects(error)

      const req = createMockRequest({ params: { projectId: PROJECT_ID } })
      const res = createMockResponse()
      const next = createMockNext()

      await ensureProjectKnowledgeBase(createMockAppConfig())(req, res, next)

      expect(next.calledOnceWith(error)).to.be.true
      expect(res.status.called).to.be.false
    })
  })
})
