import { expect } from 'chai';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { componentSubkey } from '../../../src/modules/agent-harness/domain/component-keys';
import {
  bindingMessage,
  bindingProof,
  brokerBindingKeys,
  ringBindingKey,
  verifyBindingProof,
} from '../../../src/modules/agent-harness/domain/sandbox-binding';

const VECTORS = JSON.parse(
  readFileSync(
    path.resolve(__dirname, '../../../../../../contracts/secret-key/sandbox-binding.json'),
    'utf8',
  ),
) as {
  brokerComponentKey: string;
  brokerBindingKey: string;
  ringSecret: string;
  ringBindingKey: string;
  cases: {
    orgId: string;
    runId: string;
    attemptId: string;
    message: string;
    brokerProof: string;
    ringProof: string;
  }[];
};

const STRONG = 'ring-current-test-value';
const OTHER = 'ring-previous-test-value';

describe('R38IDENT-FU-7: the broker’s sandbox binding proof', () => {
  it('derives, frames and signs as Python does (contracts/secret-key/sandbox-binding.json)', () => {
    const broker = componentSubkey('sandboxBroker', VECTORS.brokerComponentKey, 'binding');
    expect(broker.toString('hex')).to.equal(VECTORS.brokerBindingKey);
    const ring = ringBindingKey(VECTORS.ringSecret);
    expect(ring.toString('hex')).to.equal(VECTORS.ringBindingKey);
    for (const c of VECTORS.cases) {
      const binding = { orgId: c.orgId, runId: c.runId, attemptId: c.attemptId };
      expect(bindingMessage(binding).toString('hex')).to.equal(c.message);
      expect(bindingProof(broker, binding)).to.equal(c.brokerProof);
      expect(bindingProof(ring, binding)).to.equal(c.ringProof);
      expect(verifyBindingProof(c.brokerProof, binding, [ring, broker])).to.equal(true);
    }
  });

  it('no two vectors share a proof: each binds its own org, run and attempt', () => {
    const proofs = VECTORS.cases.map((c) => c.brokerProof);
    expect(new Set(proofs).size).to.equal(proofs.length);
  });

  it('refuses a proof for any other field, a foreign key, and malformed spellings', () => {
    const key = Buffer.alloc(32, 3);
    const binding = { orgId: 'org-1', runId: 'run-1', attemptId: 'run-1.a1' };
    const proof = bindingProof(key, binding);
    expect(verifyBindingProof(proof, binding, [key])).to.equal(true);
    expect(verifyBindingProof(proof, { ...binding, orgId: 'org-2' }, [key])).to.equal(false);
    expect(verifyBindingProof(proof, { ...binding, runId: 'run-2' }, [key])).to.equal(false);
    expect(verifyBindingProof(proof, { ...binding, attemptId: 'run-1.a2' }, [key])).to.equal(false);
    // Unicode lookalikes and case are other ids, not the same one.
    expect(verifyBindingProof(proof, { ...binding, orgId: 'ORG-1' }, [key])).to.equal(false);
    expect(verifyBindingProof(proof, { ...binding, orgId: 'org‑1' }, [key])).to.equal(false);
    expect(verifyBindingProof(proof, binding, [Buffer.alloc(32, 4)])).to.equal(false);
    expect(verifyBindingProof(proof, binding, [])).to.equal(false);
    const mac = proof.slice(3);
    for (const bad of [
      '',
      'v1.',
      `v1.${mac}=`,
      `v1.${mac}A`,
      `v1.${mac.slice(1)}`,
      `V1.${mac}`,
      `v1.${Buffer.from(mac, 'base64url').toString('base64')}`,
      `v1.${Buffer.from(mac, 'base64url').toString('hex')}`,
      `${proof}\n`,
      `v1.${'A'.repeat(43)}`,
      'v1.' + 'é'.repeat(43),
    ]) {
      expect(verifyBindingProof(bad, binding, [key]), JSON.stringify(bad)).to.equal(false);
    }
  });

  it('refuses a second spelling of the same bytes (the last character’s spare bits)', () => {
    const key = Buffer.alloc(32, 5);
    const binding = { orgId: 'o', runId: 'r', attemptId: 'a' };
    const proof = bindingProof(key, binding);
    const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
    const last = proof.at(-1)!;
    const sameBytes = Array.from(alphabet).filter(
      (ch) =>
        ch !== last &&
        Buffer.from(`${proof.slice(3, -1)}${ch}`, 'base64url').equals(Buffer.from(proof.slice(3), 'base64url')),
    );
    expect(sameBytes.length).to.be.greaterThan(0);
    for (const ch of sameBytes) {
      expect(verifyBindingProof(`${proof.slice(0, -1)}${ch}`, binding, [key])).to.equal(false);
    }
  });

  it('accepts the broker’s component key and either ring member, so a rotation in flight keeps sandboxes served', () => {
    const binding = { orgId: 'org-1', runId: 'run-1', attemptId: 'run-1.a1' };
    const keys = brokerBindingKeys(
      () => ({ current: STRONG, other: { role: 'previous', secret: OTHER } }),
      () => VECTORS.brokerComponentKey,
    );
    expect(keys).to.have.length(3);
    expect(verifyBindingProof(bindingProof(componentSubkey('sandboxBroker', VECTORS.brokerComponentKey, 'binding'), binding), binding, keys)).to.equal(true);
    expect(verifyBindingProof(bindingProof(ringBindingKey(STRONG), binding), binding, keys)).to.equal(true);
    expect(verifyBindingProof(bindingProof(ringBindingKey(OTHER), binding), binding, keys)).to.equal(true);
    // Once the previous key is retired, what it signed is no longer honoured.
    const retired = brokerBindingKeys(() => ({ current: STRONG, other: null }), () => null);
    expect(verifyBindingProof(bindingProof(ringBindingKey(OTHER), binding), binding, retired)).to.equal(false);
  });

  it('the proxy’s own key subkeys open nothing: its config and redis subkeys are not the binding key', () => {
    const binding = { orgId: 'org-1', runId: 'run-1', attemptId: 'run-1.a1' };
    const keys = brokerBindingKeys(() => null, () => VECTORS.brokerComponentKey);
    for (const purpose of ['config', 'redis'] as const) {
      for (const component of ['egressProxy', 'sandboxBroker'] as const) {
        const forged = bindingProof(componentSubkey(component, VECTORS.brokerComponentKey, purpose), binding);
        expect(verifyBindingProof(forged, binding, keys)).to.equal(false);
      }
    }
    const asProxy = bindingProof(componentSubkey('egressProxy', VECTORS.brokerComponentKey, 'binding'), binding);
    expect(verifyBindingProof(asProxy, binding, keys)).to.equal(false);
  });
});
