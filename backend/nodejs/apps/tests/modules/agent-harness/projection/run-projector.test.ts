import { expect } from 'chai';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { LiveDelta, ProjectedFrame } from '../../../../src/modules/agent-harness/projection/frames';
import { planReplay, ReplayOptions, ReplayStep } from '../../../../src/modules/agent-harness/projection/replay';
import { ProjectorContext, RunProjector } from '../../../../src/modules/agent-harness/projection/run-projector';
import { CitationAccess } from '../../../../src/modules/agent-harness/projection/citation-access';
import { sourceKey } from '../../../../src/modules/agent-harness/projection/citation-source';
import { parseStreamId } from '../../../../src/modules/agent-harness/projection/stream-id';
import { buildCompletion, failedModelCall, runTranscript } from '../../../../src/modules/agent-harness/projection/transcript';
import { foldTimeline } from '../../../../src/modules/agent-harness/projection/timeline';
import {
  DURABLE_SCENARIOS,
  STEERED_SCENARIOS,
  GoldenJournal,
  journalOnlySteps,
  liveSteps,
  projectorContext,
  readJournal,
  rechunked,
  reclaimedJournal,
  runSteps,
  sseBody,
} from '../../../contracts/support/durable';
import { slackRendering } from '../../../contracts/support/surfaces';
import {
  finishedConversation,
  indexed,
  leaksOf,
  live,
  liveCitation,
  readableRecords,
  recordCitation,
  webCitation,
  withCitations,
} from '../support/citations';

const retained = (journal: GoldenJournal): LiveDelta[] => journal.live.map((d) => d.frame);

function resume(
  journal: GoldenJournal,
  id: string,
  deltas: LiveDelta[] = retained(journal),
  options: ReplayOptions = {},
  context: ProjectorContext = projectorContext(journal),
): ProjectedFrame[] {
  const cursor = parseStreamId(id);
  const plan = planReplay(journal.runId, journal.events, deltas, cursor, options);
  const projector = new RunProjector(context, plan.resumeAfter);
  projector.restoreLost(plan.lostMessages);
  const frames = projector.begin();
  if (plan.lost !== null) {
    frames.push(...projector.abandon(plan.lost, plan.lost.endSent ? 'already_sent' : { sentAfter: id }));
  }
  return [...frames, ...runSteps(projector, plan.steps as ReplayStep[] as never, { begin: false })];
}

const texts = (frames: ProjectedFrame[]): string => JSON.stringify(frames.map((f) => f.frame));

/** Every recorded durable run, steered runs (T2) included. */
const EVERY_SCENARIO = [...DURABLE_SCENARIOS, ...Object.keys(STEERED_SCENARIOS)];

describe('agent-harness projection: RunProjector', () => {
  describe('L-1 / L-13: resuming from any frame id yields exactly the frames after it', () => {
    for (const scenario of EVERY_SCENARIO) {
      it(scenario, () => {
        const journal = rechunked(readJournal(scenario), 4);
        const original = runSteps(new RunProjector(projectorContext(journal)), liveSteps(journal));
        const ids = original.map((f) => f.id);
        expect(new Set(ids).size, 'ids are unique').to.equal(ids.length);
        for (let cut = 0; cut < original.length; cut += 1) {
          const rest = resume(journal, original[cut]!.id);
          expect(rest.map((f) => f.frame), `resume after ${original[cut]!.id}`).to.deep.equal(
            original.slice(cut + 1).map((f) => f.frame),
          );
        }
      });
    }
  });

  it('PR-5: a question the pre-run asked (no tool call) reaches the stream and the transcript', () => {
    const base = readJournal('agent_quick_tool_call')
    const head = base.events.filter((e) => e.type === 'run.admitted' || e.type === 'run.claimed' || e.type === 'run.spec_pinned')
    const at = head[head.length - 1]!
    const worker = (seq: number, type: string, data: Record<string, unknown>): JournalEvent => ({
      ...at, seq, type, writer: 'worker', critical: false, attemptId: 'run-golden.a1', fence: 1, data,
    })
    const toolData = { questions: [{ question: 'Which report do you need?', options: [{ label: 'Sales' }] }] }
    const events: JournalEvent[] = [
      ...head,
      worker(at.seq + 1, 'x.pipeshub.goal.presented', { description: 'report' }),
      worker(at.seq + 2, 'x.pipeshub.question.asked', { callId: 'pre-run', status: 'success', toolData }),
      worker(at.seq + 3, 'x.pipeshub.answer.finalized', {
        outcome: 'needs_input', answer: 'Which report do you need?', partTexts: [], citations: [],
        confidence: 'Medium', answerMatchType: 'Clarification Needed',
      }),
      { ...at, seq: at.seq + 4, type: 'run.completed', writer: 'node', data: { outcome: 'needs_input' } },
    ]
    const projector = new RunProjector(projectorContext(base))
    projector.begin()
    const frames = projector.project(events).map((f) => f.frame)
    const card = frames.findIndex((f) => f.type === 'CUSTOM' && f['name'] === 'ask_user_question')
    const final = frames.findIndex((f) => f.type === 'STATE_SNAPSHOT' && (f['snapshot'] as Record<string, unknown>)['final'] === true)
    expect(card).to.be.greaterThan(-1)
    expect(final).to.be.greaterThan(card)
    expect((frames[card]!['value'] as Record<string, unknown>)['toolData']).to.deep.equal(toolData)
    expect(frames[final]!['snapshot']).to.deep.include({ answer: 'Which report do you need?', answerMatchType: 'Clarification Needed' })
    expect(runTranscript(events, base.runId)).to.deep.include({ kind: 'answer', questions: [{ toolData }] })
  })

  describe('PA-9: reasoning in the legacy frame shape', () => {
    const types = (frames: ProjectedFrame[]): string[] => frames.map((f) => f.frame.type as string)
    const reasoningOf = (frames: ProjectedFrame[]): string =>
      frames.filter((f) => f.frame.type === 'REASONING_MESSAGE_CONTENT').map((f) => f.frame['delta']).join('')

    it('RS-1: live reasoning deltas stream inside the message and close when the text starts', () => {
      const journal = rechunked(readJournal('agent_quick_reasoning'), 3)
      const frames = runSteps(new RunProjector(projectorContext(journal)), liveSteps(journal))
      const order = types(frames)
      const start = order.indexOf('TEXT_MESSAGE_START')
      expect(order.slice(start, start + 3)).to.deep.equal(['TEXT_MESSAGE_START', 'REASONING_START', 'REASONING_MESSAGE_START'])
      const endOfReasoning = order.indexOf('REASONING_END')
      expect(order.indexOf('TEXT_MESSAGE_CONTENT')).to.be.greaterThan(endOfReasoning)
      const contents = frames.filter((f) => f.frame.type === 'REASONING_MESSAGE_CONTENT')
      expect(contents.length, 'one frame per delta').to.equal(3)
      expect(contents.every((f) => f.id.startsWith('d:'))).to.equal(true)
      expect(reasoningOf(frames)).to.equal('The user asks about leave. The handbook says 25 days.')
      const reasoningId = frames.find((f) => f.frame.type === 'REASONING_MESSAGE_START')!.frame['messageId']
      const textId = frames.find((f) => f.frame.type === 'TEXT_MESSAGE_START')!.frame['messageId']
      expect(reasoningId).to.not.equal(textId)
    })

    it('RS-2: journal only, the journaled reasoning is one whole reasoning message; parts and completion as legacy', () => {
      const journal = readJournal('agent_quick_reasoning')
      const frames = runSteps(new RunProjector(projectorContext(journal)), journalOnlySteps(journal))
      expect(reasoningOf(frames)).to.equal('The user asks about leave. The handbook says 25 days.')
      expect(frames.filter((f) => f.frame.type === 'REASONING_MESSAGE_CONTENT').every((f) => f.id.startsWith('j:'))).to.equal(true)
      const final = frames.find((f) => f.frame.type === 'STATE_SNAPSHOT' && (f.frame['snapshot'] as Record<string, unknown>)['final'] === true)!
      const snapshot = final.frame['snapshot'] as { parts: Array<Record<string, unknown>>; reasoning: unknown }
      expect(snapshot.parts.map((p) => p['type'])).to.deep.equal(['text', 'reasoning'])
      expect(snapshot.reasoning).to.deep.equal([{ turnIndex: 0, content: 'The user asks about leave. The handbook says 25 days.' }])
    })
  })

  it('L-16: a live run streams the message from its deltas before the response closes it', () => {
    const journal = rechunked(readJournal('agent_quick_tool_call'), 3);
    const frames = runSteps(new RunProjector(projectorContext(journal)), liveSteps(journal));
    const contents = frames.filter((f) => f.frame.type === 'TEXT_MESSAGE_CONTENT');
    expect(contents.length).to.equal(3);
    expect(contents.every((f) => f.id.startsWith('d:'))).to.equal(true);
    expect(contents.map((f) => f.frame['delta']).join('')).to.equal('You get 25 days of paid leave a year.');
  });

  it('L-2: an expired buffer ends the message the client had and restarts it under a new id', () => {
    const journal = rechunked(readJournal('agent_quick_tool_call'), 3);
    const original = runSteps(new RunProjector(projectorContext(journal)), liveSteps(journal));
    const midMessage = original.find((f) => f.frame.type === 'TEXT_MESSAGE_CONTENT')!;
    const oldId = midMessage.frame['messageId'];
    const rest = resume(journal, midMessage.id, []);
    expect(rest[0]!.frame).to.deep.include({ type: 'TEXT_MESSAGE_END', messageId: oldId });
    const restarted = rest.find((f) => f.frame.type === 'TEXT_MESSAGE_START')!;
    expect(restarted.frame['messageId']).to.not.equal(oldId);
    const shown = rest
      .filter((f) => f.frame.type === 'TEXT_MESSAGE_CONTENT' && f.frame['messageId'] === restarted.frame['messageId'])
      .map((f) => f.frame['delta'])
      .join('');
    expect(shown).to.equal('You get 25 days of paid leave a year.');
    expect(rest.some((f) => f.frame.type === 'TEXT_MESSAGE_CONTENT' && f.frame['messageId'] === oldId)).to.equal(false);
    expect(rest[rest.length - 1]!.frame.type).to.equal('RUN_FINISHED');
  });

  it('L-2: after an expired-buffer restart, resuming from any id already sent yields exactly the rest', () => {
    const journal = rechunked(readJournal('agent_quick_tool_call'), 3);
    const original = runSteps(new RunProjector(projectorContext(journal)), liveSteps(journal));
    const midMessage = original.find((f) => f.frame.type === 'TEXT_MESSAGE_CONTENT')!;
    const restarted = resume(journal, midMessage.id, []);
    expect(restarted[0]!.frame.type).to.equal('TEXT_MESSAGE_END');
    restarted.forEach((sent, i) => {
      const again = resume(journal, sent.id, []);
      expect(again.map((f) => [f.id, f.frame]), `resume after ${sent.id}`).to.deep.equal(
        restarted.slice(i + 1).map((f) => [f.id, f.frame]),
      );
    });
  });

  describe('resume is a function of the id alone (L-1, L-2, L-4, L-10)', () => {
    const journals = (): Array<[string, GoldenJournal]> => [
      ...EVERY_SCENARIO.map((name): [string, GoldenJournal] => [name, rechunked(readJournal(name), 3)]),
      ['re-claimed', reclaimedJournal()],
    ];
    /**
     * Every stream a reconnect can serve: from each cut of the live stream, with
     * deltas kept, expired, or forced lost (L-4). Live ids record when deltas
     * arrived, which a replay cannot know (L-1 checks those for gaps and
     * duplicates); a replayed stream is exact, so resuming it must be too.
     */
    type Served = [string, LiveDelta[], ProjectedFrame[], ProjectedFrame[]];
    /** [how, deltas it was served with, the stream, what the client had before it]. */
    function served(journal: GoldenJournal): Served[] {
      const original = runSteps(new RunProjector(projectorContext(journal)), liveSteps(journal));
      const kept = retained(journal);
      return original.flatMap((cut, at): Served[] => {
        const before = original.slice(0, at + 1);
        return [
          [`kept@${cut.id}`, kept, resume(journal, cut.id, kept), before],
          [`expired@${cut.id}`, [], resume(journal, cut.id, []), before],
          [`overflow@${cut.id}`, kept, resume(journal, cut.id, kept, { forceLost: true }), before],
        ];
      });
    }
    /**
     * What a client renders: every frame but state snapshots, each with the
     * state in effect when it arrives.
     */
    function observed(prefix: ProjectedFrame[], rest: ProjectedFrame[]): unknown[] {
      let state: unknown = null;
      for (const f of prefix) if (f.frame.type === 'STATE_SNAPSHOT') state = f.frame['snapshot'];
      const out: unknown[] = [];
      for (const f of rest) {
        if (f.frame.type === 'STATE_SNAPSHOT') state = f.frame['snapshot'];
        else out.push([f.frame, state]);
      }
      return out;
    }
    for (const [name, journal] of journals()) {
      it(`${name}: resuming after any frame of a replayed stream yields exactly the rest of it`, () => {
        for (const [how, deltas, stream, before] of served(journal)) {
          stream.forEach((sent, i) => {
            const again = resume(journal, sent.id, deltas);
            const rest = stream.slice(i + 1);
            if (name === 're-claimed') {
              // Whether the new fence's first request repeats the "calling the model"
              // snapshot depends on the old fence's deltas consumed before the
              // re-claim, which an id cannot name; what the client renders is the same.
              const prefix = [...before, ...stream.slice(0, i + 1)];
              expect(observed(prefix, again), `${how}, then resume after ${sent.id}`).to.deep.equal(
                observed(prefix, rest),
              );
            } else {
              expect(again.map((f) => [f.id, f.frame]), `${how}, then resume after ${sent.id}`).to.deep.equal(
                rest.map((f) => [f.id, f.frame]),
              );
            }
          });
        }
      });
      it(`${name}: no replayed stream marks the same replacement twice`, () => {
        for (const [how, , stream] of served(journal)) {
          const replaced = stream
            .filter((f) => f.frame.type === 'TEXT_MESSAGE_START' && 'replacesMessageId' in f.frame)
            .map((f) => f.frame['replacesMessageId']);
          expect(new Set(replaced).size, how).to.equal(replaced.length);
        }
      });
    }
  });

  it('L-3: deltas from an older fence are dropped and reported', () => {
    const journal = readJournal('agent_quick_tool_call');
    const drops: Array<[number, number]> = [];
    const projector = new RunProjector(projectorContext(journal), null, {
      fenceDropped: (fence, current) => drops.push([fence, current]),
      deltaGap: () => undefined,
    });
    projector.begin();
    projector.project(journal.events.filter((e) => e.seq <= 12));
    const zombie: LiveDelta = { ...journal.live[0]!.frame, fence: 0, data: { text: 'from a dead worker' } };
    expect(projector.delta(zombie)).to.deep.equal([]);
    expect(drops).to.deep.equal([[0, 1]]);
  });

  it('a live attempt with a missing delta is reported and streams no more; the journal tells the rest', () => {
    const journal = readJournal('agent_quick_tool_call');
    const first = journal.live[0]!.frame;
    const gaps: Array<[number, number, number, number]> = [];
    const projector = new RunProjector(projectorContext(journal), null, {
      fenceDropped: () => undefined,
      deltaGap: (fence, attempt, expected, got) => gaps.push([fence, attempt, expected, got]),
    });
    projector.begin();
    projector.project(journal.events.filter((e) => e.seq <= journal.live[0]!.afterSeq));
    expect(projector.delta({ ...first, n: first.n + 2 })).to.deep.equal([]);
    expect(gaps).to.deep.equal([[first.fence, first.modelAttempt, first.n, first.n + 2]]);
    expect(projector.delta(first), 'the lost attempt stays lost').to.deep.equal([]);

    const silent = new RunProjector(projectorContext(journal));
    silent.begin();
    silent.project(journal.events.filter((e) => e.seq <= journal.live[0]!.afterSeq));
    expect(silent.delta({ ...first, n: first.n + 2 })).to.deep.equal([]);

    const intact = new RunProjector(projectorContext(journal));
    intact.begin();
    intact.project(journal.events.filter((e) => e.seq <= journal.live[0]!.afterSeq));
    expect(intact.delta(first), 'without the gap the delta streams').to.not.deep.equal([]);
  });

  it('L-10: a re-claim ends the old fence\'s message; its deltas stop, the new attempt streams under a new id', () => {
    const journal = reclaimedJournal();
    const original = runSteps(new RunProjector(projectorContext(journal)), liveSteps(journal));
    const starts = original.filter((f) => f.frame.type === 'TEXT_MESSAGE_START').map((f) => f.frame['messageId']);
    const oldMessage = starts[1];
    const newMessage = starts[2];
    expect(newMessage).to.not.equal(oldMessage);
    const endOfOld = original.findIndex((f) => f.frame.type === 'TEXT_MESSAGE_END' && f.frame['messageId'] === oldMessage);
    const oldContents = original.filter((f) => f.frame.type === 'TEXT_MESSAGE_CONTENT' && f.frame['messageId'] === oldMessage);
    expect(oldContents.length, 'only fence-1 delta 1 streamed before the re-claim').to.equal(1);
    expect(original.findIndex((f) => f.frame['messageId'] === newMessage)).to.be.greaterThan(endOfOld);
    const newText = original
      .filter((f) => f.frame.type === 'TEXT_MESSAGE_CONTENT' && f.frame['messageId'] === newMessage)
      .map((f) => f.frame['delta'])
      .join('');
    expect(newText).to.equal('You get 25 days of paid leave a year.');
    // Reconnecting from the fence-1 delta resumes with the same remaining frames.
    const cutAt = original.indexOf(oldContents[0]!);
    expect(resume(journal, original[cutAt]!.id).map((f) => f.frame)).to.deep.equal(original.slice(cutAt + 1).map((f) => f.frame));
  });

  describe('PE-8 (SS1): session events on the stream (no run) add no frame for any audience', () => {
    for (const audience of ['owner', 'shared'] as const) {
      it(audience, () => {
        const journal = readJournal(DURABLE_SCENARIOS[0]!);
        const last = journal.events.at(-1)!;
        const sessionEvent = (seq: number, type: string, data: Record<string, unknown>): JournalEvent => ({
          orgId: last.orgId,
          sessionId: last.sessionId,
          streamId: last.streamId,
          seq,
          type,
          schemaVersion: 1,
          critical: false,
          ts: last.ts,
          writer: 'node',
          visibility: 'shared',
          data,
        });
        const withSessionEvents: GoldenJournal = {
          ...journal,
          events: [
            ...journal.events,
            sessionEvent(last.seq + 1, 'session.participant_changed', { by: 'p_owner', subject: 'p_ann', role: 'viewer', prevRole: 'none' }),
            sessionEvent(last.seq + 2, 'session.forked', { by: 'p_ann', fromSessionId: 'ses-0', atSeq: 3 }),
          ],
        };
        const frames = (j: GoldenJournal) =>
          runSteps(new RunProjector(projectorContext(j, audience)), journalOnlySteps(j)).map((f) => f.frame);
        expect(frames(withSessionEvents)).to.deep.equal(frames(journal));
      });
    }
  });

  describe('L-7: a shared-conversation viewer sees the answer, artifacts and citations only', () => {
    for (const scenario of EVERY_SCENARIO) {
      it(scenario, () => {
        const journal = readJournal(scenario);
        const projector = new RunProjector(projectorContext(journal, 'shared'));
        const frames = runSteps(projector, liveSteps(journal));
        const types = new Set(frames.map((f) => f.frame.type));
        for (const hidden of ['TEXT_MESSAGE_START', 'TEXT_MESSAGE_CONTENT', 'TEXT_MESSAGE_END', 'TOOL_CALL_START', 'TOOL_CALL_ARGS', 'TOOL_CALL_END', 'TOOL_CALL_RESULT', 'REASONING_START', 'REASONING_MESSAGE_CONTENT', 'REASONING_END']) {
          expect(types.has(hidden), hidden).to.equal(false);
        }
        const body = texts(frames);
        expect(body).to.not.include('goal_description');
        expect(body).to.not.include('argsSummary');
        expect(body).to.not.include('succeeded_tool_names');
        expect(body).to.not.include('ask_user_question');
        const final = frames.find((f) => f.frame.type === 'STATE_SNAPSHOT');
        // A run that failed before it answered shows the shared viewer its RUN_ERROR only.
        const parts = (final?.frame['snapshot'] as { parts: Array<Record<string, unknown>> } | undefined)?.parts ?? [];
        expect(final !== undefined || types.has('RUN_ERROR'), 'an answer or the error').to.equal(true);
        expect(parts.every((p) => p['type'] === 'text' && p['isFinal'] === true)).to.equal(true);
        expect(frames[0]!.frame).to.deep.include({ type: 'CUSTOM', name: 'conversation_created' });
      });
    }

    it('keeps the artifact card and strips tool parts from the finished conversation', () => {
      const journal = readJournal('agent_artifact_marker_answer');
      const projector = new RunProjector(projectorContext(journal, 'shared'));
      projector.begin();
      const frames = projector.project(journal.events);
      expect(frames.some((f) => f.frame.type === 'STATE_DELTA' && JSON.stringify(f.frame).includes('/artifacts/-'))).to.equal(true);
      const [finished] = projector.finish(
        { _id: 'c', messages: [{ messageType: 'bot_response', parts: [{ type: 'tool_call' }, { type: 'text', isFinal: true }], reasoning: [{}] }] },
        {},
      );
      const message = (finished!.frame['result'] as { conversation: { messages: Array<Record<string, unknown>> } }).conversation.messages[0]!;
      expect(message['parts']).to.deep.equal([{ type: 'text', isFinal: true }]);
      expect(message).to.not.have.property('reasoning');
    });
  });

  describe('C-5, C-20: external surfaces see what their legacy route rendered, with the owner\'s citations', () => {
    const answerOnly = (frames: ProjectedFrame[]): string[] =>
      frames
        .map((f) => f.frame)
        .filter(
          (f) =>
            !['CUSTOM', 'RUN_STARTED', 'RUN_ERROR', 'RUN_FINISHED', 'STATE_DELTA', 'STATE_SNAPSHOT'].includes(f.type) ||
            (f.type === 'CUSTOM' && f['name'] !== 'conversation_created') ||
            (f.type === 'STATE_SNAPSHOT' && (f['snapshot'] as Record<string, unknown>)['final'] !== true),
        )
        .map((f) => f.type);

    for (const audience of ['external', 'slack'] as const) {
      for (const scenario of EVERY_SCENARIO) {
        it(`SF-7 ${audience} ${scenario}: resumes from any id with exactly the rest, deterministically`, () => {
          const journal = rechunked(readJournal(scenario), 4);
          const ctx = projectorContext(journal, audience);
          const original = runSteps(new RunProjector(ctx), liveSteps(journal));
          if (audience === 'external') expect(answerOnly(original)).to.deep.equal([]);
          expect(runSteps(new RunProjector(ctx), liveSteps(journal))).to.deep.equal(original);
          // An answer-only stream has no delta frame, so its ids are the journal's too.
          const shown = (frames: ProjectedFrame[]) => frames.map((f) => (audience === 'external' ? [f.id, f.frame] : f.frame));
          for (let cut = 0; cut < original.length; cut += 1) {
            const rest = resume(journal, original[cut]!.id, retained(journal), {}, ctx);
            expect(shown(rest), `resume after ${original[cut]!.id}`).to.deep.equal(shown(original.slice(cut + 1)));
          }
        });
      }
    }

    it('SL-4: a Slack stream resumed between a question call\'s start and its arguments still gets the question', () => {
      const journal = readJournal('agent_ask_user_question');
      const ctx = projectorContext(journal, 'slack');
      const original = runSteps(new RunProjector(ctx), liveSteps(journal));
      const start = original.findIndex((f) => f.frame.type === 'TOOL_CALL_START');
      expect(original[start + 1]!.frame).to.deep.include({ type: 'TOOL_CALL_ARGS', toolCallId: 'call-ask' });
      const rest = resume(journal, original[start]!.id, retained(journal), {}, ctx);
      expect(rest[0]!.frame).to.deep.equal(original[start + 1]!.frame);
    });

    it('SF-4, C-20: the question card reaches Slack, whose legacy stream carried it; not MCP, nor a shared viewer', () => {
      const journal = readJournal('agent_ask_user_question');
      const card = (audience: 'external' | 'shared' | 'slack') =>
        runSteps(new RunProjector(projectorContext(journal, audience)), liveSteps(journal)).filter(
          (f) => f.frame.type === 'CUSTOM' && f.frame['name'] === 'ask_user_question',
        );
      expect(card('slack')).to.have.length(1);
      expect(card('external')).to.have.length(0);
      expect(card('shared')).to.have.length(0);
    });

    it('SL-2: Slack gets the question tool\'s arguments (the question legacy showed), no other tool\'s', () => {
      const args = (scenario: string) =>
        runSteps(new RunProjector(projectorContext(readJournal(scenario), 'slack')), liveSteps(readJournal(scenario)))
          .filter((f) => f.frame.type === 'TOOL_CALL_ARGS')
          .map((f) => f.frame);
      expect(args('agent_ask_user_question')).to.have.length(1);
      expect(args('agent_ask_user_question')[0]).to.have.keys(['type', 'toolCallId', 'delta']);
      expect(args('agent_quick_tool_call')).to.deep.equal([]);
    });

    for (const audience of ['external', 'slack'] as const) {
      it(`SF-2 ${audience}: citations are the owner's, whatever access a shared viewer would have`, () => {
        const cited = [recordCitation('rec-open', 1), recordCitation('rec-closed', 2)];
        const journal = withCitations(readJournal('agent_quick_final_answer'), cited);
        const ctx: ProjectorContext = { ...projectorContext(journal, audience), citations: () => CitationAccess.denyRecords() };
        const frames = runSteps(new RunProjector(ctx), liveSteps(journal));
        const lists = frames.flatMap((f) =>
          f.frame.type === 'STATE_DELTA'
            ? (f.frame['delta'] as Array<{ path: string; value: unknown }>).filter((op) => op.path === '/citations').map((op) => op.value)
            : f.frame.type === 'STATE_SNAPSHOT' && (f.frame['snapshot'] as Record<string, unknown>)['final'] === true
              ? [(f.frame['snapshot'] as Record<string, unknown>)['citations']]
              : [],
        );
        expect(lists.length).to.be.greaterThan(0);
        for (const list of lists) expect(list).to.deep.equal(cited);
        const projector = new RunProjector(ctx);
        projector.begin();
        projector.project(journal.events);
        const [finished] = projector.finish(finishedConversation(journal.sessionId, cited), {});
        expect(finished!.frame.type).to.equal('RUN_FINISHED');
        expect(JSON.stringify(finished!.frame)).to.not.include('unavailable');
      });

      it(`SF-7 ${audience}: across a worker crash and re-claim the surface ends as the run that did not crash`, () => {
        const crashed = reclaimedJournal();
        const clean = rechunked(readJournal('agent_quick_tool_call'), 2);
        const frames = (journal: GoldenJournal) =>
          runSteps(new RunProjector(projectorContext(journal, audience)), liveSteps(journal)).map((f) => f.frame);
        expect(frames(crashed).filter((f) => f.type === 'RUN_FINISHED')).to.have.length(1);
        if (audience === 'external') {
          expect(frames(crashed)).to.deep.equal(frames(clean));
          return;
        }
        // Slack saw the lost message start: its text is replaced by the re-sampled one, as the bot resets a preamble.
        const rendered = (journal: GoldenJournal) =>
          slackRendering(sseBody(runSteps(new RunProjector(projectorContext(journal, audience)), liveSteps(journal))));
        expect(rendered(crashed).slice(-3)).to.deep.equal(rendered(clean).slice(-3));
      });
    }
  });

  it('L-12: no frame, live or final, carries the model-authored marker', () => {
    const journal = rechunked(readJournal('agent_artifact_marker_answer'), 7);
    for (const audience of ['owner', 'shared'] as const) {
      const frames = runSteps(new RunProjector(projectorContext(journal, audience)), liveSteps(journal));
      expect(texts(frames)).to.not.include('attacker.test');
      expect(texts(frames)).to.not.include('evil.csv');
    }
    const owner = runSteps(new RunProjector(projectorContext(journal)), liveSteps(journal));
    expect(texts(owner)).to.include('::artifact[q3-revenue.csv](record:art-q3)');
  });

  it('MR-18: no frame, live or replayed from the journal, carries a sandbox link; its words do', () => {
    const marker = '::artifact[evil.csv](https://attacker.test/x){text/csv|x|x|document|1}';
    const link = '[Download fib.py](sandbox:/mnt/data/out/fib.py)';
    const swapped = JSON.parse(JSON.stringify(readJournal('agent_artifact_marker_answer')).split(marker).join(link)) as GoldenJournal;
    for (const pieces of [1, 3, 7, 40]) {
      const journal = rechunked(swapped, pieces);
      for (const steps of [liveSteps(journal), journalOnlySteps(journal)]) {
        const frames = runSteps(new RunProjector(projectorContext(journal)), steps);
        const shown = frames
          .filter((f) => f.frame.type === 'TEXT_MESSAGE_CONTENT')
          .map((f) => String(f.frame['delta']))
          .join('');
        expect(shown, `in ${pieces} pieces`).to.include('Here is the export you asked for.\n\nDownload fib.py');
        expect(texts(frames), `in ${pieces} pieces`).to.not.include('sandbox:');
      }
    }
  });

  describe('CITESTREAM: a citation is a link while the answer streams, as on legacy', () => {
    const deltaValue = (frame: ProjectedFrame, path: string): unknown =>
      (frame.frame['delta'] as Array<{ path: string; value: unknown }>).find((op) => op.path === path)?.value;
    const chunkIndexes = (frame: ProjectedFrame): unknown[] =>
      (deltaValue(frame, '/citations') as Array<{ chunkIndex: unknown }>).map((c) => c.chunkIndex);
    const answerDeltas = (frames: ProjectedFrame[]): ProjectedFrame[] =>
      frames.filter((f) => f.frame.type === 'STATE_DELTA' && deltaValue(f, '/normalizedAnswer') !== undefined);

    it('the numbered answer and its sources follow the live text that cites them, before the final answer', () => {
      const journal = readJournal('agent_live_file_citation');
      const frames = runSteps(new RunProjector(projectorContext(journal)), liveSteps(journal));
      const final = frames.findIndex((f) => f.frame.type === 'STATE_SNAPSHOT' && (f.frame['snapshot'] as Record<string, unknown>)['final'] === true);
      const live = answerDeltas(frames).filter((f) => f.id.startsWith('d:'));
      expect(live.map(chunkIndexes)).to.deep.equal([[1], [1, 2]]);
      expect(deltaValue(live[0]!, '/normalizedAnswer')).to.match(/^The Q3 launch moves to May \[1\]\(\/record\/live:/);
      const firstText = frames.findIndex((f) => f.frame.type === 'TEXT_MESSAGE_CONTENT');
      expect(frames.indexOf(live[0]!)).to.equal(firstText + 1);
      const closing = answerDeltas(frames.slice(0, final)).filter((f) => f.id.startsWith('j:'));
      expect(closing.map(chunkIndexes), 'closing the turn keeps the sources the stream showed').to.deep.equal([[1, 2], [1, 2]]);
    });

    it('rawLength counts the text the browser received, so it swaps in the numbered text', () => {
      const journal = readJournal('agent_live_file_citation');
      const frames = runSteps(new RunProjector(projectorContext(journal)), liveSteps(journal));
      const received = frames.filter((f) => f.frame.type === 'TEXT_MESSAGE_CONTENT').map((f) => String(f.frame['delta']));
      const live = answerDeltas(frames).filter((f) => f.id.startsWith('d:'));
      expect(live.map((f) => deltaValue(f, '/rawLength'))).to.deep.equal([
        received[0]!.length,
        received[0]!.length + received[1]!.length,
      ]);
    });

    it('a shared viewer gets no live answer delta, and no audience a model-authored marker', () => {
      const cited = readJournal('agent_live_file_citation');
      const shared = runSteps(new RunProjector(projectorContext(cited, 'shared')), liveSteps(cited));
      expect(answerDeltas(shared).filter((f) => f.id.startsWith('d:'))).to.deep.equal([]);
      const marked = readJournal('agent_artifact_marker_answer');
      for (const audience of ['owner', 'shared'] as const) {
        const frames = runSteps(new RunProjector(projectorContext(marked, audience)), liveSteps(marked));
        expect(texts(frames)).to.not.include('attacker.test');
      }
    });
  });

  it('L-14: a run that finished unwatched is projected whole, terminal frames included', () => {
    const journal = readJournal('agent_ask_user_question');
    const plan = planReplay(journal.runId, journal.events, [], null);
    const frames = runSteps(new RunProjector(projectorContext(journal)), plan.steps as never);
    // An ask_user_question pause sends no answer delta, as legacy since main #3592.
    expect(frames.map((f) => f.frame.type).slice(-3)).to.deep.equal(['STATE_SNAPSHOT', 'STATE_SNAPSHOT', 'RUN_FINISHED']);
    expect(frames.at(-2)!.frame['snapshot']).to.include({ final: true, status: 'waiting_input' });
    expect(frames.some((f) => f.frame.type === 'CUSTOM' && f.frame['name'] === 'ask_user_question')).to.equal(true);
  });

  it('L-18: a failure with no answer ends in RUN_ERROR with the error type as its code', () => {
    const base = readJournal('agent_quick_tool_call');
    const events = base.events.filter((e) => e.seq <= 12);
    const failed: JournalEvent = { ...events[events.length - 1]!, seq: 13, type: 'run.failed', writer: 'node', data: { errorType: 'kernel_error' } };
    const projector = new RunProjector(projectorContext(base));
    projector.begin();
    const frames = projector.project([...events, failed]);
    expect(frames[frames.length - 1]!.frame).to.deep.include({ type: 'RUN_ERROR', code: 'kernel_error' });
    expect(projector.terminal).to.deep.include({ kind: 'error', code: 'kernel_error' });
    expect(projector.finish({}, {})).to.deep.equal([]);
  });

  it('PERFA-9: a quarantined run ends in RUN_ERROR with the code the web app words, whatever its cause', () => {
    const base = readJournal('agent_quick_tool_call');
    const events = base.events.filter((e) => e.seq <= 12);
    for (const lastErrorType of ['crash_loop', 'assembly_failed', undefined]) {
      const data = { attempts: 4, ...(lastErrorType !== undefined ? { lastErrorType } : {}) };
      const quarantined: JournalEvent = { ...events[events.length - 1]!, seq: 13, type: 'run.quarantined', writer: 'node', data };
      const projector = new RunProjector(projectorContext(base));
      projector.begin();
      const frames = projector.project([...events, quarantined]);
      const last = frames[frames.length - 1]!.frame;
      expect(last).to.deep.include({ type: 'RUN_ERROR', code: 'quarantined' });
      expect(last['message']).to.equal('This run kept failing and was stopped. Please try sending your message again.');
      expect(projector.terminal).to.deep.include({ kind: 'error', code: 'quarantined' });
    }
  });

  describe('PA-10: a run the sweeper fails at its deadline ends in the legacy error answer', () => {
    const DEADLINE_MESSAGE = 'This run took too long and was stopped. Please try sending your message again.';
    const base = readJournal('agent_quick_tool_call');
    // Turn 1 ran its tool; the run then sat past its absolute deadline.
    const cut = base.events.find((e) => e.type === 'turn.completed')!.seq;
    const head = base.events.filter((e) => e.seq <= cut);
    const swept: JournalEvent = { ...head[head.length - 1]!, seq: cut + 1, type: 'run.failed', writer: 'node', data: { errorType: 'deadline_exceeded' } };
    delete swept.attemptId;
    delete swept.fence;
    const events = [...head, swept];
    const parts = [
      { type: 'text', content: DEADLINE_MESSAGE, runId: base.runId, isFinal: true },
      {
        type: 'tool_call',
        toolCallId: 'call-search',
        toolName: 'goldendocs__search_docs',
        args: '{"query": "paid leave"}',
        status: 'completed',
        runId: base.runId,
        argsSummary: 'Search Docs: "paid leave"',
        resultPreview: '{"query": "paid leave", "hits": [{"title": "Leave policy", "excerpt": "25 days of paid leave."}]}',
        resultSummary: (head.find((e) => e.type === 'x.pipeshub.tool.result_presented')!.data as Record<string, unknown>)['resultSummary'],
      },
    ];
    const completion = {
      answer: DEADLINE_MESSAGE,
      citations: [],
      confidence: 'Low',
      answerMatchType: 'Error',
      errorCode: 'deadline_exceeded',
      succeeded_tool_names: ['goldendocs__search_docs'],
      failed_tool_names: [],
      parts,
    };

    it('the stream ends with the error-answer snapshot, then Node\'s RUN_FINISHED', () => {
      const projector = new RunProjector(projectorContext(base));
      projector.begin();
      const frames = projector.project(events);
      const tail = frames.slice(-3).map((f) => f.frame);
      expect(tail.map((f) => f.type)).to.deep.equal(['STATE_SNAPSHOT', 'STATE_DELTA', 'STATE_SNAPSHOT']);
      expect((tail[0]!['snapshot'] as Record<string, unknown>)['status']).to.equal('failed');
      expect(tail[1]!['delta']).to.deep.equal([
        { op: 'replace', path: '/citations', value: [] },
        { op: 'replace', path: '/normalizedAnswer', value: DEADLINE_MESSAGE },
        { op: 'replace', path: '/rawLength', value: DEADLINE_MESSAGE.length },
      ]);
      expect(tail[2]!['snapshot']).to.deep.equal({ final: true, ...completion });
      expect(frames.some((f) => f.frame.type === 'RUN_ERROR')).to.equal(false);
      expect(projector.terminal).to.deep.equal({ kind: 'finished' });
      const [finished] = projector.finish({ _id: 'c' }, {});
      expect(finished!.frame.type).to.equal('RUN_FINISHED');
    });

    it('the transcript is the same error answer', () => {
      expect(runTranscript(events, base.runId)).to.deep.equal({ kind: 'answer', completion, questions: [] });
    });

    it('a deadline failure before any model turn appends the final text part', () => {
      const early = [...base.events.filter((e) => e.seq <= 3), { ...swept, seq: 4 }];
      expect(runTranscript(early, base.runId)).to.deep.include({ kind: 'answer' });
      const transcript = runTranscript(early, base.runId) as { completion: Record<string, unknown> };
      expect(transcript.completion['parts']).to.deep.equal([{ type: 'text', content: DEADLINE_MESSAGE, isFinal: true }]);
    });

    it('a quarantined run still ends in RUN_ERROR with its failure transcript', () => {
      const quarantined: JournalEvent = { ...swept, type: 'run.quarantined', data: { attempts: 4, lastErrorType: 'deadline_exceeded' } };
      const projector = new RunProjector(projectorContext(base));
      projector.begin();
      const frames = projector.project([...head, quarantined]);
      expect(frames[frames.length - 1]!.frame).to.deep.include({ type: 'RUN_ERROR', code: 'quarantined' });
      expect(runTranscript([...head, quarantined], base.runId)).to.deep.include({ kind: 'failure', code: 'quarantined' });
    });
  });

  describe('MR-1 / PA-11: a restarted message names the message it replaces', () => {
    const starts = (frames: ProjectedFrame[]) => frames.filter((f) => f.frame.type === 'TEXT_MESSAGE_START');
    const marked = (frames: ProjectedFrame[]) => starts(frames).filter((f) => 'replacesMessageId' in f.frame);

    it('after an expired buffer (L-2) the restart replaces the abandoned message', () => {
      const journal = rechunked(readJournal('agent_quick_tool_call'), 3);
      const original = runSteps(new RunProjector(projectorContext(journal)), liveSteps(journal));
      const midMessage = original.find((f) => f.frame.type === 'TEXT_MESSAGE_CONTENT')!;
      const oldId = midMessage.frame['messageId'];
      const rest = resume(journal, midMessage.id, []);
      const restarts = marked(rest);
      expect(restarts).to.have.length(1);
      expect(restarts[0]!.frame['replacesMessageId']).to.equal(oldId);
      expect(restarts[0]!.frame['messageId']).to.not.equal(oldId);
    });

    it('after a live overflow resync (L-4) the restart replaces the abandoned message', () => {
      const journal = rechunked(readJournal('agent_quick_tool_call'), 3);
      const projector = new RunProjector(projectorContext(journal));
      const frames = projector.begin();
      const steps = liveSteps(journal);
      // The client falls behind in the answer's message (turn 2's first text delta).
      const firstDelta = steps.findIndex((step) => 'delta' in step && step.delta.kind === 'text_delta');
      for (const step of steps.slice(0, firstDelta + 1)) {
        frames.push(...('journal' in step ? projector.project(step.journal) : projector.delta(step.delta)));
      }
      const oldId = starts(frames)[starts(frames).length - 1]!.frame['messageId'];
      // The stream service rebuilds from the last frame the client has, as a reconnect would.
      const sentAfter = frames[frames.length - 1]!.id;
      const overflow = resume(journal, sentAfter, retained(journal), { forceLost: true });
      expect(overflow[0]!.id).to.equal(`a:${sentAfter}`);
      expect(overflow[0]!.frame).to.deep.include({ type: 'TEXT_MESSAGE_END', messageId: oldId });
      const restarts = marked([...frames, ...overflow]);
      expect(restarts).to.have.length(1);
      expect(restarts[0]!.frame['replacesMessageId']).to.equal(oldId);
      // Reconnecting after any frame of it continues exactly there, retained deltas or not.
      overflow.forEach((sent, i) => {
        for (const deltas of [retained(journal), []]) {
          expect(
            resume(journal, sent.id, deltas).map((f) => [f.id, f.frame]),
            `resume after ${sent.id} (${deltas.length} deltas retained)`,
          ).to.deep.equal(overflow.slice(i + 1).map((f) => [f.id, f.frame]));
        }
      });
    });

    it('after a re-claim at a new fence (L-10) the new message replaces the old fence\'s', () => {
      const journal = reclaimedJournal();
      const original = runSteps(new RunProjector(projectorContext(journal)), liveSteps(journal));
      const all = starts(original);
      const restarts = marked(original);
      expect(restarts).to.have.length(1);
      expect(restarts[0]).to.equal(all[2]);
      expect(restarts[0]!.frame['replacesMessageId']).to.equal(all[1]!.frame['messageId']);
    });

    for (const scenario of DURABLE_SCENARIOS) {
      it(`a first start never carries the marker: ${scenario}`, () => {
        const journal = rechunked(readJournal(scenario), 3);
        const live = runSteps(new RunProjector(projectorContext(journal)), liveSteps(journal));
        const journalOnly = runSteps(new RunProjector(projectorContext(journal)), [{ journal: journal.events }]);
        // A pre-run clarification never calls the model: it has no message at all.
        // A model call that failed has the one legacy opened for it.
        const answers =
          journal.events.some((e) => e.type === 'model.response') ||
          failedModelCall(foldTimeline(journal.events, journal.runId));
        expect(starts(live).length > 0).to.equal(answers);
        expect(marked(live)).to.deep.equal([]);
        expect(marked(journalOnly)).to.deep.equal([]);
      });
    }

    it('resuming from any frame between the replaced start and the restart reproduces the marker (L-10)', () => {
      const journal = reclaimedJournal();
      const original = runSteps(new RunProjector(projectorContext(journal)), liveSteps(journal));
      const restart = marked(original)[0]!;
      const replaced = original.find((f) => f.frame.type === 'TEXT_MESSAGE_START' && f.frame['messageId'] === restart.frame['replacesMessageId'])!;
      const at = original.indexOf(restart);
      // A cursor before the replaced start replays without fence 1's deltas: the client never sees that message.
      expect(at - original.indexOf(replaced)).to.be.greaterThan(2);
      for (let cut = original.indexOf(replaced); cut < at; cut += 1) {
        const again = marked(resume(journal, original[cut]!.id));
        expect(again.map((f) => f.frame), `resume after ${original[cut]!.id}`).to.deep.equal([restart.frame]);
      }
    });

    it('resuming twice from the same expired cursor reproduces the marker (L-2)', () => {
      const journal = rechunked(readJournal('agent_quick_tool_call'), 3);
      const original = runSteps(new RunProjector(projectorContext(journal)), liveSteps(journal));
      const midMessage = original.find((f) => f.frame.type === 'TEXT_MESSAGE_CONTENT')!;
      const first = resume(journal, midMessage.id, []);
      expect(marked(first)).to.have.length(1);
      expect(resume(journal, midMessage.id, [])).to.deep.equal(first);
    });
  });
  describe('C-6: a shared viewer sees a citation only when they can read the record now', () => {
    const cited = [recordCitation('rec-open', 1), recordCitation('rec-closed', 2), webCitation(3)];
    const citing = (name: string, pieces = 3): GoldenJournal => withCitations(rechunked(readJournal(name), pieces), cited);
    const meta = { requestId: 'r', timestamp: 't', duration: 0 };
    const context = (journal: GoldenJournal, audience: 'owner' | 'shared', access?: CitationAccess): ProjectorContext => ({
      ...projectorContext(journal, audience),
      ...(access !== undefined ? { citations: () => access } : {}),
    });
    /** The live stream, ending in a RUN_FINISHED that carries the cited conversation. */
    function stream(journal: GoldenJournal, ctx: ProjectorContext, conversation: unknown[] = cited): ProjectedFrame[] {
      const projector = new RunProjector(ctx);
      const frames = projector.begin();
      for (const step of liveSteps(journal)) {
        frames.push(...('journal' in step ? projector.project(step.journal) : projector.delta(step.delta)));
      }
      return [...frames, ...projector.finish(finishedConversation(journal.sessionId, conversation), meta)];
    }
    /** Every citation list a frame carries: the final `/citations`, the final snapshot, RUN_FINISHED. */
    function citationLists(frames: ProjectedFrame[]): unknown[][] {
      const lists: unknown[][] = [];
      for (const { frame } of frames) {
        if (frame.type === 'STATE_DELTA') {
          for (const op of frame['delta'] as Array<{ path: string; value: unknown }>) {
            if (op.path === '/citations' && Array.isArray(op.value) && op.value.length > 0) lists.push(op.value);
          }
        }
        if (frame.type === 'STATE_SNAPSHOT' && (frame['snapshot'] as Record<string, unknown>)['final'] === true) {
          lists.push((frame['snapshot'] as { citations: unknown[] }).citations);
        }
        if (frame.type === 'RUN_FINISHED') {
          const messages = (frame['result'] as { conversation: { messages: Array<{ citations: Array<{ citationData: unknown }> }> } })
            .conversation.messages;
          lists.push(messages[1]!.citations.map((c) => c.citationData));
        }
      }
      return lists;
    }

    it('a viewer who can read every cited record sees every citation whole', () => {
      const frames = stream(citing('agent_quick_tool_call'), context(citing('agent_quick_tool_call'), 'shared', readableRecords('rec-open', 'rec-closed')));
      const lists = citationLists(frames);
      expect(lists).to.have.length(3);
      for (const list of lists) expect(list).to.deep.equal(cited);
    });

    it('mixed: a denied citation keeps only its number, in every frame; readable and web ones stay whole', () => {
      const journal = citing('agent_quick_tool_call');
      const frames = stream(journal, context(journal, 'shared', readableRecords('rec-open')));
      const lists = citationLists(frames);
      expect(lists).to.have.length(3);
      for (const list of lists) {
        expect(list).to.deep.equal([cited[0], { chunkIndex: 2, unavailable: true }, cited[2]]);
      }
      const body = texts(frames);
      for (const leak of leaksOf('rec-closed')) expect(body, leak).to.not.include(leak);
      expect(body).to.not.include('citation-doc-1');
    });

    it('sentinel: nothing of a denied record reaches any frame of any scenario, live or journal-only', () => {
      for (const scenario of DURABLE_SCENARIOS) {
        const journal = citing(scenario);
        const ctx = context(journal, 'shared', readableRecords());
        const live = stream(journal, ctx);
        const projector = new RunProjector(ctx);
        const whole = [...projector.begin(), ...projector.project(journal.events), ...projector.finish(finishedConversation(journal.sessionId, cited), meta)];
        for (const frames of [live, whole]) {
          const body = texts(frames);
          for (const leak of [...leaksOf('rec-open'), ...leaksOf('rec-closed')]) {
            expect(body, `${scenario}: ${leak}`).to.not.include(leak);
          }
        }
      }
    });

    it('fails closed: a shared projector given no access hides every record citation', () => {
      const journal = citing('agent_quick_final_answer');
      for (const list of citationLists(stream(journal, context(journal, 'shared')))) {
        expect(list).to.deep.equal([{ chunkIndex: 1, unavailable: true }, { chunkIndex: 2, unavailable: true }, cited[2]]);
      }
    });

    it('the owner is unchanged: every citation whole, whatever access is passed', () => {
      for (const scenario of DURABLE_SCENARIOS) {
        const journal = citing(scenario);
        const plain = stream(journal, context(journal, 'owner'));
        expect(stream(journal, context(journal, 'owner', CitationAccess.denyRecords())), scenario).to.deep.equal(plain);
        for (const list of citationLists(plain)) expect(list, scenario).to.deep.equal(cited);
      }
    });

    it('C-18: live citations are placeholders unless their indexed copy is readable, deterministic and resumable from any id', () => {
      const liveCited = [liveCitation('f-copy', 1), liveCitation('f-none', 2), recordCitation('rec-open', 3), webCitation(4)];
      const access = CitationAccess.from(
        new Map([
          [sourceKey(live('f-copy')), 'shown'],
          [sourceKey(live('f-none')), 'unverifiable'],
          [sourceKey(indexed('rec-open')), 'shown'],
        ] as const),
      );
      for (const scenario of DURABLE_SCENARIOS) {
        const journal = withCitations(rechunked(readJournal(scenario), 4), liveCited);
        const ctx = context(journal, 'shared', access);
        const original = runSteps(new RunProjector(ctx), liveSteps(journal));
        const body = texts(original.map((f) => f.frame));
        for (const leak of [...leaksOf('f-none'), 'TEMP-f-none']) expect(body, `${scenario}: ${leak}`).to.not.include(leak);
        for (let cut = 0; cut < original.length; cut += 1) {
          const rest = resume(journal, original[cut]!.id, retained(journal), {}, ctx);
          expect(rest.map((f) => [f.id, f.frame]), `${scenario}: resume after ${original[cut]!.id}`).to.deep.equal(
            original.slice(cut + 1).map((f) => [f.id, f.frame]),
          );
        }
      }
      const journal = withCitations(readJournal('agent_quick_final_answer'), liveCited);
      const frames = stream(journal, context(journal, 'shared', access), liveCited);
      const lists = citationLists(frames);
      expect(lists).to.have.length(3);
      for (const list of lists) {
        expect(list).to.deep.equal([liveCited[0], { chunkIndex: 2, unavailable: true }, liveCited[2], liveCited[3]]);
      }
    });

    it('C-19: the live file the golden run cites reaches a shared viewer only through a readable indexed copy', () => {
      const journal = rechunked(readJournal('agent_live_file_citation'), 4);
      const finalized = journal.events.find((e) => e.type === 'x.pipeshub.answer.finalized')!.data as { citations: unknown[] };
      const real = finalized.citations;
      expect(real).to.have.length(2);
      const decided = (decision: 'shown' | 'denied' | 'unverifiable'): CitationAccess =>
        CitationAccess.from(new Map([[sourceKey(live('file-q3')), decision]] as const));
      const placeholders = [{ chunkIndex: 1, unavailable: true }, { chunkIndex: 2, unavailable: true }];

      for (const list of citationLists(stream(journal, context(journal, 'owner'), real))) expect(list).to.deep.equal(real);
      for (const list of citationLists(stream(journal, context(journal, 'shared', decided('shown')), real))) {
        expect(list).to.deep.equal(real);
      }
      for (const decision of ['denied', 'unverifiable'] as const) {
        const ctx = context(journal, 'shared', decided(decision));
        const frames = stream(journal, ctx, real);
        const lists = citationLists(frames);
        expect(lists, decision).to.have.length(3);
        for (const list of lists) expect(list, decision).to.deep.equal(placeholders);
        const body = texts(frames);
        for (const leak of ['file-q3', 'Q3 plan.txt', 'The Q3 launch moves to May.', 'The Q3 budget is unchanged.']) {
          expect(body, `${decision}: ${leak}`).to.not.include(leak);
        }
        const original = runSteps(new RunProjector(ctx), liveSteps(journal));
        for (let cut = 0; cut < original.length; cut += 1) {
          const rest = resume(journal, original[cut]!.id, retained(journal), {}, ctx);
          expect(rest.map((f) => [f.id, f.frame]), `${decision}: resume after ${original[cut]!.id}`).to.deep.equal(
            original.slice(cut + 1).map((f) => [f.id, f.frame]),
          );
        }
      }
    });

    it('the shared stream is deterministic for an access set, and resumes from any id with exactly the rest', () => {
      for (const scenario of DURABLE_SCENARIOS) {
        const journal = citing(scenario, 4);
        const ctx = context(journal, 'shared', readableRecords('rec-open'));
        const original = runSteps(new RunProjector(ctx), liveSteps(journal));
        expect(runSteps(new RunProjector(ctx), liveSteps(journal)), scenario).to.deep.equal(original);
        for (let cut = 0; cut < original.length; cut += 1) {
          const rest = resume(journal, original[cut]!.id, retained(journal), {}, ctx);
          expect(rest.map((f) => [f.id, f.frame]), `${scenario}: resume after ${original[cut]!.id}`).to.deep.equal(
            original.slice(cut + 1).map((f) => [f.id, f.frame]),
          );
        }
      }
    });
  });
});

describe('C-1: a reply cut off at the token limit and its continuation are one part', () => {
  it('as legacy\'s collector joins them, live and in the saved transcript', () => {
    const journal = readJournal('agent_truncated_response_continued');
    const completion = buildCompletion(foldTimeline(journal.events, journal.runId)) as { parts: Array<Record<string, unknown>> };
    const texts = completion.parts.filter((part) => part['type'] === 'text');
    const finalized = journal.events.find((e) => e.type === 'x.pipeshub.answer.finalized')!.data as { partTexts: string[] };
    expect(texts.map((part) => part['content'])).to.deep.equal(finalized.partTexts);
    expect(texts).to.have.length(1);
    expect(texts[0]!['isFinal']).to.equal(true);
    const frames = runSteps(new RunProjector(projectorContext(journal)), liveSteps(journal));
    const final = frames.find((f) => f.frame.type === 'STATE_SNAPSHOT' && (f.frame['snapshot'] as Record<string, unknown>)['final'] === true);
    expect((final!.frame['snapshot'] as { parts: unknown[] }).parts).to.deep.equal(completion.parts);
  });
});
