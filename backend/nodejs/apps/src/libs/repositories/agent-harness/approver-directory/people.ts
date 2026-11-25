/**
 * The org's users as the approver directory reads them, per document store: who is a live person of the org.
 */
export interface ApproverPeople {
  /** A live user of this org (a service account included); an id of another shape, org or a deleted user is not. */
  isLiveUser(orgId: string, userId: string): Promise<boolean>;
}
