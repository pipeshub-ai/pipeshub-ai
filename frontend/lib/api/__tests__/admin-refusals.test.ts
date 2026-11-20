/** Refusals only an admin can lift: none by default, and the ones named once something names them. */
import { describe, it, expect, afterEach } from 'vitest';
import { adminRefusals, noAdminRefusals, setAdminRefusals } from '@/lib/api/admin-refusals';
import { refusalMessage } from '@/lib/api/api-error';
import { cannotRun } from '@/app/(main)/chat/stream-error';
import { NOT_ALLOWED, NOT_ALLOWED_CALL, NOT_ALLOWED_TEXT, testAdminRefusals } from './admin-refusals-double';

afterEach(() => setAdminRefusals(undefined));

describe('by default', () => {
  it('there are none: no run code names one, and no refused call is one', () => {
    setAdminRefusals(undefined);
    expect(adminRefusals()).toBe(noAdminRefusals);
    expect(cannotRun({ code: NOT_ALLOWED })).toBe(false);
    expect(cannotRun(undefined)).toBe(false);
    expect(refusalMessage(NOT_ALLOWED_CALL)).toBeUndefined();
  });

  it('a call refused with 402 passes on no words either', () => {
    setAdminRefusals(undefined);
    expect(refusalMessage({ type: 'UNKNOWN', statusCode: 402, message: NOT_ALLOWED_TEXT })).toBeUndefined();
  });
});

describe('once named', () => {
  it('a run that ends with one cannot run again', () => {
    setAdminRefusals(testAdminRefusals);
    expect(cannotRun({ code: NOT_ALLOWED })).toBe(true);
    expect(cannotRun({ code: 'stream_error' })).toBe(false);
  });

  it("a call refused with one passes on the server's words, and any other refused call none", () => {
    setAdminRefusals(testAdminRefusals);
    expect(refusalMessage(NOT_ALLOWED_CALL)).toBe(NOT_ALLOWED_TEXT);
    expect(refusalMessage({ ...NOT_ALLOWED_CALL, message: '' })).toBeUndefined();
    expect(refusalMessage({ type: 'SERVER', statusCode: 500, message: 'The speech service failed.' })).toBeUndefined();
    expect(refusalMessage(null)).toBeUndefined();
  });

  it('there are none again once the default is restored', () => {
    setAdminRefusals(testAdminRefusals);
    setAdminRefusals(undefined);
    expect(cannotRun({ code: NOT_ALLOWED })).toBe(false);
    expect(refusalMessage(NOT_ALLOWED_CALL)).toBeUndefined();
  });
});
