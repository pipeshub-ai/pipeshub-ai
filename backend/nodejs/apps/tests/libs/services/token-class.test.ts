import 'reflect-metadata';
import { expect } from 'chai';
import jwt from 'jsonwebtoken';
import sinon from 'sinon';
import { authJwtGenerator } from '../../../src/libs/utils/createJwt';
import { AuthTokenService } from '../../../src/libs/services/authtoken.service';
import {
  isAppSession,
  TOKEN_CLASS,
  TOKEN_CLASS_CLAIM,
  useRunTokenMinter,
  withRunBearer,
} from '../../../src/libs/services/token-class';
import {
  APP_SESSION_REQUIRED,
  requireAppSession,
} from '../../../src/libs/middlewares/require-app-session.middleware';
import { AIServiceCommand } from '../../../src/libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../src/libs/enums/http-methods.enum';

const SECRET = 'a-session-secret-of-some-length';
const SCOPED = 'a-scoped-secret-of-some-length';

const decoded = (token: string): Record<string, unknown> =>
  jwt.verify(token, SECRET) as Record<string, unknown>;

const run = (user: Record<string, unknown> | undefined, headers: Record<string, string> = {}) => {
  const next = sinon.spy();
  requireAppSession({ user, headers } as never, {} as never, next);
  const error = next.firstCall.args[0] as { code?: string; statusCode?: number } | undefined;
  return error;
};

describe('the token class: telling the app session from what runs and clients hold', () => {
  afterEach(() => {
    useRunTokenMinter(undefined);
    sinon.restore();
  });

  it('a sign-in token says it is an app session', () => {
    const token = authJwtGenerator(SECRET, 'dana@example.com', 'u1', 'o1', 'Dana', 'business', 'member');
    expect(decoded(token)[TOKEN_CLASS_CLAIM]).to.equal(TOKEN_CLASS.session);
  });

  it("a run's copy of a session keeps its claims, issue time and expiry, and says it is a run's", () => {
    const tokens = new AuthTokenService(SECRET, SCOPED);
    const session = authJwtGenerator(SECRET, 'dana@example.com', 'u1', 'o1', 'Dana', 'business', 'admin');
    const copy = tokens.runTokenFor(session);
    expect(copy).to.be.a('string');
    const before = decoded(session);
    const after = decoded(copy!);
    expect(after[TOKEN_CLASS_CLAIM]).to.equal(TOKEN_CLASS.run);
    for (const claim of ['userId', 'orgId', 'email', 'role', 'iat', 'exp']) {
      expect(after[claim], claim).to.deep.equal(before[claim]);
    }
  });

  it('nothing but a verified app session is copied: a forged token, a run token and a token without the claim', () => {
    const tokens = new AuthTokenService(SECRET, SCOPED);
    const forged = jwt.sign({ userId: 'u1', orgId: 'o1', role: 'member', [TOKEN_CLASS_CLAIM]: 'session' }, 'another-secret-entirely');
    const runToken = jwt.sign({ userId: 'u1', orgId: 'o1', role: 'member', [TOKEN_CLASS_CLAIM]: 'run' }, SECRET);
    const unmarked = jwt.sign({ userId: 'u1', orgId: 'o1', role: 'member' }, SECRET);
    expect(tokens.runTokenFor(forged)).to.equal(undefined);
    expect(tokens.runTokenFor(runToken)).to.equal(undefined);
    expect(tokens.runTokenFor(unmarked)).to.equal(undefined);
    expect(tokens.runTokenFor('not a token')).to.equal(undefined);
  });

  it('every call to the AI backend carries the run copy, never the app session itself', () => {
    const tokens = new AuthTokenService(SECRET, SCOPED);
    useRunTokenMinter((bearer) => tokens.runTokenFor(bearer));
    const session = authJwtGenerator(SECRET, 'dana@example.com', 'u1', 'o1', 'Dana', 'business', 'member');
    const command = new AIServiceCommand({
      uri: 'http://ai.test/api/v1/chat/stream',
      method: HttpMethod.POST,
      headers: { Authorization: `Bearer ${session}`, 'client-name': 'pipeshub-ai' },
      body: {},
    });
    const sent = (command as unknown as { headers: Record<string, string> }).headers.authorization;
    expect(sent).to.match(/^Bearer /);
    expect(sent).to.not.equal(`Bearer ${session}`);
    expect(decoded(sent!.slice('Bearer '.length))[TOKEN_CLASS_CLAIM]).to.equal(TOKEN_CLASS.run);
  });

  it('any other bearer goes to the AI backend as it came', () => {
    useRunTokenMinter(() => undefined);
    const headers = withRunBearer({ authorization: 'Bearer some-oauth-token' });
    expect(headers.authorization).to.equal('Bearer some-oauth-token');
  });

  describe('requireAppSession', () => {
    it('admits only the app session', () => {
      expect(run({ userId: 'u1', orgId: 'o1', [TOKEN_CLASS_CLAIM]: 'session' })).to.equal(undefined);
      expect(isAppSession({ [TOKEN_CLASS_CLAIM]: 'session' })).to.equal(true);
    });

    it("refuses a personal access token or an OAuth client, a run's token, a token without the claim, and an /mcp tool call", () => {
      const cases: Array<[string, Record<string, unknown>, Record<string, string>?]> = [
        ['oauth or personal access token', { userId: 'u1', isOAuth: true, [TOKEN_CLASS_CLAIM]: 'session' }],
        ["a run's token", { userId: 'u1', [TOKEN_CLASS_CLAIM]: 'run' }],
        ['no claim', { userId: 'u1' }],
        ['an /mcp tool call', { userId: 'u1', [TOKEN_CLASS_CLAIM]: 'session' }, { 'x-pipeshub-mcp-relay': 'x' }],
      ];
      for (const [label, user, headers] of cases) {
        const error = run(user, headers);
        expect(error?.code, label).to.equal(APP_SESSION_REQUIRED);
        expect(error?.statusCode, label).to.equal(403);
      }
    });

    it('an unauthenticated request is not admitted either', () => {
      expect(run(undefined)?.statusCode).to.equal(401);
    });
  });
});
