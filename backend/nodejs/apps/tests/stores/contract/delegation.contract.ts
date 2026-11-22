/**
 * Contract every DelegationRepository must meet: the allow-list of subjects a service account may act as, and the event log.
 * Store-neutral: the store under test supplies the repository and a way to make an event older.
 */
import { expect } from 'chai';
import type { DelegationRepository } from '../../../src/libs/repositories/delegation';

export interface DelegationFixture {
  repo: DelegationRepository;
  newId(): string;
  /** Makes an event older, so ordering and retention do not depend on the clock. */
  setEventCreatedAt(eventId: string, at: Date): Promise<void>;
}

export function describeDelegationContract(fx: DelegationFixture): void {
  const { repo } = fx;
  const HEX = /^[0-9a-f]{24}$/;
  const grantInput = (org: string, sa: string, subject: string, extra: { reason?: string } = {}) => ({
    orgId: org,
    serviceAccountId: sa,
    subjectUserId: subject,
    grantedBy: fx.newId(),
    ...extra,
  });

  it('grants once; a repeat returns the stored row with created=false', async () => {
    const org = fx.newId(), sa = fx.newId(), user = fx.newId();
    const first = await repo.grant(grantInput(org, sa, user, { reason: 'ci' }));
    expect(first.created).to.equal(true);
    expect(first.row._id).to.match(HEX);
    expect(first.row).to.include({ orgId: org, serviceAccountId: sa, subjectUserId: user, reason: 'ci' });
    const again = await repo.grant(grantInput(org, sa, user));
    expect(again.created).to.equal(false);
    expect(again.row._id).to.equal(first.row._id);
    expect(await repo.countFor(org, sa)).to.equal(1);
  });

  it('trims the reason and refuses one longer than 280 characters', async () => {
    const org = fx.newId(), sa = fx.newId()
    const padded = await repo.grant(grantInput(org, sa, fx.newId(), { reason: '  because  ' }));
    expect(padded.row.reason).to.equal('because');
    const longest = await repo.grant(grantInput(org, sa, fx.newId(), { reason: 'x'.repeat(280) }));
    expect(longest.row.reason).to.have.length(280);
    let refused: unknown;
    try {
      await repo.grant(grantInput(org, sa, fx.newId(), { reason: 'x'.repeat(281) }));
    } catch (e) {
      refused = e;
    }
    expect(refused, 'a 281-character reason must be refused').to.be.instanceOf(Error);
    expect(await repo.countFor(org, sa)).to.equal(2);
  });

  it('answers isAllowed for that org, account and subject only', async () => {
    const org = fx.newId(), sa = fx.newId(), user = fx.newId();
    await repo.grant(grantInput(org, sa, user));
    expect(await repo.isAllowed(org, sa, user)).to.equal(true);
    expect(await repo.isAllowed(org, sa, fx.newId())).to.equal(false);
    expect(await repo.isAllowed(org, fx.newId(), user)).to.equal(false);
    expect(await repo.isAllowed(fx.newId(), sa, user)).to.equal(false);
  });

  it('lists an account\'s entries newest first and counts them', async () => {
    const org = fx.newId(), sa = fx.newId();
    const users = [fx.newId(), fx.newId(), fx.newId()];
    for (const u of users) {
      await repo.grant(grantInput(org, sa, u));
      await new Promise((r) => setTimeout(r, 5));
    }
    await repo.grant(grantInput(org, fx.newId(), fx.newId()));
    expect((await repo.listFor(org, sa)).map((r) => r.subjectUserId)).to.deep.equal([...users].reverse());
    expect(await repo.countFor(org, sa)).to.equal(3);
  });

  it('revokes one entry (true once) and all of an account\'s entries (their number)', async () => {
    const org = fx.newId(), sa = fx.newId(), a = fx.newId(), b = fx.newId(), other = fx.newId();
    await repo.grant(grantInput(org, sa, a));
    await repo.grant(grantInput(org, sa, b));
    await repo.grant(grantInput(org, other, a));
    expect(await repo.revoke(org, sa, a)).to.equal(true);
    expect(await repo.revoke(org, sa, a)).to.equal(false);
    expect(await repo.revokeAllFor(org, sa)).to.equal(1);
    expect(await repo.revokeAllFor(org, sa)).to.equal(0);
    expect(await repo.isAllowed(org, other, a)).to.equal(true);
  });

  it('records events and lists them newest first, for one account or the whole org, within the limit', async () => {
    const org = fx.newId(), sa = fx.newId(), sb = fx.newId(), subject = fx.newId();
    await repo.recordEvent({ orgId: org, serviceAccountId: sa, subjectUserId: subject, requestedSubject: 'a@x.test', outcome: 'issued', scopes: ['kb:read'], jti: 'j1', expiresAt: new Date(Date.now() + 60_000) });
    await new Promise((r) => setTimeout(r, 5));
    await repo.recordEvent({ orgId: org, serviceAccountId: sa, requestedSubject: 'nobody@x.test', outcome: 'refused', refusedReason: 'not_on_allow_list' });
    await new Promise((r) => setTimeout(r, 5));
    await repo.recordEvent({ orgId: org, serviceAccountId: sb, requestedSubject: 'b@x.test', outcome: 'granted', actedBy: fx.newId(), note: 'why' });
    await repo.recordEvent({ orgId: fx.newId(), serviceAccountId: sa, requestedSubject: 'other-org', outcome: 'revoked' });

    const all = await repo.listEvents(org, undefined, 10);
    expect(all.map((e) => e.requestedSubject)).to.deep.equal(['b@x.test', 'nobody@x.test', 'a@x.test']);
    expect(all[0]).to.include({ outcome: 'granted', note: 'why', orgId: org });
    expect(all[0]!._id).to.match(HEX);
    expect(all[0]!.scopes).to.deep.equal([]);
    expect(all[1]).to.include({ refusedReason: 'not_on_allow_list' });
    expect(all[1]).to.not.have.property('subjectUserId');
    expect(all[2]).to.include({ subjectUserId: subject, jti: 'j1' });
    expect(all[2]!.scopes).to.deep.equal(['kb:read']);
    expect((await repo.listEvents(org, sa, 10)).map((e) => e.requestedSubject)).to.deep.equal(['nobody@x.test', 'a@x.test']);
    expect(await repo.listEvents(org, undefined, 1)).to.have.length(1);
  });

  it('cuts an over-long requested subject to 320 characters', async () => {
    const org = fx.newId();
    await repo.recordEvent({ orgId: org, serviceAccountId: fx.newId(), requestedSubject: 'x'.repeat(400), outcome: 'refused' });
    expect((await repo.listEvents(org, undefined, 1))[0]!.requestedSubject).to.have.length(320);
  });

  it('orders events by their creation time, not by insertion', async () => {
    const org = fx.newId(), sa = fx.newId();
    await repo.recordEvent({ orgId: org, serviceAccountId: sa, requestedSubject: 'new', outcome: 'issued' });
    await repo.recordEvent({ orgId: org, serviceAccountId: sa, requestedSubject: 'old', outcome: 'issued' });
    const events = await repo.listEvents(org, undefined, 10);
    const old = events.find((e) => e.requestedSubject === 'old')!;
    await fx.setEventCreatedAt(old._id, new Date(Date.now() - 86_400_000));
    expect((await repo.listEvents(org, undefined, 10)).map((e) => e.requestedSubject)).to.deep.equal(['new', 'old']);
  });
}
