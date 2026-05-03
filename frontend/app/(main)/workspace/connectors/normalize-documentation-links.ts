import type { DocumentationLink } from '@/app/(main)/workspace/connectors/types';

const DOC_LINK_TYPES: DocumentationLink['type'][] = ['setup', 'api', 'connector', 'pipeshub'];

/**
 * Coerce API/registry documentation link payloads into {@link DocumentationLink}.
 * Accepts `type` or legacy `doc_type`; maps unknown values (e.g. `reference`) to `setup`.
 */
export function normalizeDocumentationLinks(v: unknown): DocumentationLink[] {
  if (!Array.isArray(v) || v.length === 0) return [];
  const out: DocumentationLink[] = [];
  for (const item of v) {
    if (!item || typeof item !== 'object') continue;
    const o = item as Record<string, unknown>;
    const title = o.title;
    const url = o.url;
    if (typeof title !== 'string' || typeof url !== 'string') continue;
    const rawType = o.type ?? o.doc_type;
    let typeStr = typeof rawType === 'string' ? rawType.toLowerCase() : 'setup';
    if (typeStr === 'reference') typeStr = 'setup';
    const type = (DOC_LINK_TYPES.includes(typeStr as DocumentationLink['type'])
      ? typeStr
      : 'setup') as DocumentationLink['type'];
    out.push({ title, url, type });
  }
  return out;
}
