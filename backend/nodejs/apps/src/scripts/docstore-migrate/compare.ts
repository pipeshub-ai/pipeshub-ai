import { jsonDiff } from '../../libs/repositories/json-compare';
import { Doc, docHash } from './canonical';

export interface DocDiff {
  id: string;
  kind: 'missing_in_pg' | 'extra_in_pg' | 'differs';
  detail: string[];
}

/** Source documents (prepared) against decoded ones, by `_id`. */
export function compareDocs(source: Doc[], decoded: Doc[]): DocDiff[] {
  const out: DocDiff[] = [];
  const pg = new Map(decoded.map((d) => [String(d._id), d]));
  for (const s of source) {
    const id = String(s._id);
    const d = pg.get(id);
    pg.delete(id);
    if (!d) {
      out.push({ id, kind: 'missing_in_pg', detail: [] });
      continue;
    }
    if (docHash(s) !== docHash(d))
      out.push({ id, kind: 'differs', detail: jsonDiff(s, d).slice(0, 12) });
  }
  for (const id of pg.keys()) out.push({ id, kind: 'extra_in_pg', detail: [] });
  return out;
}
