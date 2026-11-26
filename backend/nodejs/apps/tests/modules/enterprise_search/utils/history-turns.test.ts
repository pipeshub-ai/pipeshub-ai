import fs from 'fs'
import path from 'path'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  formatPreviousConversations,
  historyTurns,
} from '../../../../src/modules/enterprise_search/utils/utils'
import { IMessage, IMessagePart } from '../../../../src/modules/enterprise_search/types/conversation.interfaces'
import { STEERED_SCENARIOS } from '../../../contracts/support/durable'
import { GOLDEN_DIR } from '../../../contracts/support/golden'
import { persistedRun } from '../../../contracts/support/surfaces'

/**
 * The history a later turn sends the model (`previousConversations`), for a
 * conversation a durable run steered (P1b T3, MG-3b): legacy continues it with
 * each steering message the run took as a user turn where it took it.
 */

type OracleToolResult = { tool_name?: string } & Record<string, unknown>

/** main's ask_user_question rows for a bot turn (#3592), frozen as the oracle reads them. */
const askRows = (msg: IMessage | undefined): OracleToolResult[] =>
  (msg?.tools ?? [])
    .filter((tool) => tool.toolName.includes('ask_user_question') && tool.toolResult)
    .map((tool) => ({
      tool_id: 'ask_user_question',
      tool_name: tool.toolName.includes('internaltools') ? tool.toolName : 'internaltools__ask_user_question',
      result: typeof tool.toolResult === 'string' ? tool.toolResult : JSON.stringify(tool.toolResult),
      status: 'success',
    }))

const askRowsForTurn = (messages: IMessage[], botIndex: number): OracleToolResult[] => {
  const own = askRows(messages[botIndex])
  if (own.length) return own
  let trailing: OracleToolResult[] = []
  for (let j = botIndex + 1; j < messages.length; j++) {
    if (messages[j]?.messageType !== 'tool_call') break
    const rows = askRows(messages[j])
    if (rows.length) trailing = rows
  }
  if (trailing.length) return trailing
  return messages[botIndex - 1]?.messageType === 'tool_call' ? askRows(messages[botIndex - 1]) : []
}

/**
 * `formatPreviousConversations` before T3 (with main's #3592 ask_user_question
 * rows), the oracle for every message without steering.
 */
const turnsBeforeT3 = (messages: IMessage[]) =>
  messages
    .map((msg, index) => ({ msg, index }))
    .filter(({ msg }) => msg.messageType !== 'error' && msg.messageType !== 'tool_call')
    .map(({ msg, index }) => {
      const partResults: OracleToolResult[] =
        msg.messageType === 'bot_response'
          ? (msg.parts ?? [])
              .filter((part) => part.type === 'tool_call' && part.toolName)
              .map((part) => {
                let args: Record<string, unknown> | undefined
                try {
                  const parsed = JSON.parse(part.args ?? '')
                  if (parsed && typeof parsed === 'object') args = parsed as Record<string, unknown>
                } catch {
                  args = undefined
                }
                return {
                  tool_id: part.toolCallId,
                  tool_name: part.toolName,
                  ...(args && { args }),
                  result: part.resultSummary || part.resultPreview || '',
                  ...(part.resultSummary && { result_summary: part.resultSummary }),
                  status: part.status === 'failed' ? 'error' : 'success',
                  ...(part.artifactId && { artifact_id: part.artifactId }),
                }
              })
          : []
      const toolResults =
        msg.messageType === 'bot_response' &&
        !partResults.some((row) => row.tool_name?.includes('ask_user_question'))
          ? [...partResults, ...askRowsForTurn(messages, index)]
          : partResults
      return {
        content: msg.content,
        role: msg.messageType,
        ...(msg.attachments && msg.attachments.length > 0 && { attachments: msg.attachments }),
        ...(msg.referenceData && msg.referenceData.length > 0 && { referenceData: msg.referenceData }),
        ...(toolResults.length > 0 && { tool_results: toolResults }),
        // Main's additions to every history turn since: a stopped reply says so, and an answer names its model.
        ...(msg.messageType === 'bot_response' && msg.status === 'stopped' && { status: 'stopped' }),
        ...(msg.messageType === 'bot_response' &&
          msg.modelInfo?.modelKey && {
            modelKey: msg.modelInfo.modelKey,
            modelName: msg.modelInfo.modelName,
            ...(msg.modelInfo.reasoningEffort && { reasoningEffort: msg.modelInfo.reasoningEffort }),
            ...(msg.modelInfo.modelNote && { modelNote: msg.modelInfo.modelNote }),
          }),
      }
    })

/** Main's failed-turn marker: an error row with no answer after it, while its question is open, is a failed answer. */
const answerFollowsAt = (messages: IMessage[], index: number): boolean => {
  for (const msg of messages.slice(index + 1)) {
    if (msg.messageType === 'bot_response') return true
    if (msg.messageType === 'user_query') return false
  }
  return false
}
const beforeT3 = (messages: IMessage[]) => {
  const turns = turnsBeforeT3(messages)
  const out: Array<Record<string, unknown>> = []
  let next = 0
  let questionOpen = false
  messages.forEach((msg, index) => {
    if (msg.messageType === 'tool_call') return
    if (msg.messageType === 'error') {
      if (questionOpen && !answerFollowsAt(messages, index)) {
        out.push({ role: 'bot_response', content: '', status: 'failed' })
        questionOpen = false
      }
      return
    }
    if (msg.messageType === 'user_query') questionOpen = true
    if (msg.messageType === 'bot_response') questionOpen = false
    out.push(turns[next++] as Record<string, unknown>)
  })
  return out
}

const tool = (id: string, extra: Partial<IMessagePart> = {}): IMessagePart => ({
  type: 'tool_call',
  toolCallId: id,
  toolName: `docs__${id}`,
  args: JSON.stringify({ q: id }),
  status: 'completed',
  resultPreview: `${id} preview`,
  resultSummary: `${id} summary`,
  ...extra,
})

const input = (content: string, messageSeq: number, consumed?: false): IMessagePart => ({
  type: 'user_input',
  content,
  messageSeq,
  ...(consumed === false ? { consumed } : {}),
})

const answer = (parts: IMessagePart[], extra: Partial<IMessage> = {}): IMessage => ({
  messageType: 'bot_response',
  content: 'The final answer.',
  parts,
  ...extra,
})

describe('history turns: a durable answer steered while it ran (MG-3b)', () => {
  afterEach(() => sinon.restore())

  it('every legacy golden transcript formats exactly as before T3', () => {
    const files = fs.readdirSync(GOLDEN_DIR).filter((file) => file.endsWith('.transcript.json'))
    expect(files.length).to.be.greaterThan(10)
    for (const file of files) {
      const { messages } = JSON.parse(fs.readFileSync(path.join(GOLDEN_DIR, file), 'utf8')) as {
        messages: IMessage[]
      }
      expect(formatPreviousConversations(messages), file).to.deep.equal(beforeT3(messages))
    }
  })

  it('messages without a taken steering message format exactly as before T3', () => {
    const messages: IMessage[] = [
      { messageType: 'user_query', content: 'q1', attachments: [{ recordId: 'r1', recordName: 'a.pdf' }] as never },
      answer([{ type: 'text', content: 'narration' }, tool('a'), { type: 'text', content: 'The final answer.', isFinal: true } as IMessagePart], {
        referenceData: [{ name: 'Doc', key: 'k' }] as never,
      }),
      { messageType: 'error', content: 'failed' },
      { messageType: 'tool_call', content: '', tools: [{ toolName: 'ask_user_question' }] as never },
      answer([tool('b'), input('never taken', 1, false)]),
      { messageType: 'feedback', content: 'thumbs' },
      answer([tool('c', { status: 'failed', resultSummary: undefined, artifactId: 'art-1' })]),
    ]
    expect(formatPreviousConversations(messages)).to.deep.equal(beforeT3(messages))
  })

  it('a message taken after a tool call is a user turn between the tool calls before and after it', () => {
    const turns = historyTurns(
      answer([{ type: 'text', content: '' }, tool('a'), input('Add Q4 too', 1), { type: 'text', content: '' }, tool('b'), { type: 'text', content: 'The final answer.', isFinal: true } as IMessagePart]),
    )
    expect(turns.map((t) => [t.role, t.content, (t.tool_results ?? []).map((r) => r.tool_id)])).to.deep.equal([
      ['bot_response', '', ['a']],
      ['user_query', 'Add Q4 too', []],
      ['bot_response', 'The final answer.', ['b']],
    ])
  })

  it('an answer the model gave before a message it then took is kept; narration before a tool call is not', () => {
    const turns = historyTurns(
      answer([
        { type: 'text', content: 'You get 25 days.' },
        input('And sick leave?', 1),
        { type: 'text', content: 'Looking it up' },
        tool('a'),
        input('Only this year', 2),
        { type: 'text', content: 'The final answer.', isFinal: true } as IMessagePart,
      ]),
    )
    expect(turns.map((t) => [t.role, t.content])).to.deep.equal([
      ['bot_response', 'You get 25 days.'],
      ['user_query', 'And sick leave?'],
      ['bot_response', ''],
      ['user_query', 'Only this year'],
      ['bot_response', 'The final answer.'],
    ])
  })

  it('a message taken before the first model call, or two at one boundary, are consecutive user turns', () => {
    const turns = historyTurns(answer([input('first', 1), input('second', 2), { type: 'text', content: 'The final answer.', isFinal: true } as IMessagePart]))
    expect(turns.map((t) => [t.role, t.content])).to.deep.equal([
      ['user_query', 'first'],
      ['user_query', 'second'],
      ['bot_response', 'The final answer.'],
    ])
  })

  it('a message the run never took is left out; the rest of the answer is unchanged', () => {
    const parts = [tool('a'), input('taken', 1), tool('b'), input('untaken', 2, false)]
    const turns = historyTurns(answer(parts, { referenceData: [{ name: 'Doc', key: 'k' }] as never }))
    expect(turns.map((t) => t.content)).to.not.include('untaken')
    const last = turns[turns.length - 1]!
    expect(last.referenceData).to.deep.equal([{ name: 'Doc', key: 'k' }])
    expect((last.tool_results ?? []).map((r) => r.tool_id)).to.deep.equal(['b'])
    expect(turns[0]).to.not.have.property('referenceData')
  })

  for (const [scenario, text] of Object.entries(STEERED_SCENARIOS)) {
    it(`${scenario}: the saved conversation continues on legacy with the message where the run took it`, async () => {
      const { run, store } = await persistedRun(scenario)
      const history = formatPreviousConversations(store.messagesOf(run.sessionId) as unknown as IMessage[])
      const roles = history.map((turn) => turn.role)
      expect(roles[0]).to.equal('user_query')
      const at = history.findIndex((turn) => turn.content === text)
      expect(at, 'the steering message is a user turn').to.be.greaterThan(1)
      expect(roles[at]).to.equal('user_query')
      expect(roles[at - 1], 'what the run did before taking it').to.equal('bot_response')
      expect(roles.slice(at + 1)).to.deep.equal(['bot_response'])
    })
  }
})
