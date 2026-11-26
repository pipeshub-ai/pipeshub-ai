/**
 * The org policy revision an admin editor loaded (`ETag`) and saves from
 * (`If-Match`, CPD-4 / QA-9). Null when the settings are not backed by the
 * policy store (`CONTROL_PLANE_POLICY_STORE=off`): saves are then last-writer-wins.
 */
export interface PolicyRevisions {
  revision(orgId: string): Promise<number | null>;
}
