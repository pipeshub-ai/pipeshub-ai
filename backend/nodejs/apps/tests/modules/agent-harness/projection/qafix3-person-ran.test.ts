/**
 * QAFIX3 W1: the Activity view of a person's `/run` (SLASH3). The person ran the command, not the agent, and no
 * model was asked: the view says so (`personRan`), names no model although the pinned spec has one, and shows the
 * line the person typed, never the launcher program that runs it in the sandbox.
 */
import { expect } from 'chai';
import { applyLive, foldActivity } from '../../../../src/modules/agent-harness/projection/activity';
import { foldTimeline } from '../../../../src/modules/agent-harness/projection/timeline';
import { ActivityJournal, RUN_CODE_TOOL } from '../support/activity-journal';

const LINE = 'python analyse.py';
const LAUNCHER = `import os, shutil, subprocess, sys\nos.makedirs('workspace', exist_ok=True)\n# ${LINE}`;
const MODEL = { provider: 'openai', model: 'gpt-6-luna' };

const pinned = (journal: ActivityJournal, runKind: string): ActivityJournal => {
  journal.add('run.spec_pinned', { specDigest: `sha256:${'c'.repeat(64)}`, spec: { runKind, model: { policy: [MODEL] } } });
  return journal;
};

const commandRun = (): ActivityJournal =>
  pinned(new ActivityJournal(), 'command')
    .turn(1, [{ callId: 'slash-run-1', name: 'run_code', args: { code: LAUNCHER, language: 'python' } }])
    .intent('t1.0', RUN_CODE_TOOL);

const fold = (journal: ActivityJournal) => foldActivity(journal.events, { runId: journal.runId, runStatus: 'running' });

describe('QAFIX3 W1 the Activity view of a /run', () => {
  it('says the person ran it and names no model, though its pinned spec names one', () => {
    const view = fold(commandRun());
    expect(view.personRan).to.equal(true);
    expect(view.model).to.equal(undefined);
    expect(foldTimeline(commandRun().events, 'run-ux5').model).to.equal(undefined);
  });

  it('an agent run keeps its model and is not marked', () => {
    const journal = pinned(new ActivityJournal(), 'interactive')
      .turn(1, [{ callId: 'c1', name: 'run_code', args: { code: "print('hi')", language: 'python' } }])
      .intent('t1.0', RUN_CODE_TOOL);
    const view = fold(journal);
    expect(view.personRan).to.equal(undefined);
    expect(view.model).to.deep.equal(MODEL);
    expect(view.commands[0]!.command).to.equal("print('hi')");
  });

  it('never shows the launcher: while running the live line fills it, once finished the record does', () => {
    const running = fold(commandRun());
    expect(running.commands[0]).to.deep.include({ commandId: 't1.0', status: 'running', command: '' });
    const live = applyLive(running, {
      runId: 'run-ux5',
      fence: 1,
      modelAttempt: 0,
      n: 1,
      kind: 'command_started',
      data: { commandId: 't1.0', source: 'run_code', command: LINE, language: 'bash' },
    });
    expect(live.commands[0]).to.deep.include({ command: LINE, language: 'bash' });
    const finished = fold(commandRun().commandFinished({ commandId: 't1.0', invocationId: 't1.0', command: LINE, language: 'bash' }));
    expect(finished.commands[0]).to.deep.include({ command: LINE, language: 'bash', status: 'completed' });
  });
});
