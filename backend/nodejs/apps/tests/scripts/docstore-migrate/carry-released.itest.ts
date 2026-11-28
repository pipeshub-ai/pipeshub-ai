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
import { CARRIERS } from '../../../src/scripts/docstore-migrate/carry';
import type { Carrier } from '../../../src/scripts/docstore-migrate/carry/types';
import { identityDocs } from './fixtures/identity';
import { haveMongod } from './mongo-harness';
import { World, startWorld } from './world';

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
        },
      };

      before(async () => {
        w = await startWorld(bin, 'migrate_released', DB);
        (CARRIERS as Carrier[]).push(carrier);
      });
      after(async () => {
        const i = CARRIERS.indexOf(carrier);
        if (i >= 0) (CARRIERS as Carrier[]).splice(i, 1);
        await w?.stop();
      });
      beforeEach(async () => {
        released.length = 0;
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
      });
    },
  );
}
