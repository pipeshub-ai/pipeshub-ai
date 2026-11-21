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

  it('edits text the call wrote where the schema leaves the type open, as many MCP servers do', () => {
    const fields = (properties: Record<string, object>, values: Record<string, unknown>) =>
      Object.fromEntries(schemaFields({ type: 'object', properties }, values).map((f) => [f.name, f.control]));
    // Untyped, or a union with a text branch (markdown or a rich-text object): text while the value is text.
    expect(fields(
      { description: { description: 'Issue description' }, body: { anyOf: [{ type: 'string' }, { type: 'object' }] }, title: {} },
      { description: 'Steps to reproduce', body: 'Hello', title: 'Login fails' },
    )).toEqual({ description: 'textarea', body: 'textarea', title: 'text' });
    // A list with untyped items, as Atlassian's labels are: chips while every item is text.
    expect(fields({ labels: { type: 'array', items: {} } }, { labels: ['bug'] })).toEqual({ labels: 'chips' });
    // Anything else stays read-only: an object value, a list of objects, an open field the call didn't set.
    expect(fields(
      { body: { anyOf: [{ type: 'string' }, { type: 'object' }] }, labels: { type: 'array', items: {} }, extra: {} },
      { body: { type: 'doc' }, labels: [{ name: 'x' }] },
    )).toEqual({ body: 'json', labels: 'json', extra: 'json' });
  });

  it('keeps enum values as they are', () => {
    const [f] = schemaFields({ type: 'object', properties: { n: { type: 'integer', enum: [1, 2] } } });
    expect(f.options).toEqual([{ value: 1, label: '1' }, { value: 2, label: '2' }]);
  });

  it('matches long-text names in any case', () => {
    expect(control({ type: 'string' }, 'Body')).toBe('textarea');
  });

  it('matches a long-text name as the whole name or its last word only', () => {
    for (const name of ['body', 'email_body', 'issueDescription', 'comment-text', 'BODY'])
      expect(control({ type: 'string' }, name), name).toBe('textarea');
    for (const name of ['message_id', 'contentType', 'html_url', 'context', 'textColor'])
      expect(control({ type: 'string' }, name), name).toBe('text');
  });

  it('maps each kind of property to a control', () => {
    expect(control({ type: 'string' }, 'subject')).toBe('text');
    expect(control({ type: 'string', format: 'email' })).toBe('email');
    expect(control({ type: 'string', format: 'date' })).toBe('date');
    expect(control({ type: 'string', format: 'date-time' })).toBe('datetime');
    expect(control({ type: 'string', maxLength: 500 })).toBe('textarea');
    expect(control({ type: 'string', maxLength: 160 }, 'message')).toBe('textarea');
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
