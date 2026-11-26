/**
 * Payload names of the two document stores (R46-CARRY, ADR-DOCMIG-01 decision 6): `<store>:<org>:<hash>`, where the row behind
 * the name is `<org>:<hash>` in both. Either store reads a name of either form for its org, so a payload the document-store move
 * carried is read by the name it was written under.
 *
 * The move does not rename the payloads it carries. Names sit where it cannot change them: inside sealed journal data and sealed
 * payloads (opened only with the org's keys, which the copy never unwraps), and inside event data whose digest the audit chain
 * holds (`dataDigest`, so a renamed ref would no longer match the record a verifier cross-checks). A rewrite would therefore leave
 * some names of the old form whatever it did, and break the audit cross-check where it reached; reading both forms needs neither.
 */
const DOCUMENT_STORE_PREFIXES = ['mongo:', 'pg:'] as const;

/** The row id behind a document-store payload name of this org (either form); null for another store's or org's name. */
export function payloadRowId(orgId: string, ref: string): string | null {
  for (const prefix of DOCUMENT_STORE_PREFIXES)
    if (
      ref.startsWith(`${prefix}${orgId}:`) &&
      ref.length > prefix.length + orgId.length + 1
    )
      return ref.slice(prefix.length);
  return null;
}
