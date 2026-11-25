/**
 * UX5 AP-1, FU-5: the activity panel's view folded from a run's journal
 * (commands, tool calls with their approvals and refusals, the plan, changed
 * files, follow-ups from the run document), and live command output
 * applied on top of it.
 */
import { expect } from 'chai';
import { HarnessRun } from '../../../../src/modules/agent-harness/domain/execution';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { JournalService } from '../../../../src/modules/agent-harness/application/journal-service';
import { RunTimelineService } from '../../../../src/modules/agent-harness/application/run-timeline-service';
import { InMemoryPayloadStore } from '../../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import {
  ActivityFrame,
  ActivityView,
  applyLive,
  emptyActivity,
  foldActivity,
  MAX_ACTIVITY_COMMANDS,
  MAX_COMMAND_TAIL_CHARS,
  mergeLive,
} from '../../../../src/modules/agent-harness/projection/activity';
import { foldTimeline } from '../../../../src/modules/agent-harness/projection/timeline';
import { buildParts } from '../../../../src/modules/agent-harness/projection/transcript';
import { testCipher } from '../support/data-protection';
import { ActivityJournal, activityScenario, RUN_CODE_TOOL } from '../support/activity-journal';

const REASON = {
  rule: 'flow_external_write',
  sensitivity: 'private',
  sources: [{ title: 'Q3 plan', recordId: 'rec-1' }],
  moreSources: 0,
  destination: '#sales',
  conversationGrant: false,
};

const fold = (journal: ActivityJournal, events: readonly JournalEvent[] = journal.events) =>
  foldActivity(events, { runId: journal.runId, runStatus: 'running' });

/** A step's status fields; what the chat shows of its result is ACTRESULTS's to test. */
const statusOf = ({ toolCallId, name, status, reason, refusal }: ActivityView['tools'][number]) =>
  JSON.parse(JSON.stringify({ toolCallId, name, status, reason, refusal })) as Record<string, unknown>;

const frame = (n: number, kind: ActivityFrame['kind'], data: Record<string, unknown>): ActivityFrame => ({
  runId: 'run-ux5',
  fence: 1,
  modelAttempt: 0,
  n,
  kind,
  data,
});

describe('agent-harness activity view (UX5 AP-1)', () => {
  it('lists the commands a run ran: finished ones from their record, running ones from the intent and the engine line', () => {
    const view = fold(activityScenario());
    expect(view.commands.map(({ commandId, source, command, status, exitCode }) => ({ commandId, source, command, status, exitCode }))).to.deep.equal([
      { commandId: 't1.0', source: 'run_code', command: "print('hi')", status: 'completed', exitCode: 0 },
      { commandId: 'e1', source: 'engine', command: 'pytest -q', status: 'failed', exitCode: 1 },
      { commandId: 'e2', source: 'engine', command: 'pytest -q tests/test_parser.py', status: 'running', exitCode: null },
    ]);
    const [runCode, engine, live] = view.commands;
    expect(runCode).to.deep.include({ language: 'python', stdout: 'hi\n', stderr: '', durationMs: 412, hasLog: true, truncated: false });
    expect(engine).to.deep.include({ stdout: '1 failed\n', hasLog: false });
    expect(live!.stdout, 'a running engine command shows its journaled output').to.equal('collecting');
    expect(live!.startedAt).to.be.a('number');
    expect(view.engine).to.equal(true);
  });

  it('a run_code call is running from its intent, with the code the model asked to run, until its record arrives', () => {
    const journal = new ActivityJournal()
      .turn(1, [{ callId: 'c-run', name: 'run_code', args: { code: 'x'.repeat(2_500), language: 'python' } }])
      .intent('t1.0', RUN_CODE_TOOL);
    const [command] = fold(journal).commands;
    expect(command).to.deep.include({ commandId: 't1.0', source: 'run_code', status: 'running', language: 'python', hasLog: false });
    expect(command!.command).to.have.length(2_000);
  });

  it('a call that is not run_code is no command', () => {
    const journal = new ActivityJournal().turn(1, [{ callId: 'c-1', name: 'search', args: { code: 'x' } }]).intent('t1.0', 'pipeshub@docs/search');
    expect(fold(journal).commands).to.deep.equal([]);
  });

  it('shows each tool call with its status, why it was held, and the rule that refused it', () => {
    const view = fold(activityScenario());
    expect(view.tools.map(statusOf)).to.deep.equal([
      { toolCallId: 'c-run', name: 'run_code', status: 'completed' },
      { toolCallId: 'c-send', name: 'slack__send_message', status: 'completed', reason: REASON },
      { toolCallId: 'c-del', name: 'drive__delete_file', status: 'blocked', refusal: { rule: 'policy' } },
      { toolCallId: 'c-post', name: 'web__post', status: 'denied', reason: REASON, refusal: { rule: 'interaction_denied' } },
    ]);
  });

  it('a call held for a person is awaiting approval, with the reason the approval card shows', () => {
    const journal = new ActivityJournal()
      .turn(1, [{ callId: 'c-send', name: 'slack__send_message', args: {} }])
      .held('ix.approve.t1.0', { rule: 'flow_external_write', sensitivity: 'private', sources: [], moreSources: 0, conversationGrant: false });
    expect(fold(journal).tools.map(statusOf)).to.deep.equal([
      {
        toolCallId: 'c-send',
        name: 'slack__send_message',
        status: 'awaiting_approval',
        reason: { rule: 'flow_external_write', sensitivity: 'private', sources: [], moreSources: 0, conversationGrant: false },
      },
    ]);
  });

  it('the plan is the engine\'s tasks, the latest write of each winning; files from engine lines and saved revisions', () => {
    const view = fold(activityScenario());
    expect(view.tasks).to.deep.equal([
      { taskId: 'task-1', title: 'Read the failing test', status: 'completed' },
      { taskId: 'task-2', title: 'Fix the parser', status: 'pending' },
    ]);
    expect(view.files).to.deep.equal([
      { path: 'src/parser.py', change: 'modified', diff: '-a\n+b' },
      // A commit names its paths from the workspace's root (`/x`); one file has one key (BGUI-11).
      { path: 'workspace/report.md', change: 'modified' },
    ]);
  });

  it('follows the run\'s status; once it ended, a command it never finished is cancelled, not running', () => {
    const journal = activityScenario();
    expect(fold(journal).status).to.equal('running');
    expect(foldActivity(journal.events, { runId: journal.runId, runStatus: 'waiting_approval' }).status).to.equal('waiting');
    journal.failed();
    const ended = fold(journal);
    expect(ended.status).to.equal('failed');
    expect(ended.commands.find((c) => c.commandId === 'e2')?.status).to.equal('cancelled');
  });

  it('ignores another run\'s events on the same stream', () => {
    const other = activityScenario(new ActivityJournal('run-other'));
    expect(foldActivity(other.events, { runId: 'run-ux5' })).to.deep.equal({ ...emptyActivity('run-ux5'), status: 'running' });
  });

  it('keeps the newest commands and counts the ones left out', () => {
    const journal = new ActivityJournal();
    for (let i = 0; i < MAX_ACTIVITY_COMMANDS + 5; i += 1) journal.commandFinished({ commandId: `cmd-${String(i)}` });
    const view = fold(journal);
    expect(view.commands).to.have.length(MAX_ACTIVITY_COMMANDS);
    expect(view.omittedCommands).to.equal(5);
    expect(view.commands[0]!.commandId).to.equal('cmd-5');
  });

  it('is the same view on every fold of the same journal (a reload, a second client)', () => {
    const journal = activityScenario();
    expect(fold(journal)).to.deep.equal(fold(journal));
  });
});

describe('agent-harness activity view: live output (UX5 AP-2, AP-3)', () => {
  const started = frame(1, 'command_started', { commandId: 'inv_1', source: 'run_code', command: 'make', startedAt: 7, attemptId: 'a1' });

  it('a started frame adds a running command; output appends to its stream; finished ends it', () => {
    let view = applyLive(emptyActivity('run-ux5'), started);
    view = applyLive(view, frame(2, 'command_output', { commandId: 'inv_1', stream: 'stdout', text: 'a' }));
    view = applyLive(view, frame(3, 'command_output', { commandId: 'inv_1', stream: 'stderr', text: 'warn' }));
    view = applyLive(view, frame(4, 'command_output', { commandId: 'inv_1', stream: 'stdout', dropped: 4096 }));
    view = applyLive(view, frame(5, 'command_output', { commandId: 'inv_1', stream: 'stdout', text: 'b' }));
    expect(view.commands).to.deep.equal([
      {
        commandId: 'inv_1', source: 'run_code', command: 'make', status: 'running', exitCode: null, startedAt: 7,
        durationMs: null, stdout: 'ab', stderr: 'warn', truncated: false, liveDropped: 4096, hasLog: false,
      },
    ]);
    view = applyLive(view, frame(6, 'command_finished', { commandId: 'inv_1', status: 'timed_out', exitCode: null, durationMs: 30_000 }));
    expect(view.commands[0]).to.deep.include({ status: 'timed_out', exitCode: null, durationMs: 30_000 });
  });

  it('keeps the newest 4,000 characters of a stream and says it cut', () => {
    let view = applyLive(emptyActivity('run-ux5'), started);
    for (let i = 0; i < 9; i += 1) {
      view = applyLive(view, frame(i + 2, 'command_output', { commandId: 'inv_1', stream: 'stdout', text: String(i).repeat(1_000) }));
    }
    const [command] = view.commands;
    expect(command!.stdout).to.have.length(MAX_COMMAND_TAIL_CHARS);
    expect(command!.stdout.endsWith('8'.repeat(1_000))).to.equal(true);
    expect(command!.stdout.startsWith('5')).to.equal(true);
    expect(command!.truncated).to.equal(true);
  });

  it('a command the journal finished is its record: later frames change nothing', () => {
    const journal = new ActivityJournal().commandFinished({ commandId: 'inv_1', command: 'make', stdout: { tail: 'done', bytes: 4, truncated: false } });
    const view = fold(journal);
    expect(applyLive(view, frame(9, 'command_output', { commandId: 'inv_1', stream: 'stdout', text: 'late' }))).to.deep.equal(view);
    expect(applyLive(view, frame(9, 'command_finished', { commandId: 'inv_1', status: 'failed', exitCode: 2 }))).to.deep.equal(view);
  });

  it('a snapshot merges the bus\'s output into the journal\'s running command, and adds one only the bus knows', () => {
    const journal = new ActivityJournal()
      .turn(1, [{ callId: 'c-run', name: 'run_code', args: { code: 'make', language: 'sh' } }])
      .intent('t1.0', RUN_CODE_TOOL);
    let live = applyLive(emptyActivity('run-ux5'), frame(1, 'command_started', { commandId: 't1.0', source: 'run_code', command: 'make', startedAt: 7 }));
    live = applyLive(live, frame(2, 'command_output', { commandId: 't1.0', stream: 'stdout', text: 'building' }));
    live = applyLive(live, frame(3, 'command_started', { commandId: 'e9', source: 'engine', command: 'ls', startedAt: 8 }));
    const merged = mergeLive(fold(journal), live);
    expect(merged.commands.map(({ commandId, source, command, status, stdout, language }) => ({ commandId, source, command, status, stdout, language }))).to.deep.equal([
      { commandId: 't1.0', source: 'run_code', command: 'make', status: 'running', stdout: 'building', language: 'sh' },
      { commandId: 'e9', source: 'engine', command: 'ls', status: 'running', stdout: '', language: undefined },
    ]);
  });
});

describe('agent-harness run timeline: follow-ups (UX5 FU-5)', () => {
  const timeline = async (journal: ActivityJournal, followups?: HarnessRun['followups']) => {
    const run = {
      runId: journal.runId, orgId: journal.orgId, sessionId: journal.sessionId, actorId: 'owner', status: 'completed', harnessMajor: 2,
      ...(followups !== undefined ? { followups } : {}),
    } as unknown as HarnessRun;
    const service = new RunTimelineService({
      runs: { streamAccess: () => Promise.resolve({ run, access: { audience: 'owner' as const } }) },
      journal: { readRun: (_org, runId, after) => Promise.resolve(journal.events.filter((e) => e.runId === runId && e.seq > after)) },
      journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
      workspaces: null,
      ledger: null,
    });
    return service.timeline({ orgId: journal.orgId, actorId: 'owner', sessionId: journal.sessionId }, journal.runId);
  };

  it('the owner\'s timeline carries the run document\'s follow-ups, never the conversation', async () => {
    const offered = [{ text: 'Compare with last quarter' }, { text: 'Draft an email' }];
    const journal = new ActivityJournal().completed();
    expect((await timeline(journal, offered)).followups).to.deep.equal(offered);
    expect(foldActivity(journal.events, { runId: journal.runId, runStatus: 'completed', followups: offered }).followups).to.deep.equal(offered);
    expect(fold(journal).followups, 'the journal alone has none').to.deep.equal([]);
    expect(JSON.stringify(buildParts(foldTimeline(journal.events, journal.runId))), 'the saved conversation').to.not.include('Compare with last quarter');
  });

  it('none stored: an empty list', async () => {
    expect((await timeline(new ActivityJournal().completed())).followups).to.deep.equal([]);
  });
});
