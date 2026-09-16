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
  uploadProjectFiles,
  deleteProjectFile,
  listProjectMembers,
  upsertProjectMembers,
  removeProjectMember,
} from '../../../../src/modules/projects/controller/project.controller'
import { ProjectService } from '../../../../src/modules/projects/services/project.service'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import { IAMServiceCommand } from '../../../../src/libs/commands/iam/iam.service.command'

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
    files: [] as any[],
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

afterEach(() => {
  sinon.restore()
})

describe('project.controller', () => {
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
      expect(res.json.calledWith({ project })).to.be.true
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
      const rows = [makeProjectDoc()]
      sinon.stub(ProjectService, 'list').resolves({ projects: rows as any, totalCount: 25 })

      const req = createMockRequest({
        query: { page: 1, limit: 10, scope: 'mine', includeArchived: false },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await listProjects(req, res, next)

      expect(next.called).to.be.false
      expect(res.status.calledWith(200)).to.be.true
      const body = res.json.firstCall.args[0]
      expect(body.projects).to.equal(rows)
      expect(body.pagination).to.deep.equal({ page: 1, limit: 10, totalCount: 25, totalPages: 3 })
    })

    it('forwards service errors to next', async () => {
      const error = new Error('db down')
      sinon.stub(ProjectService, 'list').rejects(error)

      const req = createMockRequest({ query: { page: 1, limit: 10, scope: 'mine', includeArchived: false } })
      const res = createMockResponse()
      const next = createMockNext()

      await listProjects(req, res, next)

      expect(next.calledWith(error)).to.be.true
    })
  })

  describe('getProjectById', () => {
    it('merges the computed role onto the plain project object', async () => {
      const project = makeProjectDoc({ name: 'Shared Project' })
      sinon.stub(ProjectService, 'assertAccess').resolves({ role: 'editor', project })

      const req = createMockRequest({ params: { projectId: PROJECT_ID } })
      const res = createMockResponse()
      const next = createMockNext()

      await getProjectById(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      const body = res.json.firstCall.args[0]
      expect(body.project.role).to.equal('editor')
      expect(body.project.name).to.equal('Shared Project')
    })

    it('forwards NotFoundError/ForbiddenError from assertAccess to next', async () => {
      const error = new Error('Project not found')
      sinon.stub(ProjectService, 'assertAccess').rejects(error)

      const req = createMockRequest({ params: { projectId: PROJECT_ID } })
      const res = createMockResponse()
      const next = createMockNext()

      await getProjectById(req, res, next)

      expect(next.calledWith(error)).to.be.true
    })
  })

  describe('updateProject', () => {
    it('applies the patch and returns the updated project', async () => {
      const updated = makeProjectDoc({ name: 'Renamed' })
      sinon.stub(ProjectService, 'update').resolves(updated)

      const req = createMockRequest({ params: { projectId: PROJECT_ID }, body: { name: 'Renamed' } })
      const res = createMockResponse()
      const next = createMockNext()

      await updateProject(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.calledWith({ project: updated })).to.be.true
    })
  })

  describe('deleteProject', () => {
    it('soft-deletes and returns a success message', async () => {
      const softDeleteStub = sinon.stub(ProjectService, 'softDelete').resolves()

      const req = createMockRequest({ params: { projectId: PROJECT_ID } })
      const res = createMockResponse()
      const next = createMockNext()

      await deleteProject(req, res, next)

      expect(softDeleteStub.calledWith(VALID_OID2, VALID_OID, PROJECT_ID)).to.be.true
      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.calledWith({ message: 'Project deleted successfully' })).to.be.true
    })

    it('forwards ForbiddenError (non-owner) to next', async () => {
      const error = new Error('Only the project owner can delete it')
      sinon.stub(ProjectService, 'softDelete').rejects(error)

      const req = createMockRequest({ params: { projectId: PROJECT_ID } })
      const res = createMockResponse()
      const next = createMockNext()

      await deleteProject(req, res, next)

      expect(next.calledWith(error)).to.be.true
    })
  })

  describe('archive / unarchive / pin / unpin', () => {
    it('archiveProject calls setArchived(true)', async () => {
      const project = makeProjectDoc({ isArchived: true })
      const stub = sinon.stub(ProjectService, 'setArchived').resolves(project)

      const req = createMockRequest({ params: { projectId: PROJECT_ID } })
      const res = createMockResponse()
      const next = createMockNext()

      await archiveProject(req, res, next)

      expect(stub.calledWith(VALID_OID2, VALID_OID, PROJECT_ID, true)).to.be.true
      expect(res.json.calledWith({ project })).to.be.true
    })

    it('unarchiveProject calls setArchived(false)', async () => {
      const project = makeProjectDoc({ isArchived: false })
      const stub = sinon.stub(ProjectService, 'setArchived').resolves(project)

      const req = createMockRequest({ params: { projectId: PROJECT_ID } })
      const res = createMockResponse()
      const next = createMockNext()

      await unarchiveProject(req, res, next)

      expect(stub.calledWith(VALID_OID2, VALID_OID, PROJECT_ID, false)).to.be.true
    })

    it('pinProject calls setPinned(true)', async () => {
      const project = makeProjectDoc({ isPinned: true })
      const stub = sinon.stub(ProjectService, 'setPinned').resolves(project)

      const req = createMockRequest({ params: { projectId: PROJECT_ID } })
      const res = createMockResponse()
      const next = createMockNext()

      await pinProject(req, res, next)

      expect(stub.calledWith(VALID_OID2, VALID_OID, PROJECT_ID, true)).to.be.true
    })

    it('unpinProject calls setPinned(false)', async () => {
      const project = makeProjectDoc({ isPinned: false })
      const stub = sinon.stub(ProjectService, 'setPinned').resolves(project)

      const req = createMockRequest({ params: { projectId: PROJECT_ID } })
      const res = createMockResponse()
      const next = createMockNext()

      await unpinProject(req, res, next)

      expect(stub.calledWith(VALID_OID2, VALID_OID, PROJECT_ID, false)).to.be.true
    })
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
      const assertAccessStub = sinon
        .stub(ProjectService, 'assertAccess')
        .resolves({ role: 'viewer', project: makeProjectDoc() })
      sinon.stub(ChatSession, 'find').returns(makeFindChain([]) as any)
      sinon.stub(ChatSession, 'countDocuments').resolves(0)

      const req = createMockRequest({ params: { projectId: PROJECT_ID }, query: { page: 1, limit: 20 } })
      const res = createMockResponse()
      const next = createMockNext()

      await getProjectConversations(req, res, next)

      expect(assertAccessStub.calledWith(VALID_OID2, VALID_OID, PROJECT_ID, 'viewer')).to.be.true
      expect(res.status.calledWith(200)).to.be.true
      const body = res.json.firstCall.args[0]
      expect(body.conversations).to.deep.equal([])
      expect(body.pagination).to.deep.equal({ page: 1, limit: 20, totalCount: 0, totalPages: 0 })
    })

    it('scopes the query to own rows OR project-visible rows within the project', async () => {
      sinon.stub(ProjectService, 'assertAccess').resolves({ role: 'viewer', project: makeProjectDoc() })
      const findStub = sinon.stub(ChatSession, 'find').returns(makeFindChain([]) as any)
      sinon.stub(ChatSession, 'countDocuments').resolves(0)

      const req = createMockRequest({ params: { projectId: PROJECT_ID }, query: { page: 1, limit: 20 } })
      const res = createMockResponse()
      const next = createMockNext()

      await getProjectConversations(req, res, next)

      const filter = findStub.firstCall.args[0] as any
      expect(filter.projectId.toString()).to.equal(PROJECT_ID)
      expect(filter.isDeleted).to.equal(false)
      expect(filter.$or).to.deep.equal([
        { userId: new mongoose.Types.ObjectId(VALID_OID) },
        { projectVisibility: 'project' },
      ])
    })

    it('rejects with the access error when the caller has no access', async () => {
      const error = new Error('Project not found')
      sinon.stub(ProjectService, 'assertAccess').rejects(error)

      const req = createMockRequest({ params: { projectId: PROJECT_ID }, query: { page: 1, limit: 20 } })
      const res = createMockResponse()
      const next = createMockNext()

      await getProjectConversations(req, res, next)

      expect(next.calledWith(error)).to.be.true
    })
  })

  describe('uploadProjectFiles', () => {
    const pdfMulterFile = {
      originalname: 'doc.pdf',
      mimetype: 'application/pdf',
      size: 8,
      buffer: Buffer.from('%PDF-1.4'),
    }

    it('requires editor access on the project', async () => {
      const assertAccessStub = sinon
        .stub(ProjectService, 'assertAccess')
        .resolves({ role: 'editor', project: makeProjectDoc() })
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { attachments: [{ recordId: 'rec-1' }] },
      } as any)
      sinon.stub(ProjectService, 'addFile').resolves(makeProjectDoc({ files: [{ recordId: 'rec-1' }] }))

      const handler = uploadProjectFiles(createMockAppConfig())
      const req = createMockRequest({ params: { projectId: PROJECT_ID }, files: [pdfMulterFile] })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(assertAccessStub.calledWith(VALID_OID2, VALID_OID, PROJECT_ID, 'editor')).to.be.true
      expect(next.called).to.be.false
      expect(res.status.calledWith(200)).to.be.true
    })

    it('rejects with 400 when no files are attached', async () => {
      sinon.stub(ProjectService, 'assertAccess').resolves({ role: 'editor', project: makeProjectDoc() })

      const handler = uploadProjectFiles(createMockAppConfig())
      const req = createMockRequest({ params: { projectId: PROJECT_ID }, files: [] })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0].message).to.include('At least one file')
    })

    it('rejects when the upload would exceed PROJECT_FILE_LIMITS.MAX_FILES', async () => {
      const existingFiles = Array.from({ length: 20 }, (_, i) => ({ recordId: `r${i}` }))
      sinon
        .stub(ProjectService, 'assertAccess')
        .resolves({ role: 'editor', project: makeProjectDoc({ files: existingFiles }) })

      const handler = uploadProjectFiles(createMockAppConfig())
      const req = createMockRequest({ params: { projectId: PROJECT_ID }, files: [pdfMulterFile] })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0].message).to.match(/at most 20 files/)
    })

    it('rejects unsupported attachment mime types', async () => {
      sinon.stub(ProjectService, 'assertAccess').resolves({ role: 'editor', project: makeProjectDoc() })

      const handler = uploadProjectFiles(createMockAppConfig())
      const req = createMockRequest({
        params: { projectId: PROJECT_ID },
        files: [{ ...pdfMulterFile, mimetype: 'application/zip', originalname: 'bad.zip' }],
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0].message).to.match(/Unsupported attachment type/)
    })

    it('syncs READER permission on new records to existing user members only', async () => {
      sinon.stub(ProjectService, 'assertAccess').resolves({
        role: 'editor',
        project: makeProjectDoc({
          members: [
            { principalType: 'user', principalId: new mongoose.Types.ObjectId(VALID_OID) },
            { principalType: 'team', principalId: new mongoose.Types.ObjectId(VALID_OID2) },
          ],
        }),
      })
      sinon.stub(AIServiceCommand.prototype, 'execute')
        .onFirstCall().resolves({ statusCode: 200, data: { attachments: [{ recordId: 'rec-1' }] } } as any)
        .onSecondCall().resolves({ statusCode: 200 } as any)
      sinon.stub(ProjectService, 'addFile').resolves(
        makeProjectDoc({
          files: [{ recordId: 'rec-1' }],
          members: [{ principalType: 'user', principalId: new mongoose.Types.ObjectId(VALID_OID) }],
        }),
      )

      const handler = uploadProjectFiles(createMockAppConfig())
      const req = createMockRequest({ params: { projectId: PROJECT_ID }, files: [pdfMulterFile] })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      const permissionsCall = (AIServiceCommand.prototype.execute as sinon.SinonStub).secondCall
      expect(permissionsCall).to.not.be.undefined
      expect((permissionsCall.thisValue as any).uri).to.equal(
        'http://localhost:8000/api/v1/chat/attachments/permissions',
      )
      const body = JSON.parse((permissionsCall.thisValue as any).body)
      expect(body.recordIds).to.deep.equal(['rec-1'])
      expect(body.userIds).to.deep.equal([VALID_OID])
    })

    it('does not fail the request when the AI upload backend errors', async () => {
      sinon.stub(ProjectService, 'assertAccess').resolves({ role: 'editor', project: makeProjectDoc() })
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 500, data: null } as any)

      const handler = uploadProjectFiles(createMockAppConfig())
      const req = createMockRequest({ params: { projectId: PROJECT_ID }, files: [pdfMulterFile] })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(res.status.called).to.be.false
    })
  })

  describe('deleteProjectFile', () => {
    it('removes the ref then best-effort deletes upstream via fetch', async () => {
      const project = makeProjectDoc({ files: [] })
      const removeFileStub = sinon.stub(ProjectService, 'removeFile').resolves(project)
      const fetchStub = sinon.stub(globalThis, 'fetch').resolves({ status: 204, ok: true } as Response)

      const handler = deleteProjectFile(createMockAppConfig())
      const req = createMockRequest({ params: { projectId: PROJECT_ID, recordId: 'rec-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(removeFileStub.calledWith(VALID_OID2, VALID_OID, PROJECT_ID, 'rec-1')).to.be.true
      expect(fetchStub.calledOnce).to.be.true
      expect(fetchStub.firstCall.args[0]).to.equal(
        'http://localhost:8000/api/v1/chat/attachments/rec-1',
      )
      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.calledWith({ files: project.files })).to.be.true
    })

    it('still returns 200 when the upstream cleanup fetch throws', async () => {
      const project = makeProjectDoc({ files: [] })
      sinon.stub(ProjectService, 'removeFile').resolves(project)
      sinon.stub(globalThis, 'fetch').rejects(new Error('network down'))

      const handler = deleteProjectFile(createMockAppConfig())
      const req = createMockRequest({ params: { projectId: PROJECT_ID, recordId: 'rec-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.called).to.be.false
      expect(res.status.calledWith(200)).to.be.true
    })

    it('forwards the service error (e.g. non-owner/editor) to next without calling fetch', async () => {
      const error = new Error('Forbidden')
      sinon.stub(ProjectService, 'removeFile').rejects(error)
      const fetchStub = sinon.stub(globalThis, 'fetch').resolves({ status: 204 } as Response)

      const handler = deleteProjectFile(createMockAppConfig())
      const req = createMockRequest({ params: { projectId: PROJECT_ID, recordId: 'rec-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledWith(error)).to.be.true
      expect(fetchStub.called).to.be.false
    })
  })

  describe('listProjectMembers', () => {
    it('returns the member list', async () => {
      const members = [{ principalType: 'user', principalId: VALID_OID, role: 'viewer' }]
      sinon.stub(ProjectService, 'listMembers').resolves(members as any)

      const req = createMockRequest({ params: { projectId: PROJECT_ID } })
      const res = createMockResponse()
      const next = createMockNext()

      await listProjectMembers(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.calledWith({ members })).to.be.true
    })
  })

  describe('upsertProjectMembers', () => {
    it('rejects when a member userId does not exist in IAM', async () => {
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

    it('upserts members after IAM validation and syncs file permissions for all project files', async () => {
      sinon.stub(IAMServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: { _id: VALID_OID2 } } as any)
      const upsertStub = sinon.stub(ProjectService, 'upsertMembers').resolves(
        makeProjectDoc({
          files: [{ recordId: 'rec-1' }, { recordId: 'rec-2' }],
          members: [{ principalType: 'user', principalId: new mongoose.Types.ObjectId(VALID_OID2), role: 'viewer' }],
        }),
      )
      const aiStub = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200 } as any)

      const handler = upsertProjectMembers(createMockAppConfig())
      const req = createMockRequest({
        params: { projectId: PROJECT_ID },
        body: { members: [{ principalId: VALID_OID2, role: 'viewer' }] },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(upsertStub.calledWith(VALID_OID2, VALID_OID, PROJECT_ID, [{ principalId: VALID_OID2, role: 'viewer' }]))
        .to.be.true
      expect(aiStub.calledOnce).to.be.true
      const body = JSON.parse((aiStub.firstCall.thisValue as any).body)
      expect(body.recordIds).to.deep.equal(['rec-1', 'rec-2'])
      expect(body.userIds).to.deep.equal([VALID_OID2])
      expect(res.status.calledWith(200)).to.be.true
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
  })

  describe('removeProjectMember', () => {
    it('removes the member and syncs a DELETE permission for that user across all project files', async () => {
      const removeMemberStub = sinon.stub(ProjectService, 'removeMember').resolves(
        makeProjectDoc({ files: [{ recordId: 'rec-1' }], members: [] }),
      )
      const aiStub = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200 } as any)

      const handler = removeProjectMember(createMockAppConfig())
      const req = createMockRequest({ params: { projectId: PROJECT_ID, memberUserId: VALID_OID2 } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(removeMemberStub.calledWith(VALID_OID2, VALID_OID, PROJECT_ID, VALID_OID2)).to.be.true
      expect(aiStub.calledOnce).to.be.true
      const [call] = [aiStub.firstCall]
      const options = call.thisValue as any
      expect(options.method).to.equal('DELETE')
      const body = JSON.parse(options.body)
      expect(body.recordIds).to.deep.equal(['rec-1'])
      expect(body.userIds).to.deep.equal([VALID_OID2])
      expect(res.status.calledWith(200)).to.be.true
    })

    it('forwards ForbiddenError (non-owner) to next', async () => {
      const error = new Error('Only the project owner can manage members')
      sinon.stub(ProjectService, 'removeMember').rejects(error)

      const handler = removeProjectMember(createMockAppConfig())
      const req = createMockRequest({ params: { projectId: PROJECT_ID, memberUserId: VALID_OID2 } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledWith(error)).to.be.true
    })
  })
})
