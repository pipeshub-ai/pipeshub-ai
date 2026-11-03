import { isLongText, isTechnical, lastWord, type FieldSpec } from './schema-fields';

/** A call's fields arranged as the thing it makes: who it goes to, its title, its settings, its
 * other lists (labels, tags), its text. */
export type DocumentLayout = {
  recipients: FieldSpec[];
  headline: FieldSpec | null;
  pills: FieldSpec[];
  lists: FieldSpec[];
  body: FieldSpec[];
  more: FieldSpec[];
};

// Matched on the name's last word too, so `mail_to` and `mailCc` are recipients.
const RECIPIENT_WORD = new Set(['to', 'cc', 'bcc', 'recipients', 'attendees']);
const HEADLINE_NAME = /^(title|subject|summary|name)$/i;

const blank = (v: unknown) => v === undefined || v === null || v === '' || (Array.isArray(v) && v.length === 0);
const isRecipients = (f: FieldSpec) => f.control === 'chips' && (f.schema.items?.format === 'email' || RECIPIENT_WORD.has(lastWord(f.name)));
const isHeadline = (f: FieldSpec) => (f.control === 'text' || f.control === 'textarea') && HEADLINE_NAME.test(f.name);

/** One rule for every tool: each field lands in the first region it fits, in the order given. Ids
 * (`cloudId`, a uuid), format switches and nested values go to `more`, so they never crowd what the
 * person judges. */
export function documentLayout(fields: FieldSpec[], values?: Record<string, unknown>): DocumentLayout {
  const layout: DocumentLayout = { recipients: [], headline: null, pills: [], lists: [], body: [], more: [] };
  for (const field of fields) {
    const value = values?.[field.name];
    // Given the call's values, an optional field it left empty folds away too; a required one stays to be filled.
    if (values && !field.required && blank(value)) layout.more.push(field);
    else if (isRecipients(field)) layout.recipients.push(field);
    else if (isTechnical(field, value)) layout.more.push(field);
    else if (!layout.headline && isHeadline(field)) layout.headline = field;
    else if (isLongText(field)) layout.body.push(field);
    else if (field.control === 'json') layout.more.push(field);
    else if (field.control === 'chips') layout.lists.push(field);
    else layout.pills.push(field);
  }
  return layout;
}
