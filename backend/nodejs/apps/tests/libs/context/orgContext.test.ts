/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import { EventEmitter } from 'node:events'
import {
  bindOrgContext,
  onBound,
  orgContextStorage,
  getCurrentOrgId,
  makeOrgContext,
  currentOrgContext,
  currentOrgContextOrSystem,
  runWithOrgContext,
  forOrg,
  system,
  spawnDetached,
  MissingOrgContext,
  OrgMismatch,
  SystemContextNotAllowed,
  ambientOrg,
  validateMode,
  SYSTEM_ORG_ID,
  orgContextViolations,
} from '../../../src/libs/context/orgContext'

const user = (orgId: string) => makeOrgContext({ orgId, userId: 'u1', kind: 'user', source: 'jwt' })
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

describe('orgContext', () => {
  it('fails closed when nothing is bound', () => {
    expect(() => currentOrgContext()).to.throw(MissingOrgContext)
    expect(() => currentOrgContextOrSystem()).to.throw(MissingOrgContext)
    expect(getCurrentOrgId()).to.be.undefined
  })

  it('binds for the subtree only and a nested bind must keep the org', () => {
    runWithOrgContext(user('org_a'), () => {
      expect(currentOrgContext().orgId).to.equal('org_a')
      expect(getCurrentOrgId()).to.equal('org_a')
      runWithOrgContext(user('org_a'), () => undefined)
      expect(() => runWithOrgContext(user('org_b'), () => undefined)).to.throw(OrgMismatch)
    })
    expect(orgContextStorage.getStore()).to.be.undefined
  })

  it('only the system kind may lack an org', () => {
    expect(() => makeOrgContext({ orgId: null, userId: null, kind: 'user', source: 'jwt' })).to.throw()
    expect(() => makeOrgContext({ orgId: 'o', userId: null, kind: 'system', source: 'boot' })).to.throw()
  })

  it('refuses a system context in currentOrgContext', () => {
    system('boot', () => {
      expect(currentOrgContextOrSystem().kind).to.equal('system')
      expect(() => currentOrgContext()).to.throw(SystemContextNotAllowed)
    })
  })

  it('3000 interleaved requests keep their own org across awaits', async () => {
    const results = await Promise.all(
      Array.from({ length: 3000 }, (_, i) =>
        runWithOrgContext(user(`org_${i % 7}`), async () => {
          await sleep(i % 5)
          await Promise.resolve()
          return currentOrgContext().orgId === `org_${i % 7}`
        }),
      ),
    )
    expect(results.every(Boolean)).to.equal(true)
  })

  it('per-org job iteration under system() does not leak between orgs', async () => {
    const seen: string[] = []
    await system('purge', async () => {
      for (const org of ['org_a', 'org_b', 'org_c']) {
        try {
          await forOrg(org, 'purge', async () => {
            await sleep(1)
            seen.push(currentOrgContext().orgId as string)
            if (org === 'org_a') throw new Error('org A iteration crashed')
          })
        } catch { /* the next org must not see org_a */ }
      }
      expect(currentOrgContextOrSystem().kind).to.equal('system')
    })
    expect(seen).to.deep.equal(['org_a', 'org_b', 'org_c'])
  })

  it('a client created lazily inside request A never captures that org', async () => {
    const run = async (start: <T>(fn: () => T) => T) => {
      const seen: string[] = []
      const client = new EventEmitter()
      client.on('event', () => {
        try { seen.push(currentOrgContext().orgId as string) } catch (e) { seen.push((e as Error).name) }
      })
      let timer!: NodeJS.Timeout
      runWithOrgContext(user('org_a'), () => {
        start(() => { timer = setInterval(() => client.emit('event'), 1) })
      })
      while (seen.length < 3) await sleep(2)
      clearInterval(timer)
      return seen.slice(0, 3)
    }
    // the failure being guarded: a timer created inside run() inherits the request's org
    expect(await run((fn) => fn())).to.deep.equal(['org_a', 'org_a', 'org_a'])
    expect(await run(spawnDetached)).to.deep.equal(['MissingOrgContext', 'MissingOrgContext', 'MissingOrgContext'])
  })

  it('request-scoped child work keeps the caller context without any helper', async () => {
    await runWithOrgContext(user('org_a'), async () => {
      const child = new Promise<string>((r) => setImmediate(() => r(currentOrgContext().orgId as string)))
      expect(await child).to.equal('org_a')
    })
  })

  describe('shadow / enforce', () => {
    const prev = process.env.ORG_CONTEXT_MODE
    afterEach(() => {
      if (prev === undefined) delete process.env.ORG_CONTEXT_MODE
      else process.env.ORG_CONTEXT_MODE = prev
    })

    it('shadow (default) counts missing and mismatched access and lets it through', () => {
      delete process.env.ORG_CONTEXT_MODE
      const n = (k: string) => orgContextViolations.get(k) ?? 0
      const m0 = n('missing:site-a'), x0 = n('mismatch:site-b')
      expect(ambientOrg('org_x', 'site-a')).to.equal('org_x')
      runWithOrgContext(user('org_a'), () => {
        expect(ambientOrg('org_b', 'site-b')).to.equal('org_b')
        expect(ambientOrg('org_a', 'site-b')).to.equal('org_a')
        expect(ambientOrg(undefined, 'site-b')).to.equal('org_a')
      })
      expect(n('missing:site-a')).to.equal(m0 + 1)
      expect(n('mismatch:site-b')).to.equal(x0 + 1)
    })

    it('rejects an unknown ORG_CONTEXT_MODE instead of falling back to shadow', () => {
      for (const bad of ['enforced', 'true', 'off']) {
        process.env.ORG_CONTEXT_MODE = bad
        expect(() => validateMode()).to.throw(/ORG_CONTEXT_MODE/)
        expect(() => ambientOrg('org_x', 's')).to.throw(/ORG_CONTEXT_MODE/)
      }
      process.env.ORG_CONTEXT_MODE = ' Enforce '
      expect(validateMode()).to.equal(true)
    })

    it('forOrg without an org is a typed error', () => {
      for (const missing of [null, undefined, '']) {
        expect(() => forOrg(missing, 'job', () => 1)).to.throw(MissingOrgContext)
      }
    })

    it('the bootstrap org "system" is counted apart from mismatches and refused in enforce', () => {
      process.env.ORG_CONTEXT_MODE = 'shadow'
      runWithOrgContext(user(SYSTEM_ORG_ID), () => {
        const before = orgContextViolations.get('system_token:s-sys') ?? 0
        expect(ambientOrg('org_b', 's-sys')).to.equal('org_b')
        expect(orgContextViolations.get('system_token:s-sys')).to.equal(before + 1)
        expect(orgContextViolations.get('mismatch:s-sys')).to.equal(undefined)
        process.env.ORG_CONTEXT_MODE = 'enforce'
        expect(() => ambientOrg('org_b', 's-sys')).to.throw(OrgMismatch)
      })
    })

    it('enforce fails closed', () => {
      process.env.ORG_CONTEXT_MODE = 'enforce'
      expect(() => ambientOrg('org_x', 's')).to.throw(MissingOrgContext)
      system('job', () => expect(() => ambientOrg('org_x', 's')).to.throw(MissingOrgContext))
      runWithOrgContext(user('org_a'), () => {
        expect(ambientOrg('org_a', 's')).to.equal('org_a')
        expect(() => ambientOrg('org_b', 's')).to.throw(OrgMismatch)
      })
    })
  })

  describe('bindOrgContext and onBound on every supported Node', () => {
    it('run in the org they were created in, from inside another request\'s context or none, and their timers keep it', async () => {
      const seen: Array<string | undefined> = []
      const em = new EventEmitter()
      let bound!: () => void
      runWithOrgContext(user('org_a'), () => {
        bound = bindOrgContext(() => {
          seen.push(getCurrentOrgId())
          setTimeout(() => seen.push(getCurrentOrgId()), 5)
        })
        onBound(em, 'ping', () => seen.push(getCurrentOrgId()))
      })
      orgContextStorage.exit(() => bound())                                   // the emitter's context is none
      runWithOrgContext(user('org_b'), () => { em.emit('ping') })              // ... or another request's
      await new Promise((r) => setTimeout(r, 30))
      expect(seen).to.deep.equal(['org_a', 'org_a', 'org_a'])
      let unbound!: () => unknown
      orgContextStorage.exit(() => { unbound = bindOrgContext(() => getCurrentOrgId()) })
      expect(runWithOrgContext(user('org_c'), () => unbound())).to.equal(undefined)   // created with no org: runs with none
    })
  })
})
