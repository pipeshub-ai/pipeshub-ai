/**
 * Where an org's runs may be placed, asked at admission before anything is
 * written (OWD-12): an extension point, bound to `AGENT_HARNESS_RUN_PLACEMENT`.
 * Unbound, every run is admitted here.
 */
export interface RunPlacement {
  /** Where the org's runs are placed; throws `HarnessConflictError('RUN_PLACEMENT_REFUSED')` when they may not be placed here. */
  place(orgId: string): Promise<string>;
}
