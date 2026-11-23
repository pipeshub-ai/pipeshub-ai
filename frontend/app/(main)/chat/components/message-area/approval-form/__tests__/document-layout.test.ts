import { describe, expect, it } from 'vitest';
import { documentLayout } from '../document-layout';
import { isLongText, schemaFields } from '../schema-fields';
import type { JsonSchema } from '../../../../types';

const names = (fields: { name: string }[]) => fields.map((f) => f.name);
const layout = (schema: JsonSchema) => {
  const l = documentLayout(schemaFields(schema));
  return { recipients: names(l.recipients), headline: l.headline?.name ?? null, fields: names(l.fields), pills: names(l.pills), lists: names(l.lists), body: names(l.body), more: names(l.more) };
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
    ).toEqual({ recipients: ['to', 'cc'], headline: 'subject', fields: [], pills: [], lists: [], body: ['body'], more: [] });
  });

  it('recipients match on the last word: mail_to, mail_cc, mail_bcc, mailTo', () => {
    expect(
      layout({ type: 'object', properties: { mail_to: strings, mail_cc: strings, mail_bcc: strings, mailTo: strings, to_do: strings } }),
    ).toMatchObject({ recipients: ['mail_to', 'mail_cc', 'mail_bcc', 'mailTo'], lists: ['to_do'] });
  });

  it('jira-like: summary headline, project as a field, priority pill, labels chip row, description body', () => {
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
    ).toEqual({ recipients: [], headline: 'summary', fields: ['project'], pills: ['priority'], lists: ['labels'], body: ['description'], more: [] });
  });

  it('slack-like: channel field, message body', () => {
    expect(layout({ type: 'object', properties: { channel: { type: 'string' }, message: { type: 'string' } } })).toEqual({
      recipients: [], headline: null, fields: ['channel'], pills: [], lists: [], body: ['message'], more: [],
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

  it('takes only the first headline; a second one is a field', () => {
    expect(layout({ type: 'object', properties: { title: { type: 'string' }, name: { type: 'string' } } })).toMatchObject({ headline: 'title', fields: ['name'] });
  });

  it('the title matches on the last word: mail_subject, event_title, page_title, topic; `name` only whole', () => {
    const s = { type: 'string' } as const;
    expect(layout({ type: 'object', properties: { mail_to: strings, mail_subject: s, mail_body: s } })).toMatchObject({ headline: 'mail_subject', fields: [], body: ['mail_body'] });
    expect(layout({ type: 'object', properties: { event_start_time: s, event_title: s } })).toMatchObject({ headline: 'event_title', fields: ['event_start_time'] });
    expect(layout({ type: 'object', properties: { space_key: s, page_title: s } })).toMatchObject({ headline: 'page_title', fields: ['space_key'] });
    expect(layout({ type: 'object', properties: { topic: s, agenda: s } })).toMatchObject({ headline: 'topic', body: ['agenda'] });
    expect(layout({ type: 'object', properties: { assignee_name: s, file_name: s } })).toMatchObject({ headline: null, fields: ['assignee_name', 'file_name'] });
  });

  it('short typed values are fields, never pills; only choices are pills', () => {
    expect(
      layout({
        type: 'object',
        properties: { start: { type: 'string', format: 'date-time' }, count: { type: 'integer' }, owner: { type: 'string', format: 'email' }, private: { type: 'boolean' }, state: { type: 'string', enum: ['open', 'closed'] } },
      }),
    ).toMatchObject({ fields: ['start', 'count', 'owner'], pills: ['private', 'state'] });
  });
});

describe('isLongText', () => {
  const one = (name: string, schema: JsonSchema) => isLongText(schemaFields({ type: 'object', properties: { [name]: schema } })[0]);
  it('covers the name, a long maxLength, and a long-text format', () => {
    expect(one('markdown', { type: 'string' })).toBe(true);
    expect(one('notes', { type: 'string', maxLength: 1000 })).toBe(true);
    expect(one('notes', { type: 'string', format: 'html' })).toBe(true);
    expect(one('location', { type: 'string' })).toBe(false);
    expect(one('agenda', { type: 'string' })).toBe(true);
    expect(one('business_justification', { type: 'string' })).toBe(true);
    expect(one('message', { type: 'string', maxLength: 160 })).toBe(true);
    expect(one('message_id', { type: 'string' })).toBe(false);
    // Whole names only: a code or query to run is long, a product code or a search term is not.
    expect(one('query', { type: 'string' })).toBe(true);
    expect(one('code', { type: 'string' })).toBe(true);
    expect(one('assignee_query', { type: 'string' })).toBe(false);
    expect(one('product_code', { type: 'string' })).toBe(false);
  });
});
