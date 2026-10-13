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

// Every percent-decoding of `text`, applied until nothing changes.
const decodeAll = (text: string): string => {
  let out = text
  for (let i = 0; i < 12; i += 1) {
    let next: string
    try {
      next = decodeURIComponent(out)
    } catch {
      next = out.replace(/(?:%[0-9a-f]{2})+/gi, (run) => {
        try {
          return decodeURIComponent(run)
        } catch {
          return run
        }
      })
    }
    if (next === out) break
    out = next
  }
  return out
}

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

    for (const [url, secret] of [
      ['/cb?page%3D1%26code%3D4%252F0Aea%2526restOfCode123', 'restOfCode123'],
      ['/cb?page%3D1%26password%3Ds3cret%2526correct-horse', 'correct-horse'],
    ]) {
      it(`keeps a secret whole when it holds an encoded \`&\`: ${secret}`, () => {
        const out = redactSensitiveQueryParams(url)
        expect(out).to.not.contain(secret)
        expect(decodeURIComponent(decodeURIComponent(out))).to.not.contain(secret)
      })
    }

    for (const [url, secret] of [
      ['/cb?page%3D1%2526code%3DSECRETTOKEN', 'SECRETTOKEN'],
      ['https://user:hunter2%40host%3Fcode%3D4%252F0Aea%2526TAIL', 'TAIL'],
      [`/login?returnTo=${encodeURIComponent('https://user:hunter2%40host%3Fcode%3DSECRET')}`, 'SECRET'],
    ]) {
      it(`does not let a re-split separator carry a secret out: ${secret}`, () => {
        const out = redactSensitiveQueryParams(url)
        expect(decodeURIComponent(decodeURIComponent(decodeURIComponent(out)))).to.not.contain(secret)
        expect(out).to.not.contain('hunter2')
      })
    }

    it('does not let a re-split separator carry a secret out of a parsed query', () => {
      for (const raw of [
        'page%3D1%2526code%3DSECRETTOKEN',
        `returnTo=${encodeURIComponent('https://user:hunter2%40host%3Fcode%3DSECRETTOKEN')}`,
      ]) {
        const logged = JSON.stringify(redactQueryForLog(querystring.parse(raw)))
        expect(decodeURIComponent(decodeURIComponent(logged))).to.not.contain('SECRETTOKEN')
        expect(logged).to.not.contain('hunter2')
      }
    })

    it('keeps a nested code with an encoded `&` whole inside an encoded name', () => {
      const url = '/login?returnTo%3D%2Fcb%3Fcode%3D4%252F0Aea%2526CODEWITHAMP'
      const out = redactSensitiveQueryParams(url)
      expect(out).to.not.contain('CODEWITHAMP')
      expect(decodeURIComponent(decodeURIComponent(out))).to.not.contain('CODEWITHAMP')
      const logged = JSON.stringify(
        redactQueryForLog(querystring.parse('returnTo%3D%2Fcb%3Fcode%3D4%252F0Aea%2526CODEWITHAMP')),
      )
      expect(logged).to.not.contain('CODEWITHAMP')
    })

    for (const url of [
      'https://user:hunter2%40host/x?page=1',
      'https://user:hunter2%40host/x?code=FAKE-auth-code',
      'https://user%3Ahunter2@host/x?page=1',
    ]) {
      it(`redacts a password whose \`@\` or \`:\` is encoded, with a query: ${url}`, () => {
        const out = redactSensitiveQueryParams(url)
        expect(out).to.not.contain('hunter2')
        expect(out).to.not.contain('FAKE-auth-code')
        expect(out).to.contain('/x?')
      })
    }

    for (const url of [
      'https%3A%2F%2Falice%3Ahunter2%40db.example.com%',
      `${encodeURIComponent('https://app.example.com/cb?code=hunter2')}%`,
      `/login?next=${encodeURIComponent(encodeURIComponent('https://app.example.com/cb?code=hunter2'))}%`,
    ]) {
      it(`decodes past a stray \`%\`: ${url.slice(0, 24)}`, () => {
        expect(redactSensitiveQueryParams(url)).to.not.contain('hunter2')
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

    it('keeps the rest of an encoded authority encoded, so a code holding `%26` stays whole', () => {
      // The code is `4/0Aea&TAILSECRET`: decoding the whole authority at once
      // turned its `%26` into a separator and logged the tail as a name.
      const out = redactSensitiveQueryParams(
        'https://user:hunter2%40host%3Fcode%3D4%2F0Aea%26TAILSECRET',
      )
      expect(decodeAll(out)).to.not.contain('TAILSECRET')
      expect(decodeAll(out)).to.not.contain('hunter2')
    })

    it('does not read an `@` inside an encoded query as the end of the userinfo', () => {
      // The `?` is encoded as much as the `@`, so the code's own `%40` lies
      // past the end of the authority and is not where the password stops.
      const out = redactSensitiveQueryParams(
        'https://user:hunter2%40host%3Fcode%3D4%2F0Aea%40TAILSECRET',
      )
      expect(decodeAll(out)).to.not.contain('TAILSECRET')
      expect(decodeAll(out)).to.not.contain('hunter2')
    })

    for (const [url, password, expected] of [
      ['postgres://app:sec/ret@pg/db', 'sec/ret', 'postgres://app:REDACTED@pg/db'],
      ['mongodb://root:ab/cd==@mongo:27017/db', 'ab/cd', 'mongodb://root:REDACTED@mongo:27017/db'],
      ['redis://:p/ass@redis:6379/0', 'p/ass', 'redis://:REDACTED@redis:6379/0'],
    ]) {
      it(`redacts a password holding a \`/\`: ${url.split(':')[0]}`, () => {
        expect(redactSensitiveQueryParams(url)).to.equal(expected)
        for (const logged of [
          `/debug?db=${url}`,
          `/debug?db=${encodeURIComponent(url)}`,
          // A path the structural pass cannot read sends the whole URL to
          // the fallback, which must not keep the password's head as a host.
          `${url};code=${FAKE_CODE}`,
        ]) {
          const out = decodeAll(redactSensitiveQueryParams(logged))
          expect(out, logged).to.not.contain(password)
          expect(out, logged).to.not.contain(`${password.split('/')[0]}/`)
          expect(out, logged).to.not.contain('FAKE-auth-code')
        }
      })
    }

    for (const url of [
      '/cb;code=4/0AeaSECRETX',
      '/oauth/callback;code=4/0AeaSECRETX?page=1',
      '/cb/token=SECRETX',
      '/cb&code=SECRETX',
    ]) {
      it(`does not write a secret in the path back out: ${url}`, () => {
        const out = redactSensitiveQueryParams(url)
        expect(decodeAll(out)).to.not.contain('SECRETX')
      })
    }

    for (const [url, secrets] of [
      // A `?` or `#` before the `@`, encoded as much as it, is in the password.
      ['https://user:hunter2%3Fextra%40host/db', ['hunter2', 'extra']],
      [encodeURIComponent('https://user:hunter2?extra@host/db'), ['hunter2', 'extra']],
      ['https://user:hunter2?x=1', ['hunter2']],
      // A later `@` is counted, and a host after query syntax is not kept.
      ['https://user:p%40SECRETX%3Frest%40host/db', ['SECRETX', 'rest']],
      ['postgres://app:sec%2Fret%3Fmore%40pg/db', ['sec', 'more']],
      ['mongodb://root:ab/cd==%3Fmore%40mongo:27017/db', ['ab/cd', 'more']],
      ['https://user:hunter2%23tail%40host/x?code=SECRETX', ['hunter2', 'tail', 'SECRETX']],
      // Protocol-relative, and a scheme with no `//`.
      ['//user:hunter2@host/db', ['hunter2']],
      ['//user:hunter2%40host/x?page=1', ['hunter2']],
      ['/debug?db=//user:hunter2@host/db', ['hunter2']],
      [
        `/login?returnTo=${encodeURIComponent('//user:hunter2@host/cb?code=FAKECODE')}`,
        ['hunter2', 'FAKECODE'],
      ],
      ['https:user:hunter2@host/db', ['hunter2']],
      // A stray byte between a sensitive name and its `=`.
      ['/cb?code%FF%3DSECRETTOKEN', ['SECRETTOKEN']],
      ['/cb?code%00%3DSECRETTOKEN', ['SECRETTOKEN']],
      ['/cb?code%2E%3DSECRETTOKEN', ['SECRETTOKEN']],
      ['/cb?code%20%3DSECRETTOKEN', ['SECRETTOKEN']],
      // A raw `#` in a nested password ends the outer URL inside it.
      ['/debug?a=1&db=redis://:pass#TAILSECRET@redis:6379/0', ['TAILSECRET']],
      // A sensitive name that carries its own value after an encoded `=`.
      ['/cb?X-Amz-Signature%253DSECRETSIG%26X-Amz-Date=1', ['SECRETSIG']],
      // A password that starts like a port, or with a separator.
      ['mongodb://root:%2Fab%2Fcd==@mongo:27017/db', ['ab/cd', 'ab%2Fcd']],
      ['https://user:8080%2Fhunter2@host/db', ['hunter2']],
      ['redis://:%2Fhunter2@redis:6379/0', ['hunter2']],
      ['postgres://app:%3Fmore%40pg/db', ['more']],
      ['/debug?db=mongodb://root:%2Fab%2Fcd==@mongo:27017/db', ['ab/cd', 'ab%2Fcd']],
      ['postgres://app:1234/xsecret@pg/db', ['xsecret']],
      // A user name that holds an `@`.
      ['mongodb://alice@example.com:hunter2@mongo:27017/db', ['hunter2']],
      ['postgres://alice@example.com:sec/ret@pg/db', ['sec/ret', 'sec']],
      ['smtp://alice@example.com:hunter2@mail.internal:587', ['hunter2']],
      ['//alice@example.com:hunter2@host/db', ['hunter2']],
      ['mongodb://ops@company.com:s3cret@mongo1:27017,mongo2:27017/db', ['s3cret']],
      ['https://alice@example.com:hunter2%3Fextra@host/db', ['hunter2', 'extra']],
      [
        `/debug?db=${encodeURIComponent('mongodb://alice@example.com:hunter2@mongo:27017/db')}`,
        ['hunter2'],
      ],
      // Spaces in the password.
      ['https://user:correct horse@host/db', ['correct', 'horse']],
      ['https://user:pass word@host/db?page=1', ['pass', 'word']],
      // A replaced host that had held `?code`, before a value starting with `?`.
      ['/login?returnTo%253D//user:?HEADX@TAILX@host/cb%253Fcode%3D?HEADX@TAILX', ['HEADX', 'TAILX']],
    ] as const) {
      it(`does not log the secret in ${url}`, () => {
        const out = decodeAll(redactSensitiveQueryParams(url))
        for (const secret of secrets) expect(out, out).to.not.contain(secret)
      })
    }

    it('still reads a port followed by an `@` later in the URL as a port', () => {
      expect(redactSensitiveQueryParams('http://localhost:3001/invite?email=a@b.com')).to.equal(
        'http://localhost:3001/invite?email=a@b.com',
      )
    })

    it('keeps a `?` inside a value in that value', () => {
      const out = redactSensitiveQueryParams('/cb?code=AB?TAILSECRET&page=1')
      expect(decodeAll(out)).to.not.contain('TAILSECRET')
    })

    it('keeps an ordinary path when only the query is replaced', () => {
      expect(redactSensitiveQueryParams('/oauth/callback;code=4/0AeaSECRETX?page=1')).to.equal(
        `/oauth/callback?${REDACTED}`,
      )
    })
  })

  describe('redactSensitiveQueryParams under any mix of encodings', () => {
    const encodeChar = (ch: string, level: number): string => {
      let out = ch
      for (let i = 0; i < level; i += 1) out = i === 0 ? encodeURIComponent(out) : out.replace(/%/g, '%25')
      return out
    }
    // A secret is written as its value would be at the level of the pair
    // around it, so `%26` and `%23` are an `&` and a `#` inside the secret.
    // Its two halves are checked separately: a parse that splits the secret
    // at one of those must still not log the tail.
    const SECRETS = ['HEADX', 'HEADX%26TAILX', 'HEADX%23TAILX', 'HEADX%40TAILX', 'HEADX/TAILX']
    // A password may also hold a raw `?`, `#` or `@`: each is then encoded
    // exactly as much as the separators around it.
    // It may also start with a separator or a port-like number, or hold spaces.
    const PASSWORD_SECRETS = [
      ...SECRETS,
      'HEADX?TAILX',
      'HEADX#TAILX',
      'HEADX@TAILX',
      '/HEADX/TAILX',
      '?HEADX@TAILX',
      '#HEADX',
      '8080/HEADX',
      '8080%2FHEADX',
      'HEADX TAILX',
    ]
    const MARKERS = ['HEADX', 'TAILX']
    const QUERY_TEMPLATES = [
      (s: string) => `/cb?code=${s}&page=1`,
      (s: string) => `/cb?page=1&code=${s}`,
      (s: string) => `/login?returnTo=/cb?code=${s}`,
      (s: string) => `https://app.example.com/cb#access_token=${s}`,
      (s: string) => `/cb;code=${s}`,
      (s: string) => `/oauth/callback;code=${s}?page=1`,
      // A stray byte between the name and its `=`.
      (s: string) => `/cb?code%FF=${s}&page=1`,
      (s: string) => `/cb?code%00=${s}`,
      (s: string) => `/cb?code.=${s}`,
      (s: string) => `/cb?code%20=${s}`,
    ]
    const PASSWORD_TEMPLATES = [
      (s: string) => `https://user:${s}@host.example/x?page=1`,
      (s: string) => `https://user:${s}@host.example?code=${s}`,
      (s: string) => `postgres://app:${s}@pg/db`,
      (s: string) => `//user:${s}@host.example/db`,
      (s: string) => `https:user:${s}@host.example/db`,
      // A user name that holds an `@`.
      (s: string) => `mongodb://alice@example.com:${s}@mongo:27017/db`,
      (s: string) => `//alice@example.com:${s}@host/db`,
    ]
    // Nested in a query, where a raw `#` would end the outer URL first.
    const NESTED_PASSWORD_TEMPLATES = [
      (s: string) => `/debug?db=postgres://app:${s}@pg/db`,
      (s: string) => `/debug?db=//user:${s}@host/db`,
      (s: string) => `/login?returnTo=//user:${s}@host/cb?code=${s}`,
      (s: string) => `/debug?db=mongodb://alice@example.com:${s}@mongo:27017/db`,
    ]
    const SEPARATORS = ['=', '&', '?', '#', '@', ':', ';']

    const variants = function* (template: string): Generator<string> {
      const present = SEPARATORS.filter((ch) => template.includes(ch))
      const combos = 3 ** present.length
      for (let combo = 0; combo < combos; combo += 1) {
        // The leading scheme's own `:` and `//` stay as they are.
        let variant = template.replace(/^([a-z]+:(?:\/\/)?)/, '\u0000')
        const scheme = template.match(/^([a-z]+:(?:\/\/)?)/)?.[1] ?? ''
        let rest = combo
        for (const ch of present) {
          const level = rest % 3
          rest = Math.floor(rest / 3)
          variant = variant.split(ch).join(encodeChar(ch, level))
        }
        variant = variant.replace('\u0000', scheme)
        for (let wrap = 0; wrap < 3; wrap += 1) {
          yield wrap === 0 ? variant : encodeChar(variant, wrap)
        }
      }
    }

    it('never leaves the secret readable at any decoding depth', () => {
      let checked = 0
      const cases = [
        ...QUERY_TEMPLATES.flatMap((t) => SECRETS.map((s) => t(s))),
        ...PASSWORD_TEMPLATES.flatMap((t) => PASSWORD_SECRETS.map((s) => t(s))),
        ...NESTED_PASSWORD_TEMPLATES.flatMap((t) =>
          PASSWORD_SECRETS.filter((s) => !s.includes('#')).map((s) => t(s)),
        ),
      ]
      for (const template of cases) {
        for (const input of variants(template)) {
          const out = decodeAll(redactSensitiveQueryParams(input))
          for (const marker of MARKERS) {
            expect(out, `input ${input} -> ${out}`).to.not.contain(marker)
          }
          checked += 1
        }
      }
      expect(checked).to.be.greaterThan(20000)
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

    it('keeps a secret holding an encoded `&` whole inside a parameter name', () => {
      const logged = JSON.stringify(
        redactQueryForLog(querystring.parse('page%3D1%26code%3D4%252F0Aea%2526restOfCode123')),
      )
      expect(logged).to.not.contain('restOfCode123')
    })

    it('redacts a password or a code that only a looser reading shows', () => {
      for (const [raw, secret] of [
        ['code%FF%3DSECRETTOKEN', 'SECRETTOKEN'],
        ['code%20%3DSECRETTOKEN', 'SECRETTOKEN'],
        [`db=${encodeURIComponent('//user:hunter2@host/db')}`, 'hunter2'],
        ['X-Amz-Signature%253DSECRETSIG%26X-Amz-Date=1', 'SECRETSIG'],
        [`db=${encodeURIComponent('mongodb://alice@example.com:hunter2@mongo:27017/db')}`, 'hunter2'],
      ]) {
        const logged = JSON.stringify(redactQueryForLog(querystring.parse(raw)))
        expect(decodeAll(logged), logged).to.not.contain(secret)
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
