/**
 * Contract every SessionFamilyRepository must meet (ADR-WEB-02 D9): a family starts once, rotates by compare-and-set
 * (of two rotations from one token exactly one wins) and is ended once. Store-neutral.
 */
import { expect } from 'chai';
import type { SessionFamilyRepository } from '../../../src/libs/repositories/session-family';

export interface SessionFamilyFixture {
  repo: SessionFamilyRepository;
  newOrg(): Promise<string> | string;
  newId(): string;
}

export function describeSessionFamilyContract(fx: SessionFamilyFixture): void {
  const start = async (familyId = fx.newId()) => {
    const orgId = await fx.newOrg();
    const userId = fx.newId();
    const expiresAt = new Date(Date.now() + 86_400_000);
    expect(
      await fx.repo.start({
        orgId,
        familyId,
        userId,
        currentTokenId: 't1',
        previousTokenId: null,
        rotatedAt: null,
        expiresAt,
      }),
    ).to.equal(true);
    return { orgId, familyId, userId, expiresAt };
  };

  it('starts a family once; a second start with the same id changes nothing', async () => {
    const f = await start();
    expect(
      await fx.repo.start({ ...f, currentTokenId: 'other', previousTokenId: null, rotatedAt: null }),
    ).to.equal(false);
    const row = await fx.repo.get(f.orgId, f.familyId);
    expect(row).to.deep.include({ userId: f.userId, currentTokenId: 't1', previousTokenId: null, rotatedAt: null });
    expect(row?.expiresAt.getTime()).to.equal(f.expiresAt.getTime());
    expect(row?.revokedAt).to.equal(null);
  });

  it('rotates only from the current token, and only one of two racing rotations wins', async () => {
    const f = await start();
    const at = new Date();
    const results = await Promise.all([
      fx.repo.rotate(f.orgId, f.familyId, 't1', 't2a', at),
      fx.repo.rotate(f.orgId, f.familyId, 't1', 't2b', at),
    ]);
    expect(results.filter(Boolean)).to.have.length(1);
    const row = await fx.repo.get(f.orgId, f.familyId);
    expect(row?.previousTokenId).to.equal('t1');
    expect(['t2a', 't2b']).to.include(row?.currentTokenId);
    expect(row?.rotatedAt?.getTime()).to.equal(at.getTime());
    expect(await fx.repo.rotate(f.orgId, f.familyId, 't1', 't3', new Date())).to.equal(false);
  });

  it('is ended once, and an ended family no longer rotates', async () => {
    const f = await start();
    expect(await fx.repo.revoke(f.orgId, f.familyId, 'refresh_reuse', new Date())).to.equal(true);
    expect(await fx.repo.revoke(f.orgId, f.familyId, 'refresh_reuse', new Date())).to.equal(false);
    expect(await fx.repo.rotate(f.orgId, f.familyId, 't1', 't2', new Date())).to.equal(false);
    expect((await fx.repo.get(f.orgId, f.familyId))?.revokedReason).to.equal('refresh_reuse');
  });

  it('keeps each organisation\'s families apart', async () => {
    const f = await start();
    const other = await fx.newOrg();
    expect(await fx.repo.get(other, f.familyId)).to.equal(null);
    expect(await fx.repo.rotate(other, f.familyId, 't1', 't2', new Date())).to.equal(false);
    expect(await fx.repo.revoke(other, f.familyId, 'x', new Date())).to.equal(false);
  });
}
