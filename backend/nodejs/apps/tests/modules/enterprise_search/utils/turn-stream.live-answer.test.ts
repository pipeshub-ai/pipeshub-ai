import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { conversationExtensions } from '../../../../src/modules/enterprise_search/services/conversation/conversation-extensions'
import {
  LiveAnswer,
  LiveAnswerStatus,
  LiveCitations,
  TurnStreamExtension,
} from '../../../../src/modules/enterprise_search/services/conversation/turn/turn-extension'
import { DEFAULT_RUN_LIMITS, StreamedTranscript, toLiveCitation } from '../../../../src/modules/enterprise_search/utils/turn-stream'
import { resetActiveRunsForTests, stopAllRuns } from '../../../../src/modules/enterprise_search/utils/active-runs'
import { settle } from '../controller/chat-test-harness'
import { delta, finalAnswer, flows, RUN, startStream } from '../controller/streaming-flows'

/** What the extension was told of one run's answer as it was written. */
function recordingLive(): LiveAnswer & {
  texts: string[]
  steps: unknown[][]
  cited: (LiveCitations | undefined)[]
  ends: LiveAnswerStatus[]
} {
  const texts: string[] = []
  const steps: unknown[][] = []
  const cited: (LiveCitations | undefined)[] = []
  const ends: LiveAnswerStatus[] = []
  return {
    texts,
    steps,
    cited,
    ends,
    text: ({ answer, steps: stepsSoFar, citations }) => {
      texts.push(answer)
      if (stepsSoFar) steps.push(JSON.parse(JSON.stringify(stepsSoFar)))
      cited.push(citations && JSON.parse(JSON.stringify(citations)))
    },
    done: (status) => {
      ends.push(status)
    },
  }
}

const withLive = (live: LiveAnswer | undefined): sinon.SinonSpy => {
  const liveAnswer = sinon.spy((): LiveAnswer | undefined => live)
  const extension: TurnStreamExtension = {
    respondingAgentView: () => Promise.resolve(undefined),
    runLimits: () => DEFAULT_RUN_LIMITS,
    liveAnswer,
  }
  sinon.stub(conversationExtensions().turns, 'streamFor').returns(extension)
  return liveAnswer
}

const toolStart = (toolCallId: string, extra: Record<string, unknown> = {}) => ({ runId: RUN, toolCallId, toolCallName: 'search', ...extra })

/** The newest of what was recorded. */
const last = <T>(list: readonly T[]): T | undefined => list[list.length - 1]

const CITE = { content: 'Paris is the capital of France.', chunkIndex: 1, citationType: 'vectordb|document', metadata: { recordId: 'r1', recordName: 'Atlas' } }
const NONE = { settledLength: 0, items: [] }

/**
 * The AI backend's numbered copy of the answer so far (`AGUIFormatter.answer_delta`): its citations as numbered
 * links, how much of the raw text it covers (in code points), and the citations the numbers point at.
 */
const numbered = (normalizedAnswer: string, rawSoFar: string, citations: unknown[], extra: Record<string, unknown> = {}) => ({
  runId: RUN,
  delta: [
    { op: 'replace', path: '/citations', value: citations },
    { op: 'replace', path: '/normalizedAnswer', value: normalizedAnswer },
    { op: 'replace', path: '/rawLength', value: Array.from(rawSoFar).length },
  ],
  ...extra,
})

/** The AI backend's copy of the answer once a model call was discarded (`answer_delta(discarded=True)`). */
const discardedCopy = (normalizedAnswer: string, rawSoFar: string, citations: unknown[]) => {
  const frame = numbered(normalizedAnswer, rawSoFar, citations)
  return { ...frame, delta: [...frame.delta, { op: 'replace', path: '/discarded', value: true }] }
}

describe('the answer as it is written, for the run extension (liveAnswer)', () => {
  beforeEach(() => resetActiveRunsForTests())
  afterEach(() => {
    resetActiveRunsForTests()
    sinon.restore()
  })

  for (const flow of flows) {
    it(`${flow.name}: is told the answer so far after each text and tool start, then once that it completed`, async () => {
      const live = recordingLive()
      const liveAnswer = withLive(live)
      const run = await startStream(flow)
      run.ai.send('TEXT_MESSAGE_CONTENT', delta('Let me '))
      run.ai.send('TEXT_MESSAGE_CONTENT', delta('check.'))
      run.ai.send('TOOL_CALL_START', toolStart('c1'))
      run.ai.send('TOOL_CALL_ARGS', {
        runId: RUN,
        toolCallId: 'c1',
        delta: '{"q":"secret args"}',
        argsSummary: 'secret args',
      })
      run.ai.send('TOOL_CALL_RESULT', {
        runId: RUN,
        toolCallId: 'c1',
        status: 'completed',
        resultSummary: 'secret result',
      })
      run.ai.send('CUSTOM', {
        runId: RUN,
        name: 'tool_auth_required',
        value: { message: 'Sign in to secret-tool' },
      })
      run.ai.send('TEXT_MESSAGE_CONTENT', delta('Found ', { messageId: 'm2' }))
      run.ai.send('TEXT_MESSAGE_CONTENT', delta('it.', { messageId: 'm2' }))
      run.ai.send('RUN_FINISHED', finalAnswer('Found it.'))
      run.ai.finish()
      await run.res.ended

      expect(liveAnswer.calledOnce).to.equal(true)
      const [turnRun, conversation] = liveAnswer.firstCall.args as unknown as [unknown, { _id: unknown }]
      expect(turnRun).to.be.an('object')
      expect(String(conversation._id)).to.equal(String(run.conversation()._id))
      // A tool call starts a new answer: the text before it was a step, not the answer. A regeneration tells per
      // chunk, so it may repeat an unchanged answer, which the publisher drops.
      const told = live.texts.filter((t, i) => i === 0 || t !== live.texts[i - 1])
      expect(told).to.deep.equal(['Let me ', 'Let me check.', '', 'Found ', 'Found it.'])
      expect(live.texts.join(' ')).to.not.match(/secret|Sign in/)
      expect(live.ends).to.deep.equal(['complete'])
      expect(run.answer()?.content).to.equal('Found it.')
    })
  }

  for (const flow of flows) {
    it(`${flow.name}: is told the AI backend's numbered answer and its citations, then the text streamed after it`, async () => {
      const live = recordingLive()
      withLive(live)
      const run = await startStream(flow)
      const raw = 'Paris is the capital [Atlas](ref1).'
      const cited = 'Paris is the capital [1](https://app/record/r1).'
      run.ai.send('TEXT_MESSAGE_CONTENT', delta(raw))
      run.ai.send('STATE_DELTA', numbered(cited, raw, [CITE]))
      run.ai.send('TEXT_MESSAGE_CONTENT', delta(' It is big.'))
      run.ai.send('RUN_FINISHED', finalAnswer(`${cited} It is big.`))
      run.ai.finish()
      await run.res.ended
      expect(live.texts[0]).to.equal(raw)
      expect(live.cited[0]).to.deep.equal(NONE)
      expect(last(live.texts)).to.equal(`${cited} It is big.`)
      expect(last(live.cited)).to.deep.equal({ settledLength: cited.length, items: [CITE] })
      expect(live.ends).to.deep.equal(['complete'])
      // The asker's own stream is unchanged: every frame still goes to the browser.
      expect(run.res.eventsOf('STATE_DELTA')).to.have.length(1)
    })
  }

  it('a numbered copy that does not yet cover what streamed waits: the one before it stays, with the text since', async () => {
    const live = recordingLive()
    withLive(live)
    const run = await startStream(flows[0]!)
    run.ai.send('TEXT_MESSAGE_CONTENT', delta('One [a](ref1).'))
    run.ai.send('STATE_DELTA', numbered('One [1](u1).', 'One [a](ref1).', [CITE]))
    run.ai.send('TEXT_MESSAGE_CONTENT', delta(' Two [b](ref2).'))
    // Computed before ' Two …' arrived here: behind what streamed.
    run.ai.send('STATE_DELTA', numbered('One [1](u1). Tw', 'One [a](ref1). Tw', [CITE, { ...CITE, chunkIndex: 2 }]))
    await settle()
    expect(last(live.texts)).to.equal('One [1](u1). Two [b](ref2).')
    expect(last(live.cited)).to.deep.equal({ settledLength: 'One [1](u1).'.length, items: [CITE] })
    run.ai.send('RUN_FINISHED', finalAnswer('One [1](u1). Two [2](u2).'))
    run.ai.finish()
    await run.res.ended
  })

  it('counts what the numbered copy covers in characters, as the AI backend does, not UTF-16 units', async () => {
    const live = recordingLive()
    withLive(live)
    const run = await startStream(flows[0]!)
    run.ai.send('TEXT_MESSAGE_CONTENT', delta('👍 Yes [a](ref1).'))
    run.ai.send('STATE_DELTA', numbered('👍 Yes [1](u1).', '👍 Yes [a](ref1).', [CITE]))
    run.ai.send('TEXT_MESSAGE_CONTENT', delta(' Done.'))
    await settle()
    expect(last(live.texts)).to.equal('👍 Yes [1](u1). Done.')
    run.ai.send('RUN_FINISHED', finalAnswer('👍 Yes [1](u1). Done.'))
    run.ai.finish()
    await run.res.ended
  })

  // The AI backend holds back the start of a line that may become its confidence trailer; its numbered copy counts
  // those characters already (`rawLength`), so they stream after the copy and are appended to it, as the asker's are.
  for (const held of ['\n\n**', '\n\nCon', '\n\n---']) {
    it(`appends what streams after a copy, characters the AI backend held back included (${JSON.stringify(held)})`, async () => {
      const live = recordingLive()
      withLive(live)
      const run = await startStream(flows[0]!)
      run.ai.send('TEXT_MESSAGE_CONTENT', delta('One [a](ref1).'))
      run.ai.send('STATE_DELTA', numbered('One [1](u1).', `One [a](ref1).${held}`, [CITE]))
      const rest = held.startsWith('\n\nCon') ? '\n\nConsider this.' : held.startsWith('\n\n**') ? '\n\n**Summary**' : '\n\n---\nAfter the rule.'
      run.ai.send('TEXT_MESSAGE_CONTENT', delta(rest))
      await settle()
      expect(last(live.texts)).to.equal(`One [1](u1).${rest}`)
      run.ai.send('RUN_FINISHED', finalAnswer(`One [1](u1).${rest}`))
      run.ai.finish()
      await run.res.ended
    })
  }

  it("a tool call starts a new answer: the numbered copy and its citations go with the old one; a sub-agent's never count", async () => {
    const live = recordingLive()
    withLive(live)
    const run = await startStream(flows[0]!)
    run.ai.send('TEXT_MESSAGE_CONTENT', delta('Let me check [a](ref1).'))
    run.ai.send('STATE_DELTA', numbered('Let me check [1](u1).', 'Let me check [a](ref1).', [CITE]))
    await settle()
    // The copy was taken: what goes away below was there.
    expect(last(live.texts)).to.equal('Let me check [1](u1).')
    expect(last(live.cited)).to.deep.equal({ settledLength: 'Let me check [1](u1).'.length, items: [CITE] })
    run.ai.send('TOOL_CALL_START', toolStart('c1'))
    run.ai.send('TEXT_MESSAGE_CONTENT', delta('Found it.', { messageId: 'm2' }))
    run.ai.send('STATE_DELTA', numbered('Child [9](u9).', 'Child [z](ref9).', [CITE], { runId: 'run-child', parentRunId: RUN }))
    await settle()
    expect(last(live.texts)).to.equal('Found it.')
    expect(last(live.cited)).to.deep.equal(NONE)
    run.ai.send('RUN_FINISHED', finalAnswer('Found it.'))
    run.ai.finish()
    await run.res.ended
  })

  // `_clear_preamble`: an empty copy (rawLength 0) as a tool call starts, before or after its frame.
  for (const order of ['before', 'after'] as const) {
    it(`an empty copy as a tool call starts (${order} its frame) leaves no numbered text nor citations`, async () => {
      const live = recordingLive()
      withLive(live)
      const run = await startStream(flows[0]!)
      run.ai.send('TEXT_MESSAGE_CONTENT', delta('Let me check [a](ref1).'))
      run.ai.send('STATE_DELTA', numbered('Let me check [1](u1).', 'Let me check [a](ref1).', [CITE]))
      if (order === 'before') run.ai.send('STATE_DELTA', numbered('', '', []))
      run.ai.send('TOOL_CALL_START', toolStart('c1'))
      if (order === 'after') run.ai.send('STATE_DELTA', numbered('', '', []))
      await settle()
      expect(last(live.texts)).to.equal('')
      expect(last(live.cited)).to.deep.equal(NONE)
      run.ai.send('TEXT_MESSAGE_CONTENT', delta('Found [b](ref2).', { messageId: 'm2' }))
      run.ai.send('STATE_DELTA', numbered('Found [1](u2).', 'Found [b](ref2).', [{ ...CITE, content: 'Found.' }]))
      await settle()
      expect(last(live.texts)).to.equal('Found [1](u2).')
      expect(last(live.cited)).to.deep.equal({ settledLength: 'Found [1](u2).'.length, items: [{ ...CITE, content: 'Found.' }] })
      run.ai.send('RUN_FINISHED', finalAnswer('Found [1](u2).'))
      run.ai.finish()
      await run.res.ended
    })
  }

  it('an artifact added between numbered copies changes neither the answer nor its citations', async () => {
    const live = recordingLive()
    withLive(live)
    const run = await startStream(flows[0]!)
    run.ai.send('TEXT_MESSAGE_CONTENT', delta('See [a](ref1).'))
    run.ai.send('STATE_DELTA', numbered('See [1](u1).', 'See [a](ref1).', [CITE]))
    run.ai.send('STATE_DELTA', { runId: RUN, delta: [{ op: 'add', path: '/artifacts/-', value: { artifactId: 'x1', fileName: 'report.csv' } }] })
    run.ai.send('TEXT_MESSAGE_CONTENT', delta(' Done.'))
    await settle()
    expect(last(live.texts)).to.equal('See [1](u1). Done.')
    expect(last(live.cited)).to.deep.equal({ settledLength: 'See [1](u1).'.length, items: [CITE] })
    run.ai.send('RUN_FINISHED', finalAnswer('See [1](u1). Done.'))
    run.ai.finish()
    await run.res.ended
  })

  it('a citation goes out with only the fields a chip draws that the saved citation keeps; one that cannot be drawn is left out', async () => {
    const live = recordingLive()
    withLive(live)
    const run = await startStream(flows[0]!)
    const full = {
      content: 'Paris.',
      chunkIndex: 1,
      citationType: 'vectordb|document',
      score: 0.93,
      metadata: {
        recordId: 'r1', recordName: 'Atlas', origin: 'UPLOAD', connector: 'KB', mimeType: 'text/markdown', extension: 'md',
        webUrl: '/record/r1', previewRenderable: true, pageNum: [2], blockNum: [7], bounding_box: [{ x: 1, y: 2, z: 3 }],
        virtualRecordId: 'v-secret', _collection_name: 'records', score: 0.9, blockText: 'the whole block', orgId: 'o1', topics: ['geo'],
      },
    }
    run.ai.send('TEXT_MESSAGE_CONTENT', delta('Paris [a](ref1).'))
    run.ai.send('STATE_DELTA', numbered('Paris [1](u1).', 'Paris [a](ref1).', [full, { chunkIndex: 2 }, 'junk', { ...CITE, metadata: {} }]))
    await settle()
    expect(last(live.cited)!.items).to.deep.equal([
      {
        content: 'Paris.',
        chunkIndex: 1,
        citationType: 'vectordb|document',
        metadata: {
          recordId: 'r1', recordName: 'Atlas', origin: 'UPLOAD', connector: 'KB', mimeType: 'text/markdown', extension: 'md',
          webUrl: '/record/r1', previewRenderable: true, pageNum: [2], blockNum: [7], bounding_box: [{ x: 1, y: 2 }],
        },
      },
    ])
    expect(JSON.stringify(last(live.cited))).to.not.match(/virtualRecordId|v-secret|_collection_name|score|blockText|orgId|topics/)
    run.ai.send('RUN_FINISHED', finalAnswer('Paris [1](u1).'))
    run.ai.finish()
    await run.res.ended
  })

  it('a run nobody else watches reads no numbered copy; a watched one does', () => {
    const copy = JSON.stringify({ type: 'STATE_DELTA', ...numbered('Hi [1](u1).', 'Hi [a](ref1).', [CITE]) })
    const solo = new StreamedTranscript()
    solo.feed('TEXT_MESSAGE_CONTENT', JSON.stringify({ type: 'TEXT_MESSAGE_CONTENT', ...delta('Hi [a](ref1).') }))
    solo.feed('STATE_DELTA', copy)
    expect(solo.getLiveAnswer().answer).to.equal('Hi [a](ref1).')
    expect(solo.getLiveAnswer().citations).to.deep.equal(NONE)
    const watched = new StreamedTranscript()
    watched.followNumberedCopies()
    watched.feed('TEXT_MESSAGE_CONTENT', JSON.stringify({ type: 'TEXT_MESSAGE_CONTENT', ...delta('Hi [a](ref1).') }))
    watched.feed('STATE_DELTA', copy)
    expect(watched.getLiveAnswer()).to.deep.include({ answer: 'Hi [1](u1).', citations: { settledLength: 'Hi [1](u1).'.length, items: [CITE] } })
    expect(toLiveCitation({ chunkIndex: 1 })).to.equal(undefined)
  })

  const flow = flows[0]!

  it('is told the steps before the answer as they change: the text before a tool call, then the call and its result', async () => {
    const live = recordingLive()
    withLive(live)
    const run = await startStream(flow)
    run.ai.send('TEXT_MESSAGE_CONTENT', delta('Let me check.'))
    run.ai.send('TOOL_CALL_START', toolStart('c1', { displayName: 'Searched' }))
    run.ai.send('TOOL_CALL_RESULT', { runId: RUN, toolCallId: 'c1', status: 'completed', resultSummary: '3 files' })
    run.ai.send('TEXT_MESSAGE_CONTENT', delta('Found it.', { messageId: 'm2' }))
    run.ai.send('RUN_FINISHED', finalAnswer('Found it.'))
    run.ai.finish()
    await run.res.ended
    expect(live.steps[0]).to.deep.equal([])
    expect(live.steps[1]).to.deep.equal([
      { type: 'text', content: 'Let me check.' },
      { type: 'tool_call', toolCallId: 'c1', toolName: 'search', status: 'blocked', displayName: 'Searched' },
    ])
    expect(live.steps[2]).to.deep.include({ type: 'tool_call', toolCallId: 'c1', toolName: 'search', status: 'completed', displayName: 'Searched', resultSummary: '3 files' })
    expect(live.steps[live.steps.length - 1]).to.have.length(2)
    expect(live.texts[live.texts.length - 1]).to.equal('Found it.')
  })

  // A regeneration is not relayed by TurnStreamPump: its own path must tell the steps too.
  for (const regen of flows.filter((f) => /^regenerate/.test(f.name))) {
    it(`${regen.name}: is told the steps before the answer as they change, as a first answer is, and which answer it replaces`, async () => {
      const live = recordingLive()
      const liveAnswer = withLive(live)
      const run = await startStream(regen)
      run.ai.send('TEXT_MESSAGE_CONTENT', delta('Let me check.'))
      run.ai.send('TOOL_CALL_START', toolStart('c1', { displayName: 'Searched' }))
      run.ai.send('TOOL_CALL_RESULT', { runId: RUN, toolCallId: 'c1', status: 'completed', resultSummary: '3 files' })
      run.ai.send('TEXT_MESSAGE_CONTENT', delta('Found it.', { messageId: 'm2' }))
      run.ai.send('RUN_FINISHED', finalAnswer('Found it.'))
      run.ai.finish()
      await run.res.ended
      expect(live.steps.length).to.be.greaterThan(0)
      expect(live.steps).to.deep.include([
        { type: 'text', content: 'Let me check.' },
        { type: 'tool_call', toolCallId: 'c1', toolName: 'search', status: 'blocked', displayName: 'Searched' },
      ])
      expect(last(live.steps)).to.deep.equal([
        { type: 'text', content: 'Let me check.' },
        { type: 'tool_call', toolCallId: 'c1', toolName: 'search', status: 'completed', displayName: 'Searched', resultSummary: '3 files' },
      ])
      expect(last(live.texts)).to.equal('Found it.')
      const [turnRun] = liveAnswer.firstCall.args as unknown as [{ replaces?: unknown; editedQuestion?: unknown }]
      expect(String(turnRun.replaces)).to.equal(run.prepared.replacedMessageId)
      expect(turnRun.editedQuestion).to.equal(undefined)
    })
  }

  // An edit of the last question is a regeneration with the new question.
  for (const regen of flows.filter((f) => /^regenerate/.test(f.name))) {
    it(`${regen.name}, edited: names the answer it replaces and the new question, and tells the steps`, async () => {
      const live = recordingLive()
      const liveAnswer = withLive(live)
      const edited = { ...regen, prepare: (store: Parameters<typeof regen.prepare>[0]) => ({ ...regen.prepare(store), body: { query: 'What changed, in one line?' } }) }
      const run = await startStream(edited)
      run.ai.send('TEXT_MESSAGE_CONTENT', delta('Let me check.'))
      run.ai.send('TOOL_CALL_START', toolStart('c1'))
      run.ai.send('TEXT_MESSAGE_CONTENT', delta('One line.', { messageId: 'm2' }))
      run.ai.send('RUN_FINISHED', finalAnswer('One line.'))
      run.ai.finish()
      await run.res.ended
      const [turnRun] = liveAnswer.firstCall.args as unknown as [{ replaces?: unknown; editedQuestion?: unknown }]
      expect(String(turnRun.replaces)).to.equal(run.prepared.replacedMessageId)
      expect(turnRun.editedQuestion).to.equal('What changed, in one line?')
      expect(last(live.steps)).to.have.length(2)
      expect(last(live.texts)).to.equal('One line.')
    })
  }

  it('a first answer replaces no answer', async () => {
    const liveAnswer = withLive(recordingLive())
    const run = await startStream(flows[0]!)
    run.ai.send('RUN_FINISHED', finalAnswer('Hi'))
    run.ai.finish()
    await run.res.ended
    const [turnRun] = liveAnswer.firstCall.args as unknown as [{ replaces?: unknown }]
    expect(turnRun.replaces).to.equal(undefined)
  })

  it("never passes on a sub-agent's own text", async () => {
    const live = recordingLive()
    withLive(live)
    const run = await startStream(flow)
    run.ai.send('TEXT_MESSAGE_CONTENT', delta('Asking the coding agent.'))
    run.ai.send('TEXT_MESSAGE_CONTENT', {
      runId: 'run-child',
      parentRunId: RUN,
      messageId: 'c',
      delta: 'child text',
    })
    run.ai.send('RUN_FINISHED', finalAnswer('Done.'))
    run.ai.finish()
    await run.res.ended
    expect(live.texts.some((t) => t.includes('child text'))).to.equal(false)
    expect(live.ends).to.deep.equal(['complete'])
  })

  // A regeneration is not relayed by TurnStreamPump: its own ends must tell too.
  for (const ending of [flow, flows.find((f) => f.name === 'regenerateAnswers')!]) {
    it(`${ending.name}: a run the AI backend fails ends as failed, once`, async () => {
      const live = recordingLive()
      withLive(live)
      const run = await startStream(ending)
      run.ai.send('TEXT_MESSAGE_CONTENT', delta('Partial'))
      run.ai.send('RUN_ERROR', { runId: RUN, message: 'upstream exploded' })
      run.ai.finish()
      await run.res.ended
      await settle()
      expect(live.ends).to.deep.equal(['failed'])
    })

    it(`${ending.name}: a broken stream ends as failed, once`, async () => {
      const live = recordingLive()
      withLive(live)
      const run = await startStream(ending)
      run.ai.send('TEXT_MESSAGE_CONTENT', delta('Partial'))
      await settle()
      run.ai.breakConnection(Object.assign(new Error('read ECONNRESET'), { code: 'ECONNRESET' }))
      await run.res.ended
      await settle()
      expect(live.ends).to.deep.equal(['failed'])
    })

    it(`${ending.name}: a run the server stops ends as stopped, once`, async () => {
      const live = recordingLive()
      withLive(live)
      const run = await startStream(ending)
      run.ai.send('TEXT_MESSAGE_CONTENT', delta('Half an answer'))
      await settle()
      expect(await stopAllRuns(5_000)).to.equal(1)
      await run.res.ended
      await settle()
      expect([...new Set(live.texts)]).to.deep.equal(['Half an answer'])
      expect(live.ends).to.deep.equal(['stopped'])
    })
  }

  for (const flow of flows) {
    // A hook overrules a reply that already ended (`ask_user_question.py`, `discard_text`), then the model answers again.
    it(`${flow.name}: a reply the AI backend discards leaves the others' answer, with its numbered copy and citation`, async () => {
      const live = recordingLive()
      withLive(live)
      const run = await startStream(flow)
      const atlas = { ...CITE, metadata: { recordId: 'r1', recordName: 'Atlas' } }
      const other = { ...CITE, content: 'Good.', metadata: { recordId: 'r2', recordName: 'Other' } }
      run.ai.send('TEXT_MESSAGE_START', { runId: RUN, messageId: 'm1' })
      run.ai.send('TEXT_MESSAGE_CONTENT', delta('Bad [a](ref1).'))
      run.ai.send('STATE_DELTA', numbered('Bad [1](u1).', 'Bad [a](ref1).', [atlas]))
      run.ai.send('TEXT_MESSAGE_END', { runId: RUN, messageId: 'm1' })
      await settle()
      expect(last(live.texts)).to.equal('Bad [1](u1).')
      run.ai.send('CUSTOM', { runId: RUN, name: 'message_discarded', value: { messageId: 'm1' } })
      run.ai.send('STATE_DELTA', discardedCopy('', '', []))
      await settle()
      expect(last(live.texts)).to.equal('')
      expect(last(live.cited)).to.deep.equal(NONE)
      const afterDiscard = live.texts.length
      run.ai.send('TEXT_MESSAGE_START', { runId: RUN, messageId: 'm2' })
      run.ai.send('TEXT_MESSAGE_CONTENT', delta('Good [b](ref2).', { messageId: 'm2' }))
      run.ai.send('STATE_DELTA', numbered('Good [1](u2).', 'Good [b](ref2).', [other]))
      run.ai.send('RUN_FINISHED', finalAnswer('Good [1](u2).'))
      run.ai.finish()
      await run.res.ended
      expect(live.texts.slice(afterDiscard).some((t) => t.includes('Bad'))).to.equal(false)
      expect(last(live.texts)).to.equal('Good [1](u2).')
      expect(last(live.cited)).to.deep.equal({ settledLength: 'Good [1](u2).'.length, items: [other] })
    })
  }

  it('a model call that fails mid-stream and is retried: stale copies of it after the discard are not taken', async () => {
    const live = recordingLive()
    withLive(live)
    const run = await startStream(flows[0]!)
    const bad = 'Paris is big [Atlas](ref1).'
    run.ai.send('TEXT_MESSAGE_START', { runId: RUN, messageId: 'm1' })
    run.ai.send('TEXT_MESSAGE_CONTENT', delta(bad))
    run.ai.send('STATE_DELTA', numbered('Paris is big [1](u1).', bad, [CITE]))
    run.ai.send('TEXT_MESSAGE_END', { runId: RUN, messageId: 'm1' })
    run.ai.send('CUSTOM', { runId: RUN, name: 'message_discarded', value: { messageId: 'm1' } })
    // Computed before the call was dropped: written after its discard, before the copy that ends them.
    run.ai.send('STATE_DELTA', numbered('Paris is big [1](u1).', bad, [CITE]))
    await settle()
    expect(last(live.texts)).to.equal('')
    expect(last(live.cited)).to.deep.equal(NONE)
    run.ai.send('STATE_DELTA', discardedCopy('', '', []))
    const good = 'Rome is old [Atlas](ref2).'
    const second = { ...CITE, chunkIndex: 2, content: 'Rome is old.' }
    run.ai.send('TEXT_MESSAGE_START', { runId: RUN, messageId: 'm2' })
    run.ai.send('TEXT_MESSAGE_CONTENT', delta(good, { messageId: 'm2' }))
    run.ai.send('STATE_DELTA', numbered('Rome is old [2](u2).', good, [second]))
    await settle()
    expect(last(live.texts)).to.equal('Rome is old [2](u2).')
    expect(last(live.cited)).to.deep.equal({ settledLength: 'Rome is old [2](u2).'.length, items: [second] })
    run.ai.send('RUN_FINISHED', finalAnswer('Rome is old [2](u2).'))
    run.ai.finish()
    await run.res.ended
  })

  it('a discarded continuation keeps the reply it continued, numbered as the copy after the discard says', async () => {
    const live = recordingLive()
    withLive(live)
    const run = await startStream(flows[0]!)
    const carried = 'Part one [a](ref1). '
    run.ai.send('TEXT_MESSAGE_START', { runId: RUN, messageId: 'm1' })
    run.ai.send('TEXT_MESSAGE_CONTENT', delta(carried))
    run.ai.send('TEXT_MESSAGE_END', { runId: RUN, messageId: 'm1' })
    run.ai.send('TEXT_MESSAGE_START', { runId: RUN, messageId: 'm2' })
    run.ai.send('TEXT_MESSAGE_CONTENT', delta('Half of a', { messageId: 'm2' }))
    run.ai.send('TEXT_MESSAGE_END', { runId: RUN, messageId: 'm2' })
    run.ai.send('CUSTOM', { runId: RUN, name: 'message_discarded', value: { messageId: 'm2' } })
    run.ai.send('STATE_DELTA', discardedCopy('Part one [1](u1). ', carried, [CITE]))
    await settle()
    expect(last(live.texts)).to.equal('Part one [1](u1). ')
    run.ai.send('TEXT_MESSAGE_START', { runId: RUN, messageId: 'm3' })
    run.ai.send('TEXT_MESSAGE_CONTENT', delta('Part two.', { messageId: 'm3' }))
    // The retry continues the reply too: its copy carries the text from before it, as the asker's browser shows it.
    run.ai.send('STATE_DELTA', numbered('Part one [1](u1). Part two.', `${carried}Part two.`, [CITE]))
    await settle()
    expect(last(live.texts)).to.equal('Part one [1](u1). Part two.')
    expect(last(live.cited)).to.deep.equal({ settledLength: 'Part one [1](u1). Part two.'.length, items: [CITE] })
    run.ai.send('RUN_FINISHED', finalAnswer('Part one [1](u1). Part two.'))
    run.ai.finish()
    await run.res.ended
  })

  // The AI backend asks the model again without discarding its reply (refusal recovery, the SQL answer gate): the asker
  // settles the first reply as a step and starts the answer again; so do the others.
  for (const flow of flows) {
    it(`${flow.name}: a reply asked again without a discard becomes a step; the new reply is the answer, with its citations`, async () => {
      const live = recordingLive()
      withLive(live)
      const run = await startStream(flow)
      const atlas = { ...CITE, metadata: { recordId: 'r1', recordName: 'Atlas' } }
      run.ai.send('TEXT_MESSAGE_START', { runId: RUN, messageId: 'm1' })
      run.ai.send('TEXT_MESSAGE_CONTENT', delta('I cannot find that.'))
      run.ai.send('STATE_DELTA', numbered('I cannot find that.', 'I cannot find that.', []))
      run.ai.send('TEXT_MESSAGE_END', { runId: RUN, messageId: 'm1' })
      run.ai.send('TEXT_MESSAGE_START', { runId: RUN, messageId: 'm2' })
      await settle()
      expect(last(live.texts)).to.equal('')
      run.ai.send('TEXT_MESSAGE_CONTENT', delta('It is [a](ref1).', { messageId: 'm2' }))
      run.ai.send('STATE_DELTA', numbered('It is [1](u1).', 'It is [a](ref1).', [atlas]))
      await settle()
      expect(last(live.texts)).to.equal('It is [1](u1).')
      expect(last(live.cited)).to.deep.equal({ settledLength: 'It is [1](u1).'.length, items: [atlas] })
      expect(last(live.steps)).to.deep.include({ type: 'text', content: 'I cannot find that.' })
      run.ai.send('RUN_FINISHED', finalAnswer('It is [1](u1).'))
      run.ai.finish()
      await run.res.ended
    })
  }

  it('the same message started again (a replay) changes nothing', async () => {
    const live = recordingLive()
    withLive(live)
    const run = await startStream(flows[0]!)
    run.ai.send('TEXT_MESSAGE_START', { runId: RUN, messageId: 'm1' })
    run.ai.send('TEXT_MESSAGE_CONTENT', delta('See [a](ref1).'))
    run.ai.send('STATE_DELTA', numbered('See [1](u1).', 'See [a](ref1).', [CITE]))
    run.ai.send('TEXT_MESSAGE_START', { runId: RUN, messageId: 'm1' })
    run.ai.send('TEXT_MESSAGE_CONTENT', delta(' More.'))
    await settle()
    expect(last(live.texts)).to.equal('See [1](u1). More.')
    expect(last(live.cited)).to.deep.equal({ settledLength: 'See [1](u1).'.length, items: [CITE] })
    run.ai.send('RUN_FINISHED', finalAnswer('See [1](u1). More.'))
    run.ai.finish()
    await run.res.ended
  })

  it('a new model call ends the wait for the copy written after a discard, as the asker does', async () => {
    const live = recordingLive()
    withLive(live)
    const run = await startStream(flows[0]!)
    run.ai.send('TEXT_MESSAGE_START', { runId: RUN, messageId: 'm1' })
    run.ai.send('TEXT_MESSAGE_CONTENT', delta('Bad.'))
    run.ai.send('CUSTOM', { runId: RUN, name: 'message_discarded', value: { messageId: 'm1' } })
    // No copy marked as written after the discard reaches here: the retry's own copies are taken.
    run.ai.send('TEXT_MESSAGE_START', { runId: RUN, messageId: 'm2' })
    run.ai.send('TEXT_MESSAGE_CONTENT', delta('Good [b](ref2).', { messageId: 'm2' }))
    run.ai.send('STATE_DELTA', numbered('Good [1](u2).', 'Good [b](ref2).', [CITE]))
    await settle()
    expect(last(live.texts)).to.equal('Good [1](u2).')
    expect(last(live.cited)).to.deep.equal({ settledLength: 'Good [1](u2).'.length, items: [CITE] })
    run.ai.send('RUN_FINISHED', finalAnswer('Good [1](u2).'))
    run.ai.finish()
    await run.res.ended
  })

  it('a live answer that throws never fails, nor holds up, the turn', async () => {
    withLive({
      text: () => {
        throw new Error('socket gone')
      },
      done: () => {
        throw new Error('socket gone')
      },
    })
    const run = await startStream(flow)
    run.ai.send('TEXT_MESSAGE_CONTENT', delta('Still '))
    run.ai.send('TEXT_MESSAGE_CONTENT', delta('answered.'))
    run.ai.send('RUN_FINISHED', finalAnswer('Still answered.'))
    run.ai.finish()
    await run.res.ended
    expect(run.answer()).to.include({
      messageType: 'bot_response',
      content: 'Still answered.',
    })
    expect(run.res.eventsOf('TEXT_MESSAGE_CONTENT')).to.have.length(2)
  })

  it('no live answer for the run (a solo chat): the turn runs as before', async () => {
    const liveAnswer = withLive(undefined)
    const run = await startStream(flow)
    run.ai.send('TEXT_MESSAGE_CONTENT', delta('Hello'))
    run.ai.send('RUN_FINISHED', finalAnswer('Hello'))
    run.ai.finish()
    await run.res.ended
    expect(liveAnswer.calledOnce).to.equal(true)
    expect(run.answer()?.content).to.equal('Hello')
  })
})
