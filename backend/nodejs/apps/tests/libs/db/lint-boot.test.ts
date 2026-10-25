/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { FakePool } from './fake-pool'
import {
  PgRuntime, SchemaNotReady, evaluateCapabilities, lintSql, readProfileEpoch, renderTypes, scanSource, versionAtLeast,
  waitForSchema,
} from '../../../src/libs/db'
import { makeOrgContext, runWithOrgContext } from '../../../src/libs/context/orgContext'

describe('libs/db lint', () => {
  it('rejects every session-state construct and allows the transaction-scoped forms', () => {
    const bad = [
      'SET statement_timeout = 1', 'SET SESSION AUTHORIZATION x', 'SET ROLE r', 'RESET ALL',
      "SELECT set_config('x.y', '1', false)", 'DECLARE c CURSOR WITH HOLD FOR SELECT 1', 'LISTEN ch', 'PREPARE p AS SELECT 1',
      'EXECUTE p', 'DEALLOCATE p', 'DISCARD ALL', 'CREATE TEMP TABLE t (a int)', 'SELECT pg_advisory_lock(1)',
      'SELECT pg_advisory_unlock_all()', 'SET LOCAL app.org_id = \'x\'', "SELECT set_config('app.org_id', 'x', true)",
      "SELECT set_config('role', 'pipeshub_jobs', true)", 'SET LOCAL role pipeshub_jobs', 'ROLLBACK TO SAVEPOINT s', 'SELECT pg_try_advisory_lock(1, 2)',
      'SELECT pg_advisory_xact_lock(7341, 1)', 'SELECT pg_try_advisory_xact_lock_shared(7350, 9)', 'SELECT pg_advisory_xact_lock(7340,1)', 'BEGIN', 'COMMIT', 'ROLLBACK',
    ]
    for (const s of bad) expect(lintSql(s), s).to.not.be.empty
    const good = [
      "/* pipeshub:a.b */ SELECT set_config('x.y', '1', true)", 'SET LOCAL x = 1', 'CREATE TEMP TABLE t (a int) ON COMMIT DROP',
      'SELECT pg_advisory_xact_lock(1, 2)', 'SELECT pg_try_advisory_xact_lock_shared(1, 2)',
      "UPDATE t SET a = 1 WHERE b = 'SET ROLE x'", "SELECT 'LISTEN', \"DISCARD\" -- DECLARE", 'SELECT $$ BEGIN $$',
    ]
    for (const s of good) expect(lintSql(s), s).to.deep.equal([])
  })

  it('scanSource finds untagged, interpolated and banned SQL in source text', () => {
    const src = [
      "const ok = '/* pipeshub:a.b */ SELECT id FROM t WHERE x = $1'",
      "const bad = 'SELECT id FROM t'",
      'const built = `/* pipeshub:a.c */ SELECT id FROM t WHERE x = ${x}`',
      "const prose = 'select a different approach'",
      "// SELECT x FROM y in a comment",
    ].join('\n')
    expect(scanSource(src)).to.deep.equal([
      { line: 2, problem: 'SQL without a /* pipeshub:module.fn */ tag' },
      { line: 3, problem: 'SQL built with interpolation; use parameters' },
    ])
  })

  it('no source file that uses the PG runtime carries untagged, built or banned SQL', () => {
    const root = join(__dirname, '../../../src')
    const files: string[] = []
    const walk = (d: string) => readdirSync(d).forEach((f) => {
      const p = join(d, f)
      if (statSync(p).isDirectory()) walk(p)
      else if (p.endsWith('.ts') && !p.includes(join('src', 'libs', 'db') + '/')) files.push(p)
    })
    walk(root)
    const users = files.filter((f) => /from '[./]*(libs\/)?db(\/[a-z-]+)?'/.test(readFileSync(f, 'utf8')))
    for (const f of users) expect(scanSource(readFileSync(f, 'utf8')), f).to.deep.equal([])
  })
})

describe('libs/db boot', () => {
  const rtWith = (rows: any[] | (() => any[])) =>
    new PgRuntime({ pool: new FakePool((t) => (t.includes('schema_epoch') || t.includes('profile_epoch') ? { rows: typeof rows === 'function' ? rows() : rows } : {})), defaultService: 'node', sleep: async () => {} })
  const need = { identity: { requires: 2, code: 2 } }

  it('waitForSchema returns when the epochs meet the requirements (int columns may arrive as strings)', async () => {
    await waitForSchema(rtWith([{ branch: 'identity', epoch: '3', min_code_epoch: '0' }]), need)
  })

  it('waitForSchema retries until migrate has run, then reports the problems', async () => {
    let calls = 0
    const rt = rtWith(() => (++calls < 3 ? [{ branch: 'identity', epoch: 1, min_code_epoch: 0 }] : [{ branch: 'identity', epoch: 2, min_code_epoch: 0 }]))
    await waitForSchema(rt, need, { attempts: 5, delayMs: 1, sleep: async () => {} })
    expect(calls).to.equal(3)
    const e = await waitForSchema(rtWith([{ branch: 'identity', epoch: 1, min_code_epoch: 0 }]), need, { attempts: 2, sleep: async () => {} }).catch((x) => x)
    expect(e).to.be.instanceOf(SchemaNotReady)
    expect((e as SchemaNotReady).problems).to.deep.equal(['schema identity epoch 1 < required 2'])
  })

  it('readProfileEpoch wants exactly one row', async () => {
    expect(await readProfileEpoch(rtWith([{ epoch: 4 }]))).to.equal(4)
    expect(await readProfileEpoch(rtWith([{ epoch: null }])).catch((x) => x)).to.be.instanceOf(SchemaNotReady)
    const e = await readProfileEpoch(rtWith([])).catch((x) => x)
    expect(e).to.be.instanceOf(SchemaNotReady)
  })

  it('evaluateCapabilities applies the floors and derives the flags', () => {
    const probe = (pgNum: number, ext: Record<string, string | null>) => ({
      pgNum, pgMajor: Math.floor(pgNum / 10000), preload: '', azureAllowlist: null,
      extensions: Object.fromEntries(Object.entries(ext).map(([k, v]) => [k, { installed: v, available: '9' }])),
    })
    const ok = evaluateCapabilities(probe(170011, { pg_trgm: '1.6', vector: '0.8.2' }), { vector: true })
    expect(ok).to.deep.equal({ bm25: false, hnswVacuumSafe: false, problems: [] })
    expect(evaluateCapabilities(probe(180006, { pg_trgm: '1.6', vector: '0.8.10', pg_textsearch: '1.5.1' })).hnswVacuumSafe).to.equal(true)
    expect(evaluateCapabilities(probe(160004, { pg_trgm: '1.6' })).problems[0]).to.contain('below the floor 17')
    expect(evaluateCapabilities(probe(190000, { pg_trgm: '1.6' })).problems[0]).to.contain('newer than the tested')
    expect(evaluateCapabilities(probe(170011, { pg_trgm: null }), { vector: true }).problems).to.have.length(2)
    expect(versionAtLeast('0.8.10', '0.8.4')).to.equal(true)
    expect(versionAtLeast('0.8', '0.8.4')).to.equal(false)
  })

  it('works inside a request context too (the boot read needs none)', async () => {
    const ctx = makeOrgContext({ orgId: 'o', userId: null, kind: 'user', source: 'jwt' })
    await runWithOrgContext(ctx, () => waitForSchema(rtWith([{ branch: 'identity', epoch: 2, min_code_epoch: 0 }]), need))
  })
})

describe('libs/db typegen', () => {
  it('renders deterministic Kysely types: nullable, generated, 64-bit as string, arrays', () => {
    const out = renderTypes([
      { schema: 'kernel', table: 'schema_epoch', column: 'branch', type: 'text', nullable: false, generated: '' },
      { schema: 'kernel', table: 'schema_epoch', column: 'epoch', type: 'int4', nullable: false, generated: '' },
      { schema: 'kernel', table: 'schema_epoch', column: 'min_code_epoch', type: 'int4', nullable: false, generated: 'default' },
      { schema: 'kernel', table: 'schema_epoch', column: 'big', type: 'int8', nullable: true, generated: 'always' },
      { schema: 'a', table: 'b', column: 'tags', type: '_text', nullable: false, generated: '' },
    ])
    expect(out).to.contain('export interface KernelSchemaEpoch {\n  branch: string;\n  epoch: number;\n  min_code_epoch: Generated<number>;\n  big: GeneratedAlways<string | null>;\n}')
    expect(out).to.contain('tags: string[];')
    expect(out.indexOf("'a.b'")).to.be.lessThan(out.indexOf("'kernel.schema_epoch'"))
  })
})
