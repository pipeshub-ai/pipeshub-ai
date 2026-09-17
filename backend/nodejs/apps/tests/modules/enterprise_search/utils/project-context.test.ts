import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import { ProjectService } from '../../../../src/modules/projects/services/project.service'
import {
  applyProjectScope,
  loadProjectForSession,
  resolveProjectLink,
  PROJECT_ID_UNASSIGNED,
} from '../../../../src/modules/enterprise_search/utils/project-context'
import { NotFoundError } from '../../../../src/libs/errors/http.errors'

const ORG_ID = new mongoose.Types.ObjectId().toString()
const USER_ID = new mongoose.Types.ObjectId().toString()
const PROJECT_ID = new mongoose.Types.ObjectId().toString()

function makeProject(overrides: Record<string, any> = {}): any {
  return {
    _id: new mongoose.Types.ObjectId(PROJECT_ID),
    orgId: new mongoose.Types.ObjectId(ORG_ID),
    userId: new mongoose.Types.ObjectId(USER_ID),
    instructions: undefined,
    knowledgeScope: undefined,
    tools: [],
    linkedKnowledgeBaseId: null,
    chatSharing: 'private',
    ...overrides,
  }
}

describe('project-context', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('exports the "unassigned" sentinel matching the query-filter convention', () => {
    expect(PROJECT_ID_UNASSIGNED).to.equal('unassigned')
  })

  // -----------------------------------------------------------------------
  // applyProjectScope
  // -----------------------------------------------------------------------
  describe('applyProjectScope', () => {
    it('no-ops when project is undefined', () => {
      const payload: Record<string, unknown> = { filters: { apps: ['a'] } }
      applyProjectScope(payload, undefined)
      expect(payload).to.deep.equal({ filters: { apps: ['a'] } })
    })

    it('sets projectInstructions when the project has instructions', () => {
      const payload: Record<string, unknown> = {}
      applyProjectScope(payload, makeProject({ instructions: '  Be concise.  ' }))
      expect(payload.projectInstructions).to.equal('Be concise.')
    })

    it('does not set projectInstructions for a blank/whitespace-only instructions field', () => {
      const payload: Record<string, unknown> = {}
      applyProjectScope(payload, makeProject({ instructions: '   ' }))
      expect(payload.projectInstructions).to.be.undefined
    })

    it('defaults filters.apps/kb to the whole project knowledgeScope when the request carried none', () => {
      const payload: Record<string, unknown> = {}
      applyProjectScope(
        payload,
        makeProject({ knowledgeScope: { apps: ['app-1'], kb: ['kb-1'] } }),
      )
      expect(payload.filters).to.deep.equal({ apps: ['app-1'], kb: ['kb-1'] })
    })

    it('intersects a non-empty request filter with the project scope (narrow only)', () => {
      const payload: Record<string, unknown> = { filters: { apps: ['app-1', 'outside-app'] } }
      applyProjectScope(
        payload,
        makeProject({ knowledgeScope: { apps: ['app-1', 'app-2'] } }),
      )
      expect(payload.filters).to.deep.equal({ apps: ['app-1'], kb: [] })
    })

    it('sends apps: [] when the request narrows to ids outside the project scope', () => {
      const payload: Record<string, unknown> = { filters: { apps: ['outside-app'] } }
      applyProjectScope(
        payload,
        makeProject({ knowledgeScope: { apps: ['app-1'] } }),
      )
      expect(payload.filters).to.deep.equal({ apps: [], kb: [] })
    })

    it('adds the linked hidden Collection id into filters.kb alongside any explicitly scoped kb ids', () => {
      const payload: Record<string, unknown> = {}
      applyProjectScope(
        payload,
        makeProject({
          knowledgeScope: { kb: ['kb-1'] },
          linkedKnowledgeBaseId: 'hidden-kb-1',
        }),
      )
      expect(payload.filters).to.deep.equal({ apps: [], kb: ['kb-1', 'hidden-kb-1'] })
    })

    it('always includes the linked hidden Collection id even when the request narrowed kb to a different subset', () => {
      const payload: Record<string, unknown> = { filters: { kb: ['kb-1'] } }
      applyProjectScope(
        payload,
        makeProject({
          knowledgeScope: { kb: ['kb-1', 'kb-2'] },
          linkedKnowledgeBaseId: 'hidden-kb-1',
        }),
      )
      expect(payload.filters).to.deep.equal({ apps: [], kb: ['kb-1', 'hidden-kb-1'] })
    })

    it('de-duplicates the linked hidden Collection id when it is already in knowledgeScope.kb', () => {
      const payload: Record<string, unknown> = {}
      applyProjectScope(
        payload,
        makeProject({
          knowledgeScope: { kb: ['hidden-kb-1'] },
          linkedKnowledgeBaseId: 'hidden-kb-1',
        }),
      )
      expect(payload.filters).to.deep.equal({ apps: [], kb: ['hidden-kb-1'] })
    })

    it('preserves non-apps/kb filter keys the request carried (e.g. metadata filters)', () => {
      const payload: Record<string, unknown> = { filters: { departments: ['eng'] } }
      applyProjectScope(payload, makeProject({ knowledgeScope: { apps: ['app-1'] } }))
      expect(payload.filters).to.deep.equal({ departments: ['eng'], apps: ['app-1'], kb: [] })
    })

    it('produces empty apps/kb (never "search everything") when the project has no scope at all', () => {
      const payload: Record<string, unknown> = { filters: { apps: ['request-app'] } }
      applyProjectScope(payload, makeProject())
      expect(payload.filters).to.deep.equal({ apps: [], kb: [] })
    })

    it('always sets strictScope: true so an empty scope stays empty at retrieval time', () => {
      const payload: Record<string, unknown> = {}
      applyProjectScope(payload, makeProject())
      expect(payload.strictScope).to.equal(true)
    })

    it('includes the linked KB even when no connectors are selected (files-only project)', () => {
      const payload: Record<string, unknown> = {}
      applyProjectScope(
        payload,
        makeProject({ linkedKnowledgeBaseId: 'hidden-kb-1' }),
      )
      expect(payload.filters).to.deep.equal({ apps: [], kb: ['hidden-kb-1'] })
      expect(payload.strictScope).to.equal(true)
    })

    it('includes the linked KB after a file re-upload (no connectors, no explicit kb scope)', () => {
      const payload: Record<string, unknown> = { filters: { apps: [], kb: [] } }
      applyProjectScope(
        payload,
        makeProject({ linkedKnowledgeBaseId: 'hidden-kb-1' }),
      )
      expect(payload.filters).to.deep.equal({ apps: [], kb: ['hidden-kb-1'] })
    })

    it('includes the linked KB alongside a selected connector', () => {
      const payload: Record<string, unknown> = {}
      applyProjectScope(
        payload,
        makeProject({
          knowledgeScope: { apps: ['rag-wiki'] },
          linkedKnowledgeBaseId: 'hidden-kb-1',
        }),
      )
      expect(payload.filters).to.deep.equal({ apps: ['rag-wiki'], kb: ['hidden-kb-1'] })
    })

    it('never drops the linked KB even when the request narrows kb to an empty set', () => {
      const payload: Record<string, unknown> = { filters: { kb: ['unrelated-kb'] } }
      applyProjectScope(
        payload,
        makeProject({
          knowledgeScope: { kb: [] },
          linkedKnowledgeBaseId: 'hidden-kb-1',
        }),
      )
      expect((payload.filters as any).kb).to.deep.equal(['hidden-kb-1'])
    })

    it('defaults tools to the whole project tool list when the request carried none', () => {
      const payload: Record<string, unknown> = {}
      applyProjectScope(payload, makeProject({ tools: ['project-tool'] }))
      expect(payload.tools).to.deep.equal(['project-tool'])
    })

    it('intersects a non-empty request tool list with the project tool list (narrow only)', () => {
      const payload: Record<string, unknown> = { tools: ['project-tool', 'outside-tool'] }
      applyProjectScope(payload, makeProject({ tools: ['project-tool', 'other-project-tool'] }))
      expect(payload.tools).to.deep.equal(['project-tool'])
    })

    it('treats an explicit empty tools array the same as "not narrowed" (falls back to the whole project set)', () => {
      const payload: Record<string, unknown> = { tools: [] }
      applyProjectScope(payload, makeProject({ tools: ['project-tool'] }))
      expect(payload.tools).to.deep.equal(['project-tool'])
    })

    it('sets tools to [] when the project has none configured', () => {
      const payload: Record<string, unknown> = {}
      applyProjectScope(payload, makeProject({ tools: [] }))
      expect(payload.tools).to.deep.equal([])
    })

    // The composer sends bare fullNames; older projects persisted `instanceId:fullName`.
    it('matches a bare request tool against a legacy instance-prefixed project tool', () => {
      const payload: Record<string, unknown> = { tools: ['slack.send_message'] }
      applyProjectScope(payload, makeProject({ tools: ['inst-1:slack.send_message', 'inst-2:jira.create_issue'] }))
      expect(payload.tools).to.deep.equal(['slack.send_message'])
    })

    it('emits bare fullNames (deduped) when falling back to a legacy prefixed project tool list', () => {
      const payload: Record<string, unknown> = {}
      applyProjectScope(payload, makeProject({ tools: ['inst-1:slack.send_message', 'inst-2:slack.send_message'] }))
      expect(payload.tools).to.deep.equal(['slack.send_message'])
    })

    it('strips an instance prefix off request tools before intersecting', () => {
      const payload: Record<string, unknown> = { tools: ['inst-9:slack.send_message', 'inst-9:outside.tool'] }
      applyProjectScope(payload, makeProject({ tools: ['slack.send_message'] }))
      expect(payload.tools).to.deep.equal(['slack.send_message'])
    })
  })

  // -----------------------------------------------------------------------
  // resolveProjectLink
  // -----------------------------------------------------------------------
  describe('resolveProjectLink', () => {
    it('returns {} when the body has no projectId', async () => {
      const result = await resolveProjectLink(ORG_ID, USER_ID, {})
      expect(result).to.deep.equal({})
    })

    it('resolves projectVisibility "private" by default when project.chatSharing is private', async () => {
      const project = makeProject({ chatSharing: 'private' })
      sinon.stub(ProjectService, 'assertAccess').resolves({ role: 'owner', project })
      const result = await resolveProjectLink(ORG_ID, USER_ID, { projectId: PROJECT_ID })
      expect(result.projectId).to.equal(PROJECT_ID)
      expect(result.projectVisibility).to.equal('private')
      expect(result.project).to.equal(project)
    })

    it('defaults projectVisibility to "project" when project.chatSharing is "members"', async () => {
      const project = makeProject({ chatSharing: 'members' })
      sinon.stub(ProjectService, 'assertAccess').resolves({ role: 'editor', project })
      const result = await resolveProjectLink(ORG_ID, USER_ID, { projectId: PROJECT_ID })
      expect(result.projectVisibility).to.equal('project')
    })

    it('an explicit body.projectVisibility overrides the chatSharing-derived default', async () => {
      const project = makeProject({ chatSharing: 'members' })
      sinon.stub(ProjectService, 'assertAccess').resolves({ role: 'editor', project })
      const result = await resolveProjectLink(ORG_ID, USER_ID, {
        projectId: PROJECT_ID,
        projectVisibility: 'private',
      })
      expect(result.projectVisibility).to.equal('private')
    })

    it('propagates assertAccess rejection (e.g. NotFoundError for no-access project)', async () => {
      sinon.stub(ProjectService, 'assertAccess').rejects(new NotFoundError('Project not found'))
      try {
        await resolveProjectLink(ORG_ID, USER_ID, { projectId: PROJECT_ID })
        expect.fail('Expected rejection')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })

    it('ignores a non-string projectId in the body', async () => {
      const result = await resolveProjectLink(ORG_ID, USER_ID, { projectId: 123 })
      expect(result).to.deep.equal({})
    })
  })

  // -----------------------------------------------------------------------
  // loadProjectForSession
  // -----------------------------------------------------------------------
  describe('loadProjectForSession', () => {
    it('returns undefined when projectId is undefined', async () => {
      const result = await loadProjectForSession(ORG_ID, USER_ID, undefined)
      expect(result).to.be.undefined
    })

    it('returns the project on successful access check', async () => {
      const project = makeProject()
      sinon.stub(ProjectService, 'assertAccess').resolves({ role: 'viewer', project })
      const result = await loadProjectForSession(ORG_ID, USER_ID, PROJECT_ID)
      expect(result).to.equal(project)
    })

    it('swallows a NotFoundError (deleted/inaccessible project) and returns undefined', async () => {
      sinon.stub(ProjectService, 'assertAccess').rejects(new NotFoundError('Project not found'))
      const result = await loadProjectForSession(ORG_ID, USER_ID, PROJECT_ID)
      expect(result).to.be.undefined
    })

    it('propagates non-HttpError operational failures (e.g. DB connectivity)', async () => {
      sinon
        .stub(ProjectService, 'assertAccess')
        .rejects(new Error('MongoNetworkError: connection refused'))
      try {
        await loadProjectForSession(ORG_ID, USER_ID, PROJECT_ID)
        expect.fail('Expected rejection')
      } catch (error: unknown) {
        expect(error).to.be.instanceOf(Error)
        expect((error as Error).message).to.include('MongoNetworkError')
      }
    })

    it('accepts an ObjectId projectId (as stored on the session row)', async () => {
      const project = makeProject()
      const stub = sinon.stub(ProjectService, 'assertAccess').resolves({ role: 'viewer', project })
      await loadProjectForSession(ORG_ID, USER_ID, new mongoose.Types.ObjectId(PROJECT_ID))
      expect(stub.calledWith(ORG_ID, USER_ID, PROJECT_ID, 'viewer')).to.equal(true)
    })
  })
})
