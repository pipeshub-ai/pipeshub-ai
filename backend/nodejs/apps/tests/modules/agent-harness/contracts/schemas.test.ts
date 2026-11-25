import { expect } from 'chai';
import * as core from '../../../../src/modules/agent-harness/contracts/generated/core';
import * as pipeshub from '../../../../src/modules/agent-harness/contracts/generated/ext/pipeshub';
import { loadContractSchemas, zodFor } from '../../../support/harness/contracts';

const schemas = loadContractSchemas();

describe('agent-harness contracts: generated zod schemas', () => {
  it('covers every instance schema', () => {
    expect(schemas.length).to.be.greaterThan(40);
  });

  describe('C1 examples are accepted', () => {
    for (const schema of schemas) {
      it(schema.path, () => {
        const zod = zodFor(schema.path);
        for (const example of schema.examples) {
          const result = zod.safeParse(example);
          expect(result.success, JSON.stringify(result.error?.issues.slice(0, 2))).to.equal(true);
        }
      });
    }
  });

  describe('C1 invalid fixtures are rejected', () => {
    for (const schema of schemas) {
      it(schema.path, () => {
        const zod = zodFor(schema.path);
        expect(schema.invalid).to.not.be.empty;
        for (const fixture of schema.invalid) {
          expect(zod.safeParse(fixture.instance).success, fixture.description).to.equal(false);
        }
      });
    }
  });

  it('event catalogs carry the writer allowlist from the schemas', () => {
    const events = schemas.filter((s) => s.eventType !== undefined);
    const catalog: Record<string, readonly string[]> = { ...core.EVENT_WRITERS, ...pipeshub.EVENT_WRITERS };
    expect(Object.keys(catalog).sort()).to.deep.equal(events.map((s) => s.eventType).sort());
    for (const event of events) {
      expect(catalog[event.eventType!]).to.deep.equal(event.writers);
      expect({ ...core.EVENT_SCHEMAS, ...pipeshub.EVENT_SCHEMAS }[event.eventType!]).to.equal(zodFor(event.path));
    }
    expect(core.EVENT_WRITERS['interaction.resolved']).to.deep.equal(['node']);
    expect(core.EVENT_WRITERS['tool.intent']).to.deep.equal(['worker']);
  });
});
