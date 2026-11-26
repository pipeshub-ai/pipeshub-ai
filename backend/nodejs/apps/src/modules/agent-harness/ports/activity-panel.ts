/**
 * Whether an org shows its people the activity panel (UX5). Since CTL-5 it
 * comes with the agent harness (the Labs switch is retired); the platform
 * adapter always answers yes.
 */
export interface ActivityPanelSetting {
  enabled(orgId: string): Promise<boolean>;
}
