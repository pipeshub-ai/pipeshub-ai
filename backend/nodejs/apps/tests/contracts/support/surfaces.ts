/**
 * What an external surface makes of a stream (P1b C-5): the published
 * `@pipeshub-ai/mcp` parser's tool result, and the Slack bot's AG-UI handler.
 * Also the durable run of a golden scenario persisted through the real
 * conversation module, so `RUN_FINISHED` carries the conversation Node reads.
 */
import { pathToFileURL } from 'url'
import { expect } from 'chai'
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution'
import { AguiFrame, Audience, ProjectedFrame } from '../../../src/modules/agent-harness/projection/frames'
import { RunProjector } from '../../../src/modules/agent-harness/projection/run-projector'
import { runTranscript } from '../../../src/modules/agent-harness/projection/transcript'
import { HarnessConversationReader } from '../../../src/libs/repositories/agent-harness/conversation-reader/mongo'
import { HarnessConversationWriter } from '../../../src/libs/repositories/agent-harness/conversation-writer/mongo'
import { createSlackAGUIEventHandler } from '../../../src/integrations/slack-bot/src/utils/agui-stream'
import { SlackActivityBuilder, slackActivityCallbacks } from '../../../src/integrations/slack-bot/src/utils/activity-ui'
import { formatAskUserQuestionMrkdwn } from '../../../src/integrations/slack-bot/src/utils/ask-user-format'
import { stripTinyRefCitationLinks } from '../../../src/integrations/slack-bot/src/utils/citations'
import { parseArtifactMarkers } from '../../../src/integrations/slack-bot/src/utils/parse-artifact-markers'
import { resolveSlackArtifactLink, resolveSlackErrorMessage } from '../../../src/integrations/slack-bot/src/helpers'
import { InMemoryChatStore, oid } from '../../modules/enterprise_search/controller/chat-test-harness'
import { CHAT_SCENARIOS, GoldenJournal, Step, parseSse, projectorContext, readJournal } from './durable'

interface ChatStreamState {
  conversation: Record<string, unknown> | null
  error: string | null
  conversationId: string | null
  title: string | null
}
export interface SSEFrame {
  event: string
  data: unknown
  raw: string
}
export interface McpParser {
  newChatStreamState(): ChatStreamState
  applyAGUIFrame(state: ChatStreamState, frame: SSEFrame): boolean
  iterateSSE(response: Response): AsyncIterable<SSEFrame>
  trimConversation(conversation: unknown): Record<string, unknown>
}

// The package is ESM-only; a real `import()` (not the CommonJS `require` ts-node
// would emit for `await import(...)`) is the only way to load it from here.
// eslint-disable-next-line @typescript-eslint/no-implied-eval -- the constant source keeps import() out of ts-node's CommonJS rewrite
const importESM = new Function('specifier', 'return import(specifier)') as (specifier: string) => Promise<unknown>

export const loadMcpParser = async (): Promise<McpParser> => {
  const base = '@pipeshub-ai/mcp/esm/mcp-server/tools'
  const agui = (await importESM(pathToFileURL(require.resolve(`${base}/_agui.js`)).href)) as Pick<McpParser, 'newChatStreamState' | 'applyAGUIFrame'>
  const helpers = (await importESM(pathToFileURL(require.resolve(`${base}/_helpers.js`)).href)) as Pick<McpParser, 'iterateSSE' | 'trimConversation'>
  return { ...agui, ...helpers }
}

/** Ids, clocks and stored-citation ids differ between two persisted runs; nothing else may. */
export const stable = (value: unknown): unknown =>
  JSON.parse(
    JSON.stringify(value ?? null)
      .replace(/"runId":"[^"]+"/g, '"runId":"<run>"')
      .replace(/"(?:[0-9a-f]{24}|oid-\d+)"/g, '"<oid>"')
      .replace(/"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z"|"<timestamp>"/g, '"<ts>"')
      .replace(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|uuid-\d+/g, '<uuid>'),
  )

/** The MCP `pipeshub_chat` tool's result for a stream body, drained as the package does. */
export async function mcpToolResult(parser: McpParser, body: string): Promise<{ terminal: string | null; error: string | null; result: unknown }> {
  const state = parser.newChatStreamState()
  let terminal: SSEFrame | null = null
  for await (const frame of parser.iterateSSE(new Response(body))) {
    if (parser.applyAGUIFrame(state, frame)) {
      terminal = frame
      break
    }
  }
  return {
    terminal: terminal?.event ?? null,
    error: state.error,
    result: state.conversation === null ? null : stable(parser.trimConversation(state.conversation)),
  }
}

/** One thing the Slack bot does to the thread (P1b C-20). */
export type SlackRender =
  | { activity: string }
  | { answer: string }
  | { question: string }
  | { artifact: string }
  | { final: { answer: unknown; citations: unknown } }
  | { error: string }

/**
 * Consecutive text or reasoning deltas as one: the chunking is the
 * provider's, and the bot batches both before it writes to Slack.
 */
function mergedChunks(body: string): AguiFrame[] {
  const frames: AguiFrame[] = []
  for (const { frame } of parseSse(body)) {
    const last = frames[frames.length - 1]
    const chunked = frame.type === 'TEXT_MESSAGE_CONTENT' || frame.type === 'REASONING_MESSAGE_CONTENT'
    if (chunked && last?.type === frame.type && last['runId'] === frame['runId']) {
      frames[frames.length - 1] = { ...last, delta: `${String(last['delta'])}${String(frame['delta'])}` }
    } else {
      frames.push(frame)
    }
  }
  return frames
}

/**
 * Every write the Slack bot makes to the thread for a stream, in order,
 * through its own handler, activity timeline, question and artifact
 * formatting (`index.ts`): each activity-message update, the streamed
 * answer as it stands after each append, the question, artifact links, and
 * the final answer or error. Unthrottled: the bot's timers only drop
 * intermediate writes of this sequence.
 */
export function slackRendering(body: string): SlackRender[] {
  const rendered: SlackRender[] = []
  const write = (entry: SlackRender): void => {
    if (JSON.stringify(rendered[rendered.length - 1]) !== JSON.stringify(entry)) rendered.push(entry)
  }
  const builder = new SlackActivityBuilder()
  const artifacts = new Set<string>()
  let asked = false
  let error: string | null = null
  let completion: { messages: Array<{ content?: string; citations?: unknown[]; messageType?: string }> } | null = null
  const handler = createSlackAGUIEventHandler({
    ...slackActivityCallbacks(builder, () => {
      if (!asked) write({ activity: builder.format() })
    }),
    onAnswerAccumulated: (text) => {
      if (!asked) write({ answer: parseArtifactMarkers(stripTinyRefCitationLinks(text)).text })
    },
    onClearAnswerPreamble: () => {
      if (!asked) write({ answer: '' })
    },
    onAskUserQuestion: (question) => {
      const text = formatAskUserQuestionMrkdwn(question)
      if (asked || !text) return
      asked = true
      write({ question: text })
    },
    onArtifact: (artifact) => {
      if (artifact.visibility === 'STAGING') return
      const link = resolveSlackArtifactLink(artifact)
      if (!link || artifacts.has(link)) return
      artifacts.add(link)
      write({ artifact: `*Artifact:* <${link}|${artifact.fileName ?? 'artifact'}>` })
    },
    onComplete: (conversation) => {
      completion = conversation
    },
    onError: (message) => {
      error = resolveSlackErrorMessage(message)
    },
  })
  for (const frame of mergedChunks(body)) {
    handler({ event: frame.type, data: frame })
    // The bot stops reading at the first error.
    if (error !== null) break
  }
  if (error !== null) {
    write({ error })
    return rendered
  }
  const finished = completion as { messages: Array<{ content?: string; citations?: unknown[]; messageType?: string }> } | null
  if (asked) return rendered
  if (finished === null) {
    write({ error: 'incomplete response' })
    return rendered
  }
  if (builder.hasTimeline()) {
    builder.setStatus('')
    write({ activity: builder.format() })
  }
  const bot = [...finished.messages].reverse().find((m) => m.messageType === 'bot_response')
  write({ final: { answer: bot?.content ?? null, citations: stable(bot?.citations ?? null) } })
  return rendered
}

export interface PersistedRun {
  journal: GoldenJournal
  run: HarnessRun
  store: InMemoryChatStore
  conversation: Record<string, unknown>
}

/**
 * The scenario's durable run with its transcript written by the real
 * conversation writer into an in-memory chat store, and the finished
 * conversation Node's `RUN_FINISHED` carries, read back by the real reader.
 */
export async function persistedRun(scenario: string): Promise<PersistedRun> {
  const journal = readJournal(scenario)
  const store = new InMemoryChatStore()
  store.install()
  const chat = CHAT_SCENARIOS.includes(scenario)
  const chatMode = chat ? 'internal_search' : 'quick'
  const owner = oid()
  const org = oid()
  const session = store.addSession({
    orgId: org,
    userId: owner,
    initiator: owner,
    title: journal.query.slice(0, 100),
    status: 'Inprogress',
    ...(chat ? { sessionType: 'chat' } : { agentKey: 'agent-1', sessionType: 'agent', conversationSource: 'agent_chat' }),
    harnessVersion: 'durable-v1',
    modelInfo: { chatMode },
  })
  store.addMessage(session, { messageType: 'user_query', content: journal.query })
  const run = {
    runId: journal.runId,
    orgId: String(org),
    sessionId: String(session._id),
    actorId: String(owner),
    ...(chat ? {} : { agentKey: 'agent-1' }),
    input: { model: { chatMode } },
  } as unknown as HarnessRun
  const transcript = runTranscript(journal.events, journal.runId)
  expect(transcript, 'the run ended').to.not.equal(null)
  expect(await new HarnessConversationWriter().saveRunTranscript(run, transcript!, null as never)).to.equal(true)
  const conversation = await new HarnessConversationReader().finishedConversation(run)
  expect(conversation).to.not.equal(null)
  return { journal, run, store, conversation: conversation! }
}

/** The frames an audience receives for a persisted run, fed in `steps` order. */
export function streamFor(persisted: PersistedRun, steps: Step[], audience: Audience): ProjectedFrame[] {
  const projector = new RunProjector({ ...projectorContext(persisted.journal, audience), conversationId: persisted.run.sessionId })
  const frames = projector.begin()
  for (const step of steps) {
    frames.push(...('journal' in step ? projector.project(step.journal) : projector.delta(step.delta)))
  }
  frames.push(...projector.finish(persisted.conversation, { requestId: persisted.run.runId, timestamp: new Date().toISOString(), duration: 0 }))
  return frames
}
