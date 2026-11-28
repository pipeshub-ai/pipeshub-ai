/// <reference types="mocha" />
import { expect } from 'chai'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

/**
 * CI ratchets for org context (direct_org_reads, detached_work). Counts per file may only go down.
 * To record an intentional change run
 *   ORG_CONTEXT_UPDATE_BASELINE=1 npx mocha --config <rc> tests/libs/context/org-context-guards.test.ts
 * and review the diff of org-context-baseline.json. Files a downstream build adds under src/ are counted in its own
 * `*baseline*.json` files, under the directories in ORG_CONTEXT_BASELINE_DIRS (separated by the path delimiter).
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

type Counts = Record<string, Record<string, number>>

const baselineFilesUnder = (dir: string): string[] =>
  !fs.existsSync(dir)
    ? []
    : fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
        const p = path.join(dir, e.name)
        if (e.isDirectory()) return baselineFilesUnder(p)
        return e.name.endsWith('.json') && e.name.includes('baseline') ? [p] : []
      })

const extraDirs = (): string[] => (process.env.ORG_CONTEXT_BASELINE_DIRS ?? '').split(path.delimiter).filter(Boolean)

/** The committed baseline plus every `*baseline*.json` under `dirs`, per-file counts added. */
export function loadBaseline(dirs: string[] = extraDirs()): Counts {
  const merged = JSON.parse(fs.readFileSync(BASELINE, 'utf8')) as Counts
  for (const file of dirs.flatMap(baselineFilesUnder).sort()) {
    const extra = JSON.parse(fs.readFileSync(file, 'utf8')) as Counts
    for (const [g, counts] of Object.entries(extra)) {
      merged[g] ??= {}
      for (const [f, n] of Object.entries(counts)) merged[g][f] = (merged[g][f] ?? 0) + n
    }
  }
  return merged
}

export function scan(): Counts {
  const out: Counts = Object.fromEntries(Object.keys(GUARDS).map((g) => [g, {}]))
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

  it('adds the counts of baseline files found under ORG_CONTEXT_BASELINE_DIRS to the committed ones', () => {
    const dir = fs.mkdtempSync(path.join(process.env.TMPDIR ?? '.', 'org-context-baseline-'))
    try {
      fs.mkdirSync(path.join(dir, 'nested'))
      fs.writeFileSync(path.join(dir, 'nested', 'extra-baseline.json'), JSON.stringify({ detached_work: { 'x/a.ts': 2 } }))
      fs.writeFileSync(path.join(dir, 'other.json'), JSON.stringify({ detached_work: { 'x/ignored.ts': 1 } }))
      expect(loadBaseline([dir]).detached_work).to.include({ 'x/a.ts': 2 })
      expect(loadBaseline([dir]).detached_work).to.not.have.property('x/ignored.ts')
      expect(loadBaseline([]).detached_work).to.not.have.property('x/a.ts')
      expect(loadBaseline([path.join(dir, 'missing')]).detached_work).to.not.have.property('x/a.ts')
    } finally {
      fs.rmSync(dir, { recursive: true, force: true })
    }
  })

  const found = scan()
  if (process.env.ORG_CONTEXT_UPDATE_BASELINE) {
    // Counts another directory records stay there.
    const recorded = loadBaseline(extraDirs())
    const committed = loadBaseline([])
    const own = Object.fromEntries(
      Object.entries(found).map(([g, counts]) => [
        g,
        Object.fromEntries(Object.entries(counts).filter(([f]) => (recorded[g]?.[f] ?? 0) === (committed[g]?.[f] ?? 0))),
      ]),
    )
    fs.writeFileSync(BASELINE, JSON.stringify(own, null, 1) + '\n')
  }

  for (const [g, { hint }] of Object.entries(GUARDS)) {
    it(`${g}: no new sites`, () => {
      // The committed baseline, a downstream build's own files under ORG_CONTEXT_BASELINE_DIRS, then the overlays.
      const base = withOverlays(loadBaseline())
      const grown = Object.entries(found[g]).filter(([f, n]) => n > (base[g]?.[f] ?? 0)).map(([f, n]) => `${f}: ${base[g]?.[f] ?? 0} -> ${n}`)
      expect(grown, `${g} new sites. ${hint}`).to.deep.equal([])
    })
  }
})
