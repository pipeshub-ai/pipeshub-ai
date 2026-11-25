import { expect } from 'chai';
import sinon from 'sinon';
import {
  assertNotLogged,
  assertSnakeCaseLogKeys,
  createMockLogger,
  createSnakeCaseLogKeyGuard,
} from './mock-logger';

const EMAIL = 'alice.liddell@example.test';

describe('assertNotLogged', () => {
  it('passes when no value was logged', () => {
    const logger = createMockLogger();
    logger.info('user.created', { userId: 'u-0001' });
    assertNotLogged(logger, EMAIL, 'Alice', 'Liddell');
  });

  it('finds a value in the message, in nested meta, and in any case', () => {
    for (const log of [
      (l: ReturnType<typeof createMockLogger>) => l.warn('blocked', EMAIL),
      (l: ReturnType<typeof createMockLogger>) => l.info('x', { user: { profile: { email: EMAIL } } }),
      (l: ReturnType<typeof createMockLogger>) => l.debug(`entered ${EMAIL.toUpperCase()}`),
    ]) {
      const logger = createMockLogger();
      log(logger);
      expect(() => assertNotLogged(logger, EMAIL)).to.throw('was logged');
    }
  });

  it("finds a value in an Error's message, which JSON.stringify would drop", () => {
    const logger = createMockLogger();
    logger.error('user.update.failed', new Error(`Email already in use: ${EMAIL}`));
    expect(JSON.stringify(logger.error.firstCall.args)).to.not.include(EMAIL);
    expect(() => assertNotLogged(logger, EMAIL)).to.throw('was logged');
  });

  it('searches a long list and a long string whole', () => {
    const users = Array.from({ length: 150 }, (_, i) => ({ id: `u-${String(i)}`, email: `user${String(i)}@example.test` }));
    users[120] = { id: 'u-0120', email: EMAIL };
    const listLogger = createMockLogger();
    listLogger.debug('users', users);
    expect(() => assertNotLogged(listLogger, EMAIL)).to.throw('was logged');

    const textLogger = createMockLogger();
    textLogger.info(`${'x'.repeat(20000)} ${EMAIL}`);
    expect(() => assertNotLogged(textLogger, EMAIL)).to.throw('was logged');
  });

  it('checks first and last names separately', () => {
    const logger = createMockLogger();
    logger.info('owner Liddell');
    expect(() => assertNotLogged(logger, 'Alice Liddell', 'Alice', 'Liddell')).to.throw('"Liddell" was logged');
  });

  it('fails when nothing was logged, and ignores non-level stubs', () => {
    const logger = createMockLogger();
    logger.updateDefaultMeta(EMAIL);
    expect(() => assertNotLogged(logger, EMAIL)).to.throw('no log calls');
  });

  it('works on a real logger whose level methods are stubbed', () => {
    const logger = { info: sinon.stub(), warn: sinon.stub() };
    logger.warn('blocked', EMAIL);
    expect(() => assertNotLogged(logger, EMAIL)).to.throw('was logged');
  });
});

describe('assertSnakeCaseLogKeys', () => {
  it('passes snake_case keys, a message alone, an Error and a string meta', () => {
    const logger = createMockLogger();
    logger.info('pat.created', { org_id: 'o1', pat_id: 't1', expiry_days: 30 });
    logger.info('started');
    logger.error('failed', new Error('boom'));
    logger.warn('odd', 'text');
    expect(() => assertSnakeCaseLogKeys(logger)).to.not.throw();
  });

  it('names the level, the message and every camelCase key, at any level', () => {
    const logger = createMockLogger();
    logger.debug('ok', { client_id: 'c1' });
    logger.warn('Token list reached its limit', { clientId: 'c1', userId: 'u1', limit: 5 });
    expect(() => assertSnakeCaseLogKeys(logger)).to.throw('warn "Token list reached its limit" has keys that are not snake_case: clientId, userId');
  });

  it('refuses a key with a capital or a dash, not only camelCase', () => {
    const logger = createMockLogger();
    logger.info('x', { 'client-id': 'c1' });
    expect(() => assertSnakeCaseLogKeys(logger)).to.throw('client-id');
    const other = createMockLogger();
    other.error('y', { Org: 'o1' });
    expect(() => assertSnakeCaseLogKeys(other)).to.throw('Org');
  });
});

describe('createSnakeCaseLogKeyGuard', () => {
  const test = (title: string) => ({ currentTest: { fullTitle: () => title } }) as unknown as Mocha.Context;

  it('never throws in check, and reports nothing when every key is snake_case', () => {
    const guard = createSnakeCaseLogKeyGuard();
    const good = createMockLogger();
    good.info('ok', { client_id: 'c1' });
    const bad = createMockLogger();
    bad.info('Tokens refreshed', { clientId: 'c1' });
    expect(() => guard.check(test('suite a'), good)).to.not.throw();
    expect(() => guard.report()).to.not.throw();
    expect(() => guard.check(test('suite b'), good, bad)).to.not.throw();
  });

  it('reports every failing test by title, once', () => {
    const guard = createSnakeCaseLogKeyGuard();
    const first = createMockLogger();
    first.warn('Token list reached its limit', { userId: 'u1' });
    const second = createMockLogger();
    second.info('OAuth app created', { appId: 'a1' });
    guard.check(test('suite one'), first);
    guard.check(test('suite two'), second);
    expect(() => guard.report()).to.throw(
      '"suite one": warn "Token list reached its limit" has keys that are not snake_case: userId\n' +
        '"suite two": info "OAuth app created" has keys that are not snake_case: appId',
    );
    expect(() => guard.report()).to.not.throw();
  });
});
