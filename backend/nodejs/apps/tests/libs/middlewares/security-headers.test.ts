import { expect } from 'chai'
import { createHash } from 'crypto'
import express from 'express'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'fs'
import { AddressInfo } from 'net'
import { tmpdir } from 'os'
import path from 'path'
import {
  applyApiDocsCsp,
  applyDocumentCsp,
  documentCsp,
  inlineScriptHashes,
  parseSourceList,
  securityHeaderPolicy,
  securityHeaders,
  ShellScriptHashes,
} from '../../../src/libs/middlewares/security-headers'
import { createCspReportRouter, violationsOf } from '../../../src/libs/middlewares/csp-report'
import { isUnframeablePage, UNFRAMEABLE_PAGE_PATHS } from '../../../src/libs/middlewares/no-framing.middleware'

const NONE = { scriptSrc: [], connectSrc: [], frameSrc: [] }
const BOOT = 'self.__next_f.push([1,"boot"])'
const sha = (s: string) => `'sha256-${createHash('sha256').update(s, 'utf8').digest('base64')}'`

function directive(csp: string | null, name: string): string[] {
  const found = (csp ?? '').split(';').map((d) => d.trim().split(/\s+/)).find(([n]) => n === name)
  return found ? found.slice(1) : []
}

// The same layering app.ts uses: headers for everything, a document policy for the SPA's HTML files.
async function serve(env: NodeJS.ProcessEnv, run: (base: string) => Promise<void>, trustProxy = false): Promise<void> {
  const dir = mkdtempSync(path.join(tmpdir(), 'sechdr-'))
  mkdirSync(path.join(dir, 'chat'))
  const html = `<html><head><script>${BOOT}</script><script src="/_next/static/a.js"></script></head><body></body></html>`
  writeFileSync(path.join(dir, 'index.html'), html)
  writeFileSync(path.join(dir, 'chat', 'index.html'), html)
  const app = express()
  app.disable('x-powered-by')
  app.set('trust proxy', trustProxy)
  // The hardened profile unless the case says otherwise (ADR-OPS-07): these cases were written for ADR-WEB-01.
  const policy = securityHeaderPolicy({ PIPESHUB_HARDENED: 'true', ...env }, NONE)
  const hashes = new ShellScriptHashes()
  app.use(...securityHeaders(policy))
  app.get('/api/v1/thing', (_req, res) => { res.json({ ok: true }) })
  app.use('/api/v1/docs', applyApiDocsCsp(policy), (_req, res) => { res.send('<html>docs</html>') })
  app.use(express.static(dir, { setHeaders: (res, f) => { if (f.endsWith('.html')) applyDocumentCsp(res, policy, hashes, f, res.req.path) } }))
  app.get('*', (req, res) => {
    const shell = path.join(dir, 'index.html')
    applyDocumentCsp(res, policy, hashes, shell, req.path)
    res.sendFile(shell)
  })
  const server = app.listen(0)
  try {
    await run(`http://127.0.0.1:${(server.address() as AddressInfo).port}`)
  } finally {
    server.close()
    rmSync(dir, { recursive: true, force: true })
  }
}

describe('security headers (SEC-D-07, ADR-WEB-01)', () => {
  for (const page of ['/', '/chat/', '/login/', '/workspace/nope', '/api/v1/thing', '/api/v1/nope', '/api/v1/docs/']) {
    it(`sends the baseline on ${page} and no X-Powered-By`, async () => {
      await serve({}, async (base) => {
        const h = (await fetch(base + page)).headers
        expect(h.get('x-powered-by')).to.equal(null)
        expect(h.get('x-content-type-options')).to.equal('nosniff')
        expect(h.get('referrer-policy')).to.equal('strict-origin-when-cross-origin')
        expect(h.get('x-frame-options')).to.equal('DENY')
        expect(h.get('permissions-policy')).to.include('camera=()')
        expect(directive(h.get('content-security-policy'), 'frame-ancestors')).to.deep.equal(["'none'"])
        expect(h.get('strict-transport-security')).to.equal(null)
      })
    })
  }

  it('lets the SPA shell run only its own inline scripts', async () => {
    await serve({}, async (base) => {
      for (const page of ['/', '/chat/', '/record/abc']) {
        const csp = (await fetch(base + page)).headers.get('content-security-policy')
        const scripts = directive(csp, 'script-src')
        expect(scripts).to.include(sha(BOOT))
        expect(scripts).to.not.include("'unsafe-inline'")
        expect(scripts).to.not.include("'unsafe-eval'")
        expect(directive(csp, 'object-src')).to.deep.equal(["'none'"])
        expect(directive(csp, 'form-action')).to.deep.equal(["'self'"])
        expect(directive(csp, 'base-uri')).to.deep.equal(["'self'"])
      }
    })
  })

  it('gives API responses a policy under which nothing runs', async () => {
    await serve({}, async (base) => {
      const csp = (await fetch(base + '/api/v1/thing')).headers.get('content-security-policy')
      expect(directive(csp, 'default-src')).to.deep.equal(["'none'"])
      expect(directive(csp, 'script-src')).to.deep.equal([])
    })
  })

  it('sends HSTS over HTTPS only, trusting X-Forwarded-Proto only from a trusted proxy', async () => {
    await serve({}, async (base) => {
      const spoofed = await fetch(base + '/', { headers: { 'X-Forwarded-Proto': 'https' } })
      expect(spoofed.headers.get('strict-transport-security')).to.equal(null)
    })
    await serve({}, async (base) => {
      const proxied = await fetch(base + '/', { headers: { 'X-Forwarded-Proto': 'https' } })
      expect(proxied.headers.get('strict-transport-security')).to.equal('max-age=31536000')
    }, true)
    await serve({ FRONTEND_PUBLIC_URL: 'https://pipeshub.example.com' }, async (base) => {
      expect((await fetch(base + '/')).headers.get('strict-transport-security')).to.equal('max-age=31536000')
    })
    await serve({ FRONTEND_PUBLIC_URL: 'http://pipeshub.example.com' }, async (base) => {
      expect((await fetch(base + '/')).headers.get('strict-transport-security')).to.equal(null)
    })
  })

  for (const value of ['false', 'FALSE', 'true', '0', '']) {
    it(`ignores STRICT_MODE=${JSON.stringify(value)}: nothing relaxes`, async () => {
      await serve({ STRICT_MODE: value }, async (base) => {
        const h = (await fetch(base + '/chat/')).headers
        expect(h.get('x-frame-options')).to.equal('DENY')
        expect(directive(h.get('content-security-policy'), 'script-src')).to.not.include("'unsafe-inline'")
      })
    })
  }

  describe('the embed allowlist (CSP_FRAME_ANCESTORS)', () => {
    const env = { CSP_FRAME_ANCESTORS: 'https://intranet.example.com' }

    it('lets a named site frame the app', async () => {
      await serve(env, async (base) => {
        const h = (await fetch(base + '/chat/')).headers
        expect(directive(h.get('content-security-policy'), 'frame-ancestors')).to.deep.equal(['https://intranet.example.com'])
        expect(h.get('x-frame-options')).to.equal(null)
      })
    })

    for (const page of [
      '/login', '/login/', '/LOGIN/', '/Sign-Up/', '/oauth/authorize/', '//login/', '/%6Cogin/', '/agent/claim/', '/slack/link/',
    ]) {
      it(`never lets it frame ${page}`, async () => {
        await serve(env, async (base) => {
          const h = (await fetch(base + page)).headers
          expect(h.get('x-frame-options')).to.equal('DENY')
          expect(directive(h.get('content-security-policy'), 'frame-ancestors')).to.deep.equal(["'none'"])
        })
      })
    }

    it('takes only host sources: no keyword, wildcard, scheme or second directive', () => {
      expect(parseSourceList("https://a.example, *, 'unsafe-inline', https:, data:, http://b.example:8080, https://c.example; script-src *, https://*.d.example, https://*, javascript:alert(1), wss://e.example"))
        .to.deep.equal(['https://a.example', 'http://b.example:8080', 'https://*.d.example', 'wss://e.example'])
    })
  })

  it('CSP_REPORT_ONLY reports the policy but still forbids framing', async () => {
    await serve({ CSP_REPORT_ONLY: 'TRUE' }, async (base) => {
      const h = (await fetch(base + '/chat/')).headers
      expect(directive(h.get('content-security-policy-report-only'), 'script-src')).to.include(sha(BOOT))
      expect(h.get('content-security-policy')).to.equal("frame-ancestors 'none'")
      expect(h.get('x-frame-options')).to.equal('DENY')
    })
  })

  it('hashes only inline scripts, each once, as the browser does', () => {
    const html = `<script>a()</script><SCRIPT type="module">b()</SCRIPT><script src="/x.js"></script><script defer src=/y.js></script><script></script><script>a()</script>`
    expect(inlineScriptHashes(html)).to.deep.equal([sha('a()'), sha('b()')])
  })

  it('ends a script where the browser does, at an end tag with whitespace or attributes', () => {
    const html = `<script>a()</script\t\n bar><script>b()</SCRIPT ><script>c()</script-x>d()</script/>`
    expect(inlineScriptHashes(html)).to.deep.equal([sha('a()'), sha('b()'), sha('c()</script-x>d()')])
  })

  it("lets the sign-in page reach the third parties the shipped UI calls (the lane found GitHub's API blocked)", () => {
    const csp = documentCsp(securityHeaderPolicy({ PIPESHUB_HARDENED: 'true' }, { scriptSrc: [], connectSrc: [], frameSrc: [] }), '/login/', [])
    const connect = csp.split(';').map((d) => d.trim()).find((d) => d.startsWith('connect-src')) ?? ''
    for (const origin of ['https://api.github.com', 'https://accounts.google.com', 'https://login.microsoftonline.com']) {
      expect(connect.split(' '), origin).to.include(origin)
    }
    expect(connect).to.not.match(/ https: | \*|'unsafe/)
  })

  it('lets a file preview read back the blob: URL it made (markdown, text and HTML previews failed to fetch)', () => {
    const csp = documentCsp(securityHeaderPolicy({ PIPESHUB_HARDENED: 'true' }, NONE), '/chat/', [])
    const connect = directive(csp, 'connect-src')
    expect(connect).to.include('blob:')
    expect(connect).to.not.include('data:')
    expect(connect).to.not.include('https:')
  })

  it("holds an edition's decision pages to the same rule (EditionBoot.unframeablePaths)", () => {
    const policy = securityHeaderPolicy({ PIPESHUB_HARDENED: 'true', CSP_FRAME_ANCESTORS: 'https://intranet.example.com' }, NONE, undefined, ['/Decide'])
    expect(directive(documentCsp(policy, '/decide/', []), 'frame-ancestors')).to.deep.equal(["'none'"])
    expect(directive(documentCsp(policy, '//%64ecide/x', []), 'frame-ancestors')).to.deep.equal(["'none'"])
    expect(directive(documentCsp(policy, '/decided/', []), 'frame-ancestors')).to.deep.equal(['https://intranet.example.com'])
    expect(isUnframeablePage('/decide/')).to.equal(false)
  })

  it('keeps the unframeable list the pages that approve or sign in', () => {
    expect(UNFRAMEABLE_PAGE_PATHS).to.include.members(['/login', '/sign-up', '/oauth', '/slack/link', '/agent/claim'])
    expect(isUnframeablePage('/loginx')).to.equal(false)
    expect(isUnframeablePage('/chat/login')).to.equal(false)
    expect(isUnframeablePage('/%E0%A4%A')).to.equal(false)
  })
})

describe('CSP reports', () => {
  it('reads both report formats and keeps only origins and paths', () => {
    const legacy = violationsOf({
      'csp-report': {
        'effective-directive': 'connect-src',
        'blocked-uri': 'https://evil.example/steal?token=abc',
        'document-uri': 'https://app.example/chat/?q=secret',
        disposition: 'enforce',
      },
    })
    expect(legacy).to.deep.equal([{ directive: 'connect-src', blocked: 'https://evil.example', page: '/chat/', disposition: 'enforce' }])
    const modern = violationsOf([
      { type: 'csp-violation', body: { effectiveDirective: 'script-src-elem', blockedURL: 'inline', documentURL: 'https://app.example/login/' } },
      { type: 'deprecation', body: {} },
    ])
    expect(modern).to.deep.equal([{ directive: 'script-src-elem', blocked: 'inline', page: '/login/', disposition: '' }])
    expect(violationsOf('nonsense')).to.deep.equal([])
  })

  it('answers 204 and caps what it logs per minute', async () => {
    const lines: string[] = []
    const logger = { warn: (m: string) => { lines.push(m) } } as never
    const app = express()
    app.use(createCspReportRouter(logger, () => 0))
    const server = app.listen(0)
    try {
      const url = `http://127.0.0.1:${(server.address() as AddressInfo).port}/api/v1/csp-report`
      const body = JSON.stringify({ 'csp-report': { 'effective-directive': 'img-src', 'blocked-uri': 'https://x.example/a' } })
      for (let i = 0; i < 40; i++) {
        const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/csp-report' }, body })
        expect(res.status).to.equal(204)
      }
      expect(lines.filter((l) => l === 'csp.violation')).to.have.length(30)
      const big = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/csp-report' }, body: 'x'.repeat(20_000) })
      expect(big.status).to.not.equal(204)
    } finally {
      server.close()
    }
  })
})

describe('R51-COMPAT item 8: security headers on the compatibility profile (ADR-OPS-07)', () => {
  const COMPAT = { PIPESHUB_HARDENED: '' }

  it('never lets a site frame the sign-in and consent pages, as a release without ADR-WEB-01 already refused', async () => {
    await serve({ ...COMPAT }, async (base) => {
      for (const page of ['/login/', '/sign-up', '/oauth/authorize']) {
        const h = (await fetch(base + page)).headers
        expect(h.get('x-frame-options'), page).to.equal('DENY')
        expect(h.get('content-security-policy'), page).to.equal("frame-ancestors 'none'")
      }
    })
  })

  for (const page of ['/', '/chat/', '/api/v1/thing']) {
    it(`lets any site frame ${page}, reports the CSP instead of enforcing it, and sends no HSTS`, async () => {
      await serve({ ...COMPAT, FRONTEND_PUBLIC_URL: 'https://pipeshub.example.com' }, async (base) => {
        const h = (await fetch(base + page)).headers
        expect(h.get('x-frame-options')).to.equal(null)
        expect(h.get('content-security-policy')).to.equal(null)
        expect(h.get('content-security-policy-report-only')).to.be.a('string')
        expect(directive(h.get('content-security-policy-report-only'), 'frame-ancestors')).to.deep.equal([])
        expect(h.get('strict-transport-security')).to.equal(null)
        expect(h.get('x-content-type-options')).to.equal('nosniff')
      })
    })
  }

  it('an embed allowlist still applies, as it does on the hardened profile', async () => {
    await serve({ ...COMPAT, CSP_FRAME_ANCESTORS: 'https://intranet.example.com' }, async (base) => {
      const h = (await fetch(base + '/chat/')).headers
      expect(h.get('content-security-policy')).to.equal('frame-ancestors https://intranet.example.com')
    })
  })

  it("STRICT_MODE=true, upstream's opt-in, means the hardened set", async () => {
    await serve({ ...COMPAT, STRICT_MODE: 'true' }, async (base) => {
      const h = (await fetch(base + '/chat/')).headers
      expect(h.get('x-frame-options')).to.equal('DENY')
      expect(directive(h.get('content-security-policy'), 'frame-ancestors')).to.deep.equal(["'none'"])
    })
  })

  it('CSP_REPORT_ONLY=false enforces the policies without framing rules', async () => {
    await serve({ ...COMPAT, CSP_REPORT_ONLY: 'false' }, async (base) => {
      const h = (await fetch(base + '/chat/')).headers
      expect(h.get('content-security-policy')).to.be.a('string')
      expect(directive(h.get('content-security-policy'), 'frame-ancestors')).to.deep.equal([])
      expect(h.get('x-frame-options')).to.equal(null)
    })
  })
})
