import 'reflect-metadata'
import fs from 'fs'
import path from 'path'
import { expect } from 'chai'
import sinon from 'sinon'
import { HarnessRun } from '../../src/modules/agent-harness/domain/execution'
import { runTranscript } from '../../src/modules/agent-harness/projection/transcript'
import { InMemoryChatStore } from '../modules/enterprise_search/controller/chat-test-harness'
import { DURABLE_SCENARIOS, liveSteps, readJournal, sseBody } from './support/durable'
import { McpParser, SSEFrame, loadMcpParser, persistedRun, streamFor } from './support/surfaces'
import { GOLDEN_DIR } from './support/golden'

/**
 * The durable transcript through the real conversation module (P1.8): the
 * transcript projection is written by `HarnessConversationWriter`, read back
 * for Node's `RUN_FINISHED` by `HarnessConversationReader`, and the durable
 * SSE stream is parsed by the published `@pipeshub-ai/mcp` package (L-8).
 */

interface Persisted {
  run: HarnessRun
  store: InMemoryChatStore
  body: string
}

async function persistAndStream(scenario: string): Promise<Persisted> {
  const persisted = await persistedRun(scenario)
  return { run: persisted.run, store: persisted.store, body: sseBody(streamFor(persisted, liveSteps(persisted.journal), 'owner')) }
}

const stable = (value: unknown): unknown =>
  JSON.parse(
    JSON.stringify(value)
      .replace(/"runId":"[^"]+"/g, '"runId":"<run>"')
      .replace(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|uuid-\d+/g, '<uuid>')
      // Citation documents get fresh ObjectIds (`oid-N` in the recorded golden).
      .replace(/"citationId":"(?:[0-9a-f]{24}|oid-\d+)"/g, '"citationId":"<oid>"')
      // A question card's expiry is a clock reading (`<timestamp>` in the recorded golden).
      .replace(/"expiresAt":"[^"]+"/g, '"expiresAt":"<timestamp>"'),
  )

const MESSAGE_FIELDS = ['messageType', 'content', 'contentFormat', 'citations', 'confidence', 'parts', 'tools', 'status']

describe('contract: the durable transcript and the MCP parser (P1.8, L-6, L-8)', () => {
  let parser: McpParser

  before(async () => {
    parser = await loadMcpParser()
  })

  afterEach(() => {
    sinon.restore()
  })

  for (const scenario of DURABLE_SCENARIOS) {
    it(`${scenario}: the saved messages equal the legacy transcript's`, async () => {
      const { run, store } = await persistAndStream(scenario)
      const legacy = JSON.parse(fs.readFileSync(path.join(GOLDEN_DIR, `${scenario}.transcript.json`), 'utf8')) as {
        conversation: Record<string, unknown>
        messages: Array<Record<string, unknown>>
      }
      const pick = (message: Record<string, unknown>) =>
        Object.fromEntries(MESSAGE_FIELDS.filter((k) => message[k] !== undefined).map((k) => [k, message[k]]))
      const saved = store.messagesOf(run.sessionId).filter((m) => m.messageType !== 'user_query').map(pick)
      const expected = legacy.messages.filter((m) => m.messageType !== 'user_query').map(pick)
      expect(stable(saved)).to.deep.equal(stable(expected))
      expect(store.session(run.sessionId)?.status).to.equal(legacy.conversation['status'])
    })

    it(`${scenario}: the published @pipeshub-ai/mcp parser reads the durable stream (L-8)`, async () => {
      const { run, store, body } = await persistAndStream(scenario)
      const state = parser.newChatStreamState()
      let terminal: SSEFrame | null = null
      for await (const frame of parser.iterateSSE(new Response(body))) {
        if (parser.applyAGUIFrame(state, frame)) {
          terminal = frame
          break
        }
      }
      expect(state.conversationId).to.equal(run.sessionId)
      expect(state.title).to.equal(store.session(run.sessionId)?.title)
      const transcript = runTranscript(readJournal(scenario).events, run.runId)
      if (transcript?.kind === 'failure') {
        // Legacy's own shape for a run that could not start: RUN_ERROR with its message.
        expect(terminal?.event).to.equal('RUN_ERROR')
        expect(state.error).to.equal(transcript.message)
        return
      }
      expect(terminal?.event).to.equal('RUN_FINISHED')
      expect(state.error).to.equal(null)
      const trimmed = parser.trimConversation(state.conversation)
      const lastBot = store.messagesOf(run.sessionId).filter((m) => m.messageType === 'bot_response').at(-1)
      expect(trimmed.answer).to.equal(lastBot ? lastBot.content : null)
    })
  }
})
