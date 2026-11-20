import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { resetActiveRunsForTests, stopAllRuns } from '../../../../src/modules/enterprise_search/utils/active-runs'
import { StreamedTranscript, stoppedAnswer } from '../../../../src/modules/enterprise_search/utils/turn-stream'
import { settle } from '../controller/chat-test-harness'
import { delta, flows, RUN, startStream } from '../controller/streaming-flows'

const OPENING = "I'll look up the release guidance, then list the tips."
const SEARCH = 'knowledgegraph__search'

const toolStart = (toolCallId: string, extra: Record<string, unknown> = {}): Record<string, unknown> => ({
  runId: RUN,
  toolCallId,
  toolCallName: SEARCH,
  displayName: 'Searched the knowledge base',
  ...extra,
})

/** One frame as the stream carries it. */
const feed = (transcript: StreamedTranscript, type: string, frame: Record<string, unknown>): void =>
  transcript.feed(type, JSON.stringify({ type, ...frame }))

const searchStep = (status: string, extra: Record<string, unknown> = {}): Record<string, unknown> => ({
  type: 'tool_call',
  toolCallId: 'c1',
  toolName: SEARCH,
  displayName: 'Searched the knowledge base',
  status,
  ...extra,
})

describe('a stopped run keeps what it streamed', () => {
  describe('StreamedTranscript', () => {
    it('keeps the text before a tool call as a step, the call, and the text after it as the answer so far', () => {
      const transcript = new StreamedTranscript()
      feed(transcript, 'TEXT_MESSAGE_CONTENT', delta("I'll look up "))
      feed(transcript, 'TEXT_MESSAGE_CONTENT', delta('the guidance.'))
      feed(transcript, 'TOOL_CALL_START', toolStart('c1'))
      feed(transcript, 'TOOL_CALL_ARGS', { runId: RUN, toolCallId: 'c1', delta: '{}', argsSummary: 'release guidance' })
      feed(transcript, 'TOOL_CALL_RESULT', { runId: RUN, toolCallId: 'c1', status: 'completed', resultSummary: '3 documents' })
      feed(transcript, 'TEXT_MESSAGE_CONTENT', delta('So far, three', { messageId: 'm2' }))

      expect(transcript.getText()).to.equal('So far, three')
      expect(transcript.getParts()).to.deep.equal([
        { type: 'text', content: "I'll look up the guidance." },
        searchStep('completed', { argsSummary: 'release guidance', resultSummary: '3 documents' }),
        { type: 'text', content: 'So far, three', isFinal: true },
      ])
    })

    it('a stop during the opening line keeps it as the answer so far', () => {
      const transcript = new StreamedTranscript()
      feed(transcript, 'TEXT_MESSAGE_CONTENT', delta(OPENING))

      expect(stoppedAnswer(transcript)).to.deep.equal({
        answer: OPENING,
        citations: [],
        status: 'stopped',
        parts: [{ type: 'text', content: OPENING, isFinal: true }],
      })
    })

    it('a stop while a tool runs keeps the opening line and the call, marked as cut short, and has no answer yet', () => {
      const transcript = new StreamedTranscript()
      feed(transcript, 'TEXT_MESSAGE_CONTENT', delta(OPENING))
      feed(transcript, 'TOOL_CALL_START', toolStart('c1'))

      expect(transcript.hasContent()).to.equal(true)
      expect(stoppedAnswer(transcript)).to.deep.equal({
        answer: '',
        citations: [],
        status: 'stopped',
        parts: [{ type: 'text', content: OPENING }, searchStep('blocked'), { type: 'text', content: '', isFinal: true }],
      })
    })

    it("leaves out a sub-agent's text and calls, but keeps a call a person approved wherever it ran", () => {
      const transcript = new StreamedTranscript()
      feed(transcript, 'TEXT_MESSAGE_CONTENT', delta('Asking the coding agent.'))
      feed(transcript, 'TOOL_CALL_START', toolStart('d1', { toolCallName: 'delegate' }))
      feed(transcript, 'TEXT_MESSAGE_CONTENT', { runId: 'run-child', messageId: 'c', delta: 'child text' })
      feed(transcript, 'TOOL_CALL_START', { runId: 'run-child', toolCallId: 'x1', toolCallName: 'search' })
      feed(transcript, 'TOOL_CALL_START', { runId: 'run-child', toolCallId: 'a1', toolCallName: 'create_issue', approved: true })

      const parts = transcript.getParts()
      expect(parts.map((p) => p.toolCallId ?? p.content)).to.deep.equal(['Asking the coding agent.', 'd1', 'a1', ''])
      expect(parts[2]).to.include({ toolName: 'create_issue', approved: true })
    })

    it('ignores frames it does not keep and frames that are not JSON', () => {
      const transcript = new StreamedTranscript()
      transcript.feed('TEXT_MESSAGE_CONTENT', '{not json')
      feed(transcript, 'CUSTOM', { name: 'x', value: { delta: 'no' } })
      transcript.feed(undefined, '{}')
      expect(transcript.getParts()).to.deep.equal([])
    })

    it("the legacy protocol's running text replaces the answer so far", () => {
      const transcript = new StreamedTranscript()
      transcript.setAccumulatedText('So far')
      transcript.setAccumulatedText('So far, three')
      expect(transcript.getText()).to.equal('So far, three')
      expect(transcript.getParts()).to.deep.equal([{ type: 'text', content: 'So far, three', isFinal: true }])
    })

    it('drops a model call the AI backend discarded, from where its text began, keeping the text a continued reply carried', () => {
      const transcript = new StreamedTranscript()
      feed(transcript, 'TEXT_MESSAGE_START', { runId: RUN, messageId: 'm1' })
      feed(transcript, 'TEXT_MESSAGE_CONTENT', delta('Part one. '))
      feed(transcript, 'TEXT_MESSAGE_START', { runId: RUN, messageId: 'm2' })
      feed(transcript, 'TEXT_MESSAGE_CONTENT', delta('Half of a', { messageId: 'm2' }))
      feed(transcript, 'CUSTOM', { runId: RUN, name: 'message_discarded', value: { messageId: 'm2' } })
      expect(transcript.getText()).to.equal('Part one. ')
      feed(transcript, 'TEXT_MESSAGE_CONTENT', delta('Part two.', { messageId: 'm3' }))
      expect(stoppedAnswer(transcript)).to.deep.include({ answer: 'Part one. Part two.' })
    })

    it('a discarded call after a step leaves the step; one that ended a reply a hook overruled takes the reply and all after it', () => {
      const transcript = new StreamedTranscript()
      feed(transcript, 'TEXT_MESSAGE_CONTENT', delta("I'll look up the guidance."))
      feed(transcript, 'TOOL_CALL_START', toolStart('c1'))
      feed(transcript, 'TOOL_CALL_RESULT', { runId: RUN, toolCallId: 'c1', status: 'completed' })
      feed(transcript, 'TEXT_MESSAGE_CONTENT', delta('Hal', { messageId: 'm2' }))
      feed(transcript, 'CUSTOM', { runId: RUN, name: 'message_discarded', value: { messageId: 'm2' } })
      feed(transcript, 'TEXT_MESSAGE_CONTENT', delta('So far', { messageId: 'm3' }))
      expect(transcript.getParts()).to.deep.equal([
        { type: 'text', content: "I'll look up the guidance." },
        searchStep('completed'),
        { type: 'text', content: 'So far', isFinal: true },
      ])
      const overruled = new StreamedTranscript()
      feed(overruled, 'TEXT_MESSAGE_START', { runId: RUN, messageId: 'm1' })
      feed(overruled, 'TEXT_MESSAGE_CONTENT', delta('A reply the hook overrules.'))
      feed(overruled, 'TEXT_MESSAGE_END', { runId: RUN, messageId: 'm1' })
      feed(overruled, 'TOOL_CALL_START', toolStart('c9'))
      feed(overruled, 'CUSTOM', { runId: RUN, name: 'message_discarded', value: { messageId: 'm1' } })
      expect(overruled.getParts()).to.deep.equal([])
      expect(overruled.hasContent()).to.equal(false)
    })

    it('a copy marked as written after a discard that arrives after the retry began cuts nothing of the retry', () => {
      const transcript = new StreamedTranscript()
      transcript.followNumberedCopies()
      feed(transcript, 'TEXT_MESSAGE_START', { runId: RUN, messageId: 'm1' })
      feed(transcript, 'TEXT_MESSAGE_CONTENT', delta('Bad.'))
      feed(transcript, 'CUSTOM', { runId: RUN, name: 'message_discarded', value: { messageId: 'm1' } })
      feed(transcript, 'TEXT_MESSAGE_START', { runId: RUN, messageId: 'm2' })
      feed(transcript, 'TEXT_MESSAGE_CONTENT', delta('Good ', { messageId: 'm2' }))
      feed(transcript, 'STATE_DELTA', {
        runId: RUN,
        delta: [
          { op: 'replace', path: '/normalizedAnswer', value: '' },
          { op: 'replace', path: '/rawLength', value: 0 },
          { op: 'replace', path: '/citations', value: [] },
          { op: 'replace', path: '/discarded', value: true },
        ],
      })
      feed(transcript, 'TEXT_MESSAGE_CONTENT', delta('answer.', { messageId: 'm2' }))
      expect(transcript.getText()).to.equal('Good answer.')
      expect(stoppedAnswer(transcript)).to.deep.include({ answer: 'Good answer.' })
    })

    it('discarding a reply asked again keeps the reply before it', () => {
      const transcript = new StreamedTranscript()
      transcript.followNumberedCopies()
      feed(transcript, 'TEXT_MESSAGE_START', { runId: RUN, messageId: 'm1' })
      feed(transcript, 'TEXT_MESSAGE_CONTENT', delta('I cannot find that.'))
      feed(transcript, 'TEXT_MESSAGE_END', { runId: RUN, messageId: 'm1' })
      feed(transcript, 'TEXT_MESSAGE_START', { runId: RUN, messageId: 'm2' })
      feed(transcript, 'TEXT_MESSAGE_CONTENT', delta('Which one do you mean?', { messageId: 'm2' }))
      feed(transcript, 'TEXT_MESSAGE_END', { runId: RUN, messageId: 'm2' })
      feed(transcript, 'CUSTOM', { runId: RUN, name: 'message_discarded', value: { messageId: 'm2' } })
      feed(transcript, 'STATE_DELTA', {
        runId: RUN,
        delta: [
          { op: 'replace', path: '/normalizedAnswer', value: '' },
          { op: 'replace', path: '/rawLength', value: 0 },
          { op: 'replace', path: '/discarded', value: true },
        ],
      })
      expect(transcript.getParts()).to.deep.equal([{ type: 'text', content: 'I cannot find that.', isFinal: true }])
      // The others see it as the asker does: the earlier reply a step, nothing written yet.
      expect(transcript.getLiveAnswer()).to.deep.include({ answer: '', steps: [{ type: 'text', content: 'I cannot find that.' }] })
    })

    it("a sub-agent's discard, an unknown message and a discard with no id change nothing", () => {
      const transcript = new StreamedTranscript()
      feed(transcript, 'TEXT_MESSAGE_CONTENT', delta('Kept.'))
      feed(transcript, 'CUSTOM', { runId: 'child', parentRunId: RUN, name: 'message_discarded', value: { messageId: 'm1' } })
      feed(transcript, 'CUSTOM', { runId: RUN, name: 'message_discarded', value: { messageId: 'nope' } })
      feed(transcript, 'CUSTOM', { runId: RUN, name: 'message_discarded', value: {} })
      expect(transcript.getText()).to.equal('Kept.')
    })

    it('saves no parts when nothing streamed', () => {
      const transcript = new StreamedTranscript()
      expect(transcript.getParts()).to.deep.equal([])
      expect(transcript.hasContent()).to.equal(false)
    })
  })

  describe('a run the server stops saves its steps', () => {
    beforeEach(() => resetActiveRunsForTests())
    afterEach(() => {
      resetActiveRunsForTests()
      sinon.restore()
    })

    for (const flow of flows) {
      it(`${flow.name}: stopped while a tool runs, the opening line and the step are saved, with no answer yet`, async () => {
        const run = await startStream(flow)
        run.ai.send('TEXT_MESSAGE_CONTENT', delta(OPENING))
        run.ai.send('TOOL_CALL_START', toolStart('c1'))
        await settle()

        expect(await stopAllRuns(5_000)).to.equal(1)
        await run.res.ended

        const answer = run.answer()
        expect(answer).to.include({ messageType: 'bot_response', status: 'stopped', content: '' })
        expect(answer?.parts).to.deep.equal([
          { type: 'text', content: OPENING },
          searchStep('blocked'),
          { type: 'text', content: '', isFinal: true },
        ])
      })

      it(`${flow.name}: stopped after a model call the AI backend discarded, saves only what replaced it`, async () => {
        const run = await startStream(flow)
        run.ai.send('TEXT_MESSAGE_CONTENT', delta('An abandoned attempt'))
        run.ai.send('CUSTOM', { runId: RUN, name: 'message_discarded', value: { messageId: 'm1' } })
        run.ai.send('TEXT_MESSAGE_CONTENT', delta('Kept', { messageId: 'm2' }))
        await settle()

        expect(await stopAllRuns(5_000)).to.equal(1)
        await run.res.ended

        expect(run.answer()).to.include({ status: 'stopped', content: 'Kept' })
      })
    }
  })
})
