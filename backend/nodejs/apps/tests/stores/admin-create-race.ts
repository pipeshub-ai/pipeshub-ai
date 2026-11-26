/**
 * The real `UserController.createUser` against a real user store, parked after the account's write has committed and
 * before its undo, for the store suites that race that undo against a demotion or a promotion. Only the user store is
 * real: groups and events are fakes, since neither decides a role. The everyone-group write is the gate: it is the
 * first step after the write (no password is sent, so there is no credential step), and once released it fails, so the
 * create takes the account back.
 */
import type { UserController } from '../../src/modules/user_management/controller/users.controller';
import { setGroupRepository } from '../../src/libs/repositories/group';
import { raceController } from './admin-invite-race';
import { gate } from './admin-delete-race';

/** What the create answered: `failed` carries the error it passed to `next`. */
export type CreateOutcome = { status: 'created' } | { status: 'failed'; error: unknown };

/** A create parked after its write: `parked` resolves there, `fail()` makes its group write fail, so the undo runs. */
export interface ParkedCreate {
  parked: Promise<void>;
  fail(): void;
  done: Promise<CreateOutcome>;
}

export interface CreateRace {
  start(orgId: string, actorId: string, email: string, role: 'admin' | 'member'): ParkedCreate;
  /** Puts the default stores back. */
  restore(): void;
}

/** Installs the fake group store with the gate. One create at a time. */
export function createRace(rsAvailable: boolean): CreateRace {
  let current: { park: () => void; release: Promise<void> } | undefined;
  setGroupRepository({
    addToEveryone: async () => {
      const c = current;
      if (!c) return;
      c.park();
      await c.release;
      throw new Error('everyone-group write failed');
    },
    removeFromEveryone: async () => undefined,
  } as never);
  const controller = raceController(rsAvailable) as unknown as { createUser: UserController['createUser'] };

  return {
    start(orgId, actorId, email, role) {
      const parked = gate();
      const release = gate();
      current = { park: parked.open, release: release.opened };
      let outcome: CreateOutcome = { status: 'failed', error: new Error('createUser answered nothing') };
      const done = controller
        .createUser(
          {
            user: { orgId, userId: actorId, _id: actorId },
            body: { email, fullName: 'New User', role },
            ip: '127.0.0.1',
            headers: {},
          } as never,
          { status: () => ({ json: () => { outcome = { status: 'created' }; } }) } as never,
          ((error?: unknown) => { outcome = { status: 'failed', error }; }) as never,
        )
        .then(() => outcome);
      return { parked: parked.opened, fail: release.open, done };
    },
    restore() {
      current = undefined;
      setGroupRepository();
    },
  };
}
