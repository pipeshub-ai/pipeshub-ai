/**
 * The titles of an org's chats, for an admin page that names chats its
 * records hold by id (AUDITUI). A chat that is deleted or erased, or not this
 * org's, is absent from the answer; one without a title maps to ''.
 */
export interface ChatTitleReader {
  titles(
    orgId: string,
    sessionIds: readonly string[],
  ): Promise<Map<string, string>>;
}
