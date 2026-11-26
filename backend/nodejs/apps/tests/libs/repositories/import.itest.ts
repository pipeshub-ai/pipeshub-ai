/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import { tmpdir } from 'node:os'
import { Client } from 'pg'
import { Cluster, PG_BINS, startCluster } from '../db/pg-harness'
import { importGroupDocs } from '../../../src/libs/repositories/group/pg-import'
import { insertRows, withUpsert } from '../../../src/libs/repositories/import'

/** The import layer against real PostgreSQL (one cluster per directory in PG_TEST_BINS, e.g. 17 and 18). */
const DDL = `
CREATE SCHEMA identity;
CREATE DOMAIN identity.oid24 AS text COLLATE "C" CHECK (VALUE ~ '^[0-9a-f]{24}$');
CREATE TABLE identity.user_groups (
  org_id text NOT NULL, id identity.oid24 NOT NULL, slug text NOT NULL, name text NOT NULL,
  type text NOT NULL CHECK (type IN ('admin', 'standard', 'everyone', 'custom')),
  is_deleted boolean NOT NULL DEFAULT false, deleted_by identity.oid24, version int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
  PRIMARY KEY (org_id, id), UNIQUE (org_id, slug));
CREATE TABLE identity.user_group_members (
  org_id text NOT NULL, group_id identity.oid24 NOT NULL, user_id identity.oid24 NOT NULL, pos int NOT NULL,
  PRIMARY KEY (org_id, group_id, user_id),
  FOREIGN KEY (org_id, group_id) REFERENCES identity.user_groups (org_id, id) ON DELETE CASCADE);`

const O = 'a'.repeat(24)
const hex = (n: number) => n.toString(16).padStart(24, '0')
const grp = (n: number, name: string, users: string[]) => ({
  _id: hex(n), orgId: O, slug: `usergroup-${n}`, name, type: 'custom', users,
  createdAt: '2026-01-01T00:00:00.000Z', updatedAt: '2026-01-01T00:00:00.000Z',
})

describe('import layer on real PostgreSQL', function () {
  this.timeout(120000)
  for (const bin of PG_BINS) {
    describe(bin, () => {
      let cluster: Cluster
      let c: Client
      let k: never
      before(async () => {
        cluster = await startCluster(bin, tmpdir())
        c = new Client({ host: '127.0.0.1', port: cluster.port, user: 'postgres', database: 'postgres' })
        await c.connect()
        await c.query(DDL)
        k = { query: (sql: string, p?: unknown[]) => c.query(sql, p) } as never
      })
      after(async () => {
        await c.end()
        cluster.stop()
      })
      beforeEach(() => c.query('TRUNCATE identity.user_groups CASCADE'))
      const members = async (g: string) =>
        (await c.query('SELECT user_id FROM identity.user_group_members WHERE group_id = $1 ORDER BY pos', [g])).rows.map((r) => r.user_id)

      it('an upsert batch reports the exact inserted count, whatever it updates', async () => {
        const row = (n: number, name: string) => ({
          org_id: O, id: hex(n), slug: `s${n}`, name, type: 'custom',
          created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
        })
        await insertRows(k, 'identity.user_groups', [row(1, 'a'), row(2, 'b')])
        const rows = [row(1, 'a2'), row(2, 'b2'), row(3, 'c'), row(4, 'd'), row(4, 'd2')] // 2 existing, 2 new, one key twice
        const n = await withUpsert(['identity.user_groups'], () => insertRows(k, 'identity.user_groups', rows))
        expect(n).to.equal(2)
        const { rows: got } = await c.query('SELECT id, name FROM identity.user_groups ORDER BY id')
        expect(got.map((r) => r.name)).to.deep.equal(['a2', 'b2', 'c', 'd2'])
        // a second identical run inserts nothing
        expect(await withUpsert(['identity.user_groups'], () => insertRows(k, 'identity.user_groups', rows))).to.equal(0)
      })

      it('a group written in place gets the document members', async () => {
        const [u1, u2, u3] = [hex(101), hex(102), hex(103)]
        await importGroupDocs(k, 'userGroups', [grp(1, 'g', [u1, u2])])
        await withUpsert(['identity.user_groups'], () => importGroupDocs(k, 'userGroups', [grp(1, 'g', [u3, u1])]))
        expect(await members(hex(1))).to.deep.equal([u3, u1])
      })

      it('an existing group keeps its members in insert mode', async () => {
        const [u1, u2, u3] = [hex(101), hex(102), hex(103)]
        await importGroupDocs(k, 'userGroups', [grp(1, 'g', [u1, u2])])
        const stats = await importGroupDocs(k, 'userGroups', [grp(1, 'g', [u3]), grp(2, 'h', [u3])])
        expect(await members(hex(1))).to.deep.equal([u1, u2])
        expect(await members(hex(2))).to.deep.equal([u3])
        expect(stats.inserted['identity.user_groups']).to.equal(1)
      })
    })
  }
})
