/**
 * QA6-F6 on the activity panel: an engine's own call held for approval is
 * known only by its interaction (no model turn names it), and its row says
 * which rule of which layer asked, as the card does, through every way the
 * interaction can end. With it, an engine command that journaled only its
 * `command_done` line.
 */
import { expect } from 'chai';
import { foldActivity } from '../../../../src/modules/agent-harness/projection/activity';
import { ActivityJournal } from '../support/activity-journal';
import { without } from '../support/without';

const HELD = 'ix.approve.engine-1';
const ORG_RULE = { rule: 'writes', layer: 'org', toolId: 'jira@inst-42/create_issue', conversationGrant: false };
/** The reason as the panel and the card are given it. */
const SHOWN = { rule: 'writes', layer: 'org', sources: [], moreSources: 0, conversationGrant: false };

const tools = (journal: ActivityJournal) =>
  foldActivity(journal.events, { runId: journal.runId, runStatus: 'running' }).tools;

/** The engine's approval line names the call; the worker then explains and asks. */
const asked = (): ActivityJournal =>
  new ActivityJournal().engine('approval', 'create_issue', { interactionId: HELD, toolName: 'jira__create_issue', argsJson: '{}' });

const held = (): ActivityJournal => asked().held(HELD, ORG_RULE);

describe('agent-harness activity view: an engine\'s held call (QA6-F6)', () => {
  it('is awaiting approval under its interaction\'s id, with the rule and the layer that asked', () => {
    // Its arguments are read when the step is opened (ACTRESULTS): the snapshot has their fingerprint.
    const [held_] = tools(held());
    expect(held_!.detail).to.be.a('string');
    const line = without(held_!, 'detail');
    expect(line).to.deep.equal({ toolCallId: HELD, name: 'jira__create_issue', status: 'awaiting_approval', reason: SHOWN });
  });

  it('reads completed once approved, denied once denied, and blocked once the run ended undecided; the reason stays', () => {
    const status = (journal: ActivityJournal) => tools(journal).map((tool) => [tool.status, tool.reason]);
    expect(status(held().decided(HELD, 'approved'))).to.deep.equal([['completed', SHOWN]]);
    expect(status(held().decided(HELD, 'denied'))).to.deep.equal([['denied', SHOWN]]);
    expect(status(held().failed()), 'the run ended with nobody deciding').to.deep.equal([['blocked', SHOWN]]);
  });

  it('a reason from a worker that names no layer reads as it did', () => {
    const older = { rule: 'engine_writes', toolId: 'jira@inst-42/create_issue', conversationGrant: false };
    expect(tools(asked().held(HELD, older))[0]).to.deep.include({
      status: 'awaiting_approval',
      reason: { rule: 'engine_writes', sources: [], moreSources: 0, conversationGrant: false },
    });
  });

  it('an engine command that journaled no record ends by its done line: its status when it names one, else its exit code', () => {
    const done = (data: Record<string, unknown>) => {
      const journal = new ActivityJournal()
        .engine('command', 'npm test', { callId: 'e7' })
        .engine('command_done', '', { callId: 'e7', ...data });
      const [command] = foldActivity(journal.events, { runId: journal.runId, runStatus: 'running' }).commands;
      return [command?.status, command?.exitCode];
    };
    expect(done({ exitCode: 0 })).to.deep.equal(['completed', 0]);
    expect(done({ exitCode: 2 })).to.deep.equal(['failed', 2]);
    expect(done({ exitCode: 0, status: 'cancelled' })).to.deep.equal(['cancelled', 0]);
  });
});
