import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import { ProjectService } from '../../../../src/modules/projects/services/project.service'
import {
  applyProjectContext,
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
    chatSharing: 'private',
    files: [],
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
  // applyProjectContext
  // -----------------------------------------------------------------------
  describe('applyProjectContext', () => {
    it('no-ops when project is undefined', () => {
      const payload: Record<string, unknown> = { filters: { apps: ['a'] } }
      applyProjectContext(payload, undefined)
      expect(payload).to.deep.equal({ filters: { apps: ['a'] } })
    })

    it('sets projectInstructions when the project has instructions', () => {
      const payload: Record<string, unknown> = {}
      applyProjectContext(payload, makeProject({ instructions: '  Be concise.  ' }))
      expect(payload.projectInstructions).to.equal('Be concise.')
    })

    it('does not set projectInstructions for a blank/whitespace-only instructions field', () => {
      const payload: Record<string, unknown> = {}
      applyProjectContext(payload, makeProject({ instructions: '   ' }))
      expect(payload.projectInstructions).to.be.undefined
    })

    it('falls back to project knowledgeScope as filters when the request carried none', () => {
      const payload: Record<string, unknown> = {}
      applyProjectContext(
        payload,
        makeProject({ knowledgeScope: { apps: ['app-1'], kb: [] } }),
      )
      expect(payload.filters).to.deep.equal({ apps: ['app-1'], kb: [] })
    })

    it('request-supplied non-empty filters take precedence over the project scope', () => {
      const payload: Record<string, unknown> = {
        filters: { apps: ['request-app'] },
      }
      applyProjectContext(
        payload,
        makeProject({ knowledgeScope: { apps: ['project-app'] } }),
      )
      expect(payload.filters).to.deep.equal({ apps: ['request-app'] })
    })

    it('treats an empty apps/kb filters object on the request as "no filter" and still falls back', () => {
      const payload: Record<string, unknown> = { filters: { apps: [], kb: [] } }
      applyProjectContext(
        payload,
        makeProject({ knowledgeScope: { apps: ['project-app'] } }),
      )
      expect(payload.filters).to.deep.equal({ apps: ['project-app'] })
    })

    it('merges project files into attachments, de-duplicated by recordId, request wins on conflict', () => {
      const payload: Record<string, unknown> = {
        attachments: [
          { recordId: 'shared-1', recordName: 'request-version.pdf' },
        ],
      }
      applyProjectContext(
        payload,
        makeProject({
          files: [
            { recordId: 'shared-1', recordName: 'project-version.pdf' },
            { recordId: 'project-only', recordName: 'other.pdf' },
          ],
        }),
      )
      const attachments = payload.attachments as Array<{
        recordId: string
        recordName: string
      }>
      expect(attachments).to.have.lengthOf(2)
      expect(attachments.find((a) => a.recordId === 'shared-1')?.recordName).to.equal(
        'request-version.pdf',
      )
      expect(attachments.find((a) => a.recordId === 'project-only')).to.exist
    })

    it('leaves attachments untouched when the project has no files', () => {
      const payload: Record<string, unknown> = { attachments: [{ recordId: 'a' }] }
      applyProjectContext(payload, makeProject({ files: [] }))
      expect(payload.attachments).to.deep.equal([{ recordId: 'a' }])
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
      const result = await resolveProjectLink(ORG_ID, USER_ID, { projectId: 123 as any })
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
      } catch (error: any) {
        expect(error.message).to.include('MongoNetworkError')
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
