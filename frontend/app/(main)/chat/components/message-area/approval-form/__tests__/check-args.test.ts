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
    expect(checkArgs(fields, {}).map((e) => e.path)).toEqual(['/mail_to']);
    expect(checkArgs(fields, { mail_to: '' }).map((e) => e.path)).toEqual(['/mail_to']);
  });
  it('flags values outside enum, type, length, pattern and email format', () => {
    const paths = (v: Record<string, unknown>) => checkArgs(fields, { mail_to: 'a@b.co', ...v }).map((e) => e.path);
    expect(paths({ priority: 'urgent' })).toEqual(['/priority']);
    expect(paths({ count: 'two' })).toEqual(['/count']);
    expect(paths({ count: 1.5 })).toEqual(['/count']);
    expect(paths({ code: 'A' })).toEqual(['/code']);
    expect(paths({ code: 'ABCDE' })).toEqual(['/code']);
    expect(paths({ code: 'ab' })).toEqual(['/code']);
    expect(checkArgs(fields, { mail_to: 'nope' }).map((e) => e.path)).toEqual(['/mail_to']);
  });
  it('skips optional fields that are empty', () => {
    expect(checkArgs(fields, { mail_to: 'a@b.co', priority: undefined, code: '' })).toEqual([]);
  });
});
