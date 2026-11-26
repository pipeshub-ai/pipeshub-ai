import 'reflect-metadata'
import { pathToFileURL } from 'url'
import { expect } from 'chai'
import sinon from 'sinon'
import { SCENARIOS, replay } from './support/golden'

/**
 * Runs each browser-facing golden stream through the published
 * `@pipeshub-ai/mcp` package's own `pipeshub_chat` parsing: `iterateSSE`
 * + `applyAGUIFrame` + `trimConversation`, drained exactly the way
 * `pipeshubChat.js` does it.
 */

interface ChatStreamState {
  conversation: Record<string, unknown> | null
  error: string | null
  conversationId: string | null
  title: string | null
}
interface SSEFrame {
  event: string
  data: unknown
  raw: string
}
interface McpParser {
  newChatStreamState(): ChatStreamState
  applyAGUIFrame(state: ChatStreamState, frame: SSEFrame): boolean
  iterateSSE(response: Response): AsyncIterable<SSEFrame>
  trimConversation(conversation: unknown): Record<string, unknown>
}

// The package is ESM-only; a real `import()` (not the CommonJS `require` ts-node
// would emit for `await import(...)`) is the only way to load it from here.
// eslint-disable-next-line @typescript-eslint/no-implied-eval -- the constant source keeps import() out of ts-node's CommonJS rewrite
const importESM = new Function('specifier', 'return import(specifier)') as (specifier: string) => Promise<unknown>

const loadParser = async (): Promise<McpParser> => {
  const base = '@pipeshub-ai/mcp/esm/mcp-server/tools'
  const agui = (await importESM(pathToFileURL(require.resolve(`${base}/_agui.js`)).href)) as Pick<McpParser, 'newChatStreamState' | 'applyAGUIFrame'>
  const helpers = (await importESM(pathToFileURL(require.resolve(`${base}/_helpers.js`)).href)) as Pick<McpParser, 'iterateSSE' | 'trimConversation'>
  return { ...agui, ...helpers }
}

const drain = async (parser: McpParser, body: string): Promise<{ state: ChatStreamState; terminal: SSEFrame | null }> => {
  const state = parser.newChatStreamState()
  let terminal: SSEFrame | null = null
  for await (const frame of parser.iterateSSE(new Response(body))) {
    if (parser.applyAGUIFrame(state, frame)) {
      terminal = frame
      break
    }
  }
  return { state, terminal }
}

describe('contract: the published @pipeshub-ai/mcp parser reads every golden stream (G3)', () => {
  let parser: McpParser

  before(async () => {
    parser = await loadParser()
  })

  afterEach(() => {
    sinon.restore()
  })

  for (const scenario of SCENARIOS) {
    it(scenario.name, async () => {
      const run = await replay(scenario)
      const { state, terminal } = await drain(parser, run.res.body)
      const conversationId = String(run.conversation()._id)

      expect(state.conversationId).to.equal(conversationId)
      expect(state.title).to.equal(run.conversation().title)
      expect(terminal, 'the parser found a terminal frame').to.not.equal(null)

      if (terminal?.event === 'RUN_ERROR') {
        expect(state.error).to.equal((run.answer() as Record<string, unknown>).content)
        expect(state.conversation).to.equal(null)
        return
      }
      expect(terminal?.event).to.equal('RUN_FINISHED')
      expect(state.error).to.equal(null)
      const trimmed = parser.trimConversation(state.conversation)
      expect(trimmed.conversationId).to.equal(conversationId)
      expect(trimmed.title).to.equal(run.conversation().title)
      expect(trimmed.status).to.equal(run.conversation().status)
      // The package reads the answer only from a bot_response; a graceful model
      // error is saved as an `error` message, so MCP clients get `answer: null`.
      const lastBot = run.messages().filter((m) => m.messageType === 'bot_response').at(-1)
      expect(trimmed.answer).to.equal(lastBot ? lastBot.content : null)
      if (scenario.name === 'chat_llm_error') expect(trimmed).to.include({ answer: null, status: 'Failed' })
    })
  }

  it('does not end the stream on a sub-agent RUN_FINISHED that carries no result', async () => {
    const body =
      'event: RUN_FINISHED\ndata: {"type":"RUN_FINISHED","runId":"child","parentRunId":"root"}\n\n' +
      'event: RUN_FINISHED\ndata: {"type":"RUN_FINISHED","result":{"conversation":{"_id":"c1","messages":[]},"meta":{}}}\n\n'
    const { state, terminal } = await drain(parser, body)
    expect((terminal?.data as Record<string, unknown>).result).to.not.equal(undefined)
    expect(state.conversation).to.deep.equal({ _id: 'c1', messages: [] })
  })
})
