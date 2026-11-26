/**
 * SEC-CR-07 (G-37): a secret a person pastes into a message is removed on the
 * server before any copy is kept. The rules are the shared vector's
 * `pastedInput` section, read here from the contract file so a rule without an
 * example, or a negative the rules start to match, fails Node as it fails
 * Python and the frontend.
 */
import { expect } from 'chai';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import express from 'express';
import { AddressInfo } from 'node:net';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { SECRET_MASK } from '../../../src/modules/agent-harness/contracts/vectors/token-patterns';
import { findPastedSecrets, redactPastedSecrets } from '../../../src/modules/agent-harness/domain/pasted-secrets';
import { profileExecutor } from '../../../src/modules/agent-harness/domain/run-spec';
import {
  SECRETS_REMOVED_HEADER,
  removePastedSecrets,
} from '../../../src/modules/agent-harness/transport/pasted-secret-guard';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { testModelAccess } from './support/model-access';
import { HARNESS_ENABLED } from './support/rollout';
import { VECTOR_EXAMPLES } from './support/canary-scan';
import { FakeEngineCeilings } from './support/org-policy-fakes';

interface Entry {
  kind: string;
  examples: string[][];
}

const raw = JSON.parse(
  readFileSync(
    join(__dirname, '..', '..', '..', '..', '..', '..', 'contracts', 'harness', 'vectors', 'token-patterns.json'),
    'utf8',
  ),
) as {
  pastedInput: { detectors: Entry[]; assigned: Entry; contextual: Entry; negatives: string[] };
};
const pasted = raw.pastedInput;
const PASTED_EXAMPLES = [...pasted.detectors, pasted.assigned, pasted.contextual].flatMap((entry) =>
  entry.examples.map((parts, index) => ({ id: `${entry.kind}-${index}`, kind: entry.kind, text: parts.join('') })),
);

describe('SEC-CR-07 pasted secrets: the shared rules', () => {
  for (const example of PASTED_EXAMPLES) {
    it(`finds and masks ${example.id}`, () => {
      const found = findPastedSecrets(example.text);
      expect(found.map((f) => f.kind)).to.deep.equal([example.kind]);
      const secret = example.text.slice(found[0]!.start, found[0]!.end);
      const redacted = redactPastedSecrets(example.text);
      expect(redacted.text).to.include(SECRET_MASK).and.not.include(secret);
      expect(redacted.kinds).to.deep.equal([example.kind]);
    });
  }

  for (const example of VECTOR_EXAMPLES) {
    it(`counts the token vector's ${example.id} as pasted`, () => {
      const redacted = redactPastedSecrets(`use this one: ${example.secret} thanks`);
      expect(redacted.kinds).to.have.length(1);
      expect(redacted.text).to.not.include(example.secret);
    });
  }

  for (const text of pasted.negatives) {
    it(`leaves ordinary text alone: ${text.slice(0, 40)}`, () => {
      expect(findPastedSecrets(text)).to.deep.equal([]);
      expect(redactPastedSecrets(text)).to.deep.equal({ text, kinds: [] });
    });
  }
});

describe('SEC-CR-07 pasted secrets: the server removes them', () => {
  const secret = PASTED_EXAMPLES.find((e) => e.kind === 'stripe_key')!.text;

  it('the route guard rewrites a message and a denial reason before the handler, and names only the kinds', async () => {
    const seen: unknown[] = [];
    const app = express();
    app.use(express.json());
    app.use(removePastedSecrets());
    app.post('/stream', (req, res) => {
      seen.push(req.body);
      res.json({ ok: true });
    });
    const server = app.listen(0, '127.0.0.1');
    await new Promise((resolve) => server.once('listening', resolve));
    const url = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}/stream`;
    const post = (body: unknown) =>
      fetch(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
    try {
      const rated = await post({ isHelpful: false, comments: { negative: `it should use ${secret}`, positive: 'fine' } });
      expect(rated.headers.get(SECRETS_REMOVED_HEADER)).to.equal('stripe_key');
      expect(seen.shift()).to.deep.equal({ isHelpful: false, comments: { negative: `it should use ${SECRET_MASK}`, positive: 'fine' } });
      const response = await post({ query: `deploy with ${secret} please`, reason: `no, use ${secret}`, modelKey: 'm' });
      expect(response.status).to.equal(200);
      expect(response.headers.get(SECRETS_REMOVED_HEADER)).to.equal('stripe_key');
      expect(JSON.stringify(seen)).to.not.include(secret);
      expect(seen[0]).to.deep.equal({
        query: `deploy with ${SECRET_MASK} please`,
        reason: `no, use ${SECRET_MASK}`,
        modelKey: 'm',
      });
      const clean = await post({ query: 'summarise the Q3 plan' });
      expect(clean.headers.get(SECRETS_REMOVED_HEADER)).to.equal(null);
    } finally {
      await new Promise((resolve) => server.close(resolve));
    }
    expect(seen[1]).to.deep.equal({ query: 'summarise the Q3 plan' });
  });

  it('admission removes it on every path, so the run input, the title and the journal never hold it', async () => {
    let now = 1_000;
    const store = new InMemoryExecutionStore({ now: () => now });
    const service = new RunAdmissionService({
      store,
      rollout: {
        decide: async () => ({ ...HARNESS_ENABLED, harnessMajor: 2 as const }),
        decideForOrg: async () => ({ ...HARNESS_ENABLED, harnessMajor: 2 as const, harnessOn: true, resumeInFlightRuns: true }),
      },
      clock: { now: () => now },
      agentAccess: grantingAccess((agentKey) => agentDefinition({ agentKey })),
      logger: { debug: () => undefined, info: () => undefined, warn: () => undefined, error: () => undefined },
      executors: profileExecutor,
      engineProfiles: new FakeEngineCeilings(),
      modelAccess: testModelAccess(),
      roles: { roleOf: async () => 'owner' },
    });
    const outcome = await service.submit({
      orgId: 'org-1',
      actorId: 'user-1',
      agentKey: 'agent-1',
      authorization: 'Bearer t',
      idempotencyKey: `k-${String(now++)}`,
      deadlineMs: 60_000,
      input: { query: `my key is ${secret}`, recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
    });
    if (outcome.route !== 'durable') return expect.fail('expected a durable run');
    expect(outcome.admission.run.input?.query).to.equal(`my key is ${SECRET_MASK}`);
    expect(JSON.stringify(store)).to.not.include(secret);
  });
});
