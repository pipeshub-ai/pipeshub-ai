import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import { NextFunction, Response } from 'express'
import { setConversationProject } from '../../../../src/modules/enterprise_search/controller/es_controller'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ProjectService } from '../../../../src/modules/projects/services/project.service'
import { AuthenticatedUserRequest } from '../../../../src/libs/middlewares/types'
import { IProjectDocument, ProjectAccess } from '../../../../src/modules/projects/types/project.interfaces'

const USER_ID = 'aaaaaaaaaaaaaaaaaaaaaaaa'
const ORG_ID = 'bbbbbbbbbbbbbbbbbbbbbbbb'
const CONVERSATION_ID = 'cccccccccccccccccccccccc'
const PROJECT_ID = 'dddddddddddddddddddddddd'

type SessionDoc = Awaited<ReturnType<typeof ChatSession.findOneAndUpdate>>

interface MockResponse {
  status: sinon.SinonStub
  json: sinon.SinonStub
}

function createMockRequest(body: Record<string, unknown>): AuthenticatedUserRequest {
  const req: Partial<AuthenticatedUserRequest> = {
    headers: {},
    body,
    params: { conversationId: CONVERSATION_ID },
    query: {},
    user: { userId: USER_ID, orgId: ORG_ID },
  }
  return req as AuthenticatedUserRequest
}

function createMockResponse(): MockResponse {
  const res: MockResponse = { status: sinon.stub(), json: sinon.stub() }
  res.status.returns(res)
  res.json.returns(res)
  return res
}

function sessionDoc(fields: Record<string, unknown>): SessionDoc {
  return fields as unknown as SessionDoc
}

describe('setConversationProject response', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('returns projectId and projectVisibility as null after unlinking', async () => {
    sinon.stub(ChatSession, 'findOne').resolves(sessionDoc({ _id: CONVERSATION_ID }))
    // An unlinked session has neither field; the response must still carry both keys.
    sinon.stub(ChatSession, 'findOneAndUpdate').resolves(sessionDoc({ _id: CONVERSATION_ID }))

    const res = createMockResponse()
    const next = sinon.stub<[], void>()
    await setConversationProject(
      createMockRequest({ projectId: null }),
      res as unknown as Response,
      next as NextFunction,
    )

    expect(next.called).to.equal(false)
    expect(res.status.calledWith(200)).to.equal(true)
    expect(res.json.firstCall.args[0]).to.deep.equal({
      conversationId: CONVERSATION_ID,
      projectId: null,
      projectVisibility: null,
    })
  })

  it('returns the linked projectId and visibility', async () => {
    const projectObjectId = new mongoose.Types.ObjectId(PROJECT_ID)
    const access: ProjectAccess = {
      role: 'owner',
      project: { _id: projectObjectId } as unknown as IProjectDocument,
    }
    sinon.stub(ChatSession, 'findOne').resolves(sessionDoc({ _id: CONVERSATION_ID }))
    sinon.stub(ProjectService, 'assertAccess').resolves(access)
    sinon.stub(ProjectService, 'touchActivity').resolves()
    sinon.stub(ChatSession, 'findOneAndUpdate').resolves(
      sessionDoc({ _id: CONVERSATION_ID, projectId: projectObjectId, projectVisibility: 'private' }),
    )

    const res = createMockResponse()
    await setConversationProject(
      createMockRequest({ projectId: PROJECT_ID }),
      res as unknown as Response,
      sinon.stub<[], void>() as NextFunction,
    )

    const body = res.json.firstCall.args[0]
    expect(body.projectId.toString()).to.equal(PROJECT_ID)
    expect(body.projectVisibility).to.equal('private')
  })
})
