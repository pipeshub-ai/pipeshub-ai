/**
 * AP-06 (C3.75), WP-54: the edit card lists a call's values in the tool's own argument order: the tool schema's
 * property order where the card has it (WP-56: the worker sends it as the reason's `fieldOrder`), else the order
 * the call gave them. Never the read-only card's
 * content-first order, which put Body before Subject before To on a mail.
 */
import { describe, expect, it } from 'vitest';
import { editFields } from '../approval-edits';

const MAIL = JSON.stringify({ to: 'all-staff@acme.com', subject: 'Q3 results', body: 'Hi all, the deck is attached.' });

describe('WP-54 AP-06 edit field order', () => {
  it('AP-06: a mail edits To, Subject, Body: the call’s own order', () => {
    expect(editFields(MAIL)?.map((f) => f.key)).toEqual(['to', 'subject', 'body']);
  });

  it('AP-06: the tool schema’s property order wins over the call’s order', () => {
    const args = JSON.stringify({ body: 'Hello', to: 'a@b.co', subject: 'Hi' });
    expect(editFields(args, ['to', 'subject', 'body'])?.map((f) => f.key)).toEqual(['to', 'subject', 'body']);
  });

  it('AP-06: values the schema does not name follow in the call’s order; no order keeps the call’s', () => {
    const args = JSON.stringify({ cc: 'c@d.co', body: 'Hello', to: 'a@b.co', extra: 1 });
    expect(editFields(args, ['to', 'body', 'unused'])?.map((f) => f.key)).toEqual(['to', 'body', 'cc', 'extra']);
    expect(editFields(args, [])?.map((f) => f.key)).toEqual(['cc', 'body', 'to', 'extra']);
    expect(editFields(args)?.map((f) => f.key)).toEqual(['cc', 'body', 'to', 'extra']);
  });
});
