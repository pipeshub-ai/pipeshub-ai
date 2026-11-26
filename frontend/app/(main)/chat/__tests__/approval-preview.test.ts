/**
 * UX1 CA-3: what the approval card shows of a held call: the action in human
 * terms, a preview with secrets redacted and long text cut, source links that
 * never show an id, and a reason read defensively from a response.
 */
import { describe, expect, it } from 'vitest';
import {
  approvalAction,
  PREVIEW_CHARS,
  previewPayload,
  REDACTED,
  redactedArgs,
  sourceHref,
  toApprovalReason,
} from '../approval-preview';
import { REDACT_ONLY_PATTERN_SOURCES, VENDOR_PATTERN_SOURCES } from '@/lib/contracts/harness/vectors/token-patterns';
import { VECTOR_ENTRIES, VECTOR_EXAMPLES, VECTOR_MASK } from './token-vector';

describe('approvalAction', () => {
  it('names the action and the app from the tool', () => {
    expect(approvalAction('slack__send_message')).toEqual({ action: 'Send message', app: 'Slack' });
    expect(approvalAction('slack__send_message', 'Post to Slack')).toEqual({ action: 'Post to Slack', app: 'Slack' });
    expect(approvalAction('jira_data_center__create_issue')).toEqual({ action: 'Create issue', app: 'Jira' });
    expect(approvalAction('acme__file_report')).toEqual({ action: 'File report', app: 'Acme' });
    expect(approvalAction('mcp_docs_delete_page')).toEqual({ action: 'Mcp docs delete page', app: null });
  });
});

describe('previewPayload', () => {
  it('puts the content first and keeps every other field', () => {
    const fields = previewPayload(JSON.stringify({ channel: '#sales', message: 'Q3 numbers attached' }));
    expect(fields.map((f) => f.key)).toEqual(['message', 'channel']);
    expect(fields[0]).toMatchObject({ label: 'Message', value: 'Q3 numbers attached', redacted: false });
  });

  it('redacts secrets by field name and by shape, and says so', () => {
    const fields = previewPayload(
      JSON.stringify({
        api_key: 'abc',
        text: 'use Bearer abcdefghijklmnop and sk-ABCDEFGHIJKLMNOPQRSTUV please',
      }),
    );
    const byKey = fields.find((f) => f.key === 'api_key');
    const text = fields.find((f) => f.key === 'text');
    expect(byKey).toMatchObject({ value: REDACTED, redacted: true });
    expect(text?.value).toBe(`use ${REDACTED} and ${REDACTED} please`);
    expect(text?.redacted).toBe(true);
    expect(JSON.stringify(fields)).not.toContain('abcdefghijklmnop');
  });

  it('cuts long text and keeps the whole for "Show all"', () => {
    const long = 'x'.repeat(PREVIEW_CHARS + 50);
    const [field] = previewPayload(JSON.stringify({ body: long }));
    expect(field?.truncated).toBe(true);
    expect(field?.value.length).toBe(PREVIEW_CHARS + 1);
    expect(field?.full).toBe(long);
  });

  it('shows arguments that are not an object as one field, redacted the same way', () => {
    expect(previewPayload('not json, Bearer abcdefghijklmnop')).toEqual([
      expect.objectContaining({ key: '', value: `not json, ${REDACTED}`, redacted: true }),
    ]);
  });

  it('drops empty fields and joins simple lists', () => {
    const fields = previewPayload(JSON.stringify({ to_recipients: ['a@x.com', 'b@x.com'], cc: null, subject: '' }));
    expect(fields).toEqual([expect.objectContaining({ key: 'to_recipients', value: 'a@x.com, b@x.com' })]);
  });
});

describe('sourceHref and toApprovalReason', () => {
  it('links a record to its PipesHub page and a live item to its own page; nothing else', () => {
    expect(sourceHref({ recordId: 'rec 1' })).toBe('/record/rec%201/');
    expect(sourceHref({ url: 'https://example.com/a' })).toBe('https://example.com/a');
    expect(sourceHref({ url: 'javascript:alert(1)' })).toBeNull();
    expect(sourceHref({})).toBeNull();
  });

  it('reads a reason and refuses what is not one', () => {
    expect(
      toApprovalReason({
        rule: 'flow_external_write',
        sensitivity: 'private',
        sources: [{ title: 'Plan', recordId: 'r1', extra: 1 }, 'bad'],
        moreSources: 2,
        destination: '#sales',
        conversationGrant: true,
      }),
    ).toEqual({
      rule: 'flow_external_write',
      sensitivity: 'private',
      sources: [{ title: 'Plan', recordId: 'r1' }],
      moreSources: 2,
      destination: '#sales',
      conversationGrant: true,
    });
    expect(toApprovalReason({ rule: 'x', sensitivity: 'secret' })).toBeNull();
    expect(toApprovalReason(null)).toBeNull();
  });
});

/** Every string a preview would put on screen, so a check cannot miss a field. */
const shown = (fields: ReturnType<typeof previewPayload>): string[] =>
  fields.flatMap((f) => [f.key, f.label, f.value, f.full]);

describe('QA-11 APR-26: the card masks every entry of the shared token vector', () => {
  it('QA11-7: every entry has an example its own pattern finds, compiled as the browser compiles it', () => {
    expect(VECTOR_ENTRIES.length).toBeGreaterThan(0);
    for (const entry of VECTOR_ENTRIES) {
      expect(entry.examples?.length ?? 0, `${entry.kind} has no example`).toBeGreaterThan(0);
    }
    for (const { kind, source, secret } of VECTOR_EXAMPLES) {
      expect(new RegExp(source).test(secret), kind).toBe(true);
    }
  });

  it('QA11-7: the generated module the card reads is the vector, entry for entry', () => {
    const generated = [...VENDOR_PATTERN_SOURCES, ...REDACT_ONLY_PATTERN_SOURCES].map((p) => [p.kind, p.source]);
    expect(generated).toEqual(VECTOR_ENTRIES.map((e) => [e.kind, e.source]));
    expect(REDACTED).toBe(VECTOR_MASK);
  });

  it.each(VECTOR_EXAMPLES.map((e) => [e.id, e.secret] as const))(
    'QA11-1: %s in free text is masked in the preview and in "Show all"',
    (_id, secret) => {
      const [field] = previewPayload(JSON.stringify({ text: `before ${secret} after` }));
      expect(field?.redacted).toBe(true);
      expect(field?.value).toBe(`before ${VECTOR_MASK} after`);
      expect(shown(previewPayload(JSON.stringify({ text: `${'x'.repeat(PREVIEW_CHARS)} ${secret}` }))).some((t) => t.includes(secret))).toBe(false);
    },
  );

  it.each(VECTOR_EXAMPLES.map((e) => [e.id, e.secret] as const))(
    'QA11-2: %s nested in lists and objects, as a key, or in arguments that are not JSON is masked',
    (_id, secret) => {
      const nested = JSON.stringify({ items: ['ok', secret], meta: { note: `see ${secret}` }, [secret]: 'value' });
      expect(shown(previewPayload(nested)).some((t) => t.includes(secret))).toBe(false);
      expect(redactedArgs(nested)).not.toContain(secret);
      expect(redactedArgs(nested)).toContain(VECTOR_MASK);
      const notJson = `not json { ${secret}`;
      expect(shown(previewPayload(notJson)).some((t) => t.includes(secret))).toBe(false);
      expect(redactedArgs(notJson)).toBe(`not json { ${VECTOR_MASK}`);
    },
  );
});
