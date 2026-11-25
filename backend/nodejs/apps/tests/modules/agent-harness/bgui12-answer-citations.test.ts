/**
 * BGUI-12 (BGUI11-FU-2): a job's timeline carries the sources its answer cites, so the job panel's reply draws each
 * `[N]` with its source as the chat's answer does. The engine's answer is journaled with its citations already
 * resolved (E2E3-F3); the timeline used to drop them. Only the run's owner reads the timeline, as the chat shows its
 * owner their own answer's citations; a source the worker withheld keeps its number and names nothing.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { RunTimelineService } from '../../../src/modules/agent-harness/application/run-timeline-service';
import { HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import type { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import { answerCitations } from '../../../src/modules/agent-harness/projection/answer-citations';

const RUN = 'run-child';
const run = { orgId: 'org-1', runId: RUN, sessionId: 'ses-1', status: 'completed' } as HarnessRun;
const SCOPE = { orgId: 'org-1', sessionId: 'ses-1', actorId: 'user-1' } as never;

const journalOf = (entries: Array<[string, Record<string, unknown>]>): JournalEvent[] =>
  entries.map(
    ([type, data], index) =>
      ({
        orgId: 'org-1',
        sessionId: 'ses-1',
        streamId: 'ses-1',
        seq: index + 1,
        runId: RUN,
        type,
        schemaVersion: 1,
        critical: !type.startsWith('x.'),
        ts: `2026-11-11T12:00:00.${String(index).padStart(3, '0')}Z`,
        writer: 'worker',
        visibility: 'owner',
        data,
      }) as JournalEvent,
  );

const serviceOver = (events: JournalEvent[], audience: 'owner' | 'shared') =>
  new RunTimelineService({
    runs: { streamAccess: sinon.stub().resolves({ run, access: { audience } }) },
    journal: {
      readRun: sinon
        .stub()
        .callsFake(async (_org: string, _run: string, after: number, limit: number) =>
          events.filter((e) => e.seq > after).slice(0, limit),
        ),
    },
    journals: { resolve: sinon.stub().callsFake(async (all: JournalEvent[]) => all) },
    workspaces: null,
    ledger: null,
  });

const FINAL_URL = 'https://www.iplt20.com/news/article/final-report';
const web = (chunkIndex: number, url: string, title: string) => ({
  content: `What ${title} says`,
  chunkIndex,
  metadata: { recordId: url, recordName: title, webUrl: url, origin: 'WEB_SEARCH', connector: 'WEB', recordType: 'WEBPAGE' },
  citationType: 'web|url',
});
const ANSWER_TEXT = `RCB won the final [1](${FINAL_URL}#:~:text=won) and Rabada took 29 wickets [2](https://example.org/caps).`;

const answered = (citations: unknown[]): JournalEvent[] =>
  journalOf([
    ['run.started', {}],
    ['turn.started', { turn: 1 }],
    ['x.pipeshub.answer.finalized', { outcome: 'answered', answer: ANSWER_TEXT, partTexts: [ANSWER_TEXT], citations }],
    ['run.completed', {}],
  ]);

describe('BGUI-12: a job\'s timeline carries its answer\'s citations', () => {
  it('BGUI12-N1: the owner reads each cited source beside the final text, numbered as the text is', async () => {
    const view = await serviceOver(
      answered([web(1, FINAL_URL, 'TATA IPL 2026 Final'), web(2, 'https://example.org/caps', 'Caps')]),
      'owner',
    ).timeline(SCOPE, RUN);

    const final = view.parts.find((part) => part['type'] === 'text' && part['isFinal'] === true);
    expect(final?.['content']).to.equal(ANSWER_TEXT);
    expect(view.answer?.citations.map((c) => c.chunkIndex)).to.deep.equal([1, 2]);
    expect(view.answer?.citations[0]).to.deep.include({ citationType: 'web|url', content: 'What TATA IPL 2026 Final says' });
    expect((view.answer?.citations[0] as { metadata: Record<string, unknown> }).metadata).to.deep.include({
      recordName: 'TATA IPL 2026 Final',
      webUrl: FINAL_URL,
    });
  });

  it('BGUI12-N2: a viewer other than the run\'s owner is refused the timeline, so never a cited title', async () => {
    let refused: unknown;
    try {
      await serviceOver(answered([web(1, FINAL_URL, 'TATA IPL 2026 Final')]), 'shared').timeline(SCOPE, RUN);
    } catch (error) {
      refused = error;
    }
    expect(refused).to.be.instanceOf(HarnessNotFoundError);
  });

  it('BGUI12-N3: a withheld source keeps its number and names nothing; a malformed entry is left out', () => {
    const shown = answerCitations([
      { chunkIndex: 1, unavailable: true },
      { chunkIndex: 2, content: 'x', citationType: 'vectordb|document' },
      { content: 'no number', chunkIndex: 'three', metadata: { recordName: 'Secret plan' } },
      'not a citation',
      web(4, FINAL_URL, 'Final'),
    ]);
    expect(shown.map((c) => c.chunkIndex)).to.deep.equal([1, 2, 4]);
    expect(shown[0]).to.deep.equal({ chunkIndex: 1, unavailable: true });
    expect(shown[1]).to.deep.equal({ chunkIndex: 2, unavailable: true });
    expect(JSON.stringify(shown)).to.not.include('Secret plan');
  });

  it('BGUI12-N4: an older job answered with no citations reads an empty list; a run with no answer has none', async () => {
    const older = await serviceOver(answered([]), 'owner').timeline(SCOPE, RUN);
    expect(older.answer).to.deep.equal({ citations: [] });

    const running = await serviceOver(journalOf([['run.started', {}], ['turn.started', { turn: 1 }]]), 'owner').timeline(
      SCOPE,
      RUN,
    );
    expect(running).to.not.have.property('answer');
  });
});
