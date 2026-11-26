/**
 * NP-1..NP-8 (U1): an artifact presented again as final or discarded (P3.5
 * `lifecycle`) updates its card in place instead of adding a second card.
 * The client keys cards by `artifactId`: a card sent again replaces it, and
 * `lifecycle: discarded` removes it.
 */
import { expect } from 'chai';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { AguiFrame, Audience } from '../../../../src/modules/agent-harness/projection/frames';
import { RunProjector } from '../../../../src/modules/agent-harness/projection/run-projector';
import { TimelineBuilder } from '../../../../src/modules/agent-harness/projection/timeline';
import { runTranscript } from '../../../../src/modules/agent-harness/projection/transcript';
import { projectorContext, readJournal } from '../../../contracts/support/durable';

const base = readJournal('agent_artifact_marker_answer');
const at = base.events.findIndex((e) => e.type === 'x.pipeshub.artifact.presented');
const presentedEvent = base.events[at] as JournalEvent;
const ARTIFACT = String(presentedEvent.data?.['artifactId']);

const presented = (data: Record<string, unknown>): JournalEvent => ({
  ...presentedEvent,
  data: { ...presentedEvent.data, ...data },
});

/**
 * The golden run with its one presentation replaced: `withOutcome` right
 * after the call's outcome (one commit), `later` after the turn ends.
 */
function journal(withOutcome: JournalEvent[], later: JournalEvent[] = []): JournalEvent[] {
  const rest = base.events.slice(at + 1);
  const events = [...base.events.slice(0, at), ...withOutcome, rest[0] as JournalEvent, ...later, ...rest.slice(1)];
  return events.map((event, index) => ({ ...event, seq: index + 1 }));
}

const project = (events: JournalEvent[], audience: Audience = 'owner'): AguiFrame[] => {
  const projector = new RunProjector(projectorContext(base, audience));
  projector.begin();
  return projector.project(events).map((f) => f.frame);
};

const cards = (frames: AguiFrame[]): Array<Record<string, unknown>> =>
  frames.flatMap((frame) =>
    frame.type === 'STATE_DELTA'
      ? (frame['delta'] as Array<{ path: string; value: Record<string, unknown> }>)
          .filter((op) => op.path === '/artifacts/-')
          .map((op) => op.value)
      : [],
  );

const draft = presented({ lifecycle: 'draft', version: 1 });
const final = presented({ lifecycle: 'final', version: 2 });
const discarded = presented({ lifecycle: 'discarded', version: 1 });

describe('agent-harness projection: artifact cards update in place (U1)', () => {
  it('NP-1: the timeline keeps one view per artifact, where it was first presented', () => {
    const other = presented({ artifactId: 'art-other', fileName: 'other.csv' });
    const builder = new TimelineBuilder(base.runId);
    for (const event of journal([draft, other, final])) builder.apply(event);
    const views = builder.timeline.artifacts;
    expect(views.map((v) => [v.artifactId, v.lifecycle, v.version])).to.deep.equal([
      [ARTIFACT, 'final', 2],
      ['art-other', undefined, 2],
    ]);
    expect(views[0]?.callId).to.equal('call-promote');
  });

  it('NP-2: a draft and its final after one outcome are one card, final', () => {
    const shown = cards(project(journal([draft, final])));
    expect(shown).to.have.length(1);
    expect(shown[0]).to.deep.include({ artifactId: ARTIFACT, lifecycle: 'final', version: 2 });
  });

  it('NP-3: a draft discarded before its outcome was projected is never a card', () => {
    expect(cards(project(journal([draft, discarded])))).to.deep.equal([]);
  });

  it('NP-4: a card already shown is updated by a later presentation: final, then discarded', () => {
    const toFinal = cards(project(journal([draft], [final])));
    expect(toFinal.map((c) => [c['artifactId'], c['lifecycle']])).to.deep.equal([
      [ARTIFACT, 'draft'],
      [ARTIFACT, 'final'],
    ]);
    const toDiscarded = cards(project(journal([draft], [discarded])));
    expect(toDiscarded.map((c) => [c['artifactId'], c['lifecycle']])).to.deep.equal([
      [ARTIFACT, 'draft'],
      [ARTIFACT, 'discarded'],
    ]);
  });

  it('NP-4: a later presentation of an artifact the client never had a card for adds nothing', () => {
    const hidden = presented({ lifecycle: 'draft', card: false });
    expect(cards(project(journal([hidden], [final])))).to.deep.equal([]);
    const afterRemoval = cards(project(journal([draft], [discarded, final])));
    expect(afterRemoval.map((c) => c['lifecycle'])).to.deep.equal(['draft', 'discarded']);
  });

  it('NP-5: a legacy presentation (no lifecycle) is the card it always was', () => {
    const [card] = cards(project(base.events));
    expect(card).to.not.have.property('lifecycle');
    expect(card).to.deep.include({ artifactId: ARTIFACT, fileName: 'q3-revenue.csv', version: 2 });
  });

  it('NP-6: anyone but the owner gets the final card once, never a draft or a removal', () => {
    for (const audience of ['shared', 'external', 'slack'] as const) {
      const toFinal = cards(project(journal([draft], [final]), audience));
      expect(toFinal, audience).to.have.length(1);
      expect(cards(project(journal([draft], [discarded]), audience)), audience).to.deep.equal([]);
    }
    const [shared] = cards(project(journal([draft], [final]), 'shared'));
    expect(shared).to.deep.equal({ unavailable: true });
    const [external] = cards(project(journal([draft], [final]), 'external'));
    expect(external).to.deep.include({ artifactId: ARTIFACT, lifecycle: 'final' });
  });

  it('NP-7: the frames are deterministic, so a resumed stream replays them identically', () => {
    const events = journal([draft], [final]);
    const first = new RunProjector(projectorContext(base));
    first.begin();
    const second = new RunProjector(projectorContext(base));
    second.begin();
    expect(second.project(events)).to.deep.equal(first.project(events));
  });

  it('NP-8: the saved transcript is the same whether the card was a draft first or not', () => {
    expect(runTranscript(journal([draft], [final]), base.runId)).to.deep.equal(
      runTranscript(journal([final]), base.runId),
    );
  });
});
