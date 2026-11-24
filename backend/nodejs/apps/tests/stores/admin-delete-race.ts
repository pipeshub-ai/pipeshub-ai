/**
 * The real `UserController.deleteUser` against a real user store, parked between its member check and its removal, for
 * the store suites that race it against a promotion. Only the user and credential stores are real: activity, groups,
 * projects, the OAuth hand-over and events are no-ops, since none of them decides a role. The group removal is the
 * gate: it is the first step after the member check, so everything raced in while it is parked lands before
 * `markDeleted`.
 */
import sinon from 'sinon';
import type { UserController } from '../../src/modules/user_management/controller/users.controller';
import { setGroupRepository } from '../../src/libs/repositories/group';
import { setActivityRepository } from '../../src/libs/repositories/activity';
import { ProjectService } from '../../src/modules/projects/services/project.service';
import { raceController } from './admin-invite-race';

/** What the delete answered: `refused` carries the error it passed to `next`. */
export type DeleteOutcome = { status: 'deleted' } | { status: 'refused'; error: unknown };

/** A delete parked after its member check: `parked` resolves there, `release()` lets it go on to the removal. */
export interface ParkedDelete {
  parked: Promise<void>;
  release(): void;
  done: Promise<DeleteOutcome>;
}

export interface DeleteRace {
  start(orgId: string, actorId: string, targetId: string): ParkedDelete;
  /** Puts the default stores back. */
  restore(): void;
}

/** A promise the test opens when it chooses. */
export const gate = (): { opened: Promise<void>; open: () => void } => {
  let open!: () => void;
  const opened = new Promise<void>((resolve) => {
    open = resolve;
  });
  return { opened, open };
};

/** Installs the no-op side stores with the gate. One delete at a time. */
export function deleteRace(rsAvailable: boolean): DeleteRace {
  let current: { park: () => void; release: Promise<void> } | undefined;
  setGroupRepository({
    removeFromAll: async () => {
      const c = current;
      if (!c) return;
      c.park();
      await c.release;
    },
  } as never);
  setActivityRepository({ record: async () => undefined } as never);
  const projectStubs = [
    sinon.stub(ProjectService, 'findProjectsWithLinkedKbForUser').resolves([]),
    sinon.stub(ProjectService, 'removeUserFromAllProjects').resolves(),
  ];
  const controller = raceController(rsAvailable) as unknown as {
    deleteUser: UserController['deleteUser'];
    handOverOAuthOfDeletedUser: () => Promise<void>;
  };
  controller.handOverOAuthOfDeletedUser = async () => undefined;

  return {
    start(orgId, actorId, targetId) {
      const parked = gate();
      const release = gate();
      current = { park: parked.open, release: release.opened };
      let outcome: DeleteOutcome = { status: 'refused', error: new Error('deleteUser answered nothing') };
      const done = controller
        .deleteUser(
          { user: { orgId, userId: actorId, _id: actorId }, params: { id: targetId }, ip: '127.0.0.1', headers: {} } as never,
          { json: () => { outcome = { status: 'deleted' }; } } as never,
          ((error?: unknown) => { outcome = { status: 'refused', error }; }) as never,
        )
        .then(() => outcome);
      return { parked: parked.opened, release: release.open, done };
    },
    restore() {
      current = undefined;
      for (const stub of projectStubs) stub.restore();
      setGroupRepository();
      setActivityRepository();
    },
  };
}

/** Polls `seen` until it is true; fails loudly after `ms` instead of hanging the suite. */
export async function waitUntil(what: string, seen: () => Promise<boolean>, ms = 5000): Promise<void> {
  const deadline = Date.now() + ms;
  while (!(await seen())) {
    if (Date.now() > deadline) throw new Error(`timed out waiting until ${what}`);
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
}
