/**
 * The carriers without a database (R45-CARRY, in `npm test`): how a document is read (`sourceDoc`), what row it becomes and what a
 * row gives back (`DocTable`), what the coding-engine carrier calls for, and the steps' logic (copy, follow events, verify,
 * rollback) against an in-memory MongoDB and a recording PostgreSQL. The same steps run on PostgreSQL in carry.itest.ts.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Binary, Int32, Long, ObjectId } from 'mongodb';
import { sourceDoc, canonicalText } from '../../../src/scripts/docstore-migrate/carry/plain';
import { DocTable } from '../../../src/scripts/docstore-migrate/carry/doc-table';
import { DATA_KEY_AND_WORKSPACE_TABLES } from '../../../src/scripts/docstore-migrate/carry/keys-workspaces';
import { CODING_ENGINE_CHOICES } from '../../../src/scripts/docstore-migrate/carry/choices';
import { BUILT_IN_CARRIERS, CARRIERS, EDITION_CARRIERS } from '../../../src/scripts/docstore-migrate/carry';
import { EDITION_DOCSTORE_COVERAGE } from '../../../src/edition';
import {
  applyCarried,
  copyCarried,
  restoreCarried,
  verifyCarried,
  type CarryPg,
} from '../../../src/scripts/docstore-migrate/carry/run';
import type { Carrier, Doc, SourceDoc } from '../../../src/scripts/docstore-migrate/carry/types';
import { FakeCarryMongo } from './fake-carry-mongo';
import * as fx from './carry-fixtures';
import { MAPPINGS } from '../../../src/scripts/docstore-migrate/mapping';
import { SourceReport, prepareSource } from '../../../src/scripts/docstore-migrate/source';
import { decodeDoc } from '../../../src/scripts/docstore-migrate/decode';
import { fillSlackThreadOrgs } from '../../../src/scripts/docstore-migrate/slack-threads';

const table = (collection: string): DocTable => new DocTable(DATA_KEY_AND_WORKSPACE_TABLES.find((s) => s.collection === collection)!);

/** The row as `to_jsonb` gives it back: the statement's JSON text parsed. */
const asStored = (row: Record<string, unknown>): Record<string, unknown> =>
  Object.fromEntries(Object.entries(row).map(([k, v]) => [k, k === 'doc' && typeof v === 'string' ? JSON.parse(v) : v]));

/** A PostgreSQL that runs nothing: the carriers given to the steps here are fakes. */
const noPg: CarryPg = (fn) => fn({} as never);

describe('docstore-migrate carriers without a database (R45-CARRY)', () => {
  describe('reading a document (sourceDoc)', () => {
    it('keeps an ObjectId _id as lower-case hex and a string _id as it is (a data key id is case-sensitive)', () => {
      expect(sourceDoc({ _id: new ObjectId('ABCDEFABCDEFABCDEFABCDEF'.toLowerCase()), a: 1 }, 'c')).to.deep.include({ id: 'abcdefabcdefabcdefabcdef', oid: true });
      expect(sourceDoc({ _id: 'Kid-B2', a: 1 }, 'c')).to.deep.include({ id: 'Kid-B2', oid: false, doc: { a: 1 } });
    });

    it('reads BSON numbers and bytes as the stores keep them, and refuses a value the PostgreSQL store does not keep, naming its path', () => {
      const d = sourceDoc({ _id: 'x', i: new Int32(4), l: Long.fromNumber(5), b: new Binary(Buffer.from('hi')), at: new Date(5), n: { deep: [new Int32(1)] } }, 'c');
      expect(d.refused).to.equal(undefined);
      expect(d.doc.i).to.equal(4);
      expect(d.doc.l).to.equal(5);
      expect(Buffer.isBuffer(d.doc.b) && (d.doc.b as Buffer).toString()).to.equal('hi');
      expect(d.doc.at).to.be.instanceOf(Date);
      expect(d.doc.n).to.deep.equal({ deep: [1] });
      const bad = sourceDoc({ _id: 'x', inner: { ref: new ObjectId() } }, 'agentWorkspaceHeads');
      expect(bad.refused).to.contain('agentWorkspaceHeads x').and.contain('inner.ref').and.contain('ObjectId');
      expect(sourceDoc({ _id: 'x', big: Long.fromString('9007199254740993') }, 'c').refused).to.contain('beyond 2^53');
    });

    it('reads only the picked paths, and an organisation id that is an ObjectId as hex', () => {
      const d = sourceDoc({ _id: fx.USER, orgId: new ObjectId(fx.ORG), email: 'a@x', codingEngine: 'codex', other: new ObjectId() }, 'users', {
        pick: ['codingEngine', 'orgId'],
        idPaths: ['orgId'],
      });
      expect(d.refused).to.equal(undefined);
      expect(d.doc).to.deep.equal({ codingEngine: 'codex', orgId: fx.ORG });
    });
  });

  describe('data keys and workspaces (DocTable)', () => {
    for (const [collection, docs] of Object.entries({
      agentHarnessDataKeys: fx.dataKeys(),
      agentWorkspaceHeads: fx.workspaceHeads(),
      agentWorkspaceManifests: fx.workspaceManifests(),
      agentWorkspaceKeys: fx.workspaceKeys(),
    }))
      it(`${collection}: every document comes back whole from the row it becomes`, () => {
        const t = table(collection);
        for (const raw of docs) {
          const src = sourceDoc(raw, collection);
          const back = t.docOf(asStored(t.rowOf(src)));
          expect(canonicalText(back), `${collection} ${src.id}`).to.equal(canonicalText(t.expectedDoc(src, () => undefined)));
        }
      });

    it('a data key is a row of typed columns keyed by its id; a key made before seals were counted reads zero (listed)', () => {
      const t = table('agentHarnessDataKeys');
      const [, old] = fx.dataKeys();
      const notes: string[] = [];
      const src = sourceDoc(old!, 'agentHarnessDataKeys');
      expect(t.rowOf(src)).to.deep.equal({ org_id: fx.ORG, kid: 'Kid-B2', version: 2, kek_id: 'kek-1', wrapped: 'd3JhcHBlZC1rZXktMg==', created_at_ms: 1_700_000_100_000, seals: 0 });
      expect(t.expectedDoc(src, (n) => notes.push(n)).seals).to.equal(0);
      expect(notes).to.deep.equal(['agentHarnessDataKeys: absent seals read as 0']);
    });

    it('the workspace key row keeps the document without its keys, as PgWorkspaceKeyStore writes it', () => {
      const t = table('agentWorkspaceKeys');
      const row = asStored(t.rowOf(sourceDoc(fx.workspaceKeys()[0]!, 'agentWorkspaceKeys')));
      expect(row.session_id).to.equal(fx.SESSION.toHexString());
      expect(Object.keys(row.doc as Doc).sort()).to.deep.equal(['createdAt', 'destroyedAt', 'keyId', 'wrapped']);
    });

    it('refuses a document that lacks a key value or an organisation, or whose ObjectId id a text key column would turn into a string', () => {
      const t = table('agentHarnessDataKeys');
      expect(() => t.rowOf(sourceDoc({ _id: 'k', orgId: fx.ORG, version: 1, wrapped: 'w', createdAt: 1 }, 'agentHarnessDataKeys'))).to.throw(/kekId is missing/);
      expect(() => t.rowOf(sourceDoc({ _id: 'k', version: 1, kekId: 'x', wrapped: 'w', createdAt: 1 }, 'agentHarnessDataKeys'))).to.throw(/orgId is missing/);
      expect(() => t.rowOf(sourceDoc({ ...fx.dataKeys()[0]!, _id: new ObjectId() }, 'agentHarnessDataKeys'))).to.throw(/keeps the id as text/);
    });

    it('two manifests of one workspace are two rows; their keys say which', () => {
      const t = table('agentWorkspaceManifests');
      const [a, b] = fx.workspaceManifests().map((d) => t.keyOf(sourceDoc(d, 'agentWorkspaceManifests')));
      expect(a!.rowKey).to.not.equal(b!.rowKey);
      expect(JSON.parse(b!.rowKey)).to.deep.equal([`session:${fx.SESSION.toHexString()}`, 2]);
    });
  });

  describe('coding-engine choices and the accounts an org deletion disabled', () => {
    const c = CODING_ENGINE_CHOICES;
    const read = (collection: string, raw: Doc): SourceDoc => sourceDoc(raw, collection, c.reads?.[collection] ?? {});

    it("an org's, a person's and a chat's fields become their subject's row, with the fields as the document held them", () => {
      expect(c.expected('org', read('org', fx.orgs()[0]!))).to.deep.equal([[`${fx.ORG}|org`, { defaultCodingEngine: 'claude-code', codingEngineAutoRoute: false }]]);
      expect(c.expected('org', read('org', fx.orgs()[1]!))).to.deep.equal([]);
      expect(c.expected('users', read('users', fx.users()[0]!))).to.deep.equal([
        [`${fx.ORG}|user:${fx.USER.toHexString()}`, { codingEngine: 'codex' }],
      ]);
      expect(c.expected('chatSessions', read('chatSessions', fx.chatSessions()[0]!))).to.deep.equal([
        [
          `${fx.ORG}|session:${fx.SESSION.toHexString()}`,
          { codingEngine: 'opencode', codingEnginePin: 'opencode@1.2', repository: { url: 'https://git.example/r.git', toolsetInstanceId: 'ti-1', baseRef: 'main' } },
        ],
      ]);
    });

    it('a person with a choice and no organisation is refused, not dropped', () => {
      expect(() => c.expected('users', read('users', { _id: fx.USER, codingEngine: 'codex' }))).to.throw(/no orgId/);
    });

    it('carries exactly the fields the mappings hold, read with only those fields and the organisation', () => {
      expect(c.fields).to.deep.equal({
        org: ['defaultCodingEngine', 'codingEngineAutoRoute'],
        users: ['codingEngine', 'harnessMemoryOff'],
        chatSessions: ['codingEngine', 'codingEnginePin', 'repository'],
      });
      expect(read('users', { ...fx.users()[0]!, secret: new ObjectId() }).refused).to.equal(undefined);
    });
  });

  describe('the steps, on a fake carrier', () => {
    type Call = { op: string; collection: string; ids: string[] };
    const fake = (actual: Map<string, unknown>, calls: Call[] = []): Carrier => ({
      name: 'fake',
      reason: 'a carrier that records what it is asked to do',
      collections: ['agentWorkspaceHeads'],
      fields: { users: ['codingEngine'] },
      tables: ['harness.workspace_heads'],
      async write(_k, collection, docs) {
        calls.push({ op: 'write', collection, ids: docs.map((d) => d.id) });
      },
      async remove(_k, collection, ids) {
        calls.push({ op: 'remove', collection, ids });
      },
      expected: (_c, d) => (d.doc.skip ? [] : [[d.id, d.doc]]),
      actual: async () => actual,
      async restore(_k, collection) {
        calls.push({ op: 'restore', collection, ids: [] });
        return { written: 1, removed: 0 };
      },
      async clear() {
        calls.push({ op: 'clear', collection: '', ids: [] });
      },
    });

    it('verify reports missing, extra, differing (by field name only) and duplicate entries, and never a carried value', async () => {
      const mongo = new FakeCarryMongo({
        agentWorkspaceHeads: [
          { _id: 'a', wrapped: 'SECRET-WRAPPED-KEY', revision: 1 },
          { _id: 'b', revision: 2 },
          { _id: 'c', skip: true },
        ],
        users: [],
      });
      const have = new Map<string, unknown>([
        ['a', { wrapped: 'OTHER-SECRET', revision: 1 }],
        ['z', { revision: 9 }],
      ]);
      const [heads, users] = await verifyCarried(noPg, mongo, [fake(have)]);
      expect(heads).to.deep.include({ carrier: 'fake', collection: 'agentWorkspaceHeads', expected: 2, actual: 2, missing: ['b'], extra: ['z'], ok: false });
      expect(heads!.differing).to.deep.equal([{ key: 'a', fields: ['wrapped'] }]);
      expect(JSON.stringify(heads)).not.to.contain('SECRET');
      expect(users).to.deep.include({ collection: 'users', ok: false, extra: ['a', 'z'] });
    });

    it('verify lists a document its carrier cannot read, and fails', async () => {
      const mongo = new FakeCarryMongo({ agentWorkspaceHeads: [{ _id: 'a', ref: new ObjectId() }], users: [] });
      const [heads] = await verifyCarried(noPg, mongo, [fake(new Map())]);
      expect(heads!.ok).to.equal(false);
      expect(heads!.refused[0]).to.contain('ref');
    });

    it('the copy writes every collection a carrier reads, in batches, and stops at a document it cannot read', async () => {
      const calls: Call[] = [];
      const docs = Array.from({ length: 1201 }, (_, i) => ({ _id: `h${String(i)}`, revision: i }));
      const n = await copyCarried(noPg, new FakeCarryMongo({ agentWorkspaceHeads: docs, users: [{ _id: fx.USER, codingEngine: 'x' }] }), [fake(new Map(), calls)]);
      expect(n).to.deep.equal({ 'fake:agentWorkspaceHeads': 1201, 'fake:users': 1 });
      expect(calls.filter((c) => c.collection === 'agentWorkspaceHeads').map((c) => c.ids.length)).to.deep.equal([500, 500, 201]);
      let err: Error | undefined;
      await copyCarried(noPg, new FakeCarryMongo({ agentWorkspaceHeads: [{ _id: 'a', ref: new ObjectId() }] }), [fake(new Map())]).catch((e: Error) => {
        err = e;
      });
      expect(err?.message).to.contain('nothing more was copied');
    });

    it("the follower's events: deletes before writes, only to the carriers that read the collection, nothing for an empty batch", async () => {
      const calls: Call[] = [];
      const pgCalls: boolean[] = [];
      const pg: CarryPg = (fn, write) => {
        pgCalls.push(write);
        return fn({} as never);
      };
      await applyCarried(pg, 'agentWorkspaceHeads', [{ _id: 'a', revision: 2 }], ['b'], [fake(new Map(), calls)]);
      expect(calls).to.deep.equal([
        { op: 'remove', collection: 'agentWorkspaceHeads', ids: ['b'] },
        { op: 'write', collection: 'agentWorkspaceHeads', ids: ['a'] },
      ]);
      expect(pgCalls).to.deep.equal([true]);
      await applyCarried(pg, 'agentRuns', [{ _id: 'r' }], [], [fake(new Map(), calls)]);
      await applyCarried(pg, 'agentWorkspaceHeads', [], [], [fake(new Map(), calls)]);
      expect(calls).to.have.length(2);
      let err: Error | undefined;
      await applyCarried(pg, 'agentWorkspaceHeads', [{ _id: 'a', ref: new ObjectId() }], [], [fake(new Map(), calls)]).catch((e: Error) => {
        err = e;
      });
      expect(err?.message).to.contain('the follower stopped');
    });

    it('the rollback restores every collection a carrier reads', async () => {
      const calls: Call[] = [];
      const out = await restoreCarried(noPg, new FakeCarryMongo(), [fake(new Map(), calls)]);
      expect(Object.keys(out)).to.deep.equal(['fake:agentWorkspaceHeads', 'fake:users']);
      expect(calls.map((c) => c.collection)).to.deep.equal(['agentWorkspaceHeads', 'users']);
    });
  });

  describe('Slack thread links (R45MIGR-FU-3)', () => {
    const m = MAPPINGS.find((x) => x.collection === 'slack_conversations')!;
    const conv = new ObjectId();
    const fakeDb = {
      collection: () => ({
        find: () => ({
          async *[Symbol.asyncIterator]() {
            yield { _id: conv, orgId: new ObjectId(fx.ORG) };
          },
        }),
      }),
    };
    const link = (extra: Record<string, unknown>): Record<string, unknown> => ({
      _id: new ObjectId(),
      threadId: 'T1',
      botId: 'B1',
      email: 'a@x.test',
      conversationId: conv.toHexString(),
      createdAt: new Date('2026-01-01T00:00:00Z'),
      updatedAt: new Date('2026-01-01T00:00:00Z'),
      __v: 0,
      ...extra,
    });

    it("the verifier reads a link without orgId as holding its conversation's organisation (listed, counted), and one with it as it is", async () => {
      const raws = [link({}), link({ orgId: fx.ORG.toUpperCase() })];
      await fillSlackThreadOrgs(fakeDb as never, raws);
      const rep = new SourceReport();
      const [a, b] = raws.map((r) => prepareSource(m, r, rep));
      expect(a).to.deep.include({ orgId: fx.ORG, _id: 'T1' });
      expect(a).to.not.have.property('__orgFromConversation');
      expect(b).to.deep.include({ orgId: fx.ORG });
      expect(rep.applied.get("absent orgId read as its conversation's organisation")).to.equal(1);
    });

    it('a row gives orgId back from org_id, so a rollback writes it', () => {
      const d = decodeDoc(m, { org_id: fx.ORG, thread_id: 'T1', bot_id: 'B1', email: 'a@x.test', conversation_id: conv.toHexString(), version: 0, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' }, {});
      expect(d.orgId).to.equal(fx.ORG);
    });

    it('the table is keyed through the key map by thread, bot and email', () => {
      expect(m.keyed?.columns).to.deep.equal(['thread_id', 'bot_id', 'email']);
      expect(m.dropped ?? []).to.deep.equal([]);
    });
  });

  it("the built-in carriers, then the edition's, are all there are", () => {
    expect(CARRIERS.map((c) => c.name)).to.deep.equal([...BUILT_IN_CARRIERS, ...EDITION_CARRIERS].map((c) => c.name));
    expect(EDITION_CARRIERS).to.have.length(EDITION_DOCSTORE_COVERAGE.carriers.length);
    expect(BUILT_IN_CARRIERS.map((c) => c.name)).to.deep.equal([
      'coding-engine-choices',
      'data-keys-and-workspaces',
      'audit-chain',
      'durable-runs',
      'ledgers',
      'peoples-data',
      'erasure',
      'control-plane',
    ]);
  });
});
