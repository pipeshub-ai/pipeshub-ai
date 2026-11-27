import { expect } from 'chai';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import {
  exportedNames,
  findLibraryMentions,
  findVerifyCalls,
  unlistedLibraryMentions,
  sourceFiles,
  unlistedVerifyCalls,
} from '../helpers/jwt-verify-scan';

// A signature check with jwtSecret says nothing about whether a session is live: personal access
// tokens and OAuth access tokens carry the same signature. Request authentication goes through
// AuthMiddleware (and requireSessionAuth where only a person may act). A new jsonwebtoken verify
// in src/ must be added here on purpose, with the key it checks against.
const SRC = path.resolve(__dirname, '../../src');

const ALLOWED_VERIFIERS: Record<string, readonly string[]> = {
  // the token verifier AuthMiddleware itself calls
  'libs/services/authtoken.service.ts': ['this.jwtVerificationKey', 'key'],
  // service relay and caller-role lookup tokens, scope-checked
  'libs/middlewares/mcp-relay.ts': ['key'],
  // caller-role lookup and service calls to internal routes, scope-checked, only to skip counting
  'libs/middlewares/rate-limit.middleware.ts': ['key', 'key'],
  // verifies tokens this service issues
  'modules/oauth_provider/services/oauth_token.service.ts': [
    // Five in the shared service (client assertion, refresh, introspection, revocation, userinfo).
    'this.verifyKey',
    'this.verifyKey',
    'this.verifyKey',
    'this.verifyKey',
    'this.verifyKey',
  ],
  // verifies an Azure AD id token against its JWKS
  'modules/auth/utils/azureAdTokenValidation.ts': ['jwkToPem(signingKey)'],
};

// Files that name a JWT library to sign, decode or configure, but never verify.
const MAY_NAME_LIBRARY: readonly string[] = [
  'libs/utils/createJwt.ts',
  'libs/utils/jwtConfig.ts',
  'libs/middlewares/auth.middleware.ts',
  'modules/auth/controller/userAccount.controller.ts',
  'modules/tokens_manager/utils/verifyToken.ts',
  // decode an already-verified token for a claim (harness routing, cookie expiry, session id)
  'modules/agent-harness/transport/harness-switch.ts',
  'modules/auth/session-cookie.ts',
  'modules/auth/session-rotation.ts',
];

const REMOVED_EXPORTS = [
  'userValidator',
  'adminValidator',
  'isJwtTokenValid',
  'jwtValidator',
];

describe('contract: no jsonwebtoken verify outside the allowlist', function () {
  // Parses every file under src/: slower than the default timeout on a shared CI runner.
  this.timeout(120000);
  it('verifies a JWT only in the allowlisted files, with the allowlisted keys', () => {
    const offenders = unlistedVerifyCalls(SRC, SRC, ALLOWED_VERIFIERS);
    expect(offenders).to.deep.equal([]);
  });

  it('names a JWT library only in files on the allowlists, whatever the spelling of the call', () => {
    const offenders = unlistedLibraryMentions(
      SRC,
      SRC,
      MAY_NAME_LIBRARY,
      ALLOWED_VERIFIERS,
    );
    expect(offenders).to.deep.equal([]);
  });

  describe('the mention rule sees spellings no call pattern does', () => {
    const variants: Record<string, string> = {
      'Reflect.apply':
        "import jwt from 'jsonwebtoken';\nexport const f = (t: string, k: string) => Reflect.apply(jwt.verify, null, [t, k]);",
      'comma expression':
        "import jwt from 'jsonwebtoken';\nexport const f = (t: string, k: string) => (0, jwt.verify)(t, k);",
      'renamed destructure':
        "import jwt from 'jsonwebtoken';\nconst { verify: v2 } = jwt as any;\nexport const f = (t: string, k: string) => v2(t, k);",
      'computed destructure':
        "import jwt from 'jsonwebtoken';\nconst { ['verify']: v3 } = jwt;\nexport const f = (t: string, k: string) => v3(t, k);",
      'namespace default':
        "import * as ns from 'jsonwebtoken';\nexport const f = (t: string, k: string) => (ns as any).default.verify(t, k);",
      'aliased require':
        "const r = require;\nexport const f = (t: string, k: string) => r('jsonwebtoken').verify(t, k);",
      'module.require':
        "export const f = (t: string, k: string) => module.require('jsonwebtoken').verify(t, k);",
      createRequire:
        "import { createRequire } from 'module';\nconst req = createRequire(__filename);\nexport const f = (t: string, k: string) => req('jsonwebtoken').verify(t, k);",
      'jose subpath':
        "import { jwtVerify } from 'jose/jwt/verify';\nexport const f = jwtVerify;",
      'module as argument':
        "import jwt from 'jsonwebtoken';\nconst g = (m: typeof jwt, t: string, k: string) => m.verify(t, k);\nexport const f = (t: string, k: string) => g(jwt, t, k);",
    };
    for (const [name, source] of Object.entries(variants)) {
      it(`flags ${name}`, () => {
        expect(findLibraryMentions(source)).to.have.length.greaterThan(0);
      });
    }
    it('ignores comments and unrelated strings', () => {
      expect(
        findLibraryMentions(
          "// jsonwebtoken\nconst a = 'jsonwebtoken-like'; /* 'jws' */",
        ),
      ).to.deep.equal([]);
    });
  });

  it('exports none of the removed signature-only validators', () => {
    const offenders: string[] = [];
    for (const f of sourceFiles(SRC)) {
      const names = exportedNames(fs.readFileSync(f, 'utf8'));
      for (const name of REMOVED_EXPORTS) {
        if (names.includes(name)) {
          offenders.push(`${path.relative(SRC, f)}: ${name}`);
        }
      }
    }
    expect(offenders).to.deep.equal([]);
  });

  it('sees an exported name however it is exported, and not a local one', () => {
    expect(
      exportedNames(
        [
          'export const a = 1;',
          'export async function b() {}',
          'const c = 1;',
          'export { c };',
          'const local = 1;',
          'export { local as d };',
          "export { e } from './e';",
          "export { x as f } from './x';",
          'const g = 1;',
          'export { g as default };',
          'const notExported = 1;',
          'export const { h, i: j, k: { l }, ...m } = o;',
          'export const [n, , [p]] = q;',
          'const { notExportedEither } = o;',
        ].join('\n'),
      ).sort(),
    ).to.deep.equal(
      ['a', 'b', 'c', 'd', 'default', 'e', 'f', 'h', 'j', 'l', 'm', 'n', 'p'],
    );
  });

  describe('the scanner sees every spelling of a verify call', () => {
    const variants: Record<string, string> = {
      'default import':
        "import jwt from 'jsonwebtoken';\nexport const f = (t: string, k: string) => jwt.verify(t, k);",
      'named import':
        "import { verify } from 'jsonwebtoken';\nexport const f = (t: string, k: string) => verify(t, k);",
      'renamed named import':
        "import { verify as v } from 'jsonwebtoken';\nexport const f = (t: string, k: string) => v(t, k);",
      'namespace import':
        "import * as jsonwebtoken from 'jsonwebtoken';\nexport const f = (t: string, k: string) => jsonwebtoken.verify(t, k);",
      'bracket access':
        "import jwt from 'jsonwebtoken';\nexport const f = (t: string, k: string) => jwt['verify'](t, k);",
      'line-broken chain':
        "const jwt = require('jsonwebtoken');\nexport const f = (t: string, k: string) => jwt\n  .verify(t, k);",
      'inline require':
        "export const f = (t: string, k: string) => require('jsonwebtoken').verify(t, k);",
      'alias of the module':
        "import jwt from 'jsonwebtoken';\nconst j = jwt;\nexport const f = (t: string, k: string) => j.verify(t, k);",
      'destructured from the imported module':
        "import jwt from 'jsonwebtoken';\nconst { verify } = jwt;\nexport const f = (t: string, k: string) => verify(t, k);",
      'verify taken as a value':
        "import jwt from 'jsonwebtoken';\nconst v = jwt.verify;\nexport const f = (t: string, k: string) => v(t, k);",
      '.call on verify':
        "import jwt from 'jsonwebtoken';\nexport const f = (t: string, k: string) => jwt.verify.call(null, t, k);",
      '.apply on verify':
        "import jwt from 'jsonwebtoken';\nexport const f = (t: string, k: string) => jwt.verify.apply(null, [t, k] as never);",
      'non-null assertion':
        "import jwt from 'jsonwebtoken';\nexport const f = (t: string, k: string) => jwt!.verify(t, k);",
      'angle-bracket assertion':
        "import jwt from 'jsonwebtoken';\nexport const f = (t: string, k: string) => (<any>jwt).verify(t, k);",
      satisfies:
        "import jwt from 'jsonwebtoken';\nexport const f = (t: string, k: string) => (jwt satisfies object as typeof jwt).verify(t, k);",
      'computed key':
        "import jwt from 'jsonwebtoken';\nconst m = 'ver' + 'ify';\nexport const f = (t: string, k: string) => (jwt as any)[m](t, k);",
      'template-literal key':
        "import jwt from 'jsonwebtoken';\nexport const f = (t: string, k: string) => jwt[`verify`](t, k);",
      'optional call':
        "import jwt from 'jsonwebtoken';\nexport const f = (t: string, k: string) => jwt.verify?.(t, k);",
      'dynamic import then':
        "export const f = (t: string, k: string) => import('jsonwebtoken').then((m) => m.verify(t, k));",
      'await dynamic import':
        "export const f = async (t: string, k: string) => (await import('jsonwebtoken')).verify(t, k);",
      're-exported verify': "export { verify } from 'jsonwebtoken';",
      'star re-export': "export * from 'jsonwebtoken';",
      'jws directly':
        "import jws from 'jws';\nexport const f = (t: string, k: string) => jws.verify(t, 'HS256', k);",
      'require of jws':
        "export const f = (t: string, k: string) => require('jws').verify(t, 'HS256', k);",
      'jose jwtVerify':
        "import { jwtVerify } from 'jose';\nexport const f = (t: string, k: Uint8Array) => jwtVerify(t, k);",
      'destructured require':
        "const { verify } = require('jsonwebtoken');\nexport const f = (t: string, k: string) => verify(t, k);",
    };
    for (const [name, source] of Object.entries(variants)) {
      it(`flags a ${name}`, () => {
        expect(findVerifyCalls(source)).to.have.length.greaterThan(0);
      });
    }
    it('reports the key jws.verify takes as its third argument', () => {
      for (const source of [
        "import jws from 'jws';\nexport const f = (t: string, k: string) => jws.verify(t, 'HS256', k);",
        "import { verify } from 'jws';\nexport const f = (t: string, k: string) => verify(t, 'HS256', k);",
        "import jws from 'jws';\nexport const f = (t: string, k: string) => jws.verify.call(null, t, 'HS256', k);",
      ]) {
        expect(findVerifyCalls(source).map((c) => c.key)).to.deep.equal(['k']);
      }
    });
    it('reports .call on an imported verify function in a file that may name the library', () => {
      const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'jwt-scan-'));
      try {
        fs.writeFileSync(
          path.join(dir, 'auth.middleware.ts'),
          "import { verify } from 'jsonwebtoken';\nexport const f = (t: string, k: string) => verify.call(null, t, k);",
        );
        expect(
          unlistedVerifyCalls(dir, dir, {}).concat(
            unlistedLibraryMentions(dir, dir, ['auth.middleware.ts'], {}),
          ),
        ).to.deep.equal(['auth.middleware.ts:2 verifies with k']);
      } finally {
        fs.rmSync(dir, { recursive: true, force: true });
      }
    });
    it('ignores decode, sign and a verify of something else', () => {
      expect(
        findVerifyCalls(
          "import jwt from 'jsonwebtoken';\nconst a = jwt.decode('x');\nconst b = jwt.sign({}, 'k');\nconst c = other.verify(1, 2);",
        ),
      ).to.deep.equal([]);
    });
    it('reports a new call in an allowlisted file when its key differs', () => {
      const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'jwt-scan-'));
      try {
        fs.writeFileSync(
          path.join(dir, 'a.ts'),
          "import jwt from 'jsonwebtoken';\nexport const f = (t: string, k: string, s: string) => [jwt.verify(t, k), jwt.verify(t, s)];",
        );
        expect(unlistedVerifyCalls(dir, dir, { 'a.ts': ['k'] })).to.have.length(
          1,
        );
        expect(
          unlistedVerifyCalls(dir, dir, { 'a.ts': ['k', 's'] }),
        ).to.deep.equal([]);
        expect(unlistedVerifyCalls(dir, dir, {})).to.have.length(2);
      } finally {
        fs.rmSync(dir, { recursive: true, force: true });
      }
    });
  });
});
