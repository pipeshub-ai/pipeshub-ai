/**
 * SYNCMAIN3 (#4492): a durable run's input is held to the filters a chat turn
 * accepts. The run admission body and the chat turn's run input share the
 * enterprise-search `filters` schema, so a key the chat routes refuse cannot
 * reach retrieval through `/run`.
 */
import { expect } from 'chai';
import { REQUEST_FILTER_KEYS } from '../../../src/modules/enterprise_search/validators/es_validators';
import {
  admissionBodySchema,
  runInputSchema,
} from '../../../src/modules/agent-harness/transport/schemas';

const APP = '7f6d1c8e-2a4b-4c3d-9e8f-0a1b2c3d4e5f';

describe('SYNCMAIN3 durable run filters (#4492)', () => {
  it('accepts the public keys', () => {
    const parsed = runInputSchema.parse({
      query: 'q',
      filters: { apps: [APP], recordGroups: [APP], records: [APP] },
    });
    expect(parsed.filters).to.deep.equal({
      apps: [APP],
      recordGroups: [APP],
      records: [APP],
    });
  });

  it('defaults to no filters', () => {
    expect(runInputSchema.parse({ query: 'q' }).filters).to.deep.equal({});
  });

  for (const key of ['departments', 'topics', 'strictScope', 'connectorIds', 'nope']) {
    it(`refuses ${key}, as the chat routes do`, () => {
      const result = admissionBodySchema.safeParse({
        query: 'q',
        idempotencyKey: 'k-1',
        filters: { [key]: ['x'] },
      });
      expect(result.success).to.equal(false);
      const message = result.success ? '' : result.error.issues[0]?.message;
      expect(message).to.contain('Unknown filter key(s)');
      for (const allowed of REQUEST_FILTER_KEYS) expect(message).to.contain(allowed);
    });
  }

  it('masks a token pasted as a key in the refusal (QA-14)', () => {
    const token = `ghp_${'a1B2c3D4e5'.repeat(4)}`;
    const result = runInputSchema.safeParse({ query: 'q', filters: { [token]: ['x'] } });
    expect(result.success).to.equal(false);
    const message = result.success ? '' : result.error.issues[0]?.message;
    expect(message).to.contain('Unknown filter key(s)');
    expect(message).not.to.contain(token);
  });

  it('refuses a mistyped id list', () => {
    const result = runInputSchema.safeParse({
      query: 'q',
      filters: { apps: 'not-a-list' },
    });
    expect(result.success).to.equal(false);
  });
});
