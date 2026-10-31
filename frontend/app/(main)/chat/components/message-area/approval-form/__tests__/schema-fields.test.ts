import { describe, expect, it } from 'vitest';
import { schemaFields } from '../schema-fields';

const control = (schema: object, name = 'x') =>
  schemaFields({ type: 'object', properties: { [name]: schema } })[0].control;

describe('schemaFields', () => {
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
    expect(fields[1]).toMatchObject({ label: 'Urgency', required: false, options: ['low', 'high'] });
  });

  it('returns nothing for a schema without properties', () => {
    expect(schemaFields({ type: 'object' })).toEqual([]);
  });
});
