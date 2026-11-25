/**
 * AP-06 (C3.75), WP-50: what a person may edit on a held call and what they changed. Only top-level text,
 * numbers and yes/no; lists, nested values and anything masked as a secret stay as the call has them. The receipt's
 * diff names each changed value with its old and new text, masked like the card.
 */
import { describe, expect, it } from 'vitest';
import { changedKeys, editDiff, editedArguments, editedDestination, editFields, invalidKeys } from '../approval-edits';
import { VECTOR_EXAMPLES, VECTOR_MASK } from './token-vector';

const ARGS = JSON.stringify({
  to: 'all-staff@acme.com',
  subject: 'Q3 results',
  body: 'Hi all, Q3 revenue was $4.2M, up 12%. The board deck and the full customer list are attached.',
  priority: 2,
  urgent: false,
  attachments: ['deck.pdf', 'customers.csv'],
  meta: { label: 'q3' },
});

describe('AP-06 editFields', () => {
  it('AP-06: text, numbers and yes/no are editable, in the call’s order (WP-54); lists and nested values are fixed', () => {
    const fields = editFields(ARGS);
    expect(fields).not.toBeNull();
    const kinds = Object.fromEntries((fields ?? []).map((f) => [f.key, f.kind]));
    expect(kinds).toEqual({
      body: 'text',
      subject: 'text',
      to: 'text',
      priority: 'number',
      urgent: 'boolean',
      attachments: 'fixed',
      meta: 'fixed',
    });
    expect(fields?.[0]?.key).toBe('to');
    expect(fields?.find((f) => f.key === 'body')?.multiline).toBe(true);
    expect(fields?.find((f) => f.key === 'attachments')?.preview?.value).toBe('deck.pdf, customers.csv');
  });

  it('AP-06: arguments that are not a JSON object offer no field editor', () => {
    expect(editFields('not json')).toBeNull();
    expect(editFields('[1,2]')).toBeNull();
  });

  it.each(VECTOR_EXAMPLES.map((e) => [e.id, e.secret] as const))(
    'AP-06: a value masked as a secret (%s) is never put in an input',
    (_id, secret) => {
      const fields = editFields(JSON.stringify({ message: `use ${secret}`, api_key: 'x' }));
      expect(fields?.find((f) => f.key === 'message')?.kind).toBe('fixed');
      expect(fields?.find((f) => f.key === 'message')?.preview?.value).toContain(VECTOR_MASK);
      expect(fields?.find((f) => f.key === 'api_key')?.kind).toBe('fixed');
    },
  );
});

describe('AP-06 edits', () => {
  const fields = editFields(ARGS) ?? [];

  it('AP-06: changed keys, a number that does not parse, and the arguments approved in their own types', () => {
    const draft = { to: 'leadership@acme.com', subject: 'Q3 results', priority: '3', urgent: 'true', body: fields.find((f) => f.key === 'body')!.original };
    expect(changedKeys(fields, draft).sort()).toEqual(['priority', 'to', 'urgent']);
    expect(invalidKeys(fields, { ...draft, priority: 'three' })).toEqual(['priority']);
    expect(changedKeys(fields, { ...draft, priority: 'three' })).not.toContain('priority');
    expect(editedArguments(ARGS, fields, draft)).toEqual({
      ...JSON.parse(ARGS),
      to: 'leadership@acme.com',
      priority: 3,
      urgent: true,
    });
  });

  it('AP-06: the receipt diff lists each changed value, old then new', () => {
    const edited = JSON.stringify({ ...JSON.parse(ARGS), to: 'leadership@acme.com', body: 'Hi all. The board deck is attached.' });
    expect(editDiff(ARGS, edited)).toEqual([
      { key: 'to', label: 'To', before: 'all-staff@acme.com', after: 'leadership@acme.com' },
      { key: 'body', label: 'Body', before: JSON.parse(ARGS).body, after: 'Hi all. The board deck is attached.' },
    ]);
    expect(editDiff(ARGS, ARGS)).toEqual([]);
    expect(editDiff('nope', ARGS)).toEqual([]);
  });

  it('AP-06: the receipt diff masks a secret on either side', () => {
    const [{ secret }] = VECTOR_EXAMPLES;
    const lines = editDiff(JSON.stringify({ note: 'a' }), JSON.stringify({ note: `b ${secret}` }));
    expect(lines[0]?.after).toContain(VECTOR_MASK);
    expect(JSON.stringify(lines)).not.toContain(secret);
  });
});

describe('AP-06 the receipt title', () => {
  it('AP-06: an edit that changed the destination names the new one; anything else keeps the reason as it was', () => {
    const reason = { rule: 'writes', sources: [], moreSources: 0, conversationGrant: false, destination: 'all-staff@acme.com' };
    const approval = { args: ARGS, reason, status: 'approved' as const, editedArgs: JSON.stringify({ ...JSON.parse(ARGS), to: 'leadership@acme.com' }) };
    expect(editedDestination(approval).reason?.destination).toBe('leadership@acme.com');
    expect(editedDestination({ ...approval, status: 'pending' as const }).reason?.destination).toBe('all-staff@acme.com');
    expect(editedDestination({ ...approval, editedArgs: JSON.stringify({ ...JSON.parse(ARGS), subject: 'x' }) }).reason?.destination).toBe('all-staff@acme.com');
  });
});
