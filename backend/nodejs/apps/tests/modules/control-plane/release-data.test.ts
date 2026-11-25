/** CPD2-31: the release data Node compiles is the committed contract data, item for item. */
import { expect } from 'chai';
import { readdirSync, readFileSync } from 'fs';
import { join } from 'path';
import { CONTROL_PLANE_MODULES } from '../../../src/modules/control-plane/composition';
import { CONTROL_PLANE_CONTRACTS } from './support/paths';

describe('control-plane release data (CPD2-31)', () => {
  const files = readdirSync(join(CONTROL_PLANE_CONTRACTS, 'catalog')).filter(
    (name) => name.endsWith('.json'),
  );
  const committed = new Map(
    files.map((name) => {
      const document = JSON.parse(
        readFileSync(join(CONTROL_PLANE_CONTRACTS, 'catalog', name), 'utf8'),
      ) as {
        kind: string;
        items: unknown[];
      };
      return [document.kind, document.items] as const;
    }),
  );

  it('has one contract file per production module', () => {
    expect([...committed.keys()].sort()).to.deep.equal(
      CONTROL_PLANE_MODULES.map((m) => m.kind).sort(),
    );
  });

  for (const module of CONTROL_PLANE_MODULES) {
    it(`${module.kind}: Node's items equal the contract file`, () => {
      expect(module.catalog.releaseItems()).to.deep.equal(
        committed.get(module.kind),
      );
    });
  }
});
