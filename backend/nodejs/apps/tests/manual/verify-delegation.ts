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

  console.log('\nrevoke');
  await service.revoke(org, adminId, accountId, person._id.toString());
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
  check('both attempts are recorded', events.length === 2, `got ${events.length}`);
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

  console.log('\nrevoking delegated tokens by their actor');
  const tokenService = new OAuthTokenService(
    logger as never,
    { algorithm: 'HS256', secret: 'verification-only-secret' } as never,
    'http://localhost:3000',
  );
  const liveDelegated = await OAuthAccessToken.create({
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

  const revokedCount = await tokenService.revokeDelegatedTokensForActor(
    accountId,
  );
  check('the delegated token is found by its actor', revokedCount === 1, `count=${revokedCount}`);
  check(
    'and is actually marked revoked',
    (await OAuthAccessToken.findById(liveDelegated._id))?.isRevoked === true,
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
  check(
    'but the log survives, which is the point of keeping it',
    (await DelegationEvent.countDocuments({})) === 2,
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
