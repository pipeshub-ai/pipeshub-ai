/**
 * Contract every AgentCallRepository must meet: the record of calls made with bearer tokens, one org at a time.
 * Store-neutral: the store under test supplies the repository.
 */
import { expect } from 'chai';
import type { AgentCallRepository, NewAgentCall } from '../../../src/libs/repositories/agent-call-event';

export interface AgentCallFixture {
  repo: AgentCallRepository;
  newId(): string;
}

export function describeAgentCallContract(fx: AgentCallFixture): void {
  const { repo } = fx;
  const call = (org: string, extra: Partial<NewAgentCall> = {}): NewAgentCall => ({
    orgId: org,
    userId: fx.newId(),
    tokenId: `jti-${fx.newId()}`,
    tokenKind: 'pat',
    method: 'POST',
    route: '/api/v1/knowledgeBase/:kbId/records',
    statusCode: 201,
    durationMs: 12,
    scopeDenied: false,
    rollup: false,
    ...extra,
  });

  it('stores a call and reads it back without the roll-up key', async () => {
    const org = fx.newId();
    const c = call(org, { userAgent: 'curl', channel: 'cli', requestId: 'req-1', tokenKind: 'oauth', oauthClientId: 'app-1' });
    await repo.record(c);
    const [row] = await repo.list(org, {}, 10);
    expect(row).to.deep.include({
      orgId: org, userId: c.userId, tokenId: c.tokenId, tokenKind: 'oauth', oauthClientId: 'app-1', userAgent: 'curl',
      channel: 'cli', method: 'POST', route: c.route, statusCode: 201, durationMs: 12, scopeDenied: false, requestId: 'req-1', count: 1,
    });
    expect(row!._id).to.match(/^[0-9a-f]{24}$/);
    expect(row).to.not.have.property('bucket');
  });

  it('never folds writes: two POSTs are two rows', async () => {
    const org = fx.newId(), base = call(org);
    await repo.record(base);
    await repo.record(base);
    expect(await repo.list(org, {}, 10)).to.have.length(2);
  });

  it('folds successful GETs of one token, route and minute into one row with a count', async function () {
    const org = fx.newId(), base = call(org, { method: 'GET', statusCode: 200, rollup: true });
    const minute = Math.floor(Date.now() / 60000);
    await repo.record(base);
    await repo.record(base);
    await repo.record({ ...base, route: '/api/v1/other' });
    await repo.record({ ...base, tokenId: 'another-token' });
    if (Math.floor(Date.now() / 60000) !== minute) this.skip(); // the minute turned during the test
    const rows = await repo.list(org, {}, 10);
    expect(rows).to.have.length(3);
    expect(rows.find((r) => r.tokenId === base.tokenId && r.route === base.route)!.count).to.equal(2);
  });

  it('shows an org its own rows only', async () => {
    const a = fx.newId(), b = fx.newId();
    await repo.record(call(a));
    await repo.record(call(b));
    const seenByA = await repo.list(a, {}, 10);
    expect(seenByA).to.have.length(1);
    expect(seenByA[0]!.orgId).to.equal(a);
    expect(await repo.list(fx.newId(), {}, 10)).to.deep.equal([]);
  });

  it('filters by user, token, kind, client, status class and time', async () => {
    const org = fx.newId();
    const ok = call(org, { userAgent: 'curl', statusCode: 200, tokenKind: 'pat' });
    const denied = call(org, { userAgent: 'agent', statusCode: 403, tokenKind: 'delegated', scopeDenied: true });
    const broken = call(org, { statusCode: 502, tokenKind: 'oauth' });
    for (const c of [ok, denied, broken]) await repo.record(c);
    const ids = async (f: Parameters<AgentCallRepository['list']>[1]) => (await repo.list(org, f, 10)).map((r) => r.tokenId);
    expect(await ids({ userId: ok.userId })).to.deep.equal([ok.tokenId]);
    expect(await ids({ tokenId: denied.tokenId })).to.deep.equal([denied.tokenId]);
    expect(await ids({ tokenKind: 'oauth' })).to.deep.equal([broken.tokenId]);
    expect(await ids({ client: 'agent' })).to.deep.equal([denied.tokenId]);
    expect(await ids({ statusClass: '2xx' })).to.deep.equal([ok.tokenId]);
    expect(await ids({ statusClass: '4xx' })).to.deep.equal([denied.tokenId]);
    expect(await ids({ statusClass: '5xx' })).to.deep.equal([broken.tokenId]);
    expect((await repo.list(org, { tokenId: denied.tokenId }, 1))[0]!.scopeDenied).to.equal(true);
    expect(await ids({ from: new Date(Date.now() + 60_000) })).to.deep.equal([]);
    expect(await ids({ to: new Date(Date.now() - 60_000) })).to.deep.equal([]);
    expect(await ids({ from: new Date(Date.now() - 60_000), to: new Date(Date.now() + 60_000) })).to.have.length(3);
  });

  it('pages newest first with a (createdAt, id) cursor and no row twice', async () => {
    const org = fx.newId(), tokens: string[] = [];
    for (let i = 0; i < 5; i++) {
      const c = call(org);
      tokens.push(c.tokenId);
      await repo.record(c);
    }
    const seen: string[] = [];
    let before: { createdAt: Date; id: string } | undefined;
    for (let page = 0; page < 3; page++) {
      const rows = await repo.list(org, before ? { before } : {}, 2);
      seen.push(...rows.map((r) => r.tokenId));
      const last = rows[rows.length - 1]!;
      before = { createdAt: last.createdAt, id: last._id };
    }
    expect(seen).to.deep.equal([...tokens].reverse());
    expect(await repo.list(org, { before }, 2)).to.deep.equal([]);
  });
}
