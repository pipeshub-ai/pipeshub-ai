import { describe, expect, it } from 'vitest';
import { schemaFields } from '../schema-fields';

const control = (schema: object, name = 'x') =>
  schemaFields({ type: 'object', properties: { [name]: schema } })[0].control;

describe('schemaFields', () => {
  it('unwraps a nullable property and keeps other unions read-only', () => {
    expect(control({ anyOf: [{ type: 'integer' }, { type: 'null' }] })).toBe('number');
    expect(control({ oneOf: [{ type: 'null' }, { type: 'string', format: 'email' }] })).toBe('email');
    expect(control({ anyOf: [{ type: 'object' }, { type: 'null' }] })).toBe('json');
    expect(control({ anyOf: [{ type: 'string' }, { type: 'integer' }] })).toBe('json');
    expect(control({ $ref: '#/$defs/x' })).toBe('json');
    expect(control({})).toBe('json');
  });

  it('keeps enum values as they are', () => {
    const [f] = schemaFields({ type: 'object', properties: { n: { type: 'integer', enum: [1, 2] } } });
    expect(f.options).toEqual([{ value: 1, label: '1' }, { value: 2, label: '2' }]);
  });

  it('matches long-text names in any case', () => {
    expect(control({ type: 'string' }, 'Body')).toBe('textarea');
  });

  it('maps each kind of property to a control', () => {
    expect(control({ type: 'string' }, 'subject')).toBe('text');
    expect(control({ type: 'string', format: 'email' })).toBe('email');
    expect(control({ type: 'string', format: 'date' })).toBe('date');
    expect(control({ type: 'string', format: 'date-time' })).toBe('datetime');
    expect(control({ type: 'string', maxLength: 500 })).toBe('textarea');
    expect(control({ type: 'string', maxLength: 50 }, 'body')).toBe('text');
    expect(control({ type: 'string' }, 'body')).toBe('textarea');
    expect(control({ type: 'integer' })).toBe('number');
    expect(control({ type: 'number' })).toBe('number');
    expect(control({ type: 'boolean' })).toBe('switch');
    expect(control({ type: 'string', enum: ['a', 'b'] })).toBe('select');
    expect(control({ type: 'array', items: { type: 'string' } })).toBe('chips');
    expect(control({ type: 'object' })).toBe('json');
    expect(control({ type: 'array', items: { type: 'object' } })).toBe('json');
  });

  it('lists enum options, labels, and required fields first', () => {
    const fields = schemaFields({
      type: 'object',
      required: ['mail_to'],
      properties: {
        priority: { type: 'string', enum: ['low', 'high'], title: 'Urgency' },
        mail_to: { type: 'string', format: 'email' },
      },
    });
    expect(fields.map((f) => f.name)).toEqual(['mail_to', 'priority']);
    expect(fields[0]).toMatchObject({ label: 'Mail to', required: true });
    expect(fields[1]).toMatchObject({ label: 'Urgency', required: false, options: [{ value: 'low', label: 'low' }, { value: 'high', label: 'high' }] });
  });

  it('returns nothing for a schema without properties', () => {
    expect(schemaFields({ type: 'object' })).toEqual([]);
  });
});
