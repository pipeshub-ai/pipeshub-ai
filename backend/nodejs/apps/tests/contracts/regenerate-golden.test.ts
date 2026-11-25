import 'reflect-metadata'
import fs from 'fs'
import path from 'path'
import { expect } from 'chai'
import sinon from 'sinon'
import { AGENT_KEY, Flow, seedConversation, startStream } from '../modules/enterprise_search/controller/streaming-flows'
import { FakeAIBackend, settle } from '../modules/enterprise_search/controller/chat-test-harness'
import { appConfig } from '../modules/enterprise_search/controller/streaming-flows'
import { GOLDEN_DIR, SCENARIOS, compareOrWrite, normalize, readUpstream } from './support/golden'
import { DURABLE_SCENARIOS, liveSteps, normalizeFrames, parseSse, project, readJournal } from './support/durable'
import { registerUpstreams } from '../../src/libs/http/upstream-call'
import { registerAppUpstreams } from '../../src/modules/tokens_manager/config/config'

/**
 * LAT2 GP-1, GP-2: what the browser receives from the legacy regenerate
 * routes for each recorded stream (the legacy contract, `golden/regenerate/`),
 * and the durable regenerate, streamed on the same route since LAT2, gives
 * the same frames under D-5's equivalence.
 */
const RUN_ID = '7a1c0e52-5b1d-4c3e-9f0a-2d4e6b8c0f11'

const regenerateFlow = (agent: boolean, chatMode: string | undefined): Flow => ({
  name: agent ? 'regenerateAgentAnswers' : 'regenerateAnswers',
  handler: (c) => (agent ? c.regenerateAgentAnswers(appConfig) : c.regenerateAnswers(appConfig)) as never,
  prepare: (store) => {
    const { session, botId } = seedConversation(store, agent)
    return {
      params: { conversationId: String(session._id), messageId: botId, ...(agent ? { agentKey: AGENT_KEY } : {}) },
      body: { protocol: 'agui', runId: RUN_ID, ...(chatMode !== undefined ? { chatMode } : {}) },
      existing: session,
      replacedMessageId: botId,
    }
  },
  aiPath: agent ? `/api/v1/agent/${AGENT_KEY}/chat/stream` : '/api/v1/chat/stream',
  agent,
  regenerate: true,
})

/** The asker can still read every record the answer cites, so the finished conversation hides nothing (main #5698). */
const askerReadsAll = (ai: FakeAIBackend): void => {
  ai.replyWith(/\/chat\/records\/access$/, (call) => ({
    status: 200,
    body: { accessible: call.body['recordIds'] ?? [], accessibleUrls: [], unmatchedUrls: call.body['webUrls'] ?? [] },
  }))
}

const regenerated = SCENARIOS.filter((s) => DURABLE_SCENARIOS.includes(s.name)).map((s) => ({
  name: s.name,
  flow: regenerateFlow(s.flow.agent, (s.flow.prepare({} as never).body as { chatMode?: string }).chatMode),
}))

describe('contract: the legacy regenerate routes’ frames, and the durable regenerate gives the same (LAT2 GP)', () => {
  beforeEach(() => registerAppUpstreams(appConfig))
  afterEach(() => registerUpstreams({}))

  afterEach(() => sinon.restore())

  for (const scenario of regenerated) {
    it(`${scenario.name}: legacy regenerate golden (GP-1); durable equals it (GP-2)`, async () => {
      const run = await startStream(scenario.flow, undefined, askerReadsAll)
      run.ai.sendRaw(readUpstream(scenario.name))
      run.ai.finish()
      await run.res.ended
      await settle(10)
      compareOrWrite(path.join('regenerate', `${scenario.name}.sse`), normalize(run.res.body))

      const legacy = parseSse(fs.readFileSync(path.join(GOLDEN_DIR, 'regenerate', `${scenario.name}.sse`), 'utf8')).map((f) => f.frame)
      const journal = readJournal(scenario.name)
      const [durableFirst, ...durable] = normalizeFrames(project(journal, liveSteps(journal)).map((f) => f.frame))
      const [legacyFirst, ...rest] = normalizeFrames(legacy)
      // The one intended difference: legacy regenerate opens with a bare greeting; a durable turn's first
      // frame names its conversation (and, for the owner, its run, which normalization drops).
      expect(legacyFirst).to.deep.equal({ type: 'CUSTOM', name: 'conversation_created', value: { message: 'SSE connection established' } })
      expect(durableFirst).to.deep.include({ type: 'CUSTOM', name: 'conversation_created' })
      expect(durableFirst?.value).to.have.property('conversationId')
      expect(durable).to.deep.equal(rest)
    })
  }
})
