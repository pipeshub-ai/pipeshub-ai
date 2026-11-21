import { expect } from 'chai';
import crypto from 'node:crypto';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { EncryptionService } from '../../../src/libs/encryptor/encryptor';
import { platformCipherKey, secretKeyRing } from '../../../src/libs/encryptor/secret-key-ring';
import {
  componentDocumentSchema,
  createComponentDocumentStores,
  EncryptedConfigDocumentStore,
} from '../../../src/modules/agent-harness/adapters/configuration/encrypted-config-document-store';
import { InMemoryConfigDocumentStore as ConfigDocs } from '../../../src/modules/agent-harness/adapters/in-memory-config-document-store';
import {
  componentAclRules,
  ComponentLoginMissing,
  RedisComponentLogins,
} from '../../../src/modules/agent-harness/adapters/redis/redis-component-logins';
import { componentAclLines } from '../../../src/scripts/component-acl';
import { sandboxPlaneEndpoints } from '../../../src/modules/agent-harness/adapters/configuration/sandbox-plane-endpoints';
import { ComponentCredentialPublisher } from '../../../src/modules/agent-harness/application/component-credential-publisher';
import { HarnessAuthority } from '../../../src/modules/agent-harness/application/harness-authority';
import { createHarnessAuthority } from '../../../src/modules/agent-harness/binders/authority';
import {
  AttemptKeyring,
  PREVIOUS_WORKER_CREDENTIAL_GRACE_MS,
  PublishedAttemptKeys,
  WorkerCredentialSet,
} from '../../../src/modules/agent-harness/domain/authority';
import {
  ComponentDocument,
  componentCipherKey,
  ComponentKeyError,
  componentKey,
  componentRedisLogin,
  componentSubkey,
  credentialDigest,
  EGRESS_CREDENTIAL_LABEL,
  EGRESS_STATE_LABEL,
} from '../../../src/modules/agent-harness/domain/component-keys';
import { configPaths } from '../../../src/modules/configuration_manager/paths/paths';
import { MemoryKv } from './support/model-accounts';

/**
 * ADR-KEY-02 (SEC-D-03) and ADR-CRED-01 (SEC-C-10): what Node seals for the egress proxy and the sandbox broker.
 * Each gets its own role credential and digests of the credentials it admits, never another role's credential and
 * never SECRET_KEY; the vectors are shared with Python (`contracts/secret-key/component-acl.json`).
 */
const CONTRACT = JSON.parse(
  readFileSync(path.resolve(__dirname, '../../../../../../contracts/secret-key/component-acl.json'), 'utf8'),
) as {
  subkeys: { key: string } & Record<'egress-proxy' | 'sandbox-broker', { config: string; redis: string }>;
  acl: Array<{ component: 'egress-proxy' | 'sandbox-broker'; namespace: string; kvPrefix: string; rules: string[] }>;
};
const CAMEL = { 'egress-proxy': 'egressProxy', 'sandbox-broker': 'sandboxBroker' } as const;
const SECRET = crypto.createHash('sha256').update('component test secret').digest('hex');
const PROXY_KEY = crypto.createHash('sha256').update('proxy key').digest('base64');
const BROKER_KEY = crypto.createHash('sha256').update('broker key').digest('base64');

function world(now = { t: Date.UTC(2026, 10, 13) }) {
  const clock = { now: () => now.t, set: (t: number) => { now.t = t; } };
  const stores = {
    workerCredential: new ConfigDocs<WorkerCredentialSet>(),
    attemptSigningKeys: new ConfigDocs<AttemptKeyring>(),
    attemptPublicKeys: new ConfigDocs<PublishedAttemptKeys>(),
    roleCredentials: {
      scrape: new ConfigDocs<WorkerCredentialSet>(),
      'egress-proxy': new ConfigDocs<WorkerCredentialSet>(),
      'sandbox-broker': new ConfigDocs<WorkerCredentialSet>(),
    },
  };
  const authority: HarnessAuthority = createHarnessAuthority(stores, { get: async () => null }, clock);
  const documents = {
    egressProxy: new ConfigDocs<ComponentDocument>(),
    sandboxBroker: new ConfigDocs<ComponentDocument>(),
  };
  const applied: string[] = [];
  const publisher = new ComponentCredentialPublisher({
    authority,
    documents,
    ring: () => secretKeyRing({ PIPESHUB_HARDENED: 'true', SECRET_KEY: SECRET }),
    logins: { apply: async (component) => { applied.push(component); } },
  });
  return { clock, authority, documents, publisher, applied };
}

describe('ADR-KEY-02: component keys derive alike in both languages', () => {
  for (const component of ['egress-proxy', 'sandbox-broker'] as const) {
    it(`${component}: the document key and the Redis password match the Python vectors`, () => {
      const key = CONTRACT.subkeys.key;
      expect(componentCipherKey(CAMEL[component], key)).to.equal(CONTRACT.subkeys[component].config);
      expect(componentSubkey(CAMEL[component], key, 'redis').toString('hex')).to.equal(CONTRACT.subkeys[component].redis);
    });
  }

  for (const c of CONTRACT.acl) {
    it(`${c.component} ACL rules (namespace '${c.namespace}', prefix ${c.kvPrefix}) match Python's`, () => {
      expect(componentAclRules(CAMEL[c.component], c.namespace, c.kvPrefix)).to.deep.equal(c.rules);
    });
  }

  it("a component's ACL user names a fingerprint, so two installs on one Redis never share a user", () => {
    const a = componentRedisLogin('egressProxy', PROXY_KEY);
    const b = componentRedisLogin('egressProxy', BROKER_KEY);
    expect(a.username).to.match(/^pipeshub-egress-proxy-[0-9a-f]{12}$/);
    expect(a.username).to.not.equal(b.username);
  });

  it('the proxy cannot read configuration secrets, nor the plain endpoints (R38IDENT-FU-10): no ACL rule names the KV store but its own document and state', () => {
    const rules = componentAclRules('egressProxy', '', 'pipeshub:kv:');
    const keyRules = rules.filter((rule) => rule.startsWith('~'));
    expect(keyRules).to.deep.equal([
      '~pipeshub:kv:/services/agentHarness/components/egressProxy',
      '~harness:egress:*',
    ]);
    expect(rules).to.include('-@all');
    for (const dangerous of ['+@all', '+keys', '+config', '+acl', '+flushall', '+@admin', '+client|kill', '+debug']) {
      expect(rules).to.not.include(dangerous);
    }
  });

  it('a component key must be random; the message says what to do and never the value', () => {
    expect(componentKey('egressProxy', {})).to.equal(null);
    expect(componentKey('egressProxy', { EGRESS_PROXY_COMPONENT_KEY: PROXY_KEY })).to.equal(PROXY_KEY);
    for (const weak of ['changeme', 'a'.repeat(64), ` ${PROXY_KEY}`]) {
      let message = '';
      try {
        componentKey('egressProxy', { EGRESS_PROXY_COMPONENT_KEY: weak });
      } catch (error) {
        expect(error).to.be.instanceOf(ComponentKeyError);
        message = (error as Error).message;
      }
      expect(message).to.include('openssl rand -base64 32');
      expect(message).to.not.include(weak.trim());
    }
  });
});

describe('ADR-KEY-02 / SEC-C-10: the document Node seals for each component', () => {
  it('the proxy gets its own credential, digests of who it admits, and the egress keys; never another credential', async () => {
    const w = world();
    await w.authority.provisionWorkerCredential(null);
    expect(await w.publisher.publish()).to.deep.equal(['egressProxy', 'sandboxBroker']);
    const doc = (await w.documents.egressProxy.read()).value!;
    expect(doc.role).to.equal('egress-proxy');
    expect(doc.credential.current).to.equal(await w.authority.credentialFor('egress-proxy'));
    expect(Object.keys(doc.accepts).sort()).to.deep.equal(['sandbox-broker', 'scrape', 'worker']);
    expect(doc.accepts.worker![0]!.digest).to.equal(credentialDigest((await w.authority.workerCredential())!));
    const serialized = JSON.stringify(doc);
    for (const role of ['worker', 'scrape', 'sandbox-broker'] as const) {
      expect(serialized, role).to.not.include((await w.authority.credentialFor(role))!);
    }
    expect(serialized).to.not.include(SECRET);
    const hmac = (label: string) => crypto.createHmac('sha256', Buffer.from(SECRET, 'utf8')).update(label).digest('hex');
    expect(doc.egressSealing).to.deep.equal({ state: [hmac(EGRESS_STATE_LABEL)], credentials: [hmac(EGRESS_CREDENTIAL_LABEL)] });
    expect(w.applied).to.deep.equal(['egressProxy', 'sandboxBroker']);
  });

  it('the broker admits only the worker on its routes and the scraper on /metrics, and gets no egress key', async () => {
    const w = world();
    await w.authority.provisionWorkerCredential(null);
    await w.publisher.publish();
    const doc = (await w.documents.sandboxBroker.read()).value!;
    expect(doc.role).to.equal('sandbox-broker');
    expect(Object.keys(doc.accepts).sort()).to.deep.equal(['scrape', 'worker']);
    expect(doc.egressSealing).to.equal(undefined);
  });

  it('nothing is published before the credentials are provisioned, and a republish of the same state writes nothing', async () => {
    const w = world();
    expect(await w.publisher.publish()).to.deep.equal([]);
    await w.authority.provisionWorkerCredential(null);
    await w.publisher.publish();
    const first = (await w.documents.egressProxy.read()).revision;
    await w.publisher.publish();
    expect((await w.documents.egressProxy.read()).revision).to.equal(first);
  });

  it('a rotation republishes: the previous digest carries its grace, so the component refuses it after', async () => {
    const w = world();
    await w.authority.provisionWorkerCredential(null);
    await w.publisher.publish();
    const before = (await w.authority.workerCredential())!;
    await w.authority.rotateWorkerCredential();
    await w.publisher.publish();
    const doc = (await w.documents.egressProxy.read()).value!;
    expect(doc.accepts.worker).to.have.length(2);
    expect(doc.accepts.worker![1]).to.deep.equal({
      digest: credentialDigest(before),
      validUntil: w.clock.now() + PREVIOUS_WORKER_CREDENTIAL_GRACE_MS,
    });
    expect(doc.credential.previous).to.not.equal(null);
  });

  it('the document is sealed under the component key: the platform cipher cannot open it, nor the other component', async () => {
    const kv = new MemoryKv();
    const env = { EGRESS_PROXY_COMPONENT_KEY: PROXY_KEY, SANDBOX_BROKER_COMPONENT_KEY: BROKER_KEY };
    const stores = createComponentDocumentStores(kv, env);
    const doc: ComponentDocument = {
      role: 'egress-proxy',
      credential: { current: 'phw_x', previous: null, previousValidUntil: null, rotateId: null },
      accepts: {},
    };
    expect(await stores.egressProxy!.replace(null, doc)).to.equal(true);
    const stored = await kv.get<string>(configPaths.agentHarness.componentEgressProxy);
    const opener = (key: string) =>
      new EncryptedConfigDocumentStore(
        { get: async <T>() => stored as T | null, compareAndSet: async () => false },
        EncryptionService.withKey('aes-256-gcm', key),
        configPaths.agentHarness.componentEgressProxy,
        componentDocumentSchema,
      );
    expect((await opener(componentCipherKey('egressProxy', PROXY_KEY)).read()).value).to.deep.equal(doc);
    let opened = 0;
    for (const wrong of [platformCipherKey(SECRET), componentCipherKey('sandboxBroker', BROKER_KEY), componentCipherKey('sandboxBroker', PROXY_KEY)]) {
      try {
        await opener(wrong).read();
        opened += 1;
      } catch {
        // AES-GCM refuses a key that did not seal it.
      }
    }
    expect(opened).to.equal(0);
  });

  it('no component key held: no document store, so nothing is sealed for a component this install does not run', () => {
    expect(createComponentDocumentStores(new MemoryKv(), {})).to.deep.equal({});
  });
});

describe('ADR-KEY-02 D3: the Redis login Node gives each component', () => {
  it('sets the user with a password hash only, scoped by the rules', async () => {
    const calls: unknown[][] = [];
    const provider = {
      keyNamespace: '',
      getClient: () => ({ call: async (...args: unknown[]) => { calls.push(args); return 'OK'; } }),
    };
    const logins = new RedisComponentLogins(provider as never, (component) => (component === 'egressProxy' ? PROXY_KEY : null), 'pipeshub:kv:');
    await logins.apply('egressProxy');
    await logins.apply('sandboxBroker');
    expect(calls).to.have.length(1);
    const { username, password } = componentRedisLogin('egressProxy', PROXY_KEY);
    expect(calls[0]!.slice(0, 5)).to.deep.equal(['ACL', 'SETUSER', username, 'reset', 'on']);
    expect(calls[0]![5]).to.equal(`#${crypto.createHash('sha256').update(password).digest('hex')}`);
    expect(JSON.stringify(calls)).to.not.include(password);
  });
});

describe('R38IDENT-FU-12: a managed Redis that refuses ACL SETUSER', () => {
  const refusingStore = (auth: (user: string, password: string) => Promise<string>) => {
    const opened: Array<{ disconnected: boolean }> = [];
    const provider = {
      keyNamespace: '',
      getClient: () => ({ call: async () => { throw new Error("ERR unknown command 'ACL'"); } }),
      createClient: () => {
        const connection = { disconnected: false };
        opened.push(connection);
        return {
          call: async (_command: string, user: string, password: string) => auth(user, password),
          disconnect: () => { connection.disconnected = true; },
        };
      },
    };
    return { provider, opened };
  };
  const proxyOnly = (component: string) => (component === 'egressProxy' ? PROXY_KEY : null);

  it("is fine once its operator created the user: the component's own login works, on a connection of its own", async () => {
    const { username, password } = componentRedisLogin('egressProxy', PROXY_KEY);
    const { provider, opened } = refusingStore(async (user, given) => {
      if (user !== username || given !== password) throw new Error('WRONGPASS');
      return 'OK';
    });
    await new RedisComponentLogins(provider as never, proxyOnly, 'pipeshub:kv:').apply('egressProxy');
    expect(opened).to.have.length(1);
    expect(opened[0]!.disconnected, 'the probe connection is closed').to.equal(true);
  });

  it('says what the operator must do when the user does not exist, naming the user and never the password', async () => {
    const { username, password } = componentRedisLogin('egressProxy', PROXY_KEY);
    const { provider, opened } = refusingStore(async () => { throw new Error('WRONGPASS invalid username-password pair'); });
    const refused = await new RedisComponentLogins(provider as never, proxyOnly, 'pipeshub:kv:')
      .apply('egressProxy')
      .catch((error: unknown) => error);
    expect(refused).to.be.instanceOf(ComponentLoginMissing);
    const missing = refused as ComponentLoginMissing;
    expect([missing.name, missing.component, missing.username]).to.deep.equal(['ComponentLoginMissing', 'egressProxy', username]);
    expect(missing.message).to.contain('node dist/scripts/component-acl.js');
    expect(missing.message).to.not.contain(password);
    expect(opened[0]!.disconnected).to.equal(true);
  });

  it('a login that answers anything but OK is not taken for a working user', async () => {
    const { provider } = refusingStore(async () => 'QUEUED');
    const refused = await new RedisComponentLogins(provider as never, proxyOnly, 'pipeshub:kv:')
      .apply('egressProxy')
      .catch((error: unknown) => error);
    expect(refused).to.be.instanceOf(ComponentLoginMissing);
  });

  it("the publisher's log line carries the operator's next step; the other component still publishes", async () => {
    const w = world();
    await w.authority.provisionWorkerCredential(null);
    const warned: Array<Record<string, unknown>> = [];
    const logger = { debug: () => undefined, info: () => undefined, error: () => undefined,
      warn: (_message: string, meta?: Record<string, unknown>) => { warned.push(meta ?? {}); } };
    const publisher = new ComponentCredentialPublisher({
      authority: w.authority,
      documents: w.documents,
      ring: () => secretKeyRing({ PIPESHUB_HARDENED: 'true', SECRET_KEY: SECRET }),
      logins: { apply: async (component) => {
        if (component === 'egressProxy') throw new ComponentLoginMissing(component, 'pipeshub-egress-proxy-abc');
      } },
      logger,
    });
    expect(await publisher.publish()).to.deep.equal(['sandboxBroker']);
    expect(warned).to.have.length(1);
    expect(warned[0]).to.include({ component: 'egressProxy', errorType: 'ComponentLoginMissing' });
    expect(String(warned[0]!.action)).to.contain('component-acl.js');
  });

  it('the operator script prints the same SETUSER Node sends, the password only as its hash unless asked', () => {
    const env = { EGRESS_PROXY_COMPONENT_KEY: PROXY_KEY, SANDBOX_BROKER_COMPONENT_KEY: BROKER_KEY, REDIS_KEY_NAMESPACE: 'ns1' };
    const lines = componentAclLines(env, false);
    expect(lines.filter((line) => line.startsWith('ACL SETUSER'))).to.have.length(2);
    for (const [component, key] of [['egressProxy', PROXY_KEY], ['sandboxBroker', BROKER_KEY]] as const) {
      const { username, password } = componentRedisLogin(component, key);
      const line = lines.find((l) => l.includes(username))!;
      expect(line).to.contain(`#${crypto.createHash('sha256').update(password).digest('hex')}`);
      expect(line).to.contain(`~ns1:pipeshub:kv:/services/agentHarness/components/${component}`);
      expect(line).to.not.contain('/services/endpoints');
      // A shell would read an unquoted | as a pipe.
      expect(line).to.contain('"+client|setname"');
      expect(lines.join('\n')).to.not.contain(password);
      expect(componentAclLines(env, true).join('\n')).to.contain(`password ${password}`);
    }
    expect(componentAclLines({}, false)).to.deep.equal([]);
  });
});

describe('R38IDENT-FU-10: the sandbox-plane addresses travel sealed in each component\'s document', () => {
  const plain = (doc: unknown) => ({ get: async <T>() => (doc === undefined ? null : JSON.stringify(doc)) as T | null });

  it("Node's environment wins over the plain document a store writer could have changed; only the four addresses", async () => {
    const stored = {
      modelGateway: { endpoint: 'http://attacker.example:8096' },
      toolGateway: { endpoint: 'http://tool-gateway:8098/' },
      queryBackend: { endpoint: 'http://query:8000' },
    };
    const sealed = await sandboxPlaneEndpoints(plain(stored), {
      MODEL_GATEWAY_BACKEND: 'http://model-gateway:8096',
      SANDBOX_BROKER_BACKEND: 'http://sandbox-broker:8095',
    });
    expect(sealed).to.deep.equal({
      sandboxBroker: { endpoint: 'http://sandbox-broker:8095' },
      modelGateway: { endpoint: 'http://model-gateway:8096' },
      toolGateway: { endpoint: 'http://tool-gateway:8098' },
    });
  });

  it('a store that cannot be read, or holds no JSON, gives the environment alone', async () => {
    const broken = { get: async () => { throw new Error('down'); } };
    expect(await sandboxPlaneEndpoints(broken as never, { TOOL_GATEWAY_BACKEND: 'http://tg:8098' })).to.deep.equal({
      toolGateway: { endpoint: 'http://tg:8098' },
    });
    const garbage = { get: async <T>() => 'not json' as T };
    expect(await sandboxPlaneEndpoints(garbage, {})).to.deep.equal({});
  });

  it('each published document carries them, and a change of address republishes', async () => {
    const w = world();
    await w.authority.provisionWorkerCredential(null);
    let gateway = 'http://model-gateway:8096';
    const publisher = new ComponentCredentialPublisher({
      authority: w.authority,
      documents: w.documents,
      ring: () => secretKeyRing({ PIPESHUB_HARDENED: 'true', SECRET_KEY: SECRET }),
      endpoints: async () => ({ modelGateway: { endpoint: gateway } }),
    });
    await publisher.publish();
    for (const store of [w.documents.egressProxy, w.documents.sandboxBroker]) {
      expect((await store.read()).value?.endpoints).to.deep.equal({ modelGateway: { endpoint: gateway } });
    }
    gateway = 'http://model-gateway-2:8096';
    await publisher.publish();
    expect((await w.documents.egressProxy.read()).value?.endpoints?.modelGateway?.endpoint).to.equal(gateway);
  });

  it('the sealed document schema refuses an endpoints entry outside the four, or an empty address', () => {
    const base = { role: 'egress-proxy', credential: { current: 'c', previous: null, previousValidUntil: null, rotateId: null }, accepts: {} };
    expect(componentDocumentSchema.safeParse({ ...base, endpoints: { modelGateway: { endpoint: 'http://m' } } }).success).to.equal(true);
    expect(componentDocumentSchema.safeParse({ ...base, endpoints: { queryBackend: { endpoint: 'http://q' } } }).success).to.equal(false);
    expect(componentDocumentSchema.safeParse({ ...base, endpoints: { modelGateway: { endpoint: '' } } }).success).to.equal(false);
  });
});
