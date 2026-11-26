/**
 * SLASH2 C3 conformance, Node's half, on the loop with a sandbox (L), opencode
 * (O), Codex (X) and Claude Code (C). Every executor's worker writes the
 * session workspace through the same sync (`WorkspaceService.commit` under its
 * attempt); each scripted turn below commits the way that worker's kernel does
 * (the engine kernel autosaves and ends the turn, the loop ends the turn). The
 * commands read and restore at that layer, so:
 *
 * - `/diff` lists the same files, with the same diffs, whichever executor wrote them;
 * - `/undo` restores the tree from before the turn, and the next turn on each
 *   executor hydrates that tree;
 * - `/review` expands to byte-identical text on all four.
 */
import { expect } from 'chai';
import type { Request, Response } from 'express';
import { pipeshubPromptCommands } from '../../../src/modules/agent-harness/application/pipeshub-prompt-commands';
import { SessionWorkspaceService, WorkspaceDiff } from '../../../src/modules/agent-harness/application/session-workspace-service';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { CommitReason } from '../../../src/modules/agent-harness/domain/workspace';
import { expandChatCommand, pipeshubCommandsFor } from '../../../src/modules/enterprise_search/utils/chat-command';
import { FileWrite, ORG, OWNER, scope, workspaceWorld } from './support/slash2-workspace-world';

const EXECUTORS: ReadonlyArray<[string, CommitReason[]]> = [
  ['L (durable loop, sandbox)', ['turn_end']],
  ['O (opencode)', ['autosave', 'turn_end']],
  ['X (codex)', ['autosave', 'turn_end']],
  ['C (claude_code)', ['autosave', 'turn_end']],
];

const BEFORE = { 'out/travel_policy.md': 'Travel\nBook economy.\nKeep receipts.\n', 'notes/draft.md': 'a\nb\n' };
/** The turn's edits, split over its commits. */
const TURN: Array<Record<string, FileWrite>> = [
  { 'out/travel_policy.md': 'Travel\nBook economy under 6 hours.\nKeep receipts.\nFile within 30 days.\n' },
  { 'out/summary.csv': 'trip,cost\nNYC,420\n', 'notes/draft.md': null },
];

async function review(owner: Pick<SessionWorkspaceService, 'diff'>): Promise<string> {
  const handler = expandChatCommand(async () => ({ kind: 'not_found' }), pipeshubCommandsFor(pipeshubPromptCommands(() => owner)));
  const req = { body: { query: '/review' }, params: { conversationId: 's1' }, headers: {}, user: { orgId: ORG, userId: OWNER } } as unknown as Request;
  const error = await new Promise<unknown>((resolve) => {
    void handler(req, { locals: {} } as unknown as Response, resolve);
  });
  expect(error).to.equal(undefined);
  return (req.body as { query: string }).query;
}

describe('SLASH2 C3 conformance on L, O, X and C', () => {
  const seen: Array<{ name: string; turn: WorkspaceDiff; reviewed: string }> = [];

  for (const [name, reasons] of EXECUTORS) {
    it(`${name}: /diff lists the turn's files; /undo restores the tree the next turn sees`, async () => {
      const w = workspaceWorld();
      await w.write(w.run('run-before'), BEFORE);
      const turnRun: HarnessRun = w.run('run-turn');
      for (const [i, reason] of reasons.entries()) {
        // A loop turn writes once, at its end; an engine's edits land over its commits.
        const edits = reasons.length === 1 ? Object.assign({}, ...TURN) : TURN[i];
        await w.write(turnRun, edits, reason);
      }
      const turn = await w.owner.diff(scope, 'turn');
      expect(turn.files.map((f) => [f.path, f.change])).to.deep.equal([
        ['notes/draft.md', 'deleted'],
        ['out/summary.csv', 'added'],
        ['out/travel_policy.md', 'modified'],
      ]);
      expect([turn.additions, turn.deletions]).to.deep.equal([4, 3]);
      const reviewed = await review(w.owner);
      seen.push({ name, turn, reviewed });

      await w.owner.restore(scope, { revision: turn.from, baseRevision: turn.head, idempotencyKey: `undo-${String(turn.from)}-on-${String(turn.head)}` });
      expect(await w.tree(w.run('run-next'))).to.deep.equal(BEFORE);
      expect((await w.owner.diff(scope, 'turn')).files).to.deep.equal([]);
    });
  }

  it('the four agree: the same files, diffs and /review text, byte for byte', () => {
    expect(seen).to.have.length(EXECUTORS.length);
    const [first, ...rest] = seen;
    for (const other of rest) {
      expect(other.turn.files, other.name).to.deep.equal(first!.turn.files);
      expect(other.reviewed, other.name).to.equal(first!.reviewed);
    }
  });
});
