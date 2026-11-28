/**
 * A carrier lets go of what only the move held (`Carrier.released`) once a rollback has made MongoDB the store again: on a real MongoDB
 * and PostgreSQL, a rollback that hands the store back calls it once, after the hand-over; a refused rollback does not.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { PG_BINS, haveStack } from '../../stores/pg-store-harness';
import { migrateOffline } from '../../../src/scripts/docstore-migrate/offline';
import { rollback } from '../../../src/scripts/docstore-migrate/reverse';
import { readMongoFence } from '../../../src/scripts/docstore-migrate/fence';
import { JobState } from '../../../src/scripts/docstore-migrate/state';
import { CARRIERS } from '../../../src/scripts/docstore-migrate/carry';
import type { Carrier } from '../../../src/scripts/docstore-migrate/carry/types';
import { identityDocs } from './fixtures/identity';
import { haveMongod } from './mongo-harness';
import { World, startWorld } from './world';
import { failSaveOnce } from './fail-save';
import { reset } from '../../../src/scripts/docstore-migrate/reset';
import { releaseCarried } from '../../../src/scripts/docstore-migrate/carry/run';
import { carryPg } from '../../../src/scripts/docstore-migrate/carry/steps';

const suite = haveStack && haveMongod ? describe : describe.skip;
const DB = 'es_released';
const log = (): void => undefined;

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(
    `docstore-migrate: carriers let go after a rollback (${bin})`,
    function () {
      this.timeout(300_000);
      let w: World;
      const released: string[] = [];
      const other: string[] = [];
      let failNext = false;
      const carrier: Carrier = {
        name: 'qa-released',
        reason: 'test: records when it is released',
        collections: [],
        fields: {},
        tables: ['identity.users'],
        write: async () => undefined,
        remove: async () => undefined,
        expected: () => [],
        actual: async () => new Map(),
        restore: async () => ({ written: 0, removed: 0 }),
        clear: async () => undefined,
        async released() {
          // after the hand-over: MongoDB's fence is active again (it is retired or read-only until then)
          released.push((await readMongoFence(w.rt)).mode);
          if (failNext) {
            failNext = false;
            throw new Error('not this time');
          }
        },
      };
      const ok: Carrier = {
        ...carrier,
        name: 'qa-released-ok',
        async released() {
          other.push('ok');
        },
      };
      const epoch = async (): Promise<unknown> =>
        (
          await w.stack.admin((c) =>
            c.query(
              'SELECT backend, epoch, readonly FROM platform.store_epoch',
            ),
          )
        ).rows;

      before(async () => {
        w = await startWorld(bin, 'migrate_released', DB);
        (CARRIERS as Carrier[]).push(carrier, ok);
      });
      after(async () => {
        for (const c of [carrier, ok]) {
          const i = CARRIERS.indexOf(c);
          if (i >= 0) (CARRIERS as Carrier[]).splice(i, 1);
        }
        await w?.stop();
      });
      beforeEach(async () => {
        released.length = 0;
        other.length = 0;
        failNext = false;
        await w.reseed(identityDocs());
        await w.truncate();
        await w.stack.admin((c) =>
          c.query(
            "UPDATE platform.store_epoch SET backend = 'mongo', readonly = true",
          ),
        );
      });

      it('a rollback that makes MongoDB the store again releases every carrier once, after the hand-over', async () => {
        expect(
          (
            await migrateOffline(w.rt, {
              dryRun: false,
              allowWriters: false,
              log,
            })
          ).ok,
        ).to.equal(true);
        const r = await rollback(w.rt, {
          reverse: false,
          allowWriters: false,
          log,
        });
        expect(r, r.message).to.include({ ok: true });
        expect(r.message).to.not.contain('did not let go');
        expect(released).to.deep.equal(['active']);
      });

      it('a carrier that does not let go keeps the job open; a rollback run again releases only it, and nothing else runs again', async () => {
        expect(
          (
            await migrateOffline(w.rt, {
              dryRun: false,
              allowWriters: false,
              log,
            })
          ).ok,
        ).to.equal(true);
        failNext = true;
        const r = await rollback(w.rt, {
          reverse: false,
          allowWriters: false,
          log,
        });
        expect(r, r.message).to.include({ ok: true, mode: 'no_pg_writes' });
        expect(r.message)
          .to.contain('qa-released: not this time')
          .and.contain('run `rollback` again');
        expect(
          (await JobState.current(w.rt))?.progress.release?.pending,
        ).to.deep.equal(['qa-released']);
        // no other command attaches to the open job
        const blocked = await migrateOffline(w.rt, {
          dryRun: false,
          allowWriters: false,
          log,
        }).catch((e: Error) => e);
        expect(
          String((blocked as Error).message ?? JSON.stringify(blocked)),
        ).to.contain('run `rollback` again first');
        const before = await epoch();
        const again = await rollback(w.rt, {
          reverse: false,
          allowWriters: false,
          log,
        });
        expect(again, again.message).to.include({ ok: true, mode: 'released' });
        expect(again.verify, 'no verify ran again').to.equal(undefined);
        expect(released).to.deep.equal(['active', 'active']);
        expect(
          other,
          'a carrier that let go is not released again',
        ).to.deep.equal(['ok']);
        expect(await epoch(), 'no hand-over ran again').to.deep.equal(before);
        expect(await JobState.current(w.rt), 'the job ended').to.equal(null);
        // nothing is pending any more: a third rollback is refused as before (MongoDB is the store)
        const third = await rollback(w.rt, {
          reverse: false,
          allowWriters: false,
          log,
        });
        expect(third).to.include({ ok: false, mode: 'refused' });
        expect(released).to.have.length(2);
      });

      const offlineMoved = async (): Promise<void> => {
        expect(
          (
            await migrateOffline(w.rt, {
              dryRun: false,
              allowWriters: false,
              log,
            })
          ).ok,
        ).to.equal(true);
      };
      const back = (failAt?: string): ReturnType<typeof rollback> => {
        const hooks = process.env.MIGRATE_TEST_HOOKS;
        process.env.MIGRATE_TEST_HOOKS = '1';
        return rollback(w.rt, {
          reverse: false,
          allowWriters: false,
          log,
          ...(failAt ? { failAt } : {}),
        }).finally(() => {
          if (hooks === undefined) delete process.env.MIGRATE_TEST_HOOKS;
          else process.env.MIGRATE_TEST_HOOKS = hooks;
        });
      };
      const pending = async (): Promise<string[] | undefined> =>
        (await JobState.current(w.rt))?.progress.release?.pending;

      it('a pending carrier this run does not know, or one with no step to let go, stays pending and is named; it ends only once it let go', async () => {
        await offlineMoved();
        failNext = true;
        expect((await back()).message).to.contain('qa-released: not this time');
        expect(await pending()).to.deep.equal(['qa-released']);
        // not in this run's carriers
        const i = CARRIERS.indexOf(carrier);
        (CARRIERS as Carrier[]).splice(i, 1);
        const unknown = await back().finally(() =>
          (CARRIERS as Carrier[]).push(carrier),
        );
        expect(unknown, unknown.message).to.include({
          ok: false,
          mode: 'released',
        });
        expect(unknown.message)
          .to.contain('qa-released')
          .and.contain('not a carrier of this run');
        expect(await pending(), 'still pending').to.deep.equal(['qa-released']);
        // known, but without the step
        const step = carrier.released;
        delete carrier.released;
        const none = await back().finally(() => (carrier.released = step));
        expect(none, none.message).to.include({ ok: false, mode: 'released' });
        expect(none.message)
          .to.contain('qa-released')
          .and.contain('has no step to let go');
        expect(await pending(), 'still pending').to.deep.equal(['qa-released']);
        const done = await back();
        expect(done, done.message).to.include({ ok: true, mode: 'released' });
        expect(released).to.deep.equal(['active', 'active']);
        expect(await JobState.current(w.rt)).to.equal(null);
      });

      it('a release whose job cannot be ended is ended by a rollback run again, without letting go twice', async () => {
        await offlineMoved();
        failNext = true;
        await back();
        const r = await back('release-finish');
        expect(r, r.message).to.include({ ok: false, mode: 'released' });
        expect(r.message)
          .to.contain('qa-released')
          .and.contain('run `rollback` again');
        expect(
          await pending(),
          'all let go, the job still to end',
        ).to.deep.equal([]);
        expect(released).to.deep.equal(['active', 'active']);
        const again = await back();
        expect(again, again.message).to.include({ ok: true, mode: 'released' });
        expect(released, 'not let go a third time').to.deep.equal([
          'active',
          'active',
        ]);
        expect(await JobState.current(w.rt), 'the job ended').to.equal(null);
      });

      it('a release that cannot be recorded leaves no open job without its list, and says so', async () => {
        await offlineMoved();
        const r = await back('release-record');
        expect(r, r.message).to.include({ ok: true, mode: 'no_pg_writes' });
        expect(r.message)
          .to.contain('could not be recorded')
          .and.contain('qa-released');
        const cur = await JobState.current(w.rt);
        expect(
          cur === null || (cur.progress.release?.pending ?? []).length > 0,
          'no running job has lost the list',
        ).to.equal(true);
      });

      it('reset refuses while a release holds the job and names it; --abandon-release records what was given up', async () => {
        await offlineMoved();
        failNext = true;
        await back();
        const refused = await reset(w.rt, { force: false });
        expect(refused, refused.message).to.include({ ok: false });
        expect(refused.message)
          .to.contain('qa-released')
          .and.contain('--abandon-release');
        expect(await pending(), 'the release is still held').to.deep.equal([
          'qa-released',
        ]);
        const given = await reset(w.rt, {
          force: false,
          abandonRelease: true,
        } as never);
        expect(given, given.message).to.include({ ok: true });
        expect(given.message).to.contain('qa-released');
        const last = await JobState.latest(w.rt);
        expect(last?.status).to.equal('cancelled');
        expect(
          (last?.progress as Record<string, any>).releaseAbandoned?.pending,
        ).to.deep.equal(['qa-released']);
      });

      it('no carrier lets go when the release cannot be recorded first', async () => {
        await offlineMoved();
        const undo = failSaveOnce((p) => p.release !== undefined);
        const r = await back().finally(undo);
        expect(r, r.message).to.include({ ok: true, mode: 'no_pg_writes' });
        expect(released, 'no carrier ran').to.deep.equal([]);
        expect(other, 'no carrier ran').to.deep.equal([]);
        expect(r.message)
          .to.contain('did not start')
          .and.contain('qa-released');
        expect(
          await JobState.current(w.rt),
          'no running job without its list',
        ).to.equal(null);
      });

      it('a retry that only ends the job does not save the progress first', async () => {
        await offlineMoved();
        failNext = true;
        await back();
        await back('release-finish');
        expect(await pending()).to.deep.equal([]);
        const undo = failSaveOnce((p) => p.release?.pending.length === 0);
        const r = await back().finally(undo);
        expect(r, r.message).to.include({ ok: true, mode: 'released' });
        expect(await JobState.current(w.rt), 'the job ended').to.equal(null);
      });

      it('every carrier with the step lets go again after it already has (a retry may call it twice)', async () => {
        await offlineMoved();
        expect((await back()).ok).to.equal(true);
        const withStep = CARRIERS.filter((c) => c.released !== undefined);
        expect(withStep.length).to.be.greaterThan(0);
        expect(await releaseCarried(carryPg(w.rt), withStep)).to.deep.equal([]);
        expect(await releaseCarried(carryPg(w.rt), withStep)).to.deep.equal([]);
      });

      it('a refused rollback releases nothing', async () => {
        expect(
          (
            await migrateOffline(w.rt, {
              dryRun: false,
              allowWriters: false,
              log,
            })
          ).ok,
        ).to.equal(true);
        await w.stack.admin((c) =>
          c.query(
            'DELETE FROM identity.users WHERE id = (SELECT id FROM identity.users ORDER BY id LIMIT 1)',
          ),
        );
        const r = await rollback(w.rt, {
          reverse: false,
          allowWriters: false,
          log,
        });
        expect(r.ok).to.equal(false);
        expect(released).to.deep.equal([]);
        expect(other).to.deep.equal([]);
      });
    },
  );
}
