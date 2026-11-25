/**
 * Stores this build does not know of, erased with a person, an org or a
 * conversation: an extension point. Bound at `DATA_ERASER_EXTENSION` in the
 * harness container (a conversation's erasure job, step `extension_stores`)
 * and in the user-management container (user, service-account and org
 * deletion). Unbound: `noDataEraserExtension`, which erases nothing. Each
 * method is idempotent and returns how many items it removed.
 */
export interface DataEraserExtension {
  eraseUser(orgId: string, userId: string): Promise<number>;
  eraseOrg(orgId: string): Promise<number>;
  eraseSession(orgId: string, sessionId: string): Promise<number>;
}

export const DATA_ERASER_EXTENSION = 'DataEraserExtension';

export const noDataEraserExtension: DataEraserExtension = Object.freeze({
  eraseUser: () => Promise.resolve(0),
  eraseOrg: () => Promise.resolve(0),
  eraseSession: () => Promise.resolve(0),
});
