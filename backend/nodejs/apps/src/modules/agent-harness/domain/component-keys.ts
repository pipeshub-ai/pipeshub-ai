import { createHash, createHmac, hkdfSync } from 'node:crypto';
import { readFileSync } from 'node:fs';
import {
  SecretKeyRing,
  weakSecretReason,
} from '../../../libs/encryptor/secret-key-ring';
import {
  HarnessCredentialRole,
  SandboxPlaneComponent,
} from './credential-roles';
import { WorkerCredentialSet } from './authority';

/**
 * ADR-KEY-02 (SEC-D-03): the egress proxy and the sandbox broker face sandbox code, so they never hold `SECRET_KEY`.
 * Each has its own random key, which Node holds too (`EGRESS_PROXY_COMPONENT_KEY`, `SANDBOX_BROKER_COMPONENT_KEY`,
 * or a `_FILE` naming one). Under subkeys HKDF derives from it Node seals, for that component alone, what it needs:
 * its role credential, digests of the credentials it admits, and (the proxy) the egress sealing keys derived from the
 * `SECRET_KEY` ring. The component derives its Redis ACL login from the same key. Python's twin is
 * `app/utils/encryption/component_key.py`; both derive alike.
 */
export const COMPONENT_NAMES: Readonly<Record<SandboxPlaneComponent, string>> =
  {
    egressProxy: 'egress-proxy',
    sandboxBroker: 'sandbox-broker',
  };

export const COMPONENT_KEY_ENV: Readonly<
  Record<SandboxPlaneComponent, string>
> = {
  egressProxy: 'EGRESS_PROXY_COMPONENT_KEY',
  sandboxBroker: 'SANDBOX_BROKER_COMPONENT_KEY',
};

const SALT = Buffer.from('pipeshub/component', 'utf8');
// harness_core/sandbox/egress/state.py STATE_KEY_LABEL and sealed.py CREDENTIAL_KEY_LABEL: keep in step.
export const EGRESS_STATE_LABEL = 'harness.egress.state.v1';
export const EGRESS_CREDENTIAL_LABEL = 'pipeshub egress sealed credential v1';

type Env = Readonly<Record<string, string | undefined>>;

export class ComponentKeyError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ComponentKeyError';
  }
}

export function componentSubkey(
  component: SandboxPlaneComponent,
  key: string,
  purpose: 'config' | 'redis' | 'binding',
): Buffer {
  return Buffer.from(
    hkdfSync(
      'sha256',
      Buffer.from(key, 'utf8'),
      SALT,
      Buffer.from(`${COMPONENT_NAMES[component]}/${purpose}/v1`, 'utf8'),
      32,
    ),
  );
}

/** The component's key as Node holds it; null when the deployment runs no such component. A weak key is refused. */
export function componentKey(
  component: SandboxPlaneComponent,
  env: Env = process.env,
): string | null {
  const name = COMPONENT_KEY_ENV[component];
  const file = env[`${name}_FILE`]?.trim();
  let key = env[name] ?? '';
  if (file) {
    try {
      key = readFileSync(file, 'utf8').trim();
    } catch (error) {
      throw new ComponentKeyError(
        `${name}_FILE cannot be read (${error instanceof Error ? error.name : 'error'})`,
      );
    }
  }
  if (key === '') return null;
  const reason = weakSecretReason(key);
  if (reason !== null) {
    throw new ComponentKeyError(
      `${name} is not a random key (${reason}): set it to the output of \`openssl rand -base64 32\` (install.sh generates it), here and on the ${COMPONENT_NAMES[component]}`,
    );
  }
  return key;
}

export const componentCipherKey = (
  component: SandboxPlaneComponent,
  key: string,
): string => componentSubkey(component, key, 'config').toString('hex');

/**
 * The component's Redis ACL login. The user name carries a fingerprint of the key, so two installs sharing one Redis
 * (lane stacks on one host) never overwrite each other's user.
 */
export function componentRedisLogin(
  component: SandboxPlaneComponent,
  key: string,
): { username: string; password: string } {
  const password = componentSubkey(component, key, 'redis').toString('hex');
  const fingerprint = createHash('sha256')
    .update(password, 'utf8')
    .digest('hex')
    .slice(0, 12);
  return {
    username: `pipeshub-${COMPONENT_NAMES[component]}-${fingerprint}`,
    password,
  };
}

/** A credential a component admits, as it may hold it: a digest, and when it stops being accepted (null: current). */
export interface AcceptedDigest {
  digest: string;
  validUntil: number | null;
}

/** The sandbox-plane part of `/services/endpoints`, as `<service>: { <key>: url }` (R38IDENT-FU-10). */
export type SealedEndpoints = Partial<
  Record<
    'sandboxBroker' | 'egressProxy' | 'modelGateway' | 'toolGateway',
    Record<string, string>
  >
>;

export interface ComponentDocument {
  role: HarnessCredentialRole;
  credential: WorkerCredentialSet;
  accepts: Partial<Record<HarnessCredentialRole, AcceptedDigest[]>>;
  egressSealing?: { state: string[]; credentials: string[] };
  /**
   * Where the component's peers answer, sealed with the rest (R38IDENT-FU-10): the component reads them here and
   * never from the plain `/services/endpoints`, which any holder of the store's default login could rewrite.
   */
  endpoints?: SealedEndpoints;
}

export const credentialDigest = (credential: string): string =>
  createHash('sha256').update(credential, 'utf8').digest('hex');

export function acceptedDigests(set: WorkerCredentialSet): AcceptedDigest[] {
  const digests: AcceptedDigest[] = [
    { digest: credentialDigest(set.current), validUntil: null },
  ];
  if (set.previous !== null && set.previousValidUntil !== null) {
    digests.push({
      digest: credentialDigest(set.previous),
      validUntil: set.previousValidUntil,
    });
  }
  return digests;
}

/**
 * The egress proxy's sealing keys, derived from each ring secret exactly as `AeadRing` derives them (HMAC-SHA256 of
 * the secret under the sealer's label), the writer's first: the proxy opens what it and the worker sealed, and holds
 * no key that opens anything else.
 */
export function egressSealingKeys(ring: SecretKeyRing): {
  state: string[];
  credentials: string[];
} {
  const secrets = [
    ring.current,
    ...(ring.other !== null ? [ring.other.secret] : []),
  ];
  const derive = (label: string): string[] =>
    secrets.map((secret) =>
      createHmac('sha256', Buffer.from(secret, 'utf8'))
        .update(label, 'utf8')
        .digest('hex'),
    );
  return {
    state: derive(EGRESS_STATE_LABEL),
    credentials: derive(EGRESS_CREDENTIAL_LABEL),
  };
}
