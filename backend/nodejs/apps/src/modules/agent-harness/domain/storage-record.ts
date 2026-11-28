/**
 * ADR-RUN-01 D2 (WP-98): a workspace revision records the references the engine fetched (repositories `ph git clone`
 * checked out) by address and version, never by their files. The worker writes the record
 * (`harness_core/workspace/sync/storage_classes.py`); the person's file views list its entries as references and
 * never list the record as a file. `contracts/harness/vectors/storage-record.json` pins both sides.
 */
export const STORAGE_RECORD_DIR = '.pipeshub';
export const STORAGE_RECORD_PATH = `${STORAGE_RECORD_DIR}/references.json`;
export const STORAGE_RECORD_SCHEMA = 1;
/** A record past these is read as holding no references (a correct worker never writes one). */
export const MAX_RECORD_BYTES = 64 * 1024;
export const MAX_RECORD_REFERENCES = 64;

/** A fetched copy as the person sees it: "onyx-dot-app/onyx at 57cb7b8, fetched copy, not saved". */
export interface WorkspaceReference {
  path: string;
  label: string;
  address: string;
  version?: string;
}

export function isStorageRecordPath(path: string): boolean {
  return (
    path === STORAGE_RECORD_DIR || path.startsWith(`${STORAGE_RECORD_DIR}/`)
  );
}

/** `onyx-dot-app/onyx`: the address without its scheme and host (Python `Reference.label`). */
export function referenceLabel(address: string): string {
  const scheme = address.indexOf('://');
  const rest = scheme < 0 ? address : address.slice(scheme + 3);
  const slash = rest.indexOf('/');
  const path = slash < 0 ? '' : rest.slice(slash + 1);
  const label = path.replace(/\.git$/, '').replace(/^\/+|\/+$/g, '');
  return label !== '' ? label : rest;
}

/** The references a record holds, by path; none for a record this version cannot read. */
export function decodeStorageRecord(bytes: Buffer): WorkspaceReference[] {
  if (bytes.length > MAX_RECORD_BYTES) return [];
  let document: unknown;
  try {
    document = JSON.parse(bytes.toString('utf8'));
  } catch {
    return [];
  }
  if (typeof document !== 'object' || document === null) return [];
  const body = document as { schema?: unknown; references?: unknown };
  if (
    body.schema !== STORAGE_RECORD_SCHEMA ||
    !Array.isArray(body.references)
  ) {
    return [];
  }
  const byPath = new Map<string, WorkspaceReference>();
  for (const raw of body.references.slice(0, MAX_RECORD_REFERENCES)) {
    if (typeof raw !== 'object' || raw === null) continue;
    const entry = raw as Record<string, unknown>;
    const { path, address, version } = entry;
    if (typeof path !== 'string' || path === '') continue;
    if (typeof address !== 'string' || !address.startsWith('https://'))
      continue;
    byPath.set(path, {
      path,
      label: referenceLabel(address),
      address,
      ...(typeof version === 'string' && version !== '' ? { version } : {}),
    });
  }
  return [...byPath.values()].sort((a, b) =>
    a.path < b.path ? -1 : a.path > b.path ? 1 : 0,
  );
}
