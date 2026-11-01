/** The pieces of the migrator that need no database: source canonicalisation, the path census, the decoder, the comparison. */
import 'reflect-metadata';
import { expect } from 'chai';
import { Decimal128, Long, ObjectId } from 'mongodb';
import { Census, judgeCensus } from '../../../src/scripts/docstore-migrate/census';
import { compareDocs } from '../../../src/scripts/docstore-migrate/compare';
import { decodeDoc } from '../../../src/scripts/docstore-migrate/decode';
import type { CollectionMapping } from '../../../src/scripts/docstore-migrate/mapping/types';
import { SourceReport, prepareSource } from '../../../src/scripts/docstore-migrate/source';
import { loadConfig, redact } from '../../../src/scripts/docstore-migrate/config';
import { docHash } from '../../../src/scripts/docstore-migrate/canonical';

const M: CollectionMapping = {
  collection: 'things',
  repository: 'org',
  table: 'x.things',
  idCol: 'id',
  orgPath: 'orgId',
  orgCol: 'org_id',
  versionCol: 'version',
  nullsCol: 'nulls',
  fields: [
    { col: 'org_id', path: 'orgId', type: 'oid' },
    { col: 'title', path: 'title', type: 'text' },
    { col: 'note', path: 'note', type: 'text', presence: 'listed' },
    { col: 'meta', path: 'metadata', type: 'json' },
    { col: 'at', path: 'at', type: 'date' },
    { col: 'ms', path: 'lastActivityAt', type: 'epoch_ms' },
    { col: 'secret', path: 'code', type: 'sha256' },
    { col: 'logo', path: 'logo', type: 'bytea_b64' },
  ],
  children: [{ table: 'x.members', parentCol: 'thing_id', path: 'members', orderCol: 'pos', fields: [{ col: 'user_id', path: 'userId', type: 'oid' }], derived: ['org_id'] }],
  derived: [],
};

const oid = new ObjectId();

describe('docstore-migrate engine', () => {
  describe('source side: types and NUL', () => {
    it('brings Long, Decimal128, undefined, old dates, dotted keys and U+0000 to one form, counting the strips', () => {
      const rep = new SourceReport();
      const d = prepareSource(
        M,
        {
          _id: oid,
          orgId: oid,
          title: 'a\u0000b',
          metadata: { big: Long.fromString('9007199254740993'), small: Long.fromNumber(7), price: Decimal128.fromString('1.10'), gone: undefined, 'a.b': 1, 'k\u0000': 2 },
          at: new Date('1969-07-20T20:17:40.000Z'),
          members: [{ userId: oid }],
        },
        rep,
      ) as Record<string, any>;
      expect(d.title).to.equal('ab');
      expect(d.metadata).to.deep.equal({ big: '9007199254740993', small: 7, price: '1.10', 'a.b': 1, k: 2 });
      expect(d.at).to.equal('1969-07-20T20:17:40.000Z');
      expect(d.orgId).to.equal(String(oid));
      expect([...rep.nul.values()].reduce((a, b) => a + b, 0)).to.equal(2);
    });

    it('hashes a secret and reads an absent array as empty (listed)', () => {
      const rep = new SourceReport();
      const d = prepareSource(M, { _id: oid, orgId: oid, code: 'abc' }, rep) as Record<string, any>;
      expect(d.code).to.equal('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
      expect(d.members).to.deep.equal([]);
      expect(rep.applied.get('absent array members read as []')).to.equal(1);
    });
  });

  describe('decoder', () => {
    const root = { id: String(oid), org_id: String(oid), title: null, note: null, meta: { a: 1 }, at: '2026-01-02T03:04:05.678+00:00', ms: '2026-01-02T03:04:05.678+00:00', secret: null, logo: '\\x6869', version: 3, nulls: ['note'] };
    it('puts columns at their paths, NULL as absent, listed NULL as null, ms as a number, bytea as base64', () => {
      const d = decodeDoc(M, root, { 'x.members': [{ pos: 1, user_id: 'bb'.repeat(12) }, { pos: 0, user_id: 'aa'.repeat(12) }] });
      expect(d).to.deep.equal({
        _id: String(oid),
        orgId: String(oid),
        note: null,
        metadata: { a: 1 },
        at: '2026-01-02T03:04:05.678Z',
        lastActivityAt: Date.parse('2026-01-02T03:04:05.678Z'),
        logo: 'aGk=',
        __v: 3,
        members: [{ userId: 'aa'.repeat(12) }, { userId: 'bb'.repeat(12) }],
      });
    });
  });

  describe('comparison', () => {
    it('reports a missing, an extra and a differing document, and ignores key order', () => {
      const a = { _id: '1', x: { p: 1, q: 2 } };
      const b = { _id: '1', x: { q: 2, p: 1 } };
      expect(docHash(a)).to.equal(docHash(b));
      expect(compareDocs([a, { _id: '2' }, { _id: '3', v: 1 }], [b, { _id: '3', v: 2 }, { _id: '9' }]).map((d) => `${d.id}:${d.kind}`)).to.deep.equal(['2:missing_in_pg', '3:differs', '9:extra_in_pg']);
    });
    it('absent is not null', () => {
      expect(compareDocs([{ _id: '1', t: null }], [{ _id: '1' }])[0]?.kind).to.equal('differs');
    });
  });

  describe('path census', () => {
    it('names a path the mapping does not know, with its count', () => {
      const c = new Census();
      for (let i = 0; i < 100; i++) c.add(i === 0 ? { _id: 1, title: 't', x: { y: 1 } } : { _id: 1, title: 't' });
      const f = judgeCensus(M, c).filter((x) => x.kind === 'unmapped_path');
      expect(f).to.have.length(2); // x and x.y
      expect(f.map((x) => `${x.collection}.${x.path}:${x.count}`)).to.include('things.x.y:1');
    });
    it('does not flag anything beneath a json column, and flags stored nulls in an omit-class field', () => {
      const c = new Census();
      c.add({ _id: 1, metadata: { deep: { er: [1, { z: 2 }] } }, title: null, note: null });
      const f = judgeCensus(M, c);
      expect(f.filter((x) => x.kind === 'unmapped_path')).to.deep.equal([]);
      expect(f.filter((x) => x.kind === 'null_in_omit_field').map((x) => x.path)).to.deep.equal(['title']);
    });
    it('counts documents that hold U+0000 by path', () => {
      const c = new Census();
      c.add({ title: 'a\u0000', metadata: { 'k\u0000': 1 } });
      expect(judgeCensus(M, c).filter((x) => x.kind === 'nul').map((x) => x.path).sort()).to.deep.equal(['metadata.<key>', 'title']);
    });
  });

  describe('configuration', () => {
    it('reads psycopg style URLs and never prints a password', () => {
      const cfg = loadConfig({ MONGO_URI: 'mongodb://u:p@h/', PG_IMPORT_URL: 'postgresql://j:secret@h/db', PG_MIGRATE_URL: 'postgresql+psycopg://m:secret@h/db' } as never);
      expect(cfg.pgMigrateUrl).to.equal('postgresql://m:secret@h/db');
      expect(redact(cfg.pgMigrateUrl)).to.equal('postgresql://m:***@h/db');
      expect(redact('connect ECONNREFUSED mongodb://root:hunter2@mongodb:27017/')).to.not.include('hunter2');
    });
  });
});
