/**
 * E2 NA-1..NA-5, NA-8 and GT-9 (Node half): the gateway-token authority.
 * `contracts/harness/vectors/gateway-token.json` is shared with the Python verifier.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { createPrivateKey, createPublicKey } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { InMemoryConfigDocumentStore } from '../../../src/modules/agent-harness/adapters/in-memory-config-document-store';
import { AttemptTokenAuthority } from '../../../src/modules/agent-harness/application/attempt-token-authority';
import {
  decodeAttemptToken,
  encodeAttemptToken,
  signingKeyId,
  verifyAttemptSignature,
} from '../../../src/modules/agent-harness/application/attempt-token-codec';
import { GatewayTokenAuthority } from '../../../src/modules/agent-harness/application/gateway-token-authority';
import {
  AttemptKeyring,
  PublishedAttemptKeys,
  RETIRED_KEY_RETENTION_MS,
} from '../../../src/modules/agent-harness/domain/authority';
import { HarnessUnauthorizedError } from '../../../src/modules/agent-harness/domain/errors';
import {
  GATEWAY_TOKEN_AUDIENCE,
  GATEWAY_TOKEN_TTL_MS,
  GatewayClaims,
  gatewayGrantDigests,
  grantDigest,
  modelRouteId,
} from '../../../src/modules/agent-harness/domain/gateway-token';
import { RunSpec } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';

const VECTOR = JSON.parse(
  readFileSync(
    join(
      __dirname,
      '../../../../../../contracts/harness/vectors/gateway-token.json',
    ),
    'utf8',
  ),
) as {
  jwk: Record<string, string>;
  kid: string;
  publicKeyPem: string;
  nowSeconds: number;
  toolIds: string[];
  toolIdsDigest: string;
  routesDigest: string;
  header: Record<string, unknown>;
  tokens: Array<{
    binding: string;
    payload: Record<string, unknown>;
    token: string;
  }>;
  refused: Array<{
    binding: string;
    reason: string;
    payload: Record<string, unknown>;
    token: string;
  }>;
};

const START = Date.UTC(2026, 9, 3);

const manualClock = (start = START) => {
  let now = start;
  return {
    now: () => now,
    advance: (ms: number) => {
      now += ms;
    },
  };
};

const RUN: GatewayClaims = {
  binding: 'run',
  orgId: 'org-1',
  sessionId: 'session-1',
  runId: 'run-2',
  parentRunId: 'run-1',
  attemptId: 'run-2.a1',
  fence: 3,
  toolIdsDigest: grantDigest(['crm@inst-1/lookup']),
  routesDigest: grantDigest([]),
};

const setup = () => {
  const clock = manualClock();
  const signing = new InMemoryConfigDocumentStore<AttemptKeyring>();
  const published = new InMemoryConfigDocumentStore<PublishedAttemptKeys>();
  const authority = new GatewayTokenAuthority({ signingKeys: signing, publishedKeys: published, clock });
  return { clock, signing, published, authority };
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

describe('E2: gateway-token authority', () => {
  it('NA-1: a run token carries the binding, an hgk_ kid and a 10-minute TTL', async () => {
    const { authority } = setup();
    await authority.provision(null);
    const issued = await authority.issue(RUN);
    const decoded = decodeAttemptToken(issued.token);
    expect(decoded.header).to.deep.include({ alg: 'EdDSA', typ: 'JWT' });
    expect((decoded.header as { kid: string }).kid).to.match(/^hgk_/);
    expect(Object.keys(decoded.payload as object)).to.deep.equal([
      'iss',
      'aud',
      'bnd',
      'orgId',
      'sessionId',
      'runId',
      'parentRunId',
      'attemptId',
      'fence',
      'toolIdsDigest',
      'routesDigest',
      'iat',
      'exp',
    ]);
    expect(issued.expiresAt).to.equal(START + GATEWAY_TOKEN_TTL_MS);
    expect(await authority.verify(issued.token)).to.deep.equal(RUN);
  });

  it("SR-14: a stage-1 request token Node signed before S1R is refused as malformed", async () => {
    const clock = manualClock(VECTOR.nowSeconds * 1000);
    const signing = new InMemoryConfigDocumentStore<AttemptKeyring>();
    const privateKey = createPrivateKey({ key: { ...VECTOR.jwk } as never, format: 'jwk' });
    await signing.replace(null, {
      activeKid: VECTOR.kid,
      keys: [
        {
          kid: VECTOR.kid,
          privateKey: privateKey.export({ type: 'pkcs8', format: 'pem' }),
          publicKey: VECTOR.publicKeyPem,
          createdAt: VECTOR.nowSeconds * 1000,
          retiredAt: null,
        },
      ],
      rotateId: null,
    });
    const authority = new GatewayTokenAuthority({
      signingKeys: signing,
      publishedKeys: new InMemoryConfigDocumentStore<PublishedAttemptKeys>(),
      clock,
    });
    expect(VECTOR.refused.length).to.be.greaterThan(0);
    for (const entry of VECTOR.refused) {
      expect(entry.payload.bnd).to.equal('request');
      expect(await refusal(authority.verify(entry.token))).to.equal('malformed');
    }
    for (const run of VECTOR.tokens) {
      expect((await authority.verify(run.token)).binding).to.equal('run');
    }
  });

  it('NA-3: rotation signs with a new kid; the old verifies within the overlap, then not', async () => {
    const { authority, clock, published } = setup();
    const before = await authority.issue(RUN);
    await authority.rotate();
    const after = await authority.issue(RUN);
    const kid = (token: string) =>
      (decodeAttemptToken(token).header as { kid: string }).kid;
    expect(kid(after.token)).to.not.equal(kid(before.token));
    clock.advance(GATEWAY_TOKEN_TTL_MS - 1);
    expect(await authority.verify(before.token)).to.deep.equal(RUN);
    clock.advance(RETIRED_KEY_RETENTION_MS);
    expect(await refusal(authority.verify(after.token))).to.equal('expired');
    expect(await refusal(authority.verify(before.token))).to.equal(
      'unknown_key',
    );
    const document = (await published.read()).value!;
    expect(document.audience).to.equal(GATEWAY_TOKEN_AUDIENCE);
    expect(JSON.stringify(document)).to.not.include('PRIVATE KEY');
    expect(document.keys).to.have.length(2);
  });

  it('NA-4: attempt tokens and gateway tokens never stand in for each other', async () => {
    const clock = manualClock();
    const attempts = new AttemptTokenAuthority({
      signingKeys: new InMemoryConfigDocumentStore(),
      publishedKeys: new InMemoryConfigDocumentStore(),
      clock,
    });
    const { authority } = setup();
    const attemptToken = (
      await attempts.issue(
        {
          orgId: 'org-1',
          runId: 'run-2',
          attemptId: 'run-2.a1',
          fence: 3,
          workerId: 'w',
        },
        60_000,
      )
    ).token;
    const gatewayToken = (await authority.issue(RUN)).token;
    expect(await refusal(authority.verify(attemptToken))).to.equal(
      'unknown_key',
    );
    expect(await refusal(attempts.verify(gatewayToken))).to.equal(
      'unknown_key',
    );
  });

  it('NA-5: refuses expired, unknown-kid, foreign-audience and tampered tokens', async () => {
    const { authority, clock } = setup();
    const issued = await authority.issue(RUN);
    const [header, payload, signature] = issued.token.split('.') as [
      string,
      string,
      string,
    ];
    const forged = Buffer.from(
      JSON.stringify({
        ...(decodeAttemptToken(issued.token).payload as object),
        runId: 'run-9',
      }),
    ).toString('base64url');
    expect(
      await refusal(authority.verify(`${header}.${forged}.${signature}`)),
    ).to.equal('signature');
    const otherKid = Buffer.from(
      JSON.stringify({ alg: 'EdDSA', typ: 'JWT', kid: 'hgk_other' }),
    ).toString('base64url');
    expect(
      await refusal(authority.verify(`${otherKid}.${payload}.${signature}`)),
    ).to.equal('unknown_key');
    const none = Buffer.from(
      JSON.stringify({ alg: 'none', typ: 'JWT', kid: 'x' }),
    ).toString('base64url');
    expect(await refusal(authority.verify(`${none}.${payload}.`))).to.equal(
      'malformed',
    );
    clock.advance(GATEWAY_TOKEN_TTL_MS);
    expect(await refusal(authority.verify(issued.token))).to.equal('expired');
  });

  it('GT-9: the shared vector signs byte-identically and verifies', () => {
    const key = createPrivateKey({
      key: { ...VECTOR.jwk } as never,
      format: 'jwk',
    });
    const publicKey = createPublicKey(VECTOR.publicKeyPem);
    expect(signingKeyId(publicKey, 'hgk_')).to.equal(VECTOR.kid);
    for (const entry of VECTOR.tokens) {
      const token = encodeAttemptToken(VECTOR.header, entry.payload, key);
      expect(token, entry.binding).to.equal(entry.token);
      expect(
        verifyAttemptSignature(decodeAttemptToken(token), publicKey),
      ).to.equal(true);
    }
  });

  it('NA-8: grant digests equal the Python side (shared vector)', () => {
    expect(grantDigest(VECTOR.toolIds)).to.equal(VECTOR.toolIdsDigest);
    expect(grantDigest([...VECTOR.toolIds].reverse())).to.equal(
      VECTOR.toolIdsDigest,
    );
    expect(grantDigest([])).to.equal(VECTOR.routesDigest);
  });

  it('NG-1: the routes digest is the pinned model policy, equal to the Python side', () => {
    const routes = JSON.parse(
      readFileSync(
        join(
          __dirname,
          '../../../../../../contracts/harness/vectors/model-routes.json',
        ),
        'utf8',
      ),
    ) as {
      routes: Array<{ provider: string; model: string }>;
      routesDigest: string;
      emptyDigest: string;
    };
    expect(grantDigest(routes.routes.map(modelRouteId))).to.equal(
      routes.routesDigest,
    );
    const spec = {
      grants: { tools: [] },
      model: { policy: routes.routes, promptVersion: 'p' },
    } as unknown as RunSpec;
    expect(gatewayGrantDigests(spec).routesDigest).to.equal(
      routes.routesDigest,
    );
    expect(grantDigest([])).to.equal(routes.emptyDigest);
  });

  it('A1 AD-10: a pinned model account is part of the route id, as in Python', () => {
    const vector = JSON.parse(
      readFileSync(
        join(
          __dirname,
          '../../../../../../contracts/harness/vectors/model-routes.json',
        ),
        'utf8',
      ),
    ) as {
      accountRoutes: RunSpec['model']['policy'];
      accountRouteIds: string[];
      accountRoutesDigest: string;
    };
    expect(vector.accountRoutes.map(modelRouteId)).to.deep.equal(
      vector.accountRouteIds,
    );
    const spec = {
      grants: { tools: [] },
      model: { policy: vector.accountRoutes, promptVersion: 'p' },
    } as unknown as RunSpec;
    expect(gatewayGrantDigests(spec).routesDigest).to.equal(
      vector.accountRoutesDigest,
    );
  });
});
