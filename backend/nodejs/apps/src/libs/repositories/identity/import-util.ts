/** Small conversions the identity importers share (Mongo document values to column values). */
import { NeutralDoc, idText } from '../import';

export const str = (v: unknown): string | null =>
  typeof v === 'string' ? v : null;
export const bool = (v: unknown, dflt = false): boolean =>
  typeof v === 'boolean' ? v : dflt;
export const version = (d: NeutralDoc): number =>
  typeof d.__v === 'number' ? d.__v : 0;

/** The moment an ObjectId was made, for a document that never stored a creation time. */
export const oidTime = (hex: string): string =>
  new Date(parseInt(hex.slice(0, 8), 16) * 1000).toISOString();

/** `created_at` and `updated_at` columns are NOT NULL: a missing stamp falls back to the other one, then to the id's time. */
export function stamps(
  d: NeutralDoc,
  id: string,
): { created_at: unknown; updated_at: unknown } {
  const created = d.createdAt ?? d.updatedAt ?? oidTime(id);
  return { created_at: created, updated_at: d.updatedAt ?? created };
}

export function needId(d: NeutralDoc, what: string): string {
  const id = idText(d._id);
  if (!id) throw new Error(`${what} _id '${String(d._id)}' is not an ObjectId`);
  return id;
}

/** Addresses are stored in lower case. */
export const lowerEmail = (v: unknown): string | null =>
  typeof v === 'string' ? v.toLowerCase() : null;
