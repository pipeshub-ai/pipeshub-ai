import { expect } from 'chai';
import sinon from 'sinon';
import { assertNotLogged, createMockLogger } from './mock-logger';

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
