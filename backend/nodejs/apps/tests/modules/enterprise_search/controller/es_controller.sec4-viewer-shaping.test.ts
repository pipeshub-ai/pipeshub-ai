import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import { Logger } from '../../../../src/libs/services/logger.service'
import { ProjectService } from '../../../../src/modules/projects/services/project.service'
import { assertNoCanary, VECTOR_EXAMPLES, VECTOR_MASK } from '../../agent-harness/support/canary-scan'
import { FakeAIBackend, FakeSSEResponse, InMemoryChatStore, oid, settle } from './chat-test-harness'

/**
 * SEC4-N3 on the conversation GET, the one shared view of a conversation's
 * history: a message is shaped on the server unless it is the viewer's own
 * (`senderId`, or the initiator's when there is none, as session forks
 * decide). The raw response body is what a participant's devtools show.
 */

const appConfig = { aiBackend: 'http://ai.test', iamBackend: 'http://iam.test' } as never
const ORG = oid()
const OWNER = oid()
const RECIPIENT = oid()
const AGENT_KEY = 'agent-1'
const OWNER_KEY = VECTOR_EXAMPLES.find((e) => e.kind === 'github_token')!.secret
const RECIPIENT_KEY = VECTOR_EXAMPLES.find((e) => e.kind === 'slack_token')!.secret
const PASSWORD = 'hunter2-plain-words'

type JsonHandler = (req: never, res: never, next: never) => Promise<unknown>

function toolTurn(secret: string): Record<string, unknown> {
  return {
    messageType: 'bot_response',
    content: `Done; the key was ${secret}.`,
    parts: [
      {
        type: 'tool_call',
        toolCallId: 'c1',
        toolName: 'jira__create_issue',
        args: JSON.stringify({ title: 'deploy', password: PASSWORD, token: secret }),
        argsSummary: `Create with ${secret}`,
        status: 'completed',
        resultPreview: `{"echo": "${secret}"}`,
        resultSummary: `Created with ${secret}`,
      },
      { type: 'approval', interactionId: 'ix', toolCallId: 'c2', toolName: 'slack__post', args: { text: secret, apiKey: PASSWORD }, status: 'approved' },
      { type: 'engine_task', taskId: 't', frames: [{ kind: 'command', command: `curl -H "X: ${secret}"`, output: secret }] },
      { type: 'text', content: `Done; the key was ${secret}.`, isFinal: true },
    ],
  }
}

function setup() {
  const store = new InMemoryChatStore()
  store.install()
  new FakeAIBackend().install()
  sinon.stub(Logger.getInstance(), 'warn')
  const projectId = oid()
  ;(ProjectService.getAccessibleProjectIds as sinon.SinonStub).resolves([projectId])
  const shared = { isShared: true, sharedWith: [{ userId: RECIPIENT, accessLevel: 'read' }], harnessVersion: 'durable-v1' }
  const sessions = {
    chat: store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, title: 'Chat', sessionType: 'chat', ...shared }),
    agent: store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, title: 'Agent', sessionType: 'agent', agentKey: AGENT_KEY, conversationSource: 'agent_chat', projectId, projectVisibility: 'project', harnessVersion: 'durable-v1' }),
  }
  for (const session of Object.values(sessions)) {
    store.addMessage(session, { messageType: 'user_query', content: 'Create the issue' })
    store.addMessage(session, toolTurn(OWNER_KEY))
    store.addMessage(session, { messageType: 'user_query', content: 'Mine now', senderId: RECIPIENT })
    store.addMessage(session, { ...toolTurn(RECIPIENT_KEY), senderId: RECIPIENT })
  }
  return { sessions }
}

function request(userId: Types.ObjectId, params: Record<string, string>): never {
  return {
    headers: { authorization: `Bearer ${String(userId)}` },
    params,
    body: {},
    query: {},
    user: { userId: String(userId), orgId: String(ORG), email: 'someone@example.com' },
    context: { requestId: 'req-sec4' },
  } as never
}

async function read(handler: JsonHandler, req: never): Promise<{ body: string; messages: Array<Record<string, unknown>> }> {
  const res = new FakeSSEResponse()
  const next = sinon.stub()
  await handler(req, res as never, next as never)
  await settle()
  expect(next.called, String(next.firstCall?.args[0])).to.equal(false)
  const body = JSON.stringify(res.jsonBody)
  return { body, messages: (res.jsonBody as { conversation: { messages: Array<Record<string, unknown>> } }).conversation.messages }
}

const cases = [
  {
    name: 'chat',
    handler: () => controller.getConversationById(appConfig, {}) as JsonHandler,
    params: (s: ReturnType<typeof setup>) => ({ conversationId: String(s.sessions.chat._id) }),
  },
  {
    name: 'agent',
    handler: () => controller.getAgentConversationById(appConfig, {}) as JsonHandler,
    params: (s: ReturnType<typeof setup>) => ({ conversationId: String(s.sessions.agent._id), agentKey: AGENT_KEY }),
  },
]

describe('SEC4-N3: the conversation GET shapes every message that is not the viewer\'s own', () => {
  afterEach(() => sinon.restore())

  for (const kind of cases) {
    it(`${kind.name}: a participant receives the owner's tool calls masked and their own turn as it was`, async () => {
      const s = setup()
      const out = await read(kind.handler(), request(RECIPIENT, kind.params(s)))

      const owners = out.messages.filter((m) => m['senderId'] === undefined)
      expect(owners).to.have.length(2)
      assertNoCanary('the owner\'s turns, as the participant receives them', owners, { secrets: [OWNER_KEY, PASSWORD] })
      expect(JSON.stringify(owners)).to.include(VECTOR_MASK).and.include('Create the issue')
      expect(out.body).to.not.include(OWNER_KEY)
      const own = JSON.stringify(out.messages.filter((m) => m['senderId'] !== undefined))
      expect(own, 'the participant\'s own turn is theirs to see').to.include(RECIPIENT_KEY).and.include(PASSWORD)
    })

    it(`${kind.name}: the owner receives a collaborator's turn masked and their own as it was`, async () => {
      const s = setup()
      const out = await read(kind.handler(), request(OWNER, kind.params(s)))

      expect(out.body).to.include(OWNER_KEY)
      expect(out.body).to.not.include(RECIPIENT_KEY)
      const collaborators = out.messages.filter((m) => m['senderId'] !== undefined)
      expect(collaborators).to.have.length(2)
      assertNoCanary('the collaborator\'s turns, as the owner receives them', collaborators, { secrets: [RECIPIENT_KEY, PASSWORD] })
    })
  }
})
