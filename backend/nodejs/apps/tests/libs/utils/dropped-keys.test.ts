import 'reflect-metadata';
import { expect } from 'chai';
import { DROPPED_KEYS, setOwnKey } from '../../../src/libs/utils/dropped-keys';
import { sanitizeForResponse } from '../../../src/utils/xss-sanitization';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { Logger } from '../../../src/libs/services/logger.service';

// One constant, used by every place that copies request-shaped data into a new object.
describe('DROPPED_KEYS and the copies that use it', () => {
  const parsed = () =>
    JSON.parse(
      '{"a":1,"__proto__":{"authConfig":{"clientId":"x"}},"n":{"constructor":"c","prototype":[1],"ok":"v"}}',
    );

  it('names the three keys', () => {
    expect([...DROPPED_KEYS].sort()).to.deep.equal([
      '__proto__',
      'constructor',
      'prototype',
    ]);
  });

  it('setOwnKey defines the key as data without touching the prototype', () => {
    const target: Record<string, unknown> = {};
    setOwnKey(target, '__proto__', { authConfig: 1 });
    setOwnKey(target, 'constructor', 'k');
    setOwnKey(target, 'plain', 2);
    expect(Object.getPrototypeOf(target)).to.equal(Object.prototype);
    expect((target as any).authConfig).to.equal(undefined);
    expect(Object.keys(target).sort()).to.deep.equal([
      '__proto__',
      'constructor',
      'plain',
    ]);
    expect(
      Object.getOwnPropertyDescriptor(target, '__proto__')!.value,
    ).to.deep.equal({ authConfig: 1 });
  });

  it('sanitizeForResponse drops the keys at every depth and keeps plain objects', () => {
    const out = sanitizeForResponse(parsed());
    expect(Object.getPrototypeOf(out)).to.equal(Object.prototype);
    expect(out.authConfig).to.equal(undefined);
    expect(Object.keys(out)).to.deep.equal(['a', 'n']);
    expect(Object.keys(out.n)).to.deep.equal(['ok']);
  });

  it('the error response copy drops the keys at every depth', () => {
    const out = (ErrorMiddleware as any).sanitizeErrorResponse({
      error: parsed(),
    });
    expect(Object.getPrototypeOf(out.error)).to.equal(Object.prototype);
    expect(out.error.authConfig).to.equal(undefined);
    expect(Object.keys(out.error)).to.deep.equal(['a', 'n']);
    expect(Object.keys(out.error.n)).to.deep.equal(['ok']);
  });

  it('the log copy keeps every key as data but never lets __proto__ become the prototype', () => {
    const out = (Logger.getInstance() as any).sanitizeForLogging(
      parsed(),
      new WeakSet(),
      false,
    );
    expect(Object.getPrototypeOf(out)).to.equal(Object.prototype);
    expect(out.authConfig).to.equal(undefined);
    expect(Object.keys(out).sort()).to.deep.equal(['__proto__', 'a', 'n']);
    expect(Object.keys(out.n).sort()).to.deep.equal([
      'constructor',
      'ok',
      'prototype',
    ]);
  });
});
