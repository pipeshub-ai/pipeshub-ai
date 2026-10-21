import { AxiosError, type AxiosResponse } from 'axios';
import { describe, expect, it } from 'vitest';
import { ErrorType, isGoneError, processError } from '../api-error';

function axiosError(status: number, data: unknown): AxiosError {
  const err = new AxiosError('Request failed');
  err.response = { status, data, headers: {}, statusText: '', config: {} } as unknown as AxiosResponse;
  return err;
}

describe('processError code and details', () => {
  it('reads code and nested details from a structured error body', () => {
    const p = processError(
      axiosError(409, { error: { code: 'CONVERSATION_BUSY', message: 'Busy', details: { x: 1 } } }),
    );
    expect(p.type).toBe(ErrorType.CONFLICT);
    expect(p.code).toBe('CONVERSATION_BUSY');
    expect(p.details).toEqual({ x: 1 });
    expect(p.message).toBe('Busy');
  });

  it('prefers top-level details over nested ones', () => {
    const p = processError(
      axiosError(409, { details: { top: true }, error: { code: 'C', message: 'm', details: { x: 1 } } }),
    );
    expect(p.details).toEqual({ top: true });
  });

  it('leaves code undefined for a legacy string error', () => {
    const p = processError(axiosError(500, { error: 'boom' }));
    expect(p.code).toBeUndefined();
    expect(p.details).toBeUndefined();
    expect(p.message).toBe('boom');
  });
});

describe('isGoneError', () => {
  it('is true for 404 and 403 only', () => {
    expect(isGoneError(processError(axiosError(404, {})))).toBe(true);
    expect(isGoneError(processError(axiosError(403, {})))).toBe(true);
    expect(isGoneError(processError(axiosError(500, {})))).toBe(false);
    expect(isGoneError(processError(axiosError(409, {})))).toBe(false);
  });
});
