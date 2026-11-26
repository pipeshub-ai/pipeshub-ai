/**
 * Whether an org lets collaborators send turns into conversations shared with
 * them (SS2). Since CTL-5 it comes with the agent harness (the Labs switch is
 * retired); the platform adapter always answers yes.
 */
export interface CollaboratorTurnsSetting {
  enabled(orgId: string): Promise<boolean>;
}
