/** A tool's small view of its result (Python `result_view.py`): a table of records or one
 * record's fields. It comes from a tool's output, so nothing in it is trusted: anything not
 * shaped like a view is dropped, strings are capped and links must be plain http(s). */
export type ToolResultView =
  | { kind: 'records'; columns: string[]; rows: Array<{ cells: string[]; url?: string }>; total: number }
  | { kind: 'fields'; fields: Array<{ label: string; value: string }>; url?: string };

const MAX_ROWS = 20;
const MAX_COLUMNS = 5;
const MAX_FIELDS = 12;
const MAX_CHARS = 300;

const text = (value: unknown): string => (typeof value === 'string' ? value.slice(0, MAX_CHARS) : '');

/** An absolute http(s) link with a host and no user-info, else undefined. */
export function safeLink(value: unknown): string | undefined {
  if (typeof value !== 'string' || value.length > 2000) return undefined;
  try {
    const url = new URL(value);
    if ((url.protocol !== 'http:' && url.protocol !== 'https:') || !url.hostname || url.username || url.password) {
      return undefined;
    }
    return url.href;
  } catch {
    return undefined;
  }
}

export function toolResultView(value: unknown): ToolResultView | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const raw = value as Record<string, unknown>;
  if (raw.kind === 'records') {
    if (!Array.isArray(raw.columns) || !Array.isArray(raw.rows)) return null;
    const columns = raw.columns.slice(0, MAX_COLUMNS).map(text);
    if (columns.length === 0) return null;
    const rows = raw.rows.slice(0, MAX_ROWS).flatMap((row) => {
      if (!row || typeof row !== 'object' || !Array.isArray((row as { cells?: unknown }).cells)) return [];
      const cells = (row as { cells: unknown[] }).cells.slice(0, columns.length).map(text);
      while (cells.length < columns.length) cells.push('');
      const url = safeLink((row as { url?: unknown }).url);
      return [{ cells, ...(url ? { url } : {}) }];
    });
    if (rows.length === 0) return null;
    const total = typeof raw.total === 'number' && Number.isFinite(raw.total) ? Math.max(raw.total, rows.length) : rows.length;
    return { kind: 'records', columns, rows, total };
  }
  if (raw.kind === 'fields') {
    if (!Array.isArray(raw.fields)) return null;
    const fields = raw.fields.slice(0, MAX_FIELDS).flatMap((field) => {
      if (!field || typeof field !== 'object') return [];
      const label = text((field as { label?: unknown }).label);
      const shown = text((field as { value?: unknown }).value);
      return label ? [{ label, value: shown }] : [];
    });
    if (fields.length === 0) return null;
    const url = safeLink(raw.url);
    return { kind: 'fields', fields, ...(url ? { url } : {}) };
  }
  return null;
}

/** Whether a result preview is JSON (or the start of it) rather than prose. */
export function looksLikeJson(preview: string): boolean {
  const start = preview.trimStart()[0];
  return start === '{' || start === '[';
}
