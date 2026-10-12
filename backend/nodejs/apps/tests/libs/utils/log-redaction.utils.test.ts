import querystring from 'querystring'
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

    it('redacts a pair whose `=` arrived encoded twice (code%253Dvalue)', () => {
      const out = redactSensitiveQueryParams('/cb?code%253Dencoded-secret&page=1')
      expect(out).to.not.contain('encoded-secret')
      expect(out).to.contain('page=1')
    })

    for (const url of [
      `/cb?page%3D1%26code%3D${FAKE_CODE}`,
      `/cb?foo%26code%3D${FAKE_CODE}`,
      `/cb?scope%3Dopenid%26code%3D${FAKE_CODE}%26state%3D${FAKE_STATE}`,
      `/login?returnTo%3D%2Fcb%3Fcode%3D${FAKE_CODE}`,
    ]) {
      it(`redacts a secret in a later pair of an encoded name: ${url.slice(0, 22)}`, () => {
        const out = redactSensitiveQueryParams(url)
        expect(out).to.not.contain('FAKE-auth-code')
        expect(decodeURIComponent(out)).to.not.contain('FAKE-auth-code')
        expect(decodeURIComponent(out)).to.not.contain(FAKE_STATE)
      })
    }

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
      expect(out).to.contain('db.example.com/x?a=1#b')
    })

    for (const url of [
      'https://user:hunter2@db.example.com/x',
      'mongodb://root:hunter2@mongo:27017/db',
      'mongodb://root:hunter2@mongo1:27017,mongo2:27017/db?replicaSet=rs0',
      'redis://:hunter2@redis:6379/0',
      'amqp://guest:hunter2@rabbit:5672/vhost',
      'postgres://app:p@ss:hunter2@pg/db',
    ]) {
      it(`redacts the password of a userinfo URL with no query: ${url.split(':')[0]}`, () => {
        const out = redactSensitiveQueryParams(url)
        expect(out).to.not.contain('hunter2')
        expect(out).to.contain('REDACTED')
      })
    }

    it('redacts the password of a percent-encoded userinfo URL with no query', () => {
      const url = 'https://alice:hunter2@db.example.com'
      for (const logged of [encodeURIComponent(url), encodeURIComponent(encodeURIComponent(url))]) {
        const out = redactSensitiveQueryParams(logged)
        expect(out).to.not.contain('hunter2')
        expect(out).to.contain('REDACTED')
      }
    })

    it('redacts a userinfo URL nested, encoded, inside a parameter', () => {
      const out = redactSensitiveQueryParams(
        `/debug?db=${encodeURIComponent('postgres://app:hunter2@pg/db')}`,
      )
      expect(decodeURIComponent(out)).to.not.contain('hunter2')
    })

    it('leaves an encoded value with no secret as it was', () => {
      expect(redactSensitiveQueryParams('alice%40example.com')).to.equal('alice%40example.com')
    })

    it('keeps the user name and host of a userinfo URL', () => {
      expect(redactSensitiveQueryParams('mongodb://root:hunter2@mongo:27017/db')).to.equal(
        'mongodb://root:REDACTED@mongo:27017/db',
      )
    })

    it('leaves a userinfo URL without a password alone', () => {
      expect(redactSensitiveQueryParams('ssh://git@github.com/org/repo')).to.equal(
        'ssh://git@github.com/org/repo',
      )
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

    it('redacts a secret in a later pair of a parameter name', () => {
      for (const raw of [
        `page%3D1%26code%3D${FAKE_CODE}`,
        `scope%3Dopenid%26code%3D${FAKE_CODE}%26state%3D${FAKE_STATE}`,
        `foo%26code%3D${FAKE_CODE}&page=2`,
      ]) {
        const logged = JSON.stringify(redactQueryForLog(querystring.parse(raw)))
        expect(logged).to.not.contain('FAKE-auth-code')
        expect(logged).to.not.contain(FAKE_STATE)
      }
    })

    it('keeps an ordinary parameter name as it was', () => {
      expect(redactQueryForLog(querystring.parse('a%3Db=1&page=2'))).to.deep.equal({
        'a=b': '1',
        page: '2',
      })
    })

    it('drops the secret from a parameter name that holds it (code%3D<code>)', () => {
      // Express parses `?code%3D<code>&page=1` as a parameter named "code=<code>".
      for (const query of [
        querystring.parse(`code%3D${FAKE_CODE}&page=1`),
        querystring.parse(`code%253D${FAKE_CODE}&page=1`),
      ]) {
        const out = redactQueryForLog(query) as Record<string, unknown>
        expect(JSON.stringify(out)).to.not.contain('FAKE-auth-code')
        expect(out.code).to.equal(REDACTED)
        expect(out.page).to.equal('1')
      }
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

    it('redacts URL fields reached through a cycle and marks the cycle', () => {
      const request: Record<string, unknown> = {
        originalUrl: `/cb?code=${FAKE_CODE}`,
      }
      const meta: Record<string, unknown> = { request }
      request.self = meta
      request.again = request
      const out = redactLogMetadata(meta) as Record<string, any>
      expect(String(out.request.originalUrl)).to.not.contain('FAKE-auth-code')
      expect(out.request.self).to.equal('[Circular]')
      expect(out.request.again).to.equal('[Circular]')
      expect(request.originalUrl).to.contain(FAKE_CODE)
    })

    it('redacts a shared, non-cyclic object each time it appears', () => {
      const shared = { url: `/cb?code=${FAKE_CODE}` }
      const out = redactLogMetadata({ first: shared, second: shared, list: [shared] })
      const logged = JSON.stringify(out)
      expect(logged).to.not.contain('FAKE-auth-code')
      expect(logged).to.not.contain('Circular')
    })

    it('keeps an untouched cycle-free shared object as the same reference', () => {
      const shared = { a: 1 }
      const meta = { first: shared, second: shared }
      expect(redactLogMetadata(meta)).to.equal(meta)
    })

    it('replaces plain containers past the depth limit instead of passing them through', () => {
      let deep: Record<string, unknown> = {
        url: `/cb?code=${FAKE_CODE}`,
        authorization: 'Bearer fake-bearer',
      }
      for (let i = 0; i < 20; i++) deep = { child: deep }
      const logged = JSON.stringify(redactLogMetadata({ deep, count: 3, name: 'x' }))
      expect(logged).to.not.contain('FAKE-auth-code')
      expect(logged).to.not.contain('fake-bearer')
      expect(logged).to.contain('[Truncated]')
      expect(logged).to.contain('"count":3')
    })

    it('leaves non-container values past the depth limit as they are', () => {
      let deep: unknown = 'leaf-value'
      for (let i = 0; i < 20; i++) deep = [deep]
      const logged = JSON.stringify(redactLogMetadata({ deep }))
      expect(logged).to.contain('[Truncated]')
      expect(redactLogMetadata('leaf', 99)).to.equal('leaf')
      expect(redactLogMetadata(42, 99)).to.equal(42)
    })

    it('does not touch a field merely named key', () => {
      // Rate-limit logs carry `key: <limiter key>`; only the query parameter is a credential.
      const meta = { key: 'ip:127.0.0.1', path: '/api/v1/x' }
      expect(redactLogMetadata(meta)).to.equal(meta)
    })
  })
})
