/**
 * Contract every SemanticSearchRepository must meet. Store-neutral: the store
 * under test supplies the repository, a unit of work and a few probes.
 */
import { expect } from 'chai';
import type { SemanticSearchRepository } from '../../../src/libs/repositories/semantic-search';
import type { NewSearchHit } from '../../../src/libs/repositories/semantic-search';
import type { ScopedUnitOfWork } from './unit-of-work-scope';

export interface SemanticSearchFixture {
  repo: SemanticSearchRepository;
  uow: ScopedUnitOfWork;
  /** A fresh, valid id for the store. */
  newId(): string;
  /** Number of stored hit documents with these ids. */
  countHits(ids: string[]): Promise<number>;
  /** Moves a search's creation time (to test date ranges and ordering). */
  setCreatedAt(searchId: string, at: Date): Promise<void>;
}

export function describeSemanticSearchContract(fx: SemanticSearchFixture): void {
  const { repo } = fx;
  const hit = (orgId: string, n: number): NewSearchHit => ({
    content: `hit ${n}`,
    chunkIndex: n,
    citationType: 'vector',
    metadata: {
      orgId,
      mimeType: 'text/plain',
      recordId: `rec-${n}`,
      recordName: `Record ${n}`,
      origin: 'UPLOAD',
      recordVersion: 0,
    },
  });
  const make = async (
    orgId: string,
    userId: string,
    o: { hits?: number; query?: string; records?: Array<[string, string]> } = {},
  ) =>
    repo.create({
      orgId,
      userId,
      query: o.query ?? 'q',
      limit: 5,
      hits: o.hits === undefined ? [hit(orgId, 1), hit(orgId, 2)] : Array.from({ length: o.hits }, (_, i) => hit(orgId, i)),
      records: new Map(o.records ?? [['r1', '{"a":1}']]),
    });
  const ids = (rows: Array<{ _id: string }>) => rows.map((r) => r._id);

  let org: string;
  let alice: string;
  let bob: string;
  let carol: string;
  beforeEach(() => {
    org = fx.newId();
    alice = fx.newId();
    bob = fx.newId();
    carol = fx.newId();
  });

  describe('create', () => {
    it('stores the search with its hits in order and returns string ids', async () => {
      const s = await make(org, alice);
      expect(s._id).to.be.a('string');
      expect(s.orgId).to.equal(org);
      expect(s.userId).to.equal(alice);
      expect(s.citationIds).to.have.length(2);
      s.citationIds.forEach((c) => expect(c).to.be.a('string'));
      expect(s.records).to.deep.equal({ r1: '{"a":1}' });
      expect(s.isShared).to.equal(false);
      expect(s.isArchived).to.equal(false);
      expect(s.sharedWith).to.deep.equal([]);
      expect(s.__v).to.equal(0);
      const full = await repo.findVisible(org, alice, s._id, { populateCitations: true });
      expect((full!.citationIds as Array<{ content: string }>).map((c) => c.content)).to.deep.equal(['hit 1', 'hit 2']);
    });

    it('stores a search without a hit list', async () => {
      const s = await repo.create({ orgId: org, userId: alice, query: 'q', limit: 1, records: new Map() });
      expect(s.citationIds).to.deep.equal([]);
      expect(s.records).to.deep.equal({});
    });

    it('works inside a unit of work', async () => {
      const s = await fx.uow.run((tx) =>
        repo.create({ orgId: org, userId: alice, query: 'tx', limit: 1, hits: [hit(org, 1)], records: new Map() }, tx),
        { org },
      );
      expect(await repo.findVisible(org, alice, s._id)).to.not.equal(null);
      expect(await fx.countHits(s.citationIds as string[])).to.equal(1);
    });
  });

  describe('findVisible', () => {
    it('shows the owner their search, with hits populated as documents without __v', async () => {
      const s = await make(org, alice);
      const r = await repo.findVisible(org, alice, s._id, { populateCitations: true });
      expect(r!._id).to.equal(s._id);
      const c = r!.citationIds[0] as unknown as Record<string, unknown>;
      expect(c._id).to.be.a('string');
      expect(c).to.not.have.property('__v');
      expect(c.content).to.equal('hit 1');
      expect((c.metadata as { orgId: string }).orgId).to.equal(org);
    });

    it('leaves hits as ids when not populating', async () => {
      const s = await make(org, alice);
      const r = await repo.findVisible(org, alice, s._id);
      r!.citationIds.forEach((c) => expect(c).to.be.a('string'));
    });

    it('hides a search from other users, other orgs and non-members of a share', async () => {
      const s = await make(org, alice);
      expect(await repo.findVisible(org, bob, s._id)).to.equal(null);
      expect(await repo.findVisible(fx.newId(), alice, s._id)).to.equal(null);
      expect(await repo.findVisible(org, alice, fx.newId())).to.equal(null);
    });

    it('requires isShared and a membership entry together', async () => {
      const s = await make(org, alice);
      await repo.setSharing(s._id, { isShared: true, shareLink: 'l', sharedWith: [{ userId: bob, accessLevel: 'read' }] });
      expect((await repo.findVisible(org, bob, s._id))!._id).to.equal(s._id);
      expect(await repo.findVisible(org, carol, s._id)).to.equal(null);
      // members listed but sharing switched off: not visible
      await repo.setSharing(s._id, { isShared: false, sharedWith: [{ userId: bob, accessLevel: 'read' }] });
      expect(await repo.findVisible(org, bob, s._id)).to.equal(null);
    });

    it('does not show archived searches unless asked, and only then', async () => {
      const s = await make(org, alice);
      await repo.archive(s._id, alice);
      expect(await repo.findVisible(org, alice, s._id)).to.equal(null);
      expect((await repo.findVisible(org, alice, s._id, { archived: true }))!._id).to.equal(s._id);
      const live = await make(org, alice);
      expect(await repo.findVisible(org, alice, live._id, { archived: true })).to.equal(null);
    });
  });

  describe('listVisible', () => {
    it('lists owned and shared-with-me searches, newest first, ties by id descending', async () => {
      const a = await make(org, alice);
      const b = await make(org, alice);
      const c = await make(org, alice);
      const t = new Date('2024-01-01T00:00:00Z');
      for (const s of [a, b, c]) await fx.setCreatedAt(s._id, t);
      const shared = await make(org, bob);
      await repo.setSharing(shared._id, { isShared: true, sharedWith: [{ userId: alice, accessLevel: 'read' }] });
      await make(org, bob); // not visible
      const r = await repo.listVisible(org, alice, { skip: 0, limit: 10 });
      expect(r.total).to.equal(4);
      const expected = [a, b, c, shared].map((s) => s._id).sort().reverse();
      expect(ids(r.rows)).to.deep.equal(expected);
    });

    it('sorts by createdAt in either direction and pages with skip and limit', async () => {
      const rows = [] as Array<{ _id: string }>;
      for (let i = 0; i < 4; i++) {
        const s = await make(org, alice);
        await fx.setCreatedAt(s._id, new Date(Date.UTC(2024, 0, 1 + i)));
        rows.push(s);
      }
      const asc = await repo.listVisible(org, alice, { skip: 0, limit: 10, sort: { field: 'createdAt', direction: 1 } });
      expect(ids(asc.rows)).to.deep.equal(ids(rows));
      const page = await repo.listVisible(org, alice, { skip: 1, limit: 2, sort: { field: 'createdAt', direction: -1 } });
      expect(ids(page.rows)).to.deep.equal([rows[2]._id, rows[1]._id]);
      expect(page.total).to.equal(4);
    });

    it('rows with identical timestamps keep one order in either direction, and pages neither repeat nor skip one', async () => {
      const t = new Date('2024-03-03T00:00:00Z');
      const rows = [] as Array<{ _id: string }>;
      for (let i = 0; i < 5; i++) {
        const s = await make(org, alice);
        await fx.setCreatedAt(s._id, t);
        rows.push(s);
      }
      const sortedIds = rows.map((r) => r._id).sort();
      for (const [direction, expected] of [[1, sortedIds], [-1, [...sortedIds].reverse()]] as const) {
        const paged: string[] = [];
        for (let skip = 0; skip < 5; skip += 2) {
          const page = await repo.listVisible(org, alice, { skip, limit: 2, sort: { field: 'createdAt', direction } });
          paged.push(...ids(page.rows));
        }
        expect(paged, `direction ${String(direction)}`).to.deep.equal(expected);
        expect(new Set(paged).size).to.equal(5);
      }
    });

    it('honours the owned and shared switches', async () => {
      const mine = await make(org, alice);
      const theirs = await make(org, bob);
      await repo.setSharing(theirs._id, { isShared: true, sharedWith: [{ userId: alice, accessLevel: 'write' }] });
      expect(ids((await repo.listVisible(org, alice, { skip: 0, limit: 10, owned: true, shared: false })).rows)).to.deep.equal([mine._id]);
      expect(ids((await repo.listVisible(org, alice, { skip: 0, limit: 10, owned: false, shared: true })).rows)).to.deep.equal([theirs._id]);
    });

    it('rejects owned=false and shared=false together', async () => {
      let err: Error | undefined;
      try {
        await repo.listVisible(org, alice, { skip: 0, limit: 1, owned: false, shared: false });
      } catch (e) {
        err = e as Error;
      }
      expect(err?.message).to.equal('Either owned or shared must be true');
    });

    it('filters by date range and by share flag', async () => {
      const old = await make(org, alice);
      const recent = await make(org, alice);
      await fx.setCreatedAt(old._id, new Date('2020-01-01T00:00:00Z'));
      await fx.setCreatedAt(recent._id, new Date('2024-06-01T00:00:00Z'));
      const from = await repo.listVisible(org, alice, { skip: 0, limit: 10, startDate: new Date('2023-01-01') });
      expect(ids(from.rows)).to.deep.equal([recent._id]);
      expect(from.total).to.equal(1);
      const until = await repo.listVisible(org, alice, { skip: 0, limit: 10, endDate: new Date('2021-01-01') });
      expect(ids(until.rows)).to.deep.equal([old._id]);
      await repo.setSharing(recent._id, { isShared: true, sharedWith: [{ userId: bob, accessLevel: 'read' }] });
      expect(ids((await repo.listVisible(org, alice, { skip: 0, limit: 10, isShared: true })).rows)).to.deep.equal([recent._id]);
      expect(ids((await repo.listVisible(org, alice, { skip: 0, limit: 10, isShared: false })).rows)).to.deep.equal([old._id]);
    });

    it('leaves archived searches out of rows and total', async () => {
      const a = await make(org, alice);
      await make(org, alice);
      await repo.archive(a._id, alice);
      const r = await repo.listVisible(org, alice, { skip: 0, limit: 10 });
      expect(r.total).to.equal(1);
      expect(ids(r.rows)).to.not.include(a._id);
    });

    it('counts per org, not across orgs', async () => {
      const other = fx.newId();
      await make(org, alice);
      await make(other, alice); // same user id in another org
      await make(other, alice);
      expect((await repo.listVisible(org, alice, { skip: 0, limit: 10 })).total).to.equal(1);
      expect((await repo.listVisible(other, alice, { skip: 0, limit: 10 })).total).to.equal(2);
    });

    it('returns rows with string ids and plain records, hits as ids', async () => {
      await make(org, alice);
      const [row] = (await repo.listVisible(org, alice, { skip: 0, limit: 1 })).rows;
      expect(row._id).to.be.a('string');
      expect(row.orgId).to.equal(org);
      expect(row.userId).to.equal(alice);
      row.citationIds.forEach((c) => expect(c).to.be.a('string'));
      expect(row.records).to.deep.equal({ r1: '{"a":1}' });
      expect(row.createdAt).to.be.instanceOf(Date);
    });
  });

  describe('setSharing', () => {
    it('stores the sharing fields and returns the row as it was before', async () => {
      const s = await make(org, alice);
      const before = await repo.setSharing(s._id, {
        isShared: true,
        shareLink: 'https://x/api/v1/search/' + s._id,
        sharedWith: [{ userId: bob, accessLevel: 'write' }],
      });
      expect(before!.isShared).to.equal(false);
      expect(before!.sharedWith).to.deep.equal([]);
      const after = await repo.findVisible(org, alice, s._id);
      expect(after!.isShared).to.equal(true);
      expect(after!.shareLink).to.equal('https://x/api/v1/search/' + s._id);
      expect(after!.sharedWith.map((m) => [m.userId, m.accessLevel])).to.deep.equal([[bob, 'write']]);
    });

    it('can switch sharing off and keep the old link out of the update when none is given', async () => {
      const s = await make(org, alice);
      await repo.setSharing(s._id, { isShared: true, shareLink: 'l', sharedWith: [{ userId: bob, accessLevel: 'read' }] });
      const prev = await repo.setSharing(s._id, { isShared: false, sharedWith: [] });
      expect(prev!.sharedWith).to.have.length(1);
      expect(prev!.shareLink).to.equal('l');
      const now = await repo.findVisible(org, alice, s._id);
      expect(now!.isShared).to.equal(false);
      expect(now!.sharedWith).to.deep.equal([]);
    });

    it('returns null for an unknown id', async () => {
      expect(await repo.setSharing(fx.newId(), { sharedWith: [] })).to.equal(null);
    });
  });

  describe('unshare', () => {
    it('with no recipient left switches sharing off, removes the link and returns the row after the change', async () => {
      const s = await make(org, alice);
      const link = 'https://x/api/v1/search/' + s._id;
      await repo.setSharing(s._id, { isShared: true, shareLink: link, sharedWith: [{ userId: bob, accessLevel: 'read' }] });

      const after = await repo.unshare(s._id, []);

      expect(after!.isShared).to.equal(false);
      expect(after!.sharedWith).to.deep.equal([]);
      expect(after).to.not.have.property('shareLink');
      const stored = await repo.findVisible(org, alice, s._id);
      expect(stored!.isShared).to.equal(false);
      expect(stored).to.not.have.property('shareLink');
    });

    it('with a recipient left keeps the sharing flag and the link', async () => {
      const s = await make(org, alice);
      const link = 'https://x/api/v1/search/' + s._id;
      await repo.setSharing(s._id, {
        isShared: true,
        shareLink: link,
        sharedWith: [{ userId: bob, accessLevel: 'read' }, { userId: carol, accessLevel: 'write' }],
      });

      const after = await repo.unshare(s._id, [{ userId: carol, accessLevel: 'write' }]);

      expect(after!.isShared).to.equal(true);
      expect(after!.shareLink).to.equal(link);
      expect(after!.sharedWith.map((m) => [m.userId, m.accessLevel])).to.deep.equal([[carol, 'write']]);
    });

    it('returns null for an unknown id', async () => {
      expect(await repo.unshare(fx.newId(), [])).to.equal(null);
    });
  });

  describe('archive and unarchive', () => {
    it('archives, then unarchives only archived rows the viewer can see', async () => {
      const s = await make(org, alice);
      const prev = await repo.archive(s._id, alice);
      expect(prev!.isArchived).to.equal(false);
      const archived = await repo.findVisible(org, alice, s._id, { archived: true });
      expect(archived!.isArchived).to.equal(true);
      expect(archived!.archivedBy).to.equal(alice);
      expect(await repo.unarchive(org, bob, s._id)).to.equal(null);
      expect(await repo.unarchive(fx.newId(), alice, s._id)).to.equal(null);
      const was = await repo.unarchive(org, alice, s._id);
      expect(was!.isArchived).to.equal(true);
      const back = await repo.findVisible(org, alice, s._id);
      expect(back!.isArchived).to.equal(false);
      expect(back!.archivedBy ?? null).to.equal(null);
      expect(await repo.unarchive(org, alice, s._id)).to.equal(null);
    });

    it('a read, unarchive or delete by id also honours the request\'s own narrowing', async () => {
      const s = await make(org, alice);
      expect(await repo.findVisible(org, alice, s._id, { query: { isShared: true } })).to.equal(null);
      expect((await repo.findVisible(org, alice, s._id, { query: { isShared: false } }))?._id).to.equal(s._id);
      expect(await repo.delete(org, alice, s._id, { isShared: true })).to.equal(null);
      await repo.archive(s._id, alice);
      expect(await repo.unarchive(org, alice, s._id, { isShared: true })).to.equal(null);
      expect((await repo.unarchive(org, alice, s._id, { isShared: false }))?._id).to.equal(s._id);
    });

    it('archive of an unknown id is null', async () => {
      expect(await repo.archive(fx.newId(), alice)).to.equal(null);
    });
  });

  describe('delete', () => {
    it('deletes the search and its hits, only for a viewer who can see it', async () => {
      const s = await make(org, alice);
      const hitIds = s.citationIds as string[];
      expect(await repo.delete(org, bob, s._id)).to.equal(null);
      expect(await repo.delete(fx.newId(), alice, s._id)).to.equal(null);
      expect(await fx.countHits(hitIds)).to.equal(2);
      const gone = await repo.delete(org, alice, s._id);
      expect(gone!._id).to.equal(s._id);
      expect(await repo.findVisible(org, alice, s._id)).to.equal(null);
      expect(await fx.countHits(hitIds)).to.equal(0);
    });

    it('leaves other searches hits alone', async () => {
      const a = await make(org, alice);
      const b = await make(org, alice);
      await repo.delete(org, alice, a._id);
      expect(await fx.countHits(b.citationIds as string[])).to.equal(2);
    });

    it('works inside a unit of work', async () => {
      const s = await make(org, alice);
      await fx.uow.run((tx) => repo.delete(org, alice, s._id, undefined, tx), { org });
      expect(await repo.findVisible(org, alice, s._id)).to.equal(null);
    });
  });

  describe('deleteVisible', () => {
    it('deletes every visible search of the org, with hits, and counts them', async () => {
      const a = await make(org, alice);
      const b = await make(org, alice);
      const other = await make(org, bob);
      const foreign = await make(fx.newId(), alice);
      expect(await repo.deleteVisible(org, alice, {})).to.equal(2);
      expect(await repo.findVisible(org, alice, a._id)).to.equal(null);
      expect(await repo.findVisible(org, alice, b._id)).to.equal(null);
      expect(await fx.countHits([...(a.citationIds as string[]), ...(b.citationIds as string[])])).to.equal(0);
      expect(await repo.findVisible(org, bob, other._id)).to.not.equal(null);
      expect(await repo.findVisible(foreign.orgId, alice, foreign._id)).to.not.equal(null);
      expect(await repo.deleteVisible(org, alice, {})).to.equal(0);
    });

    it('with owned-only leaves searches shared with the viewer; with defaults it removes them too', async () => {
      const shared = await make(org, bob);
      await repo.setSharing(shared._id, { isShared: true, sharedWith: [{ userId: alice, accessLevel: 'read' }] });
      await make(org, alice);
      expect(await repo.deleteVisible(org, alice, { shared: false })).to.equal(1);
      expect(await repo.findVisible(org, bob, shared._id)).to.not.equal(null);
      expect(await repo.deleteVisible(org, alice, {})).to.equal(1);
      expect(await repo.findVisible(org, bob, shared._id)).to.equal(null);
    });

    it('respects the date range and skips archived searches', async () => {
      const old = await make(org, alice);
      const archived = await make(org, alice);
      await fx.setCreatedAt(old._id, new Date('2020-01-01T00:00:00Z'));
      await repo.archive(archived._id, alice);
      await make(org, alice);
      expect(await repo.deleteVisible(org, alice, { endDate: new Date('2021-01-01') })).to.equal(1);
      expect(await repo.findVisible(org, alice, archived._id, { archived: true })).to.not.equal(null);
    });

    it('works inside a unit of work', async () => {
      await make(org, alice);
      expect(await fx.uow.run((tx) => repo.deleteVisible(org, alice, {}, tx), { org })).to.equal(1);
    });
  });

  describe('findCitations', () => {
    it('returns the org hit documents without __v and drops foreign or unknown ids', async () => {
      const mine = await make(org, alice);
      const foreign = await make(fx.newId(), alice);
      const wanted = [...(mine.citationIds as string[]), ...(foreign.citationIds as string[]), fx.newId()];
      const rows = await repo.findCitations(org, wanted);
      expect(rows.map((r) => r._id).sort()).to.deep.equal([...(mine.citationIds as string[])].sort());
      rows.forEach((r) => {
        expect(r).to.not.have.property('__v');
        expect(r._id).to.be.a('string');
      });
      expect(await repo.findCitations(org, [])).to.deep.equal([]);
    });
  });
}
