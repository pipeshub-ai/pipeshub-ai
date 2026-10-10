import 'reflect-metadata'
import { expect } from 'chai'
import mongoose, { Types } from 'mongoose'
import sinon from 'sinon'
import { AuditEvent } from '../../../src/libs/audit/audit-event.schema'
import { MongoAuditWriter } from '../../../src/libs/audit/audit.writer'
import { AIServiceCommand } from '../../../src/libs/commands/ai_service/ai.service.command'
import { AccessPreviewService } from '../../../src/modules/authz/access-preview.service'
import { AuthorizationService } from '../../../src/modules/authz/authz.service'
import { ExplainService } from '../../../src/modules/authz/explain.service'
import { ChatAccessLoader } from '../../../src/modules/authz/loaders/chat.loader'
import { ChatSession } from '../../../src/modules/enterprise_search/schema/chat.session.schema'
import { updateProject } from '../../../src/modules/projects/controller/project.controller'
import { Project } from '../../../src/modules/projects/schema/project.schema'
import { ProjectServiceAccessAdapter } from '../../../src/modules/projects/services/project-access.adapter'
import { ForbiddenError } from '../../../src/libs/errors/http.errors'

const uri = process.env.PCC_MONGO_URI

;(uri ? describe : describe.skip)('authz user routes against a real MongoDB (PH07-20, PH07-21)', function () {
  this.timeout(30_000)
  const org = new Types.ObjectId()
  const [owner, editor, viewer, outsider] = [1, 2, 3, 4].map(() => new Types.ObjectId())
  const orgId = String(org)
  const appConfig = { connectorBackend: 'http://connectors', iamBackend: 'http://iam' } as never

  before(async () => {
    await mongoose.connect(uri as string)
    await Promise.all([ChatSession.init(), Project.init(), AuditEvent.init()])
  })
  after(async () => {
    await Promise.all([ChatSession.deleteMany({ orgId: org }), Project.deleteMany({ orgId: org }), AuditEvent.deleteMany({ orgId: org })])
    await mongoose.disconnect()
  })
  afterEach(() => sinon.restore())

  const makeProject = (over: Record<string, unknown> = {}) =>
    Project.create({
      orgId: org,
      userId: owner,
      name: 'P',
      members: [
        { principalType: 'user', principalId: editor, role: 'editor', addedBy: owner },
        { principalType: 'user', principalId: viewer, role: 'viewer', addedBy: owner },
      ],
      ...over,
    })

  const patch = async (as: Types.ObjectId, projectId: string, body: Record<string, unknown>, flagOn: boolean) => {
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: { teamIds: [] } } as never)
    const json = sinon.stub()
    const res = { status: sinon.stub().returnsThis(), json }
    const next = sinon.stub()
    const deps = { flags: { isEnabled: async () => flagOn }, audit: new MongoAuditWriter() }
    await updateProject(appConfig, deps)(
      { headers: {}, params: { projectId }, body, user: { userId: String(as), orgId }, context: { requestId: 'req-it' } } as never,
      res as never,
      next,
    )
    sinon.restore()
    return { next, json }
  }

  describe('PATCH projectChatAccess', () => {
    it('the owner sets it: stored, aclVersion +1 by $inc, one audit row', async () => {
      const project = await makeProject({ aclVersion: 4 })
      const id = String(project._id)
      const { next, json } = await patch(owner, id, { projectChatAccess: 'editor' }, true)
      expect(next.called).to.equal(false)
      const stored = await Project.findById(id).lean()
      expect(stored!.projectChatAccess).to.equal('editor')
      expect(stored!.aclVersion).to.equal(5)
      expect(json.firstCall.args[0].project.aclVersion).to.equal(5)
      const rows = await AuditEvent.find({ orgId: org, targetId: id }).lean()
      expect(rows).to.have.length(1)
      expect(rows[0]).to.deep.include({ action: 'project.chatAccessChanged', targetType: 'project', requestId: 'req-it' })
      expect(String(rows[0]!.actorUserId)).to.equal(String(owner))
      expect(rows[0]!.before).to.deep.equal({ projectChatAccess: 'viewer' })
      expect(rows[0]!.after).to.deep.equal({ projectChatAccess: 'editor', aclVersion: 5 })
    })

    it('a project editor is refused and nothing changes', async () => {
      const project = await makeProject({ aclVersion: 1 })
      const id = String(project._id)
      const { next } = await patch(editor, id, { projectChatAccess: 'editor' }, true)
      expect(next.firstCall.args[0]).to.be.instanceOf(ForbiddenError)
      const stored = await Project.findById(id).lean()
      expect(stored!.projectChatAccess).to.equal('viewer')
      expect(stored!.aclVersion).to.equal(1)
      expect(await AuditEvent.countDocuments({ orgId: org, targetId: id })).to.equal(0)
    })

    it('with the flag off the field is ignored: same stored project, no audit, no version bump', async () => {
      const project = await makeProject({ aclVersion: 1 })
      const id = String(project._id)
      const { next } = await patch(owner, id, { projectChatAccess: 'editor', description: 'd' }, false)
      expect(next.called).to.equal(false)
      const stored = await Project.findById(id).lean()
      expect(stored!.projectChatAccess).to.equal('viewer')
      expect(stored!.description).to.equal('d')
      expect(stored!.aclVersion).to.equal(1)
      expect(await AuditEvent.countDocuments({ orgId: org, targetId: id })).to.equal(0)
    })

    it('re-sending the current value changes nothing', async () => {
      const project = await makeProject({ aclVersion: 1, projectChatAccess: 'editor' })
      const id = String(project._id)
      await patch(owner, id, { projectChatAccess: 'editor' }, true)
      expect((await Project.findById(id).lean())!.aclVersion).to.equal(1)
      expect(await AuditEvent.countDocuments({ orgId: org, targetId: id })).to.equal(0)
    })
  })

  describe('preview and explain over real documents', () => {
    const chats = new ChatAccessLoader()
    const projects = new ProjectServiceAccessAdapter()
    const preview = new AccessPreviewService(chats, projects)
    const snapshot = async (): Promise<string> =>
      JSON.stringify({
        chats: await ChatSession.find({ orgId: org }).sort({ _id: 1 }).lean(),
        projects: await Project.find({ orgId: org }).sort({ _id: 1 }).lean(),
        audit: await AuditEvent.countDocuments({ orgId: org }),
      })

    it('previews unlink, link and a visibility flip without writing anything', async () => {
      const current = await makeProject({ chatSharing: 'members' })
      const target = await makeProject({ members: [{ principalType: 'user', principalId: outsider, role: 'viewer', addedBy: owner }], chatSharing: 'members' })
      const chat = await ChatSession.create({
        orgId: org,
        userId: owner,
        initiator: owner,
        projectId: current._id,
        projectVisibility: 'project',
        sharedWith: [{ principalType: 'user', userId: viewer, accessLevel: 'read' }],
      })
      const caller = { userId: String(owner), orgId, teamIds: [] as string[] }
      const before = await snapshot()

      const unlink = await preview.preview(caller, String(chat._id), { type: 'unlink' }, async () => [])
      expect(unlink.loses.map((p) => p.userId)).to.have.members([String(editor)])
      expect(unlink.gains).to.deep.equal([])

      const link = await preview.preview(caller, String(chat._id), { type: 'link', projectId: String(target._id) }, async () => [])
      expect(link.gains.map((p) => p.userId)).to.deep.equal([String(outsider)])
      expect(link.loses.map((p) => p.userId)).to.have.members([String(editor)])

      const flip = await preview.preview(caller, String(chat._id), { type: 'visibility', visibility: 'private' }, async () => [])
      expect(flip.loses.map((p) => p.userId)).to.have.members([String(editor)])

      expect(await snapshot()).to.equal(before)
    })

    it('does not name the members of a project the caller cannot open', async () => {
      const hidden = await Project.create({
        orgId: org,
        userId: outsider,
        name: 'Hidden',
        members: [{ principalType: 'user', principalId: editor, role: 'editor', addedBy: outsider }],
      })
      const chat = await ChatSession.create({ orgId: org, userId: owner, initiator: owner })
      const caller = { userId: String(owner), orgId, teamIds: [] as string[] }
      let error: Error | undefined
      try {
        await preview.preview(caller, String(chat._id), { type: 'link', projectId: String(hidden._id) }, async () => [])
      } catch (e) {
        error = e as Error
      }
      expect(error?.message).to.equal('Project not found')
      expect(error?.message).to.not.include(String(editor))
    })

    it('explains with the team rows of a real chat and the subject teams given by the directory', async () => {
      const chat = await ChatSession.create({
        orgId: org,
        userId: owner,
        initiator: owner,
        sharedWith: [{ principalType: 'team', teamId: 'team-x', accessLevel: 'write' }],
      })
      const flags = { isEnabled: async () => true }
      const authz = new AuthorizationService({ chats, projects, flags })
      const explainer = new ExplainService(authz, chats, async () => false)
      const caller = { userId: String(owner), orgId, teamIds: [] as string[] }
      const result = await explainer.explainUser(caller, String(editor), { type: 'chat', id: String(chat._id) }, async () => ['team-x'])
      expect(result.role).to.equal('editor')
      expect(result.via).to.deep.equal([{ type: 'team', ref: 'redacted', role: 'editor' }])
      const unresolved = await explainer.explainUser(caller, String(editor), { type: 'chat', id: String(chat._id) }, async () => 'unresolved')
      expect(unresolved.teamsUnresolved).to.equal(true)
    })
  })
})
