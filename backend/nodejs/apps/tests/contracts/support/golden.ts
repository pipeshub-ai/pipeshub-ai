/**
 * Replays the legacy Python AI streams recorded by
 * `backend/python/tests/contracts/test_sse_golden.py` through the real chat
 * controller and captures what the browser receives and what Mongo keeps.
 *
 * Reuses the enterprise_search chat harness: Mongoose documents are real (so
 * schema defaults and validation apply), only the driver calls are faked.
 */
import fs from 'fs'
import path from 'path'
import { expect } from 'chai'
import { AGENT_KEY, Flow, Run, appConfig, startStream } from '../../modules/enterprise_search/controller/streaming-flows'
import { settle } from '../../modules/enterprise_search/controller/chat-test-harness'

export const GOLDEN_DIR = path.join(__dirname, '..', 'golden')
export const UPSTREAM_DIR = path.join(GOLDEN_DIR, 'upstream')

/** Must match `RUN_ID` in the Python recorder, before normalization. */
const RUN_ID = '7a1c0e52-5b1d-4c3e-9f0a-2d4e6b8c0f11'

export const updateGoldens = (): boolean => process.env.UPDATE_GOLDENS === '1'

export interface Scenario {
  name: string
  flow: Flow
}

const chatFlow = (query: string): Flow => ({
  name: 'streamChat',
  handler: (c) => c.streamChat(appConfig) as never,
  prepare: () => ({ params: {}, body: { query, chatMode: 'internal_search', protocol: 'agui', runId: RUN_ID } }),
  aiPath: '/api/v1/chat/stream',
  agent: false,
  regenerate: false,
})

const agentFlow = (query: string, chatMode: string): Flow => ({
  name: 'streamAgentConversation',
  handler: (c) => c.streamAgentConversation(appConfig) as never,
  prepare: () => ({ params: { agentKey: AGENT_KEY }, body: { query, chatMode, protocol: 'agui', runId: RUN_ID } }),
  aiPath: `/api/v1/agent/${AGENT_KEY}/chat/stream`,
  agent: true,
  regenerate: false,
})

/** One entry per recorded upstream stream; queries match the Python scenarios. */
export const SCENARIOS: Scenario[] = [
  { name: 'chat_answer_with_citations', flow: chatFlow('What is the refund policy?') },
  { name: 'agent_quick_tool_call', flow: agentFlow('How much paid leave do I get?', 'quick') },
  { name: 'agent_react_tool_error_then_success', flow: agentFlow('What is the status of ticket T-42?', 'react') },
  { name: 'agent_ask_user_question', flow: agentFlow('Build me the sales report', 'quick') },
  { name: 'agent_artifact_marker_answer', flow: agentFlow('Export Q3 revenue as CSV', 'quick') },
  { name: 'agent_cancel_mid_stream', flow: agentFlow('Export the Q3 report', 'quick') },
  { name: 'agent_quick_reasoning', flow: agentFlow('How much paid leave do I get?', 'quick') },
  { name: 'agent_quick_final_answer', flow: agentFlow('How much paid leave do I get?', 'quick') },
  { name: 'agent_final_answer_with_write', flow: agentFlow('Export the Q3 report', 'quick') },
  { name: 'agent_search_tools_unavailable', flow: agentFlow('Close the Acme deal', 'quick') },
  { name: 'agent_pre_run_clarification', flow: agentFlow('report', 'react') },
  { name: 'agent_live_file_citation', flow: agentFlow('What does the Q3 plan say?', 'quick') },
  { name: 'chat_llm_error', flow: chatFlow('What is the refund policy?') },
  { name: 'chat_llm_unavailable', flow: chatFlow('What is the refund policy?') },
  { name: 'chat_llm_rejected', flow: chatFlow('What is the refund policy?') },
]

export const readUpstream = (name: string): string => fs.readFileSync(path.join(UPSTREAM_DIR, `${name}.sse`), 'utf8')

/** Feeds a recorded upstream stream into the controller and waits for it to finish saving. */
export async function replay(scenario: Scenario): Promise<Run> {
  const run = await startStream(scenario.flow)
  run.ai.sendRaw(readUpstream(scenario.name))
  run.ai.finish()
  await run.res.ended
  await settle(10)
  return run
}

const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/g
const OBJECT_ID = /\b[0-9a-f]{24}\b/g
const ISO_TS = /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?/g
// Clock readings Node stores as numbers rather than ISO strings.
const CLOCK_FIELDS = /"(lastActivityAt|duration)":(\s?)\d+/g

/**
 * Stable placeholders for generated ids and clock readings; nothing else
 * changes. Ids are numbered by first appearance, so a repeated id still
 * visibly refers to the same thing.
 */
export const normalize = (raw: string): string => {
  const seen = new Map<string, string>()
  const stable = (prefix: string) => (id: string): string => {
    if (!seen.has(id)) seen.set(id, `${prefix}-${seen.size + 1}`)
    return seen.get(id) as string
  }
  return raw
    .replace(UUID, stable('node-uuid'))
    .replace(OBJECT_ID, stable('oid'))
    .replace(ISO_TS, '<timestamp>')
    .replace(CLOCK_FIELDS, '"$1":$2"<clock>"')
}

/** Compares against the golden, or re-records it under `UPDATE_GOLDENS=1`. */
export const compareOrWrite = (file: string, actual: string): void => {
  const target = path.join(GOLDEN_DIR, file)
  if (updateGoldens()) {
    fs.mkdirSync(path.dirname(target), { recursive: true })
    fs.writeFileSync(target, actual)
    return
  }
  expect(fs.existsSync(target), `missing golden ${file}; record it with UPDATE_GOLDENS=1`).to.equal(true)
  expect(actual, `${file} drifted from the legacy contract; re-record with UPDATE_GOLDENS=1 only if intended`).to.equal(
    fs.readFileSync(target, 'utf8'),
  )
}
