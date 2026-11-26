import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Container } from 'inversify';
import nodeCrypto from 'crypto';
import { createPrivateKey, randomBytes, sign } from 'node:crypto';
import jwt from 'jsonwebtoken';
import { EncryptionService } from '../../../src/libs/encryptor/encryptor';
import { InMemoryConfigDocumentStore } from '../../../src/modules/agent-harness/adapters/in-memory-config-document-store';
import {
  createHarnessAuthorityStores,
  EncryptedConfigDocumentStore,
  workerCredentialSetSchema,
} from '../../../src/modules/agent-harness/adapters/configuration/encrypted-config-document-store';
import { AttemptTokenAuthority } from '../../../src/modules/agent-harness/application/attempt-token-authority';
import { decodeAttemptToken } from '../../../src/modules/agent-harness/application/attempt-token-codec';
import { HarnessAuthority } from '../../../src/modules/agent-harness/application/harness-authority';
import {
  constantTimeEquals,
  WorkerCredentialAuthority,
} from '../../../src/modules/agent-harness/application/worker-credential-authority';
import {
  ATTEMPT_TOKEN_AUDIENCE,
  ATTEMPT_TOKEN_SLACK_MS,
  AttemptClaims,
  AttemptKeyring,
  AUTHORITY_CACHE_TTL_MS,
  MAX_ATTEMPT_TOKEN_LIFETIME_MS,
  PREVIOUS_WORKER_CREDENTIAL_GRACE_MS,
  PublishedAttemptKeys,
  RETIRED_KEY_RETENTION_MS,
  WorkerCredentialSet,
} from '../../../src/modules/agent-harness/domain/authority';
import { HarnessUnauthorizedError } from '../../../src/modules/agent-harness/domain/errors';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { configPaths } from '../../../src/modules/configuration_manager/paths/paths';
import {
  AGENT_HARNESS_AUTHORITY,
  prepareAgentHarnessAuthority,
} from '../../../src/modules/agent-harness/composition';

const START = Date.UTC(2026, 8, 26);

const manualClock = (start = START) => {
  let now = start;
  return {
    now: () => now,
    advance: (ms: number) => {
      now += ms;
    },
  };
};

const refusal = async (work: Promise<unknown>): Promise<string> => {
  try {
    await work;
  } catch (error) {
    expect(error).to.be.instanceOf(HarnessUnauthorizedError);
    return (error as HarnessUnauthorizedError).reason;
  }
  return expect.fail('expected a HarnessUnauthorizedError');
};

const claims: AttemptClaims = {
  orgId: 'org-1',
  runId: 'run-1',
  attemptId: 'attempt-1',
  fence: 3,
  workerId: 'worker-1',
};

const segment = (value: unknown) =>
  Buffer.from(JSON.stringify(value)).toString('base64url');

describe('WorkerCredentialAuthority', () => {
  const setup = () => {
    const clock = manualClock();
    const store = new InMemoryConfigDocumentStore<WorkerCredentialSet>();
    let n = 0;
    const authority = new WorkerCredentialAuthority(
      store,
      clock,
      () => `phw_credential_${String((n += 1))}`,
    );
    return { clock, store, authority };
  };

  it('A-11: provisions once; concurrent replicas converge on one credential', async () => {
    const { store, clock } = setup();
    let n = 0;
    const replicas = [1, 2, 3].map(
      () =>
        new WorkerCredentialAuthority(store, clock, () => `phw_${String((n += 1))}`),
    );
    await Promise.all(replicas.map((replica) => replica.provision(null)));
    await Promise.all(replicas.map((replica) => replica.provision(null)));
    expect(store.writes).to.equal(1);
    const { value } = await store.read();
    for (const replica of replicas) {
      await replica.verify(value!.current);
    }
  });

  it('A-5: refuses a missing, empty or wrong credential, and an unprovisioned deployment', async () => {
    const { authority } = setup();
    expect(await refusal(authority.verify('phw_credential_1'))).to.equal(
      'credential',
    );
    await authority.provision(null);
    await authority.verify('phw_credential_1');
    expect(await refusal(authority.verify(undefined))).to.equal('credential');
    expect(await refusal(authority.verify(''))).to.equal('credential');
    expect(await refusal(authority.verify('phw_credential_2'))).to.equal(
      'credential',
    );
    expect(await refusal(authority.verify('phw_credential_1 '))).to.equal(
      'credential',
    );
  });

  it('A-6: current and previous claim during the grace window, only current after it', async () => {
    const { authority, clock } = setup();
    await authority.provision(null);
    await authority.rotate();
    await authority.verify('phw_credential_2');
    await authority.verify('phw_credential_1');
    clock.advance(PREVIOUS_WORKER_CREDENTIAL_GRACE_MS);
    expect(await refusal(authority.verify('phw_credential_1'))).to.equal(
      'credential',
    );
    await authority.verify('phw_credential_2');
  });

  it('A-6: an operator rotation id rotates exactly once', async () => {
    const { authority, store } = setup();
    await authority.provision(null);
    await authority.provision('rotate-1');
    await authority.provision('rotate-1');
    const { value } = await store.read();
    expect(value).to.include({
      current: 'phw_credential_2',
      previous: 'phw_credential_1',
      rotateId: 'rotate-1',
    });
    expect(store.writes).to.equal(2);
  });

  it('A-6: another replica accepts a rotated credential without waiting for its cache', async () => {
    const { store, clock, authority } = setup();
    await authority.provision(null);
    const other = new WorkerCredentialAuthority(store, clock, () => 'unused');
    await other.verify('phw_credential_1');
    await authority.rotate();
    clock.advance(PREVIOUS_WORKER_CREDENTIAL_GRACE_MS + 1);
    await other.verify('phw_credential_2');
    expect(await refusal(other.verify('phw_credential_1'))).to.equal(
      'credential',
    );
  });

  it('compares through equal-length digests', () => {
    expect(constantTimeEquals('abc', 'abc')).to.equal(true);
    expect(constantTimeEquals('abc', 'abcd')).to.equal(false);
    expect(constantTimeEquals('', 'x'.repeat(10_000))).to.equal(false);
  });
});

describe('AttemptTokenAuthority', () => {
  const setup = (clock = manualClock()) => {
    const signingKeys = new InMemoryConfigDocumentStore<AttemptKeyring>();
    const publicKeys = new InMemoryConfigDocumentStore<PublishedAttemptKeys>();
    const authority = new AttemptTokenAuthority({ signingKeys, publishedKeys: publicKeys, clock });
    return { clock, signingKeys, publicKeys, authority };
  };

  it('issues an EdDSA token bound to the attempt, with lease + slack lifetime', async () => {
    const { authority, clock } = setup();
    const issued = await authority.issue(claims, 60_000);
    const decoded = decodeAttemptToken(issued.token);
    expect(decoded.header).to.deep.include({ alg: 'EdDSA', typ: 'JWT' });
    expect(decoded.payload).to.deep.include({
      ...claims,
      aud: ATTEMPT_TOKEN_AUDIENCE,
    });
    expect(issued.expiresAt).to.equal(
      clock.now() + 60_000 + ATTEMPT_TOKEN_SLACK_MS,
    );
    expect(await authority.verify(issued.token)).to.deep.equal(claims);
  });

  it('caps the lifetime at the maximum lease', async () => {
    const { authority, clock } = setup();
    const issued = await authority.issue(claims, 24 * 60 * 60 * 1000);
    expect(issued.expiresAt).to.equal(
      clock.now() + MAX_ATTEMPT_TOKEN_LIFETIME_MS,
    );
  });

  it('A-7: refuses an expired token', async () => {
    const { authority, clock } = setup();
    const issued = await authority.issue(claims, 1_000);
    clock.advance(1_000 + ATTEMPT_TOKEN_SLACK_MS);
    expect(await refusal(authority.verify(issued.token))).to.equal('expired');
  });

  it('A-7: refuses tampered payloads and signatures, and malformed tokens', async () => {
    const { authority } = setup();
    const { token } = await authority.issue(claims, 60_000);
    const [header, payload, signature] = token.split('.') as [
      string,
      string,
      string,
    ];
    const forged = segment({
      ...(JSON.parse(Buffer.from(payload, 'base64url').toString()) as object),
      runId: 'run-2',
    });
    expect(
      await refusal(authority.verify(`${header}.${forged}.${signature}`)),
    ).to.equal('signature');
    const flipped = Buffer.from(signature, 'base64url');
    flipped[0] = (flipped[0] ?? 0) ^ 1;
    expect(
      await refusal(
        authority.verify(`${header}.${payload}.${flipped.toString('base64url')}`),
      ),
    ).to.equal('signature');
    for (const bad of [undefined, '', 'a.b', 'a.b.c.d', '!!.!!.!!', `${header}.${payload}.`]) {
      expect(await refusal(authority.verify(bad))).to.equal('malformed');
    }
  });

  it('PERFE-10: a token verified once is not checked by its signature again; tampering and its key still are', async () => {
    const { authority, clock } = setup();
    const issued = await authority.issue(claims, 60_000);
    const [header, payload, signature] = issued.token.split('.') as [string, string, string];
    const flipped = Buffer.from(signature, 'base64url');
    flipped[0] = (flipped[0] ?? 0) ^ 1;
    const verify = sinon.spy(nodeCrypto, 'verify');
    try {
      expect(await authority.verify(issued.token)).to.deep.equal(claims);
      expect(await authority.verify(issued.token)).to.deep.equal(claims);
      expect(verify.callCount, 'the second call reuses the first check').to.equal(1);
      expect(
        await refusal(authority.verify(`${header}.${payload}.${flipped.toString('base64url')}`)),
      ).to.equal('signature');
      expect(verify.callCount).to.equal(2);
    } finally {
      verify.restore();
    }
    await authority.rotate();
    clock.advance(RETIRED_KEY_RETENTION_MS);
    expect(await refusal(authority.verify(issued.token))).to.be.oneOf(['unknown_key', 'expired']);
  });

  it('A-7: refuses alg none, HS256 and an unknown kid', async () => {
    const { authority } = setup();
    const { token } = await authority.issue(claims, 60_000);
    const [header, payload] = token.split('.') as [string, string];
    const kid = (decodeAttemptToken(token).header as { kid: string }).kid;
    expect(
      await refusal(
        authority.verify(`${segment({ alg: 'none', typ: 'JWT', kid })}.${payload}.`),
      ),
    ).to.equal('malformed');
    expect(
      await refusal(
        authority.verify(`${segment({ alg: 'none', typ: 'JWT', kid })}.${payload}.AA`),
      ),
    ).to.equal('algorithm');
    const hs256 = jwt.sign(
      { ...claims, aud: ATTEMPT_TOKEN_AUDIENCE },
      'scoped-secret',
      { header: { alg: 'HS256', kid } as never },
    );
    expect(await refusal(authority.verify(hs256))).to.equal('algorithm');
    const unknown = `${segment({ alg: 'EdDSA', typ: 'JWT', kid: 'hak_other' })}.${payload}.${token.split('.')[2]}`;
    expect(await refusal(authority.verify(unknown))).to.equal('unknown_key');
    header;
  });

  it('A-7: refuses a validly signed token with the wrong audience or issuer', async () => {
    const { authority, signingKeys } = setup();
    await authority.provision(null);
    const ring = (await signingKeys.read()).value!;
    const key = ring.keys[0]!;
    const forge = (overrides: Record<string, unknown>) => {
      const input = `${segment({ alg: 'EdDSA', typ: 'JWT', kid: key.kid })}.${segment(
        {
          iss: 'pipeshub-node/agent-harness',
          aud: ATTEMPT_TOKEN_AUDIENCE,
          ...claims,
          iat: Math.floor(START / 1000),
          exp: Math.floor(START / 1000) + 60,
          ...overrides,
        },
      )}`;
      const signature = sign(
        null,
        Buffer.from(input),
        createPrivateKey(key.privateKey),
      ).toString('base64url');
      return `${input}.${signature}`;
    };
    expect(await authority.verify(forge({}))).to.deep.equal(claims);
    expect(
      await refusal(authority.verify(forge({ aud: 'pipeshub-agent-harness' }))),
    ).to.equal('audience');
    expect(await refusal(authority.verify(forge({ iss: 'python' })))).to.equal(
      'issuer',
    );
    expect(await refusal(authority.verify(forge({ extra: true })))).to.equal(
      'malformed',
    );
  });

  it('A-4: after rotation new tokens use the new key and old ones verify until expiry', async () => {
    const { authority, clock } = setup();
    const before = await authority.issue(claims, 60_000);
    await authority.rotate();
    const after = await authority.issue(claims, 60_000);
    const kid = (token: string) =>
      (decodeAttemptToken(token).header as { kid: string }).kid;
    expect(kid(after.token)).to.not.equal(kid(before.token));
    expect(await authority.verify(before.token)).to.deep.equal(claims);
    clock.advance(60_000 + ATTEMPT_TOKEN_SLACK_MS);
    expect(await refusal(authority.verify(before.token))).to.equal('expired');
  });

  it('A-4: a retired key stops verifying after its retention, and is pruned on the next rotation', async () => {
    const { authority, clock, signingKeys } = setup();
    const before = await authority.issue(claims, 60_000);
    await authority.rotate();
    clock.advance(RETIRED_KEY_RETENTION_MS);
    expect(await refusal(authority.verify(before.token))).to.be.oneOf([
      'unknown_key',
      'expired',
    ]);
    await authority.rotate();
    const ring = (await signingKeys.read()).value!;
    expect(ring.keys).to.have.length(2);
    expect(ring.keys.map((key) => key.kid)).to.not.include(
      (decodeAttemptToken(before.token).header as { kid: string }).kid,
    );
  });

  it('A-4: another replica verifies tokens signed with a key it has not cached yet', async () => {
    const clock = manualClock();
    const { signingKeys, publicKeys, authority } = setup(clock);
    await authority.provision(null);
    const other = new AttemptTokenAuthority({ signingKeys, publishedKeys: publicKeys, clock });
    await other.verify((await other.issue(claims, 60_000)).token);
    await authority.rotate();
    clock.advance(AUTHORITY_CACHE_TTL_MS / 2);
    const fresh = await authority.issue(claims, 60_000);
    expect(await other.verify(fresh.token)).to.deep.equal(claims);
  });

  it('A-11: provisions once across replicas and publishes public keys only', async () => {
    const clock = manualClock();
    const { signingKeys, publicKeys } = setup(clock);
    const replicas = [1, 2, 3].map(
      () => new AttemptTokenAuthority({ signingKeys, publishedKeys: publicKeys, clock }),
    );
    await Promise.all(replicas.map((replica) => replica.provision(null)));
    expect(signingKeys.writes).to.equal(1);
    const ring = (await signingKeys.read()).value!;
    const published = (await publicKeys.read()).value!;
    expect(published.audience).to.equal(ATTEMPT_TOKEN_AUDIENCE);
    expect(published.keys.map((key) => key.kid)).to.deep.equal([ring.activeKid]);
    expect(JSON.stringify(published)).to.not.contain('PRIVATE KEY');
    expect(published.keys[0]!.publicKey).to.contain('BEGIN PUBLIC KEY');
    await replicas[0]!.rotate();
    const rotated = (await publicKeys.read()).value!;
    expect(rotated.keys).to.have.length(2);
  });

  it('A-4: rotates on a new operator rotation id once, and on key age', async () => {
    const { authority, signingKeys, clock } = setup();
    await authority.provision(null);
    await authority.provision('rotate-1');
    await authority.provision('rotate-1');
    expect((await signingKeys.read()).value!.keys).to.have.length(2);
    clock.advance(30 * 24 * 60 * 60 * 1000);
    await authority.issue(claims, 1_000);
    const ring = (await signingKeys.read()).value!;
    expect(ring.keys.filter((key) => key.retiredAt === null)).to.have.length(1);
    expect(ring.keys.at(-1)!.kid).to.equal(ring.activeKid);
    expect(ring.keys.at(-1)!.createdAt).to.equal(clock.now());
  });
});

describe('HarnessAuthority', () => {
  const run = (overrides: Partial<HarnessRun> = {}): HarnessRun =>
    ({
      runId: 'run-1',
      orgId: 'org-1',
      status: 'running',
      fence: 3,
      attemptId: 'attempt-1',
      workerId: 'worker-1',
      ...overrides,
    }) as HarnessRun;

  const setup = (current: HarnessRun | null) => {
    const clock = manualClock();
    const tokens = new AttemptTokenAuthority({
      signingKeys: new InMemoryConfigDocumentStore(),
      publishedKeys: new InMemoryConfigDocumentStore(),
      clock,
    });
    const authority = new HarnessAuthority(
      new WorkerCredentialAuthority(new InMemoryConfigDocumentStore(), clock),
      tokens,
      { get: async () => current },
    );
    return { authority, tokens };
  };

  it('A-2/A-9: binds the token to its run, attempt and fence', async () => {
    const { authority } = setup(run());
    const { token } = await authority.issueFor(run(), 60_000);
    const verified = await authority.verifyAttemptToken(token, 'run-1');
    expect(verified).to.deep.equal(claims);
    expect(
      await authority.authorizeAttempt(verified, {
        attemptId: 'attempt-1',
        fence: 3,
      }),
      'PERFD-13: the run as the check read it',
    ).to.deep.equal(run());
    expect(
      await refusal(authority.verifyAttemptToken(token, 'run-2')),
    ).to.equal('claims');
    expect(
      await refusal(
        authority.authorizeAttempt(verified, { attemptId: 'attempt-2', fence: 3 }),
      ),
    ).to.equal('claims');
    expect(
      await refusal(
        authority.authorizeAttempt(verified, { attemptId: 'attempt-1', fence: 4 }),
      ),
    ).to.equal('claims');
  });

  it('A-3: refuses a token whose attempt a later claim superseded', async () => {
    const { authority } = setup(run({ fence: 4, attemptId: 'attempt-2' }));
    const verified = await authority.verifyAttemptToken(
      (await authority.issueFor(run(), 60_000)).token,
      'run-1',
    );
    expect(
      await refusal(
        authority.authorizeAttempt(verified, { attemptId: 'attempt-1', fence: 3 }),
      ),
    ).to.equal('superseded');
  });

  it('lets a finished attempt through so a retried commit replays its receipt', async () => {
    const { authority } = setup(
      run({ status: 'completed', attemptId: null, workerId: null }),
    );
    const verified = await authority.verifyAttemptToken(
      (await authority.issueFor(run(), 60_000)).token,
      'run-1',
    );
    await authority.authorizeAttempt(verified, {
      attemptId: 'attempt-1',
      fence: 3,
    });
  });
});

describe('EncryptedConfigDocumentStore', () => {
  const fakeKv = () => {
    const values = new Map<string, string>();
    return {
      values,
      get: async <T>(key: string) => (values.get(key) ?? null) as T | null,
      compareAndSet: async <T>(key: string, expected: T | null, next: T) => {
        if ((values.get(key) ?? null) !== expected) return false;
        values.set(key, next as unknown as string);
        return true;
      },
    };
  };
  const cipher = EncryptionService.getInstance(
    'aes-256-gcm',
    randomBytes(32).toString('hex'),
  );

  it('A-11: stores documents encrypted under the harness paths, with CAS on the ciphertext', async () => {
    const kv = fakeKv();
    const stores = createHarnessAuthorityStores(kv, cipher);
    const tokens = new AttemptTokenAuthority({
      signingKeys: stores.attemptSigningKeys,
      publishedKeys: stores.attemptPublicKeys,
    });
    const workers = new WorkerCredentialAuthority(stores.workerCredential);
    await workers.provision(null);
    await tokens.provision(null);
    expect([...kv.values.keys()].sort()).to.deep.equal(
      [
        configPaths.agentHarness.attemptPublicKeys,
        configPaths.agentHarness.attemptSigningKeys,
        configPaths.agentHarness.workerCredential,
      ].sort(),
    );
    for (const stored of kv.values.values()) {
      expect(stored).to.match(/^[0-9a-f]+:[0-9a-f]+:[0-9a-f]+$/);
    }
    const credential = (await stores.workerCredential.read()).value!.current;
    expect(credential).to.match(/^phw_[A-Za-z0-9_-]{43}$/);
    await workers.verify(credential);
    expect(
      await stores.workerCredential.replace(null, {
        current: 'phw_x',
        previous: null,
        previousValidUntil: null,
        rotateId: null,
      }),
    ).to.equal(false);
  });

  it('fails closed on a document that does not match its schema', async () => {
    const kv = fakeKv();
    kv.values.set('/p', cipher.encrypt(JSON.stringify({ current: '' })));
    const store = new EncryptedConfigDocumentStore(
      kv,
      cipher,
      '/p',
      workerCredentialSetSchema,
    );
    let failed = false;
    try {
      await store.read();
    } catch {
      failed = true;
    }
    expect(failed).to.equal(true);
  });
});

describe('prepareAgentHarnessAuthority', () => {
  const ready = {
    REPLICA_SET_AVAILABLE: 'true',
    AGENT_HARNESS_RELAY_ENABLED: 'true',
    AGENT_HARNESS_WORKER_ENABLED: 'true',
  };
  const setup = (provision: sinon.SinonStub) => {
    const container = new Container();
    container.bind(AGENT_HARNESS_AUTHORITY).toConstantValue({ provision });
    const logger = { info: sinon.stub(), error: sinon.stub() };
    return { container, logger };
  };

  it('touches nothing on a deployment that did not ask for the harness', async () => {
    const provision = sinon.stub().resolves();
    const { container, logger } = setup(provision);
    await prepareAgentHarnessAuthority(container, logger as never, {});
    expect(provision.called).to.equal(false);
  });

  it('A-11: provisions at startup and passes the operator rotation id', async () => {
    const provision = sinon.stub().resolves();
    const { container, logger } = setup(provision);
    await prepareAgentHarnessAuthority(container, logger as never, ready);
    await prepareAgentHarnessAuthority(container, logger as never, {
      ...ready,
      ROTATE_SIGNING_SECRETS: ' rotate-7 ',
    });
    expect(provision.args).to.deep.equal([[null], ['rotate-7']]);
  });

  it('logs and keeps starting when provisioning fails; claims stay refused', async () => {
    const provision = sinon.stub().rejects(new Error('kv down'));
    const { container, logger } = setup(provision);
    await prepareAgentHarnessAuthority(container, logger as never, ready);
    expect(logger.error.calledOnce).to.equal(true);
  });

  describe('D1 WC-1: the sandbox broker needs the worker credential with durable runs off', () => {
    const brokerOnly = (provisionWorkerCredential: sinon.SinonStub) => {
      const provision = sinon.stub().resolves();
      const container = new Container();
      container
        .bind(AGENT_HARNESS_AUTHORITY)
        .toConstantValue({ provision, provisionWorkerCredential });
      const logger = { info: sinon.stub(), error: sinon.stub() };
      return { container, logger, provision };
    };

    it('provisions only the worker credential when SANDBOX_MODE=broker', async () => {
      const provisionWorkerCredential = sinon.stub().resolves();
      const { container, logger, provision } = brokerOnly(provisionWorkerCredential);
      await prepareAgentHarnessAuthority(container, logger as never, {
        SANDBOX_MODE: ' Broker ',
        ROTATE_SIGNING_SECRETS: 'rotate-9',
      });
      expect(provisionWorkerCredential.args).to.deep.equal([['rotate-9']]);
      expect(provision.called).to.equal(false);
    });

    it('provisions everything when the harness is on, whatever the sandbox mode', async () => {
      const provisionWorkerCredential = sinon.stub().resolves();
      const { container, logger, provision } = brokerOnly(provisionWorkerCredential);
      await prepareAgentHarnessAuthority(container, logger as never, {
        ...ready,
        SANDBOX_MODE: 'broker',
      });
      expect(provision.args).to.deep.equal([[null]]);
      expect(provisionWorkerCredential.called).to.equal(false);
    });

    for (const mode of ['docker', 'e2b', 'local', '']) {
      it(`touches nothing with durable runs off and SANDBOX_MODE=${JSON.stringify(mode)}`, async () => {
        const provisionWorkerCredential = sinon.stub().resolves();
        const { container, logger, provision } = brokerOnly(provisionWorkerCredential);
        await prepareAgentHarnessAuthority(container, logger as never, {
          SANDBOX_MODE: mode,
        });
        expect(provision.called).to.equal(false);
        expect(provisionWorkerCredential.called).to.equal(false);
      });
    }
  });
});
