import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Kind, turnWorld } from '../helpers/turn-world'

const KINDS: Kind[] = ['chat', 'agent']

/** Frames and rows with ids, timestamps and run ids dropped, so only the shape a client or a reader sees remains. */
async function record(extended: boolean, kind: Kind) {
  const w = turnWorld({})
  const turn = await w.startRegenerate('A', kind)
  w.s.ai.send('TEXT_MESSAGE_CONTENT', { runId: 'run-root', messageId: 'm1', delta: 'Regenerated.' })
  await w.answerStream('Regenerated.')
  await turn.res.ended
  const frames = turn.res.events().map((e) => {
    const data = JSON.parse(JSON.stringify(e.data).replace(/"runId":"[^"]*"/g, '"runId":"<run>"'))
    if (e.event !== 'RUN_FINISHED') return { event: e.event, data }
    const { conversation, recordsUsed } = data.result
    const message = conversation.messages[0]
    return {
      event: e.event,
      resultKeys: Object.keys(data.result).sort(),
      recordsUsed,
      conversation: { status: conversation.status, rev: conversation.rev, activeRunHeld: conversation.activeRun != null },
      message: { messageType: message.messageType, content: message.content, keys: ['requestedBy', 'inReplyTo', 'runId'].filter((k) => k in message) },
    }
  })
  const rows = w.rows(kind).map((r) => ({
    messageType: r.messageType,
    content: r.content,
    seq: r.seq,
    keys: Object.keys(r).filter((k) => ['requestedBy', 'inReplyTo', 'runId', 'authorUserId'].includes(k)).sort(),
  }))
  const session = { status: w.session(kind).status, activeRun: w.session(kind).activeRun ?? null }
  return { frames, rows, session, headers: Object.keys(turn.res.headers).sort() }
}

/** What regenerate emits and writes today, recorded as the safety net for unifying it with the follow-up pump */
const expected = (extended: boolean) => {
  const delta = { event: 'TEXT_MESSAGE_CONTENT', data: { type: 'TEXT_MESSAGE_CONTENT', runId: '<run>', messageId: 'm1', delta: 'Regenerated.' } }
  return {
    frames: [
      {
        event: 'CUSTOM',
        data: { type: 'CUSTOM', name: 'conversation_created', value: { message: 'SSE connection established', ...(extended && { runId: '<run>' }) } },
      },
      delta,
      delta,
      {
        event: 'RUN_FINISHED',
        resultKeys: ['conversation', 'meta', 'recordsUsed'],
        recordsUsed: 0,
        conversation: { status: 'Complete', rev: extended ? 2 : 1, activeRunHeld: extended },
        message: { messageType: 'bot_response', content: 'Regenerated.', keys: extended ? ['requestedBy', 'inReplyTo', 'runId'] : ['requestedBy', 'inReplyTo'] },
      },
    ],
    rows: [
      { messageType: 'user_query', content: 'question', seq: 1, keys: [] },
      { messageType: 'bot_response', content: 'Regenerated.', seq: 2, keys: extended ? ['inReplyTo', 'requestedBy', 'runId'] : ['inReplyTo', 'requestedBy'] },
    ],
    session: { status: 'Complete', activeRun: null },
    headers: ['access-control-allow-origin', 'cache-control', 'connection', 'content-type', 'x-accel-buffering', ...(extended ? ['x-run-id'] : [])],
  }
}

describe('regenerate characterization (frames and rows recorded before the turn pump is unified)', () => {
  afterEach(() => sinon.restore())
  for (const kind of KINDS) {
    it(`${kind}: the SSE frames and the resulting rows are unchanged`, async () => {
      expect(await record(false, kind)).to.deep.equal(expected(false))
    })
  }
})
