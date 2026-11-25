import { createHmac, hkdfSync, timingSafeEqual } from 'node:crypto';
import type { SecretKeyRing } from '../../../libs/encryptor/secret-key-ring';
import { componentKey, componentSubkey } from './component-keys';

/**
 * R38IDENT-FU-7 (ADR-CRED-01 D6): the sandbox broker vouches for which run attempt each sandbox it starts serves.
 * When it registers a sandbox's egress binding with the proxy, it attaches a MAC over (org, run, attempt) under a key
 * the proxy never holds; the proxy forwards it when it asks for that run's gateway token, and Node issues only
 * against a proof it verifies. The proxy's own credential alone therefore names no run: a foothold in the proxy mints
 * tokens only for attempts whose sandboxes the broker put behind it. Python's twin is
 * `app/harness/sandbox/binding_proof.py`; `contracts/secret-key/sandbox-binding.json` holds both to the same bytes.
 */
const MESSAGE_LABEL = Buffer.from('pipeshub sandbox binding v1', 'utf8');
const RING_SALT = Buffer.from('pipeshub/sandbox-binding', 'utf8');
const RING_INFO = Buffer.from('ring/v1', 'utf8');
const PROOF = /^v1\.([A-Za-z0-9_-]{43})$/;

export interface SandboxBinding {
  orgId: string;
  runId: string;
  attemptId: string;
}

/** Each field length-prefixed, so no choice of ids makes two bindings share bytes. */
export function bindingMessage(binding: SandboxBinding): Buffer {
  const parts: Buffer[] = [MESSAGE_LABEL];
  for (const field of [binding.orgId, binding.runId, binding.attemptId]) {
    const bytes = Buffer.from(field, 'utf8');
    const length = Buffer.alloc(4);
    length.writeUInt32BE(bytes.length);
    parts.push(length, bytes);
  }
  return Buffer.concat(parts);
}

/** A broker that holds `SECRET_KEY` (a from-source run, no component key) signs under this. */
export const ringBindingKey = (secret: string): Buffer =>
  Buffer.from(
    hkdfSync('sha256', Buffer.from(secret, 'utf8'), RING_SALT, RING_INFO, 32),
  );

export function bindingProof(key: Buffer, binding: SandboxBinding): string {
  return `v1.${createHmac('sha256', key).update(bindingMessage(binding)).digest('base64url')}`;
}

/** Whether any of `keys` made `proof` for exactly this binding; a malformed proof is simply not one. */
export function verifyBindingProof(
  proof: string,
  binding: SandboxBinding,
  keys: readonly Buffer[],
): boolean {
  const match = PROOF.exec(proof);
  if (match === null) return false;
  const encoded = match[1] ?? '';
  const presented = Buffer.from(encoded, 'base64url');
  // base64url decoding is lenient about the last character's spare bits; a re-encoding that differs is a forgery.
  if (presented.length !== 32 || presented.toString('base64url') !== encoded)
    return false;
  let found = false;
  for (const key of keys) {
    const expected = createHmac('sha256', key)
      .update(bindingMessage(binding))
      .digest();
    found = timingSafeEqual(expected, presented) || found;
  }
  return found;
}

/**
 * Every key a genuine broker may sign under now: its component key's binding subkey, and the binding key of each
 * `SECRET_KEY` ring member (a broker run with the root key; the other member covers a rotation in flight).
 */
export function brokerBindingKeys(
  ring: () => SecretKeyRing | null,
  brokerKey: () => string | null = () => componentKey('sandboxBroker'),
): Buffer[] {
  const keys: Buffer[] = [];
  const own = brokerKey();
  if (own !== null) keys.push(componentSubkey('sandboxBroker', own, 'binding'));
  const secrets = ring();
  if (secrets !== null) {
    keys.push(ringBindingKey(secrets.current));
    if (secrets.other !== null) keys.push(ringBindingKey(secrets.other.secret));
  }
  return keys;
}
