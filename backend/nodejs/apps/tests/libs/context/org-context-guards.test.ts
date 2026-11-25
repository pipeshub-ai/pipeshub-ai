/// <reference types="mocha" />
import { expect } from 'chai'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

/**
 * CI ratchets for org context (direct_org_reads, detached_work). Counts per file may only go down.
 * To record an intentional change run
 *   ORG_CONTEXT_UPDATE_BASELINE=1 npx mocha --config <rc> tests/libs/context/org-context-guards.test.ts
 * and review the diff of org-context-baseline.json.
 *
 * detached_work: long-lived work (timers, workers, consumers, lazily created clients) starts inside
 *   spawnDetached; AsyncLocalStorage propagates into everything created inside a request.
 * direct_org_reads: new reads of the org straight off the request user bypass the context.
 */
const SRC = path.resolve(__dirname, '../../../src')
const BASELINE = path.join(__dirname, 'org-context-baseline.json')
const GUARDS: Record<string, { pattern: RegExp; exempt: string[]; hint: string }> = {
  'detached_work': {
    pattern: /\bsetInterval\(|\bnew Worker\(/g,
    // detachedInterval: the helper that starts a repeating timer inside spawnDetached
    exempt: ['libs/context/orgContext.ts'],
    hint: 'Start it with detachedInterval, or inside spawnDetached (libs/context/orgContext.ts).',
  },
  'direct_org_reads': {
    pattern: /\breq\.user(?:[?!]?\.orgId\b|[?!]?\.?\[\s*['\"]orgId['\"]\s*\])/g,
    exempt: ['libs/middlewares/auth.middleware.ts'],
    hint: 'Read the org from currentOrgContext() instead of the request user.',
  },
}

function* walk(dir: string): Generator<string> {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name)
    if (e.isDirectory()) yield* walk(p)
    else if (e.name.endsWith('.ts') && !e.name.endsWith('.d.ts')) yield p
  }
}

function scan(): Record<string, Record<string, number>> {
  const out: Record<string, Record<string, number>> = Object.fromEntries(Object.keys(GUARDS).map((g) => [g, {}]))
  for (const file of walk(SRC)) {
    const rel = path.relative(SRC, file).split(path.sep).join('/')
    const text = fs.readFileSync(file, 'utf8')
    for (const [g, { pattern, exempt }] of Object.entries(GUARDS)) {
      const n = exempt.includes(rel) ? 0 : (text.match(pattern) ?? []).length
      if (n) out[g]![rel] = n
    }
  }
  return out
}

/**
 * The committed baseline plus the per-file counts of every `org-context-baseline*.json` under the
 * directories in `SEAM_BASELINE_DIRS` (separated by the path delimiter; default none), for code kept
 * outside this tree. Counts are added, so an overlay can only allow more sites in its own files.
 */
export function withOverlays(
  base: Record<string, Record<string, number>>,
  dirs: string[] = (process.env.SEAM_BASELINE_DIRS ?? '').split(path.delimiter).filter(Boolean),
): Record<string, Record<string, number>> {
  const merged = Object.fromEntries(Object.entries(base).map(([g, m]) => [g, { ...m }]))
  for (const dir of dirs) {
    if (!fs.existsSync(dir)) continue
    for (const name of fs.readdirSync(dir).filter((n) => /^org-context-baseline.*\.json$/.test(n)).sort()) {
      const extra = JSON.parse(fs.readFileSync(path.join(dir, name), 'utf8')) as Record<string, Record<string, number>>
      for (const [g, counts] of Object.entries(extra)) {
        if (!merged[g]) continue
        for (const [f, n] of Object.entries(counts)) merged[g]![f] = (merged[g]![f] ?? 0) + n
      }
    }
  }
  return merged
}

describe('org context CI ratchets', () => {
  it('adds an overlay directory\'s counts to the committed baseline', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'org-ctx-'))
    fs.writeFileSync(path.join(dir, 'org-context-baseline.extra.json'), JSON.stringify({ direct_org_reads: { 'a.ts': 2 } }))
    const merged = withOverlays({ direct_org_reads: { 'a.ts': 1 }, detached_work: {} }, [dir])
    expect(merged.direct_org_reads).to.deep.equal({ 'a.ts': 3 })
    expect(merged.detached_work).to.deep.equal({})
  })

  it('direct_org_reads counts plain, optional and non-null-asserted reads', () => {
    const hits = ['req.user.orgId', 'req.user?.orgId', 'req.user!.orgId', "req.user['orgId']", 'req.user?.["orgId"]'].map((t) => (t.match(GUARDS.direct_org_reads!.pattern) ?? []).length)
    expect(hits).to.deep.equal([1, 1, 1, 1, 1])
  })

  const found = scan()
  if (process.env.ORG_CONTEXT_UPDATE_BASELINE) {
    fs.writeFileSync(BASELINE, JSON.stringify(found, null, 1) + '\n')
  }
  const base = withOverlays(JSON.parse(fs.readFileSync(BASELINE, 'utf8')) as typeof found)

  for (const [g, { hint }] of Object.entries(GUARDS)) {
    it(`${g}: no new sites`, () => {
      const grown = Object.entries(found[g]).filter(([f, n]) => n > (base[g]![f] ?? 0)).map(([f, n]) => `${f}: ${base[g]![f] ?? 0} -> ${n}`)
      expect(grown, `${g} new sites. ${hint}`).to.deep.equal([])
    })
  }
})
