import { expect } from 'chai';
import jwt from 'jsonwebtoken';
import { refreshTokenJwtGenerator } from '../../../src/libs/utils/createJwt';
import { UnauthorizedError } from '../../../src/libs/errors/http.errors';
import {
  legacyFamilyId,
  LEGACY_TOKEN_ID,
  REFRESH_REUSE_GRACE_MS,
  REFRESH_REUSE_REASON,
  SessionReuseDetected,
  SessionRotation,
  SIGNED_OUT_REASON,
} from '../../../src/modules/auth/session-rotation';
import { InMemorySessionFamilies } from '../../support/auth/in-memory-session-families';

type Claims = { userId: string; orgId: string; sid?: string; jti?: string; exp: number; iat: number };
const claimsOf = (token: string): Claims => jwt.decode(token) as Claims;

// R43AUTH-FU-1 (ADR-WEB-02 D9): every refresh replaces the refresh token; a replaced token presented again ends the
// sign-in it belongs to, except within a short grace for a second tab or a retried answer.
describe('refresh-token rotation with reuse detection', () => {
  let store: InMemorySessionFamilies;
  let now: Date;
  let sessions: SessionRotation;

  const present = (token: string) => sessions.rotate(token, claimsOf(token));

  beforeEach(() => {
    store = new InMemorySessionFamilies();
    now = new Date('2026-11-14T10:00:00Z');
    sessions = new SessionRotation(() => 'scoped-secret', () => store, () => now);
  });

  it('starts a family at sign-in: the token names it and its first token id', async () => {
    const started = await sessions.start('u1', 'o1');
    const claims = claimsOf(started.refreshToken);
    expect(claims.sid).to.equal(started.familyId);
    expect(claims.jti).to.be.a('string').and.not.empty;
    const family = await store.get('o1', started.familyId);
    expect(family).to.include({ userId: 'u1', currentTokenId: claims.jti, previousTokenId: null });
    expect(family?.expiresAt.getTime()).to.equal(claims.exp * 1000);
  });

  it('replaces the token on every refresh, keeping the family and the end of the sign-in', async () => {
    const first = (await sessions.start('u1', 'o1')).refreshToken;
    now = new Date(now.getTime() + 3_600_000);
    const second = await present(first);
    expect(second.outcome).to.equal('rotated');
    const a = claimsOf(first);
    const b = claimsOf(second.refreshToken);
    expect(b.sid).to.equal(a.sid);
    expect(b.jti).to.not.equal(a.jti);
    expect(b.exp, 'a rotation does not extend the sign-in').to.equal(a.exp);
    expect(await store.get('o1', a.sid as string)).to.include({ currentTokenId: b.jti, previousTokenId: a.jti });
    const third = await present(second.refreshToken);
    expect(third.outcome).to.equal('rotated');
  });

  it('answers the token just replaced, within the grace, with the current token (a second tab)', async () => {
    const first = (await sessions.start('u1', 'o1')).refreshToken;
    const winner = await present(first);
    now = new Date(now.getTime() + REFRESH_REUSE_GRACE_MS - 1);
    const late = await present(first);
    expect(late.outcome).to.equal('grace');
    expect(claimsOf(late.refreshToken).jti).to.equal(claimsOf(winner.refreshToken).jti);
    expect((await store.get('o1', winner.familyId))?.revokedAt).to.equal(null);
  });

  it('two refreshes with the same token at the same moment both succeed and end on the same token', async () => {
    const first = (await sessions.start('u1', 'o1')).refreshToken;
    const [a, b] = await Promise.all([present(first), present(first)]);
    expect([a.outcome, b.outcome].sort()).to.deep.equal(['grace', 'rotated']);
    expect(claimsOf(a.refreshToken).jti).to.equal(claimsOf(b.refreshToken).jti);
  });

  it('ends the family when a replaced token comes back after the grace, and refuses its current token too', async () => {
    const first = (await sessions.start('u1', 'o1')).refreshToken;
    const second = await present(first);
    now = new Date(now.getTime() + REFRESH_REUSE_GRACE_MS + 1);
    let error: unknown;
    try {
      await present(first);
    } catch (e) {
      error = e;
    }
    expect(error).to.be.instanceOf(SessionReuseDetected);
    expect(error).to.be.instanceOf(UnauthorizedError);
    expect((error as SessionReuseDetected).endedNow).to.equal(true);
    expect((error as SessionReuseDetected).familyId).to.equal(second.familyId);
    const family = await store.get('o1', second.familyId);
    expect(family?.revokedReason).to.equal(REFRESH_REUSE_REASON);

    let current: unknown;
    try {
      await present(second.refreshToken);
    } catch (e) {
      current = e;
    }
    expect(current, 'the holder of the newest token must sign in again').to.be.instanceOf(UnauthorizedError);
    expect(current).to.not.be.instanceOf(SessionReuseDetected);
  });

  it('a token two rotations old is a replay at once', async () => {
    const first = (await sessions.start('u1', 'o1')).refreshToken;
    const second = await present(first);
    await present(second.refreshToken);
    let error: unknown;
    try {
      await present(first);
    } catch (e) {
      error = e;
    }
    expect(error).to.be.instanceOf(SessionReuseDetected);
  });

  it('refuses a family that is gone or belongs to another person', async () => {
    const orphan = refreshTokenJwtGenerator('u1', 'o1', 'scoped-secret', { sid: 'missing', jti: 'j1' });
    let gone: unknown;
    try {
      await present(orphan);
    } catch (e) {
      gone = e;
    }
    expect(gone).to.be.instanceOf(UnauthorizedError);

    const started = await sessions.start('u1', 'o1');
    const forged = refreshTokenJwtGenerator('u2', 'o1', 'scoped-secret', {
      sid: started.familyId,
      jti: claimsOf(started.refreshToken).jti as string,
    });
    let other: unknown;
    try {
      await present(forged);
    } catch (e) {
      other = e;
    }
    expect(other).to.be.instanceOf(UnauthorizedError);
    expect((await store.get('o1', started.familyId))?.revokedAt).to.equal(null);
  });

  it('adopts a token issued before rotation into a family, once, so a copy of it is caught too', async () => {
    const legacy = refreshTokenJwtGenerator('u1', 'o1', 'scoped-secret');
    const adopted = await present(legacy);
    expect(adopted.outcome).to.equal('adopted');
    expect(adopted.familyId).to.equal(legacyFamilyId(legacy));
    expect(claimsOf(adopted.refreshToken).exp).to.equal(claimsOf(legacy).exp);
    expect(await store.get('o1', adopted.familyId)).to.include({ previousTokenId: LEGACY_TOKEN_ID });

    const second = await present(legacy);
    expect(second.outcome, 'a second tab moving at the same time').to.equal('grace');

    now = new Date(now.getTime() + REFRESH_REUSE_GRACE_MS + 1);
    let error: unknown;
    try {
      await present(legacy);
    } catch (e) {
      error = e;
    }
    expect(error).to.be.instanceOf(SessionReuseDetected);
  });

  it('an ended family is not ended twice: only the first replay is recorded', async () => {
    const first = (await sessions.start('u1', 'o1')).refreshToken;
    await present(first);
    now = new Date(now.getTime() + REFRESH_REUSE_GRACE_MS + 1);
    const ends: boolean[] = [];
    for (let i = 0; i < 2; i += 1) {
      try {
        await present(first);
      } catch (e) {
        if (e instanceof SessionReuseDetected) ends.push(e.endedNow);
        else ends.push(false);
      }
    }
    expect(ends).to.deep.equal([true, false]);
  });

  // R48MIGFIX-3: a refresh answered just before a sign-out mints a token the recorded sign-out spares (it ends tokens
  // issued more than a second before it), and its cookie may land after the sign-out cleared the old one.
  describe('a sign-out ends the families its cookies held', () => {
    const refusal = async (token: string): Promise<unknown> => {
      try {
        await present(token);
        return null;
      } catch (e) {
        return e;
      }
    };

    it('a token a refresh minted just before the sign-out no longer refreshes', async () => {
      const first = (await sessions.start('u1', 'o1')).refreshToken;
      const raced = await present(first);
      expect(await sessions.endSignedOut('o1', 'u1', [first])).to.equal(1);
      expect(await store.get('o1', raced.familyId)).to.include({ revokedReason: SIGNED_OUT_REASON });
      expect(await refusal(raced.refreshToken)).to.be.instanceOf(UnauthorizedError);
      expect(await refusal(first), 'nor does the one it replaced').to.be.instanceOf(UnauthorizedError);
    });

    it('a refresh that arrives after the sign-out is refused', async () => {
      const first = (await sessions.start('u1', 'o1')).refreshToken;
      await sessions.endSignedOut('o1', 'u1', [first]);
      expect(await refusal(first)).to.be.instanceOf(UnauthorizedError);
    });

    it('ends only the caller\'s own families, and passes over what is not a session token', async () => {
      const mine = await sessions.start('u1', 'o1');
      const theirs = await sessions.start('u2', 'o1');
      const elsewhere = await sessions.start('u1', 'o2');
      const ended = await sessions.endSignedOut('o1', 'u1', [
        theirs.refreshToken,
        elsewhere.refreshToken,
        'not-a-jwt',
        refreshTokenJwtGenerator('u1', 'o1', 'scoped-secret'),
        mine.refreshToken,
        mine.refreshToken,
      ]);
      expect(ended).to.equal(1);
      expect((await store.get('o1', mine.familyId))?.revokedAt).to.not.equal(null);
      expect((await store.get('o1', theirs.familyId))?.revokedAt).to.equal(null);
      expect((await store.get('o2', elsewhere.familyId))?.revokedAt).to.equal(null);
    });
  });
});

// R51-COMPAT item 7 (R50OFF-FU-1, ADR-OPS-07): a body-token client on the compatibility profile that did not ask for
// rotation keeps its token, as before D9; a token its family replaced is still a replay.
describe('R51-COMPAT: a refresh that keeps its token', () => {
  let store: InMemorySessionFamilies;
  let now: Date;
  let sessions: SessionRotation;

  beforeEach(() => {
    store = new InMemorySessionFamilies();
    now = new Date('2026-11-14T10:00:00Z');
    sessions = new SessionRotation(() => 'scoped-secret', () => store, () => now);
  });

  it('accepts the same token at every refresh and changes nothing in its family', async () => {
    const first = (await sessions.start('u1', 'o1')).refreshToken;
    for (let i = 0; i < 3; i += 1) {
      now = new Date(now.getTime() + REFRESH_REUSE_GRACE_MS * 2);
      const kept = await sessions.keep(first, claimsOf(first));
      expect(kept).to.include({ refreshToken: first, outcome: 'kept' });
    }
    const family = await store.get('o1', claimsOf(first).sid as string);
    expect(family).to.include({ currentTokenId: claimsOf(first).jti, previousTokenId: null, revokedAt: null });
  });

  it('a token from before rotation works and starts no family', async () => {
    const legacy = refreshTokenJwtGenerator('u1', 'o1', 'scoped-secret');
    expect((await sessions.keep(legacy, claimsOf(legacy))).outcome).to.equal('kept');
    expect(await store.get('o1', legacyFamilyId(legacy))).to.equal(null);
  });

  it('still ends a family whose replaced token comes back after the grace', async () => {
    const first = (await sessions.start('u1', 'o1')).refreshToken;
    const second = await sessions.rotate(first, claimsOf(first));
    now = new Date(now.getTime() + REFRESH_REUSE_GRACE_MS + 1);
    let error: unknown;
    try {
      await sessions.keep(first, claimsOf(first));
    } catch (e) {
      error = e;
    }
    expect(error).to.be.instanceOf(SessionReuseDetected);
    expect((await store.get('o1', second.familyId))?.revokedReason).to.equal(REFRESH_REUSE_REASON);
  });

  it('refuses a token of a family that ended', async () => {
    const first = (await sessions.start('u1', 'o1')).refreshToken;
    await sessions.endSignedOut('o1', 'u1', [first]);
    let error: unknown;
    try {
      await sessions.keep(first, claimsOf(first));
    } catch (e) {
      error = e;
    }
    expect(error).to.be.instanceOf(UnauthorizedError);
  });
});

