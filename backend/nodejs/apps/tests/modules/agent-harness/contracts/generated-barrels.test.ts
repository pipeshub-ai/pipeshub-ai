import { expect } from 'chai';
import { ZodType } from 'zod/v3';
import * as core from '../../../../src/modules/agent-harness/contracts/generated/core';
import * as pipeshub from '../../../../src/modules/agent-harness/contracts/generated/ext/pipeshub';
import { overlayEventTypes } from '../../../support/harness/contracts';

/**
 * What a reader of a generated barrel gets: every `…Schema` export is a zod
 * schema, and the barrel's event schemas are exactly the ones its
 * `EVENT_SCHEMAS` catalog dispatches on (the same objects, none missing, none
 * extra), so a validator picked by name and one picked by event type agree.
 */
const barrels = { core, pipeshub } as const;
const groups: Record<keyof typeof barrels, string> = { core: 'core', pipeshub: 'ext/pipeshub' };

const schemaExports = (barrel: Record<string, unknown>): Array<[string, unknown]> =>
  Object.keys(barrel)
    .filter((name) => name.endsWith('Schema'))
    .map((name) => [name, barrel[name]]);

describe('agent-harness contracts: the generated barrels', () => {
  for (const [label, barrel] of Object.entries(barrels)) {
    describe(label, () => {
      const exported = schemaExports(barrel as Record<string, unknown>);

      it('exports only zod schemas under …Schema names', () => {
        expect(exported.length).to.be.greaterThan(10);
        for (const [name, schema] of exported) {
          expect(schema, name).to.be.instanceOf(ZodType);
        }
      });

      it('exports by name exactly the event schemas its catalog maps by type', () => {
        // An edition's overlay adds event types to this catalog; its own barrel exports their schemas.
        const added = new Set(overlayEventTypes(groups[label as keyof typeof barrels]));
        const catalog = Object.entries(barrel.EVENT_SCHEMAS)
          .filter(([type]) => !added.has(type))
          .map(([, schema]) => schema);
        const eventExports = exported
          .filter(([name]) => name.endsWith('EventSchema'))
          .map(([, schema]) => schema);
        expect(eventExports).to.have.length(catalog.length);
        for (const schema of catalog) {
          expect(eventExports).to.include(schema);
        }
        expect(Object.keys(barrel.EVENT_SCHEMAS)).to.include.members([...added]);
      });

      it('names a writer for every event type it validates', () => {
        expect(Object.keys(barrel.EVENT_WRITERS).sort()).to.deep.equal(
          Object.keys(barrel.EVENT_SCHEMAS).sort(),
        );
      });
    });
  }
});
