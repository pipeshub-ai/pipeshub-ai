/**
 * How an approval card reads a tool call: labels, which field is the
 * headline, the body, the metadata, and which are ids. The web card applies
 * the same rules (frontend `approval-form/schema-fields.ts`,
 * `document-layout.ts` and `tool-approval.ts`); a change here goes there too,
 * and both test the same examples.
 */

interface FieldSchema {
  type?: string | string[];
  title?: string;
  format?: string;
  maxLength?: number;
  items?: FieldSchema;
  anyOf?: FieldSchema[];
  oneOf?: FieldSchema[];
}

interface ObjectSchema {
  properties?: Record<string, FieldSchema>;
  required?: string[];
}

export interface ApprovalField {
  name: string;
  label: string;
  value: unknown;
}

/** A call's set fields by the region of the card they belong in. */
export interface ApprovalLayout {
  recipients: ApprovalField[];
  headline: ApprovalField | null;
  body: ApprovalField[];
  meta: ApprovalField[];
  /** Ids and nested values: still shown, under Details. */
  details: ApprovalField[];
}

const INITIALISM: Record<string, string> = {
  id: 'ID',
  ids: 'IDs',
  url: 'URL',
  urls: 'URLs',
  uuid: 'UUID',
  html: 'HTML',
  api: 'API',
};
const PLUMBING_WORD = new Set(['name', 'key']);
const WORD_SPLIT = /[_\-\s.]+|(?<=[a-z0-9])(?=[A-Z])/;

const words = (name: string): string[] =>
  name.split(WORD_SPLIT).filter(Boolean).map((w) => w.toLowerCase());

/** The name's last word, so `email_body` and `issueDescription` match but `message_id` doesn't. */
const lastWord = (name: string): string => words(name).pop() ?? '';

/** A field's label: the schema's title, else its name in sentence case ("issueTypeName" is "Issue type"). */
export function fieldLabel(name: string, title?: string | null): string {
  if (title?.trim()) return title.trim();
  const parts = words(name);
  if (parts.length > 1 && PLUMBING_WORD.has(parts[parts.length - 1]!)) parts.pop();
  if (parts.length === 0) return name;
  const text = parts.map((w) => INITIALISM[w] ?? w).join(' ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}

// shortcut: a fixed list of products a hub server (Atlassian's covers Jira and Confluence) can act in;
// move to the server's own metadata once MCP servers describe their products.
const PRODUCTS = ['Jira', 'Confluence', 'Slack', 'Gmail', 'Outlook', 'Notion', 'GitHub', 'GitLab', 'Asana', 'Trello', 'Zendesk', 'Salesforce', 'HubSpot', 'SharePoint', 'OneDrive', 'Dropbox', 'Figma', 'Linear'];

/** Where a call acts, as a person names it: the product its tool names ("createJiraIssue" on "Atlassian Rovo" is Jira), else the server. */
export function approvalProduct(toolName: string, serverName: string): string {
  const toolWords = new Set(words(toolName));
  const product = PRODUCTS.find((p) => toolWords.has(p.toLowerCase()));
  return product && !serverName.toLowerCase().includes(product.toLowerCase())
    ? product
    : serverName;
}

/** The tool's name made readable when it has no title: `create_issue` is "Create issue". */
export function toolLabel(toolName: string, title?: string | null): string {
  if (title?.trim()) return title.trim();
  const parts = toolName
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .split(/[_\-\s./]+/)
    .filter(Boolean);
  if (parts.length === 0) return toolName;
  return parts
    .map((w, i) => (i === 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w))
    .join(' ');
}

function unwrapNullable(schema: FieldSchema): FieldSchema {
  const union = schema.anyOf ?? schema.oneOf;
  const rest = union?.filter((s) => s.type !== 'null');
  return union && rest?.length === 1 && union.length === 2
    ? { ...rest[0], title: schema.title ?? rest[0]!.title }
    : schema;
}

const LONG_TEXT_WORD = new Set(['body', 'description', 'content', 'message', 'text', 'comment', 'markdown', 'html']);
const LONG_TEXT_FORMAT = new Set(['markdown', 'html', 'textarea']);
const RECIPIENT_WORD = new Set(['to', 'cc', 'bcc', 'recipients', 'attendees']);
const HEADLINE_NAME = /^(title|subject|summary|name)$/i;
const ID_WORD = new Set(['id', 'ids', 'uuid', 'guid']);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const blank = (v: unknown): boolean =>
  v === undefined || v === null || v === '' || (Array.isArray(v) && v.length === 0);
const isText = (v: unknown): v is string => typeof v === 'string';
const isTextList = (v: unknown): boolean => Array.isArray(v) && v.every(isText);

/** Every set argument of a call, laid out by the same rule as the web card. */
export function approvalLayout(
  args: Record<string, unknown>,
  inputSchema: unknown,
): ApprovalLayout {
  const schema = (inputSchema && typeof inputSchema === 'object' ? inputSchema : {}) as ObjectSchema;
  const properties = schema.properties ?? {};
  const required = new Set(schema.required ?? []);
  const described = Object.keys(properties);
  // Required ones first, then the schema's order, then what the schema leaves out: nothing is dropped.
  const names = [
    ...described.filter((n) => required.has(n)),
    ...described.filter((n) => !required.has(n)),
    ...Object.keys(args).filter((n) => !(n in properties)),
  ];
  const layout: ApprovalLayout = { recipients: [], headline: null, body: [], meta: [], details: [] };
  for (const name of names) {
    const value = args[name];
    if (blank(value)) continue;
    const prop = unwrapNullable(properties[name] ?? {});
    const field = { name, label: fieldLabel(name, prop.title), value };
    const word = lastWord(name);
    if (isTextList(value) && (prop.items?.format === 'email' || RECIPIENT_WORD.has(word))) layout.recipients.push(field);
    else if (ID_WORD.has(word) || prop.format === 'uuid' || (isText(value) && UUID.test(value.trim()))) layout.details.push(field);
    else if (!layout.headline && isText(value) && HEADLINE_NAME.test(name)) layout.headline = field;
    else if (isText(value) && (LONG_TEXT_FORMAT.has(prop.format ?? '') || (prop.maxLength ?? 0) > 200 || LONG_TEXT_WORD.has(word))) layout.body.push(field);
    else if (value !== null && typeof value === 'object' && !isTextList(value)) layout.details.push(field);
    else layout.meta.push(field);
  }
  return layout;
}

const TIME_ARG_NAME = /(^|[_\-.])(at|ts|time|timestamp|date|datetime)$|^(ts|timestamp)$|(At|Time|Timestamp|Date)$/;

/** A Unix time under a time-like name, as epoch seconds; null otherwise. */
export function epochSeconds(name: string, value: unknown): number | null {
  if (!TIME_ARG_NAME.test(name)) return null;
  const n =
    typeof value === 'number'
      ? value
      : typeof value === 'string' && /^\d{9,13}(\.\d+)?$/.test(value.trim())
        ? Number(value)
        : NaN;
  if (n >= 1e12 && n < 1e13) return Math.floor(n / 1000);
  if (n >= 1e9 && n < 1e10) return Math.floor(n);
  return null;
}

// A list item that reads the same in a comma list, so `['a, b']` and `['a', 'b']` can't look alike.
const plainListItem = (item: unknown): boolean =>
  typeof item === 'string' && item !== '' && !item.includes(',') && item.trim() === item;

/** A value as text: plain lists as a comma list, booleans as Yes or No, anything else as JSON. */
export function valueText(value: unknown): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'number') return String(value);
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (Array.isArray(value) && value.length > 0 && value.every(plainListItem)) return value.join(', ');
  return JSON.stringify(value) ?? String(value);
}
