import { createHmac } from 'node:crypto';

/**
 * Signed grant for the records and storage documents of one knowledge-base upload.
 *
 * The connector service never trusts a storage document id from a request
 * body. The gateway is the only party that creates storage documents; once it
 * has, it signs the ids it created together with the org, user, KB and folder
 * they were created for, and sends the token next to the files. The connector
 * takes every storage pointer and the record key from the grant, never from the body.
 *
 * Wire form: `<b64url(claims JSON)>.<b64url(HMAC-SHA256(key, <b64url(claims JSON)>))>`
 * with key = HMAC-SHA256(scopedJwtSecret, GRANT_CONTEXT): the existing service
 * secret, domain-separated so a grant is never a service token. The verifier is
 * `app/connectors/sources/localKB/upload_grant.py`; keep the two in step (the
 * shared vector is pinned in both test suites).
 */
export const KB_UPLOAD_GRANT_CONTEXT = 'pipeshub/kb-upload-grant/v1';
export const KB_UPLOAD_GRANT_VERSION = 1;
export const KB_UPLOAD_GRANT_TTL_SECONDS = 300;

export interface KbUploadGrantScope {
  /** The shared scoped-JWT secret (never logged). */
  secret: string;
  userId: string;
  /** The destination folder; absent for the KB root. */
  folderId?: string;
}

export interface KbUploadGrantInput {
  secret: string;
  orgId: string;
  userId: string;
  kbId: string;
  folderId?: string | null;
  /** One entry per file: the record key the gateway minted and the storage document it created, the MIME type it detected and the extension it computed from the name ("" for none). */
  documents: {
    recordKey: string;
    documentId: string;
    mimeType: string;
    extension: string;
  }[];
  /** Seconds since the epoch; defaults to now. */
  now?: number;
  ttlSeconds?: number;
}

const b64url = (data: Buffer | string): string =>
  Buffer.from(data).toString('base64url');

export function deriveKbUploadGrantKey(secret: string): Buffer {
  // An HMAC keyed with '' is a public constant, so anyone could sign with it.
  if (!secret) {
    throw new Error('Scoped JWT secret is not configured');
  }
  return createHmac('sha256', secret).update(KB_UPLOAD_GRANT_CONTEXT).digest();
}

export function signKbUploadGrant(input: KbUploadGrantInput): string {
  const { secret, orgId, userId, kbId, documents } = input;
  if (!orgId || !userId || !kbId) {
    throw new Error('A KB upload grant needs an org, a user and a KB');
  }
  if (
    documents.length === 0 ||
    documents.some((d) => !d.recordKey || !d.documentId || !d.mimeType) ||
    new Set(documents.map((d) => d.recordKey)).size !== documents.length ||
    new Set(documents.map((d) => d.documentId)).size !== documents.length
  ) {
    throw new Error(
      'A KB upload grant needs one record key, storage document and MIME type per file',
    );
  }
  const iat = input.now ?? Math.floor(Date.now() / 1000);
  // Keys in alphabetical order, so the bytes are the same as the Python signer's.
  const claims = {
    documents: documents
      .map((d): [string, string, string, string] => [
        d.recordKey,
        d.documentId,
        d.mimeType,
        d.extension,
      ])
      .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0)),
    exp: iat + (input.ttlSeconds ?? KB_UPLOAD_GRANT_TTL_SECONDS),
    folderId: input.folderId ?? '',
    iat,
    kbId,
    orgId,
    userId,
    v: KB_UPLOAD_GRANT_VERSION,
  };
  const body = b64url(JSON.stringify(claims));
  const mac = createHmac('sha256', deriveKbUploadGrantKey(secret))
    .update(body)
    .digest();
  return `${body}.${b64url(mac)}`;
}
