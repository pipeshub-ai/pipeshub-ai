import { expect } from 'chai'
import {
  REDACTED,
  isSensitiveQueryKey,
  redactHeadersForLog,
  redactLogMetadata,
  redactQueryForLog,
  redactSensitiveQueryParams,
} from '../../../src/libs/utils/log-redaction.utils'

const FAKE_CODE = '4/0FAKE-auth-code-not-real'
const FAKE_STATE = 'ZmFrZS1zdGF0ZQ'

describe('log-redaction.utils', () => {
  describe('redactSensitiveQueryParams', () => {
    it('redacts an OAuth authorization code', () => {
      // The concrete leak: /oauth/callback?code=... is a credential that can be
      // exchanged for tokens by whoever reads the access log first.
      const out = redactSensitiveQueryParams(
        '/api/v1/mcp-servers/oauth/callback?code=abc123&state=xyz',
      )
      expect(out).to.not.contain('abc123')
      expect(out).to.contain(`code=${encodeURIComponent(REDACTED)}`)
    })

    it('redacts state, which with the code makes a replayable callback', () => {
      const out = redactSensitiveQueryParams(`/cb?code=${FAKE_CODE}&state=${FAKE_STATE}`)
      expect(out).to.not.contain(FAKE_STATE)
    })

    it('keeps the path and non-sensitive params intact', () => {
      const out = redactSensitiveQueryParams('/api/v1/docs?page=2&sort=name')
      expect(out).to.equal('/api/v1/docs?page=2&sort=name')
    })

    it('leaves a url without a query string untouched', () => {
      expect(redactSensitiveQueryParams('/api/v1/health')).to.equal(
        '/api/v1/health',
      )
    })

    it('redacts presigned-url signatures', () => {
      const out = redactSensitiveQueryParams(
        '/f.pdf?X-Amz-Signature=deadbeef&X-Amz-Credential=AKIA',
      )
      expect(out).to.not.contain('deadbeef')
      expect(out).to.not.contain('AKIA')
    })

    it('is case-insensitive on the parameter name', () => {
      const out = redactSensitiveQueryParams('/cb?CODE=secret')
      expect(out).to.not.contain('secret')
    })

    it('redacts an absolute referrer while keeping its origin', () => {
      // A Referer is absolute and can itself be an OAuth callback. Stripping the
      // origin would remove the only thing that makes a referrer worth logging.
      const out = redactSensitiveQueryParams(
        'https://app.example.com/cb?code=secret&page=2',
      )
      expect(out).to.not.contain('secret')
      expect(out).to.contain('https://app.example.com/cb')
      expect(out).to.contain('page=2')
    })

    it('redacts the Google connector callback referrer seen in the error log', () => {
      const referer =
        `http://localhost:3001/connectors/oauth/callback/Drive?state=${FAKE_STATE}` +
        `&iss=https://accounts.google.com&code=${FAKE_CODE}` +
        '&scope=https://www.googleapis.com/auth/drive.readonly'
      const out = redactSensitiveQueryParams(referer)
      expect(out).to.not.contain('FAKE-auth-code')
      expect(out).to.not.contain(FAKE_STATE)
      expect(out).to.contain('/connectors/oauth/callback/Drive')
      expect(out).to.contain('iss=')
      expect(out).to.contain('scope=')
    })

    for (const key of [
      'access_token',
      'refresh_token',
      'id_token',
      'token',
      'client_secret',
      'password',
      'api_key',
      'apikey',
      'apiKey',
      'X-Api-Key',
      'key',
      'signature',
      'sig',
      'user_password',
      'clientSecret',
      'sessionToken',
    ]) {
      it(`redacts the query parameter ${key}`, () => {
        const out = redactSensitiveQueryParams(
          `https://svc.example.com/x?${encodeURIComponent(key)}=hidden-value&page=2`,
        )
        expect(out).to.not.contain('hidden-value')
        expect(out).to.contain('page=2')
      })
    }

    for (const key of [
      'passage',
      'bypass',
      'keyword',
      'monkey',
      'key_id',
      'api_version',
      'signed_up',
      'codec',
      'statement',
    ]) {
      it(`keeps an ordinary parameter that only contains those letters: ${key}`, () => {
        expect(
          redactSensitiveQueryParams(`/x?${key}=visible-value`),
        ).to.equal(`/x?${key}=visible-value`)
      })
    }

    it('leaves `key` in the path alone; only the query parameter is a credential', () => {
      expect(redactSensitiveQueryParams('/api/v1/kv/key/code?page=1')).to.equal(
        '/api/v1/kv/key/code?page=1',
      )
    })

    it('redacts tokens in the fragment (implicit grant)', () => {
      const out = redactSensitiveQueryParams(
        'https://app.example.com/cb#access_token=frag-secret&expires_in=3600',
      )
      expect(out).to.not.contain('frag-secret')
      expect(out).to.contain('expires_in=3600')
    })

    it('redacts a code inside an SPA hash route', () => {
      const out = redactSensitiveQueryParams('https://app.example.com/#/cb?code=hash-secret')
      expect(out).to.not.contain('hash-secret')
      expect(out).to.contain('#/cb?code=')
    })

    it('leaves an ordinary fragment alone', () => {
      expect(redactSensitiveQueryParams('/docs?page=1#section-2')).to.equal(
        '/docs?page=1#section-2',
      )
    })

    it('redacts a pair whose `=` arrived percent-encoded (code%3Dvalue)', () => {
      const out = redactSensitiveQueryParams('/cb?page=1&code%3Dencoded-secret')
      expect(out).to.not.contain('encoded-secret')
    })

    it('redacts a callback URL nested, encoded, inside another parameter', () => {
      const out = redactSensitiveQueryParams(
        `/login?returnTo=${encodeURIComponent(`/cb?code=${FAKE_CODE}&page=2`)}`,
      )
      expect(out).to.not.contain('FAKE-auth-code')
      expect(out).to.contain('returnTo=')
    })

    it('redacts a double-encoded nested callback URL', () => {
      const inner = `https://app.example.com/cb?code=${FAKE_CODE}`
      const out = redactSensitiveQueryParams(
        `/login?next=${encodeURIComponent(encodeURIComponent(inner))}`,
      )
      expect(out).to.not.contain('FAKE-auth-code')
      expect(decodeURIComponent(decodeURIComponent(out))).to.not.contain('FAKE-auth-code')
    })

    it('redacts a whole URL that was logged percent-encoded', () => {
      const out = redactSensitiveQueryParams(
        encodeURIComponent(`https://app.example.com/cb?code=${FAKE_CODE}`),
      )
      expect(out).to.not.contain('FAKE-auth-code')
      expect(out).to.not.contain(encodeURIComponent(FAKE_CODE))
    })

    it('redacts the password in a URL with userinfo', () => {
      const out = redactSensitiveQueryParams('https://user:hunter2@db.example.com/x?a=1#b')
      expect(out).to.not.contain('hunter2')
    })

    it('drops the query entirely when the url cannot be parsed', () => {
      // Falling back to pass-through would be the one case where a secret
      // survives, so an unparseable url loses its query rather than its safety.
      const out = redactSensitiveQueryParams('http://[::bad::]/cb?code=secret')
      expect(out).to.not.contain('secret')
    })

    it('drops the fragment too when the url cannot be parsed', () => {
      const out = redactSensitiveQueryParams('http://[::bad::]/cb#access_token=secret')
      expect(out).to.not.contain('secret')
    })

    it('keeps a relative path as written', () => {
      const out = redactSensitiveQueryParams('not a url?code=secret')
      expect(out).to.not.contain('secret')
      expect(out.startsWith('not a url?')).to.equal(true)
    })
  })

  describe('isSensitiveQueryKey', () => {
    it('matches names case-insensitively', () => {
      expect(isSensitiveQueryKey('Code')).to.equal(true)
      expect(isSensitiveQueryKey('STATE')).to.equal(true)
      expect(isSensitiveQueryKey('Key')).to.equal(true)
    })
  })

  describe('redactHeadersForLog', () => {
    it('masks credential headers and redacts URL-valued ones', () => {
      const out = redactHeadersForLog({
        authorization: 'Bearer fake-bearer',
        cookie: 'session=fake-cookie',
        'x-session-token': 'fake-session-token',
        'x-access-token': 'fake-access-token',
        'x-api-key': 'fake-api-key',
        referer: `https://app.example.com/cb?code=${FAKE_CODE}`,
        location: `https://app.example.com/cb#access_token=fake-loc`,
        'user-agent': 'mocha',
      })
      const logged = JSON.stringify(out)
      for (const secret of [
        'fake-bearer',
        'fake-cookie',
        'fake-session-token',
        'fake-access-token',
        'fake-api-key',
        'FAKE-auth-code',
        'fake-loc',
      ]) {
        expect(logged).to.not.contain(secret)
      }
      expect(out.authorization).to.equal(REDACTED)
      expect(out['user-agent']).to.equal('mocha')
      expect(out.referer).to.contain('https://app.example.com/cb')
    })

    it('tolerates missing headers', () => {
      expect(redactHeadersForLog(undefined)).to.deep.equal({})
    })
  })

  describe('redactQueryForLog', () => {
    it('masks sensitive keys and URLs nested in values', () => {
      const out = redactQueryForLog({
        code: FAKE_CODE,
        state: FAKE_STATE,
        page: '2',
        returnTo: `/cb?code=${FAKE_CODE}`,
        filter: { token: 'nested-token' },
        ids: ['a', 'b'],
      }) as Record<string, unknown>
      const logged = JSON.stringify(out)
      expect(logged).to.not.contain('FAKE-auth-code')
      expect(logged).to.not.contain(FAKE_STATE)
      expect(logged).to.not.contain('nested-token')
      expect(out.page).to.equal('2')
      expect(out.ids).to.deep.equal(['a', 'b'])
    })
  })

  describe('redactLogMetadata', () => {
    it('redacts URL-valued fields and secret headers at any depth', () => {
      const out = redactLogMetadata({
        context: {
          request: {
            originalUrl: `/api/v1/connectors/oauth/callback?code=${FAKE_CODE}`,
            headers: {
              referer: `http://localhost:3001/cb?code=${FAKE_CODE}`,
              authorization: 'Bearer fake-bearer',
            },
          },
        },
        redirectUrl: `/oauth/authorize?state=${FAKE_STATE}`,
        callbackUrls: [`/cb?code=${FAKE_CODE}`],
      })
      const logged = JSON.stringify(out)
      expect(logged).to.not.contain('FAKE-auth-code')
      expect(logged).to.not.contain(FAKE_STATE)
      expect(logged).to.not.contain('fake-bearer')
      expect(logged).to.contain('/api/v1/connectors/oauth/callback')
    })

    it('returns the same object when there is nothing to redact', () => {
      const meta = { userId: 'u1', url: '/api/v1/docs?page=2', nested: { a: [1, 2] } }
      expect(redactLogMetadata(meta)).to.equal(meta)
    })

    it('does not mutate the caller object', () => {
      const meta = { url: `/cb?code=${FAKE_CODE}` }
      redactLogMetadata(meta)
      expect(meta.url).to.contain(FAKE_CODE)
    })

    it('leaves Errors and other class instances alone and survives cycles', () => {
      const err = new Error('boom')
      const meta: Record<string, unknown> = { err, url: `/cb?code=${FAKE_CODE}` }
      meta.self = meta
      const out = redactLogMetadata(meta) as Record<string, unknown>
      expect(out.err).to.equal(err)
      expect(String(out.url)).to.not.contain('FAKE-auth-code')
    })

    it('does not touch a field merely named key', () => {
      // Rate-limit logs carry `key: <limiter key>`; only the query parameter is a credential.
      const meta = { key: 'ip:127.0.0.1', path: '/api/v1/x' }
      expect(redactLogMetadata(meta)).to.equal(meta)
    })
  })
})
