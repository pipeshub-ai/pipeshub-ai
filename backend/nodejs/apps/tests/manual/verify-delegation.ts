/**
 * Real-MongoDB verification for service-account delegation.
 *
 * The unit suite mocks Mongoose, so it can prove the branching but not the
 * database: a unique index that does not exist, a query that silently matches
 * nothing, or a TTL that was never built all pass a mocked test. Everything
 * here runs against a real mongod.
 *
 *   npx cross-env TS_NODE_PROJECT=tsconfig.test.json npx ts-node <this file>
 */
import 'reflect-metadata';
import mongoose from 'mongoose';
import { DelegationService } from '../../src/modules/oauth_provider/services/delegation.service';

const MONGO = process.env.VERIFY_MONGO_URI ?? 'mongodb://127.0.0.1:37117/delegation_verify';

let passed = 0;
let failed = 0;

function check(name: string, condition: boolean, detail = ''): void {
  if (condition) {
    passed += 1;
    console.log(`  ok    ${name}`);
  } else {
    failed += 1;
    console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ''}`);
  }
}

async function expectThrows(
  name: string,
  run: () => Promise<unknown>,
  contains: string,
): Promise<void> {
  try {
    await run();
    check(name, false, 'it did not throw');
  } catch (error) {
    const message = (error as Error).message;
    check(name, message.includes(contains), `message was: ${message}`);
  }
}

async function main(): Promise<void> {
  await mongoose.connect(MONGO);
  await mongoose.connection.db!.dropDatabase();

  const { Users } = await import(
    '../../src/modules/user_management/schema/users.schema'
  );
  const { ServiceAccountDelegation } = await import(
    '../../src/modules/oauth_provider/schema/service-account-delegation.schema'
  );
  const { DelegationEvent, DELEGATION_EVENT_RETENTION_DAYS } = await import(
    '../../src/modules/oauth_provider/schema/delegation-event.schema'
  );
  const { OAuthAccessToken } = await import(
    '../../src/modules/oauth_provider/schema/oauth.access_token.schema'
  );
  const { OAuthTokenService } = await import(
    '../../src/modules/oauth_provider/services/oauth_token.service'
  );

  await Promise.all([
    ServiceAccountDelegation.init(),
    DelegationEvent.init(),
    OAuthAccessToken.init(),
    Users.init(),
  ]);

  const logger = {
    info: () => undefined,
    debug: () => undefined,
    warn: () => undefined,
    error: () => undefined,
  };
  const service = new DelegationService(logger as never);

  const orgId = new mongoose.Types.ObjectId();
  const otherOrgId = new mongoose.Types.ObjectId();

  const mk = async (doc: Record<string, unknown>) =>
    (await Users.create({
      orgId,
      firstName: 'T',
      ...doc,
    })) as unknown as { _id: mongoose.Types.ObjectId };

  const account = await mk({
    email: 'svc-nightly@service.pipeshub.internal',
    fullName: 'Nightly sync',
    kind: 'service',
    role: 'member',
  });
  const person = await mk({
    email: 'person@example.com',
    fullName: 'A Person',
    kind: 'human',
    role: 'member',
  });
  const boss = await mk({
    email: 'boss@example.com',
    fullName: 'The Boss',
    kind: 'human',
    role: 'admin',
  });
  const disabled = await mk({
    email: 'away@example.com',
    kind: 'human',
    role: 'member',
    isDisabled: true,
  });
  const otherOrgPerson = (await Users.create({
    orgId: otherOrgId,
    email: 'outsider@example.com',
    kind: 'human',
    role: 'member',
  })) as unknown as { _id: mongoose.Types.ObjectId };

  const org = orgId.toString();
  const accountId = account._id.toString();
  const adminId = boss._id.toString();

  console.log('\nresolveSubject, against real records');
  check(
    'an ordinary colleague is accepted',
    (await service.resolveSubject(org, 'person@example.com')).ok === true,
  );
  check(
    'an administrator is refused',
    (await service.resolveSubject(org, 'boss@example.com')).refusedReason ===
      'subject_is_admin',
  );
  check(
    'a service account is refused',
    (await service.resolveSubject(org, 'svc-nightly@service.pipeshub.internal'))
      .refusedReason === 'subject_is_service_account',
  );
  check(
    'a disabled person is refused',
    (await service.resolveSubject(org, 'away@example.com')).refusedReason ===
      'subject_disabled',
  );
  check(
    'someone in another organisation is invisible',
    (await service.resolveSubject(org, 'outsider@example.com')).refusedReason ===
      'subject_not_found',
  );
  check(
    'a user id resolves as well as an address',
    (await service.resolveSubject(org, person._id.toString())).ok === true,
  );
  check(
    'a malformed subject is refused rather than throwing',
    (await service.resolveSubject(org, 'not-an-id-or-email')).refusedReason ===
      'subject_not_found',
  );

  // A value that arrived as an object rather than a string must never be read
  // as a set of Mongo operators. The route validators refuse one before it
  // gets here, so these call the service directly: the point is that the
  // queries are safe on their own, without relying on a check three frames up.
  //
  // "Never matches" is the property being tested, and both a clean refusal and
  // a thrown cast error satisfy it. Which one happens depends on the field:
  // `$eq` keeps the value a value, and Mongoose's own casting then refuses an
  // object on an ObjectId path rather than querying with it.
  console.log('\nqueries treat input as values, not operators');
  const injection = { $ne: null } as unknown as string;

  const neverMatches = async (
    name: string,
    run: () => Promise<boolean>,
  ): Promise<void> => {
    try {
      check(name, (await run()) === false, 'it matched something');
    } catch {
      check(name, true);
    }
  };

  check(
    'an operator object cannot stand in for a subject',
    (await service.resolveSubject(org, injection)).ok === false,
  );
  await service.grant(org, adminId, accountId, 'person@example.com');
  await neverMatches('nor for a service account id', () =>
    service.isDelegationAllowed(org, injection, person._id.toString()),
  );
  await neverMatches('nor for the person on a real delegation', () =>
    service.isDelegationAllowed(org, accountId, injection),
  );
  await neverMatches('nor for both at once', () =>
    service.isDelegationAllowed(org, injection, injection),
  );
  check(
    'and the real pair still matches, so the check is not vacuous',
    (await service.isDelegationAllowed(
      org,
      accountId,
      person._id.toString(),
    )) === true,
  );
  await service.revoke(org, adminId, accountId, person._id.toString());

  console.log('\ngrant and the allow-list');
  const granted = await service.grant(
    org,
    adminId,
    accountId,
    'person@example.com',
    'nightly digest',
  );
  check('a grant is written', granted.subjectUserId === person._id.toString());
  check(
    'the pair is now allowed',
    (await service.isDelegationAllowed(org, accountId, person._id.toString())) ===
      true,
  );
  check(
    'an ungranted pair is not allowed',
    (await service.isDelegationAllowed(org, accountId, boss._id.toString())) ===
      false,
  );

  // The unique index is the thing a mocked test cannot prove.
  const before = await ServiceAccountDelegation.countDocuments({});
  await service.grant(org, adminId, accountId, 'person@example.com');
  const after = await ServiceAccountDelegation.countDocuments({});
  check(
    'granting twice does not create a second row',
    before === after,
    `before=${before} after=${after}`,
  );

  const indexes = await ServiceAccountDelegation.collection.indexes();
  const unique = indexes.find(
    (i) =>
      i.unique === true &&
      JSON.stringify(i.key) ===
        JSON.stringify({ orgId: 1, serviceAccountId: 1, subjectUserId: 1 }),
  );
  check('the uniqueness rule exists in the database', unique !== undefined);

  await expectThrows(
    'an administrator cannot be put on the allow-list',
    () => service.grant(org, adminId, accountId, 'boss@example.com'),
    'administrator',
  );
  await expectThrows(
    'a person from another organisation cannot be added',
    () => service.grant(org, adminId, accountId, 'outsider@example.com'),
    'No such person',
  );
  await expectThrows(
    'a service account from another organisation cannot be targeted',
    () =>
      service.grant(
        otherOrgId.toString(),
        adminId,
        accountId,
        'person@example.com',
      ),
    'Service account not found',
  );

  console.log('\nlistDelegations reflects what the exchange would do');
  const promoted = await mk({
    email: 'rising@example.com',
    kind: 'human',
    role: 'member',
  });
  await service.grant(org, adminId, accountId, 'rising@example.com');
  await Users.updateOne({ _id: promoted._id }, { $set: { role: 'admin' } });
  const listed = await service.listDelegations(org, accountId);
  const promotedRow = listed.find(
    (d) => d.subjectUserId === promoted._id.toString(),
  );
  check(
    'a person promoted to administrator is still listed',
    promotedRow !== undefined,
  );
  check(
    'and is marked unusable rather than hidden',
    promotedRow?.inactive === true,
    `inactive=${String(promotedRow?.inactive)}`,
  );
  check(
    'the exchange would refuse them too',
    (await service.resolveSubject(org, 'rising@example.com')).refusedReason ===
      'subject_is_admin',
  );

  // The auth middleware re-reads this row on every request a delegated token
  // makes, with these exact clauses. Running them here proves the casting
  // works against real documents — a unit test with a stubbed model cannot.
  console.log('\nthe query the middleware repeats on every request');
  const middlewareQuery = {
    orgId: { $eq: org },
    serviceAccountId: { $eq: accountId },
    subjectUserId: { $eq: person._id.toString() },
  };
  check(
    'it finds a live permission',
    (await ServiceAccountDelegation.findOne(middlewareQuery)
      .select('_id')
      .lean()
      .exec()) !== null,
  );
  check(
    'and not one in another organisation',
    (await ServiceAccountDelegation.findOne({
      ...middlewareQuery,
      orgId: { $eq: otherOrgId.toString() },
    })
      .select('_id')
      .lean()
      .exec()) === null,
  );

  console.log('\nrevoke');
  await service.revoke(org, adminId, accountId, person._id.toString());
  check(
    'the middleware query stops matching the moment it is withdrawn',
    (await ServiceAccountDelegation.findOne(middlewareQuery)
      .select('_id')
      .lean()
      .exec()) === null,
  );
  check(
    'a revoked pair is no longer allowed',
    (await service.isDelegationAllowed(org, accountId, person._id.toString())) ===
      false,
  );
  await expectThrows(
    'revoking something that is not there is an error, not a silent success',
    () => service.revoke(org, adminId, accountId, person._id.toString()),
    'Delegation not found',
  );

  console.log('\nthe audit log');
  await service.recordEvent({
    orgId: org,
    serviceAccountId: accountId,
    subjectUserId: person._id.toString(),
    requestedSubject: 'person@example.com',
    outcome: 'issued',
    scopes: ['kb:read'],
    jti: 'jti-abc',
  });
  await service.recordEvent({
    orgId: org,
    serviceAccountId: accountId,
    requestedSubject: 'boss@example.com',
    outcome: 'refused',
    refusedReason: 'subject_is_admin',
  });
  const events = await service.listEvents(org);
  const exchanges = events.filter(
    (e) => e.outcome === 'issued' || e.outcome === 'refused',
  );
  check(
    'both attempts are recorded',
    exchanges.length === 2,
    `got ${exchanges.length}`,
  );

  // The allow-list changes share the log, so a revoked permission still has a
  // record of who granted it.
  const grants = events.filter((e) => e.outcome === 'granted');
  const revocations = events.filter((e) => e.outcome === 'revoked');
  check('grants are recorded', grants.length > 0);
  check('revocations are recorded', revocations.length > 0);
  check(
    'a grant names the administrator who made it',
    grants.every((e) => e.actedBy === adminId),
  );
  check(
    'and resolves their address for the reader',
    grants.some((e) => e.actedByEmail === 'boss@example.com'),
  );
  check(
    'a grant keeps the reason that was given',
    grants.some((e) => e.note === 'nightly digest'),
  );
  check(
    'the refusal is recorded with its cause',
    events.some(
      (e) => e.outcome === 'refused' && e.refusedReason === 'subject_is_admin',
    ),
  );
  check(
    'the issued record names the token',
    events.some((e) => e.outcome === 'issued' && e.jti === 'jti-abc'),
  );
  check(
    'the log reads newest first',
    events[0].createdAt >= events[1].createdAt,
  );
  check(
    'addresses are resolved for the reader',
    events.some((e) => e.serviceAccountEmail?.startsWith('svc-nightly')),
  );

  const eventIndexes = await DelegationEvent.collection.indexes();
  const ttl = eventIndexes.find((i) => i.expireAfterSeconds !== undefined);
  check('the log expires rather than growing forever', ttl !== undefined);
  check(
    'and it is kept past the longest service-token lifetime',
    (ttl?.expireAfterSeconds ?? 0) === DELEGATION_EVENT_RETENTION_DAYS * 86400 &&
      DELEGATION_EVENT_RETENTION_DAYS > 365,
  );
  check(
    'the TTL counts from when the record was written, not when the token expired',
    JSON.stringify(ttl?.key) === JSON.stringify({ createdAt: 1 }),
    JSON.stringify(ttl?.key),
  );

  // Everything above drives the service directly. This drives the whole
  // exchange: a real signed service token, the real token service, and the
  // real audit writes — the only way to know the pieces agree.
  const tokenService = new OAuthTokenService(
    logger as never,
    { algorithm: 'HS256', secret: 'verification-only-secret' } as never,
    'http://localhost:3000',
  );

  console.log('\nthe exchange itself, end to end');
  const { TokenExchangeService } = await import(
    '../../src/modules/oauth_provider/services/token-exchange.service'
  );
  const { OAuthApp } = await import(
    '../../src/modules/oauth_provider/schema/oauth.app.schema'
  );

  const app = await OAuthApp.create({
    clientId: `svc-system:${org}`,
    clientSecretEncrypted: 'unused',
    name: 'Service Tokens',
    orgId,
    createdBy: boss._id,
    redirectUris: [],
    allowedGrantTypes: [],
    allowedScopes: ['kb:read', 'semantic:write'],
    isConfidential: true,
  });

  // Built as the Enterprise edition does. The checks below are about what the
  // exchange writes and revokes against a real database, which is the same in
  // both editions; whether the capability is offered at all is a separate
  // question with its own unit tests. Left at the community default, every
  // check here would pass for the wrong reason, because the exchange would
  // refuse before touching Mongo.
  const exchangeService = new TokenExchangeService(
    logger as never,
    tokenService,
    service,
    true,
  );

  const serviceToken = await tokenService.generateTokens(
    app as never,
    accountId,
    org,
    ['kb:read', 'semantic:write'],
    false,
  );

  await service.grant(org, adminId, accountId, 'person@example.com');
  const issued = await exchangeService.exchange({
    subjectToken: serviceToken.accessToken,
    requestedSubject: 'person@example.com',
  });
  check('a delegated token is issued', issued.access_token.length > 0);
  check('for five minutes', issued.expires_in === 300);

  // The claim every authorisation check reads must name the person, and `act`
  // must name the account that obtained it.
  const issuedPayload = await tokenService.verifyAccessToken(
    issued.access_token,
  );
  check(
    'and it speaks as the person, not the service account',
    issuedPayload.userId === person._id.toString(),
  );
  check(
    'while recording who obtained it',
    issuedPayload.act?.sub === accountId,
  );
  check(
    'the issue is in the log, tied to the token by jti',
    (await DelegationEvent.countDocuments({
      outcome: 'issued',
      jti: issuedPayload.jti,
    })) === 1,
  );

  // Someone the account may not act as.
  //
  // An active member who was never granted, deliberately. A disabled person
  // would be refused by `resolveSubject` before the allow-list is consulted
  // at all, so the check would pass while never reaching the path it names.
  await mk({
    email: 'ungranted@example.com',
    fullName: 'Not Granted',
    kind: 'human',
    role: 'member',
  });
  await expectThrows(
    'acting as someone not on the list is refused',
    () =>
      exchangeService.exchange({
        subjectToken: serviceToken.accessToken,
        requestedSubject: 'ungranted@example.com',
      }),
    'may not act as',
  );
  check(
    'and the log records that exact cause, for that exact person',
    (await DelegationEvent.countDocuments({
      outcome: 'refused',
      refusedReason: 'not_on_allow_list',
      requestedSubject: 'ungranted@example.com',
    })) === 1,
  );

  // And the other shape, separately, so neither can stand in for the other.
  await expectThrows(
    'acting as a disabled person is refused before the list is consulted',
    () =>
      exchangeService.exchange({
        subjectToken: serviceToken.accessToken,
        requestedSubject: 'away@example.com',
      }),
    'may not act as',
  );
  check(
    'and is recorded as a disabled subject, not as a missing permission',
    (await DelegationEvent.countDocuments({
      outcome: 'refused',
      refusedReason: 'subject_disabled',
      requestedSubject: 'away@example.com',
    })) === 1,
  );

  console.log('\nattempts by an account that is no longer usable');
  await Users.updateOne({ _id: account._id }, { $set: { isDisabled: true } });

  await expectThrows(
    'a disabled account cannot exchange its token',
    () =>
      exchangeService.exchange({
        subjectToken: serviceToken.accessToken,
        requestedSubject: 'person@example.com',
      }),
    'disabled',
  );
  check(
    'and the attempt is persisted, not only refused',
    (await DelegationEvent.countDocuments({
      outcome: 'refused',
      refusedReason: 'service_account_disabled',
    })) === 1,
  );

  // The case the required-string rule used to swallow: a disabled account
  // asking for nobody at all. The refusal is decided before the subject is
  // looked at, so the record carries a blank subject and must still be saved.
  await expectThrows(
    'and so is an attempt that names nobody',
    () =>
      exchangeService.exchange({
        subjectToken: serviceToken.accessToken,
      }),
    'disabled',
  );
  check(
    'including when it asked for nobody at all',
    (await DelegationEvent.countDocuments({
      outcome: 'refused',
      refusedReason: 'service_account_disabled',
      requestedSubject: '',
    })) === 1,
  );

  await Users.updateOne({ _id: account._id }, { $set: { isDisabled: false } });

  console.log('\nrevoking delegated tokens by their actor');
  const handMadeDelegated = await OAuthAccessToken.create({
    tokenHash: 'hash-delegated',
    clientId: `svc-system:${org}`,
    userId: person._id,
    actorUserId: account._id,
    orgId,
    scopes: ['kb:read'],
    expiresAt: new Date(Date.now() + 300_000),
  });
  const someoneElses = await OAuthAccessToken.create({
    tokenHash: 'hash-unrelated',
    clientId: `pat-system:${org}`,
    userId: person._id,
    orgId,
    scopes: ['kb:read'],
    expiresAt: new Date(Date.now() + 300_000),
  });

  // Counted first rather than hard-coded: the end-to-end section above issued
  // a real delegated token for this same account, so the number here depends
  // on what ran before. What matters is that the sweep finds every live one.
  const liveDelegated = await OAuthAccessToken.countDocuments({
    actorUserId: account._id,
    isRevoked: false,
  });
  const revokedCount = await tokenService.revokeDelegatedTokensForActor(
    accountId,
  );
  check(
    'the delegated tokens are found by their actor',
    revokedCount === liveDelegated && liveDelegated >= 2,
    `revoked=${revokedCount} live=${liveDelegated}`,
  );
  check(
    'and none is left behind',
    (await OAuthAccessToken.countDocuments({
      actorUserId: account._id,
      isRevoked: false,
    })) === 0,
  );
  check(
    'and is actually marked revoked',
    (await OAuthAccessToken.findById(handMadeDelegated._id))?.isRevoked ===
      true,
  );
  check(
    'the person’s own token is untouched',
    (await OAuthAccessToken.findById(someoneElses._id))?.isRevoked === false,
  );

  console.log('\nremoving every delegation when an account goes away');
  await service.grant(org, adminId, accountId, 'person@example.com');
  await service.revokeAllForServiceAccount(org, accountId);
  check(
    'the allow-list is emptied',
    (await ServiceAccountDelegation.countDocuments({
      serviceAccountId: account._id,
    })) === 0,
  );
  const survived = await DelegationEvent.countDocuments({});
  check(
    'but the log survives, which is the point of keeping it',
    survived >= 4,
    `only ${survived} records left`,
  );
  check(
    'including who granted a permission that no longer exists',
    (await DelegationEvent.countDocuments({ outcome: 'granted' })) > 0,
  );

  await mongoose.connection.db!.dropDatabase();
  await mongoose.disconnect();

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed === 0 ? 0 : 1);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
