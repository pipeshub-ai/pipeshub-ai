import { describe, expect, it } from 'vitest';
import { documentLayout } from '../document-layout';
import { isLongText, schemaFields } from '../schema-fields';
import type { JsonSchema } from '../../../../types';

const names = (fields: { name: string }[]) => fields.map((f) => f.name);
const layout = (schema: JsonSchema) => {
  const l = documentLayout(schemaFields(schema));
  return { recipients: names(l.recipients), headline: l.headline?.name ?? null, pills: names(l.pills), body: names(l.body), more: names(l.more) };
};
const strings = { type: 'array', items: { type: 'string' } } as const;

describe('documentLayout', () => {
  it('email tool: to/cc chips, subject headline, body body', () => {
    expect(
      layout({
        type: 'object',
        required: ['to', 'subject', 'body'],
        properties: { to: strings, cc: { type: 'array', items: { type: 'string', format: 'email' } }, subject: { type: 'string' }, body: { type: 'string' } },
      }),
    ).toEqual({ recipients: ['to', 'cc'], headline: 'subject', pills: [], body: ['body'], more: [] });
  });

  it('jira-like: summary headline, priority/labels pills, description body', () => {
    expect(
      layout({
        type: 'object',
        properties: {
          project: { type: 'string' },
          summary: { type: 'string' },
          description: { type: 'string' },
          priority: { type: 'string', enum: ['High', 'Low'] },
          labels: strings,
        },
      }),
    ).toEqual({ recipients: [], headline: 'summary', pills: ['project', 'priority', 'labels'], body: ['description'], more: [] });
  });

  it('slack-like: channel pill, message body', () => {
    expect(layout({ type: 'object', properties: { channel: { type: 'string' }, message: { type: 'string' } } })).toEqual({
      recipients: [], headline: null, pills: ['channel'], body: ['message'], more: [],
    });
  });

  it('non-string title goes to more fields', () => {
    expect(layout({ type: 'object', properties: { title: { type: 'object' }, name: { type: 'string' } } })).toMatchObject({ headline: 'name', more: ['title'] });
  });

  it('nested object goes to more fields', () => {
    expect(
      layout({ type: 'object', properties: { subject: { type: 'string' }, meta: { type: 'object' }, rows: { type: 'array', items: { type: 'object' } } } }),
    ).toMatchObject({ headline: 'subject', pills: [], more: ['meta', 'rows'] });
  });

  it('takes only the first headline; a second one is a pill', () => {
    expect(layout({ type: 'object', properties: { title: { type: 'string' }, name: { type: 'string' } } })).toMatchObject({ headline: 'title', pills: ['name'] });
  });
});

describe('isLongText', () => {
  const one = (name: string, schema: JsonSchema) => isLongText(schemaFields({ type: 'object', properties: { [name]: schema } })[0]);
  it('covers the name, a long maxLength, and a long-text format', () => {
    expect(one('markdown', { type: 'string' })).toBe(true);
    expect(one('notes', { type: 'string', maxLength: 1000 })).toBe(true);
    expect(one('notes', { type: 'string', format: 'html' })).toBe(true);
    expect(one('notes', { type: 'string' })).toBe(false);
    expect(one('message', { type: 'string', maxLength: 160 })).toBe(true);
    expect(one('message_id', { type: 'string' })).toBe(false);
  });
});
