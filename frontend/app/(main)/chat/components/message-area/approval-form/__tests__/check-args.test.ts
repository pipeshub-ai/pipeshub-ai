import { describe, expect, it } from 'vitest';
import { checkArgs } from '../check-args';
import { schemaFields } from '../schema-fields';

const fields = schemaFields({
  type: 'object',
  required: ['mail_to'],
  properties: {
    mail_to: { type: 'string', format: 'email' },
    priority: { type: 'string', enum: ['low', 'high'] },
    count: { type: 'integer' },
    code: { type: 'string', minLength: 2, maxLength: 4, pattern: '^[A-Z]+$' },
  },
});

describe('checkArgs', () => {
  it('accepts valid values', () => {
    expect(checkArgs(fields, { mail_to: 'a@b.co', priority: 'low', count: 2, code: 'AB' })).toEqual([]);
  });
  it('flags a missing required field', () => {
    expect(checkArgs(fields, {})).toMatchObject([{ path: '/mail_to', code: 'required' }]);
    expect(checkArgs(fields, { mail_to: '' }).map((e) => e.path)).toEqual(['/mail_to']);
  });
  it('flags values outside enum, type, length and email format', () => {
    const paths = (v: Record<string, unknown>) => checkArgs(fields, { mail_to: 'a@b.co', ...v }).map((e) => e.path);
    expect(paths({ priority: 'urgent' })).toEqual(['/priority']);
    expect(paths({ count: 'two' })).toEqual(['/count']);
    expect(paths({ count: 1.5 })).toEqual(['/count']);
    expect(paths({ code: 'A' })).toEqual(['/code']);
    expect(paths({ code: 'ABCDE' })).toEqual(['/code']);
    expect(checkArgs(fields, { mail_to: 'nope' }).map((e) => e.path)).toEqual(['/mail_to']);
  });
  it('gives codes and params to translate', () => {
    const one = (v: Record<string, unknown>) => checkArgs(fields, { mail_to: 'a@b.co', ...v })[0];
    expect(one({ priority: 'urgent' })).toMatchObject({ code: 'enum' });
    expect(one({ count: 'two' })).toMatchObject({ code: 'type', params: { type: 'integer' } });
    expect(one({ code: 'A' })).toMatchObject({ code: 'minLength', params: { min: 2 } });
    expect(one({ code: 'ABCDE' })).toMatchObject({ code: 'maxLength', params: { max: 4 } });
    expect(checkArgs(fields, { mail_to: 'nope' })[0]).toMatchObject({ code: 'email' });
    expect(one({ priority: 'urgent' }).message).toBeTruthy();
  });
  it('leaves a pattern to the server, which bounds how long a pattern may run', () => {
    expect(checkArgs(fields, { mail_to: 'a@b.co', code: 'ab' })).toEqual([]);
    const slow = schemaFields({ type: 'object', properties: { q: { type: 'string', pattern: '^(a+)+$' } } });
    expect(checkArgs(slow, { q: `${'a'.repeat(40)}!` })).toEqual([]);
  });
  it('skips optional fields that are empty', () => {
    expect(checkArgs(fields, { mail_to: 'a@b.co', priority: undefined, code: '' })).toEqual([]);
  });
});
