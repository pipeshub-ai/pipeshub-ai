/**
 * C4.10: the dimensions Node applies that no store pipeline holds.
 *
 * - `quotas.*` (C410-Q3, C410-Q6): caps on active runs from the run's pin; a cap only lowers the release's quota,
 *   and a run admitted without a pin keeps the release's quotas unless a caps source is bound.
 * - `ui.reasoning` (C410-U1..U3): off in the run's pin, its reasoning is never folded: no reasoning part in the
 *   saved conversation, none on the answer, no REASONING frame live or replayed.
 * - `ui.terminal` (C410-U4..U6): off, the activity view lists commands by name with no output and no log, live
 *   output included, and the full log is not served.
 * - The run fields a document keeps of its pin (C410-W4, C410-R4).
 */
import { expect } from 'chai';
import { Container } from 'inversify';
import { boundOrNull } from '../../../src/modules/agent-harness/binders/context';
import { AGENT_HARNESS_ADMISSION_QUOTA_CAPS } from '../../../src/modules/agent-harness/binders/tokens';
import {
  ADMISSION_QUOTAS,
  cappedQuotas,
  quotaCapsOfPin,
} from '../../../src/modules/agent-harness/domain/admission-quotas';
import {
  personWaitWindowMs,
  runDisplay,
  runPolicyFields,
} from '../../../src/modules/agent-harness/domain/run-policy-fields';
import {
  applyLive,
  foldActivity,
  mergeLive,
  withoutHiddenOutput,
} from '../../../src/modules/agent-harness/projection/activity';
import { RunProjector } from '../../../src/modules/agent-harness/projection/run-projector';
import { foldTimeline } from '../../../src/modules/agent-harness/projection/timeline';
import { buildCompletion, buildParts } from '../../../src/modules/agent-harness/projection/transcript';
import { liveSteps, projectorContext, readJournal, runSteps, type GoldenJournal } from '../../contracts/support/durable';
import { ActivityJournal, RUN_CODE_TOOL } from './support/activity-journal';
import { pinWith } from './support/execution-store-contract/run-policy';

/** `journal` whose pinned spec carries a pin with `ui` set. */
const withUi = (journal: GoldenJournal, ui: Record<string, boolean>): GoldenJournal => ({
  ...journal,
  events: journal.events.map((event) => {
    if (event.type !== 'run.spec_pinned') return event;
    const data = event.data as { spec: { extensions?: Record<string, Record<string, unknown>> } };
    const extensions = data.spec.extensions ?? {};
    return {
      ...event,
      data: {
        ...data,
        spec: {
          ...data.spec,
          extensions: { ...extensions, 'x.pipeshub': { ...(extensions['x.pipeshub'] ?? {}), resolved: pinWith({ ui }) } },
        },
      },
    };
  }),
});

const REASONING = readJournal('agent_quick_reasoning');

describe('C4.10 quotas: an org caps its active runs', () => {
  it('C410-Q3: a cap only lowers the release quota; none, zero or a larger one keeps it', () => {
    expect(cappedQuotas(ADMISSION_QUOTAS, { activeRunsPerOrg: 5, activeRunsPerUser: 2 })).to.include({
      activeRunsPerOrg: 5,
      activeRunsPerUser: 2,
      admissionsPerOrg: ADMISSION_QUOTAS.admissionsPerOrg,
    });
    expect(cappedQuotas(ADMISSION_QUOTAS, { activeRunsPerOrg: 10_000, activeRunsPerUser: 0 })).to.include({
      activeRunsPerOrg: ADMISSION_QUOTAS.activeRunsPerOrg,
      activeRunsPerUser: ADMISSION_QUOTAS.activeRunsPerUser,
    });
    expect(cappedQuotas(ADMISSION_QUOTAS, undefined)).to.equal(ADMISSION_QUOTAS);
    expect(quotaCapsOfPin(pinWith({ quotas: { activeRunsPerOrg: 7 } }))).to.deep.equal({
      activeRunsPerOrg: 7,
      activeRunsPerUser: 10,
    });
  });

  it('C410-Q6: no caps source bound: a run without a pin keeps the release quotas', () => {
    const container = new Container();
    expect(boundOrNull(container, AGENT_HARNESS_ADMISSION_QUOTA_CAPS)).to.equal(null);
    expect(cappedQuotas(ADMISSION_QUOTAS, undefined)).to.equal(ADMISSION_QUOTAS);
  });
});

describe('C4.10 run fields from the pin', () => {
  it("C410-W4: interactions.waitS is kept on the run and only ever shortens the deployment's window", () => {
    expect(runPolicyFields(pinWith({ interactions: { waitS: 600 } }))).to.deep.equal({ personWaitS: 600 });
    expect(personWaitWindowMs({ personWaitS: 600 }, 3_600_000)).to.equal(600_000);
    expect(personWaitWindowMs({ personWaitS: 7_200 }, 3_600_000)).to.equal(3_600_000);
    expect(personWaitWindowMs({}, 3_600_000)).to.equal(3_600_000);
  });

  it('C410-R4: only autoResume off is kept; the release (on) and a run without a pin keep nothing', () => {
    expect(runPolicyFields(pinWith({ recovery: { autoResume: false } }))).to.deep.equal({ autoResume: false });
    expect(runPolicyFields(pinWith({}))).to.deep.equal({});
    expect(runPolicyFields(undefined)).to.deep.equal({});
    expect(runDisplay(pinWith({ ui: { reasoning: false } }))).to.deep.equal({ reasoning: false, terminal: true });
    expect(runDisplay(undefined)).to.deep.equal({ reasoning: true, terminal: true });
  });
});

describe('WP-69 interactions.conversationGrants on the run', () => {
  it('WP69-CG-3: only conversationGrants off is kept on the run, with the other fields; the release keeps nothing', () => {
    expect(runPolicyFields(pinWith({ interactions: { conversationGrants: false } }))).to.deep.equal({
      conversationGrants: false,
    });
    expect(
      runPolicyFields(pinWith({ interactions: { conversationGrants: false, waitS: 600 }, recovery: { autoResume: false } })),
    ).to.deep.equal({ personWaitS: 600, autoResume: false, conversationGrants: false });
    expect(runPolicyFields(pinWith({ interactions: { conversationGrants: true } }))).to.deep.equal({});
  });
});

describe('C4.10 ui.reasoning: hidden reasoning never reaches the browser', () => {
  const reasoningFrames = (journal: GoldenJournal): string[] => {
    const projector = new RunProjector(projectorContext(journal, 'owner'));
    return runSteps(projector, liveSteps(journal), { begin: true })
      .map((f) => f.frame.type)
      .filter((type) => type.startsWith('REASONING'));
  };

  it('C410-U1: shown by default: the saved parts, the answer and the stream carry it', () => {
    const timeline = foldTimeline(REASONING.events, REASONING.runId);
    expect(buildParts(timeline).some((part) => part.type === 'reasoning')).to.equal(true);
    expect(buildCompletion(timeline)).to.have.property('reasoning');
    expect(reasoningFrames(withUi(REASONING, { reasoning: true }))).to.not.deep.equal([]);
  });

  it('C410-U2: off in the pin: no reasoning part, none on the answer', () => {
    const hidden = withUi(REASONING, { reasoning: false });
    const timeline = foldTimeline(hidden.events, hidden.runId);
    expect(timeline.display).to.deep.equal({ reasoning: false, terminal: true });
    expect(buildParts(timeline).some((part) => part.type === 'reasoning')).to.equal(false);
    expect(buildCompletion(timeline)).to.not.have.property('reasoning');
    expect(timeline.turns.every((turn) => turn.reasoning === undefined)).to.equal(true);
  });

  it('C410-U3: off in the pin: no REASONING frame, from live deltas or the journal', () => {
    expect(reasoningFrames(withUi(REASONING, { reasoning: false }))).to.deep.equal([]);
  });
});

describe('C4.10 ui.terminal: hidden output never reaches the browser', () => {
  const pinnedRun = (terminal: boolean | undefined): ActivityJournal => {
    const journal = new ActivityJournal();
    journal.add('run.spec_pinned', {
      specDigest: `sha256:${'c'.repeat(64)}`,
      spec: {
        runKind: 'interactive',
        ...(terminal === undefined ? {} : { extensions: { 'x.pipeshub': { resolved: pinWith({ ui: { terminal } }) } } }),
      },
    });
    return journal
      .turn(1, [{ callId: 'c1', name: 'run_code', args: { code: "print('hi')", language: 'python' } }])
      .intent('t1.0', RUN_CODE_TOOL)
      .commandFinished({
        commandId: 't1.0',
        invocationId: 't1.0',
        command: "print('hi')",
        stdout: { tail: 'hi\n', bytes: 3, truncated: false },
        log: { ref: 'inline:aGkK', hash: `sha256:${'c'.repeat(64)}`, size: 3 },
      });
  };
  const fold = (journal: ActivityJournal) =>
    foldActivity(journal.events, { runId: journal.runId, runStatus: 'running' });

  it('C410-U4: shown by default and with ui.terminal on: the output and the log', () => {
    for (const terminal of [undefined, true]) {
      const view = withoutHiddenOutput(fold(pinnedRun(terminal)));
      expect(view.outputHidden).to.equal(undefined);
      expect(view.commands[0]).to.deep.include({ stdout: 'hi\n', hasLog: true });
    }
  });

  it('C410-U5: off: the command by name and status, no output, no log', () => {
    const view = withoutHiddenOutput(fold(pinnedRun(false)));
    expect(view.outputHidden).to.equal(true);
    expect(view.commands[0]).to.deep.include({
      command: "print('hi')",
      status: 'completed',
      stdout: '',
      stderr: '',
      hasLog: false,
    });
  });

  it("C410-U6: off: live output merged into the journal's view is hidden too", () => {
    const journal = new ActivityJournal();
    journal.add('run.spec_pinned', {
      specDigest: `sha256:${'c'.repeat(64)}`,
      spec: { runKind: 'interactive', extensions: { 'x.pipeshub': { resolved: pinWith({ ui: { terminal: false } }) } } },
    });
    const base = fold(journal);
    const live = applyLive(base, {
      runId: journal.runId,
      fence: 1,
      modelAttempt: 0,
      n: 1,
      kind: 'command_output',
      data: { commandId: 'live-1', stream: 'stdout', text: 'secret\n' },
    });
    const merged = withoutHiddenOutput(mergeLive(base, live));
    expect(merged.commands.map((c) => c.stdout)).to.deep.equal(['']);
  });
});
