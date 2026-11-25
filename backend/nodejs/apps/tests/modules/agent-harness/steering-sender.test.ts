/**
 * BUGS-B (SYNC4 §10, steering by collaborators): a message joins a running
 * run only from the run's own actor. The worker renders a steered message's
 * attachments and searches its knowledge as the run's actor and pin, so a
 * steer by anyone else would be read with the actor's access instead of the
 * sender's. Admission routes another participant's message to a next run
 * (`waitsAsNextRun`, NR-1/NR-3), which runs as its sender; the steer write
 * itself refuses a sender who is not the actor, so no store path can widen.
 */
import { expect } from 'chai';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import { waitsAsNextRun } from '../../../src/modules/agent-harness/domain/next-runs';
import { assertSteeredByActor } from '../../../src/modules/agent-harness/domain/steering';

describe('agent-harness steering sender (BUGS-B)', () => {
  it('lets the run\'s own actor steer it', () => {
    expect(() => assertSteeredByActor({ actorId: 'user-a' }, 'user-a')).not.to.throw();
  });

  it('refuses a steer by anyone but the actor at the write itself', () => {
    expect(() => assertSteeredByActor({ actorId: 'user-a' }, 'user-b'))
      .to.throw(HarnessConflictError)
      .with.property('code', 'RUN_ACTIVE');
  });

  it('agrees with admission: exactly the messages that would not steer wait as a next run', () => {
    for (const sender of ['user-a', 'user-b']) {
      const waits = waitsAsNextRun({ actorId: 'user-a' }, { actorId: sender, nextRunId: undefined });
      const steers = (() => {
        try {
          assertSteeredByActor({ actorId: 'user-a' }, sender);
          return true;
        } catch {
          return false;
        }
      })();
      expect(steers).to.equal(!waits);
    }
  });
});
