/**
 * MA1 (QA37-K1, Node half): `child.spawned.data.model`. What Node writes for a
 * role-assigned child is the contract's shape, an event without the member is
 * as before, and the schema's examples and invalid fixtures answer the same in
 * zod as in JSON Schema and pydantic (`tests/unit/contracts_harness`).
 */
import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import { childSpawnedEventSchema } from '../../src/modules/agent-harness/contracts/generated/core/events/child-spawned';
import { childSpawnedEvent } from '../../src/modules/agent-harness/domain/child-runs';
import { validateJournalAppend } from '../../src/modules/agent-harness/domain/journal';

const EVENTS = join(__dirname, '../../../../../contracts/harness/core/events');
const SCHEMA = JSON.parse(readFileSync(join(EVENTS, 'child.spawned.schema.json'), 'utf8')) as { examples: Array<{ data: Record<string, unknown> }> };
const INVALID = JSON.parse(readFileSync(join(EVENTS, 'child.spawned.invalid.json'), 'utf8')) as Array<{ description: string; instance: unknown }>;
const DIGEST = `sha256:${'a'.repeat(64)}`;

const appended = (event: ReturnType<typeof childSpawnedEvent>, writer: 'node' | 'worker' = 'node') => ({
  orgId: 'org_1',
  sessionId: 'ses_1',
  streamId: 'ses_1',
  runId: 'run_1',
  writer,
  events: [event],
});

describe('MA1 `child.spawned.data.model` (QA37-K1)', () => {
  it('QA37-K1: the schema’s examples validate, one without `model` and one with it', () => {
    expect(SCHEMA.examples.map((example) => 'model' in example.data)).to.deep.equal([false, true]);
    for (const example of SCHEMA.examples) expect(childSpawnedEventSchema.safeParse(example).success).to.equal(true);
  });

  for (const fixture of INVALID) {
    it(`QA37-K1: invalid: ${fixture.description}`, () => {
      expect(childSpawnedEventSchema.safeParse(fixture.instance).success).to.equal(false);
    });
  }

  it('QA37-K1: Node writes `model` only for a role-assigned child, and exactly its three members', () => {
    const plain = childSpawnedEvent('run_c1', 't1.0', DIGEST, 't1');
    expect(plain.data).to.deep.equal({ childRunId: 'run_c1', invocationId: 't1.0', taskId: 't1', grantDigest: DIGEST });
    const role = childSpawnedEvent('run_c2', 't2.0', DIGEST, undefined, {
      provider: 'anthropic',
      model: 'claude-haiku-4-5',
      role: 'researcher',
      // What the spawn knows beside the contract's members never reaches the journal.
      ...({ modelKey: 'mk-b' } as object),
    });
    expect(role.data).to.deep.equal({
      childRunId: 'run_c2',
      invocationId: 't2.0',
      grantDigest: DIGEST,
      model: { provider: 'anthropic', model: 'claude-haiku-4-5', role: 'researcher' },
    });
    expect(() => validateJournalAppend(appended(plain))).not.to.throw();
    expect(() => validateJournalAppend(appended(role))).not.to.throw();
  });

  it('QA37-K1: a role key outside the pattern never reaches the journal', () => {
    const bad = childSpawnedEvent('run_c2', 't2.0', DIGEST, undefined, { provider: 'openai', model: 'gpt-x', role: 'Researcher One' });
    expect(() => validateJournalAppend(appended(bad))).to.throw();
  });
});
