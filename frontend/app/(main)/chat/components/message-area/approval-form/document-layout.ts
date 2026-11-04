import { isLongText, isTechnical, lastWord, type FieldSpec } from './schema-fields';

/** A call's fields arranged as the thing it makes: who it goes to, its title, its short fields
 * (dates, places, names), its choices, its other lists (labels, tags), its text. */
export type DocumentLayout = {
  recipients: FieldSpec[];
  headline: FieldSpec | null;
  /** Short typed values, edited in place: a popover would hide what is being typed. */
  fields: FieldSpec[];
  /** Choices (a list or yes/no), set from a pill. */
  pills: FieldSpec[];
  lists: FieldSpec[];
  body: FieldSpec[];
  more: FieldSpec[];
};

// Matched on the name's last word too, so `mail_to` and `mailCc` are recipients.
const RECIPIENT_WORD = new Set(['to', 'cc', 'bcc', 'recipients', 'attendees']);
// The last word too, so `mail_subject`, `event_title` and `page_title` are the title; `name` only
// whole, since `assignee_name` or `file_name` are not.
const HEADLINE_WORD = new Set(['title', 'subject', 'summary', 'topic']);
const HEADLINE_NAME = /^(name|display_?name)$/i;

const blank = (v: unknown) => v === undefined || v === null || v === '' || (Array.isArray(v) && v.length === 0);
const isRecipients = (f: FieldSpec) => f.control === 'chips' && (f.schema.items?.format === 'email' || RECIPIENT_WORD.has(lastWord(f.name)));
const isHeadline = (f: FieldSpec) => (f.control === 'text' || f.control === 'textarea') && (HEADLINE_WORD.has(lastWord(f.name)) || HEADLINE_NAME.test(f.name));
const isChoice = (f: FieldSpec) => f.control === 'select' || f.control === 'switch';

/** One rule for every tool: each field lands in the first region it fits, in the order given. Ids
 * (`cloudId`, a uuid), format switches and nested values go to `more`, so they never crowd what the
 * person judges. */
export function documentLayout(fields: FieldSpec[], values?: Record<string, unknown>): DocumentLayout {
  const layout: DocumentLayout = { recipients: [], headline: null, fields: [], pills: [], lists: [], body: [], more: [] };
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
    else if (isChoice(field)) layout.pills.push(field);
    else layout.fields.push(field);
  }
  return layout;
}
