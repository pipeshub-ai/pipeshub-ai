import type { PolicyLayer } from './policy-alias';

/** A layer above the one being saved: the deployment's, and for an agent's layer also its org's. */
export interface LayerAbove {
  readonly layer: 'deployment' | 'org';
  /** Null: that scope has no layer yet. */
  readonly policy: PolicyLayer | null;
}

/**
 * The editor context and save check of a policy section an edition offers,
 * through `CONTROL_PLANE_POLICY_SECTION_RULES`. What the rule reads from the
 * layers above is added to the resolver's ceilings and locks for the section's
 * dimensions, if it has any.
 */
export interface PolicySectionRule {
  readonly section: string;
  /** Only the org's layer holds it; an agent's may not set it. Absent: any scope the section's paths allow. */
  readonly orgOnly?: boolean;
  /** Only the org's admins read or change it, whatever a delegation or an agent's edit rights say. */
  readonly adminOnly?: boolean;
  /** The org layer's value travels in the org's live layer, as it is, for the workers and gateways that apply it. */
  readonly live?: boolean;
  /** What the editor shows beside the section: ceilings by dimension path, and the paths fixed above. */
  context(above: readonly LayerAbove[]): {
    ceilings: Record<string, unknown>;
    locked: readonly string[];
  };
  /** Throws a `CpRefusal` when `value` may not be saved at the scope under the layers above. */
  check(
    scope: 'org' | 'agent',
    value: unknown,
    above: readonly LayerAbove[],
  ): void;
}

/**
 * The members of the policy layer as this repository's schema writes them
 * (`policy-layer.schema.json`). An edition's contract overlay may add sections
 * beside them (`x-extensible`); each such section is held only through the
 * edition's `PolicySectionRule`: without one it is refused (`CP_NOT_EXPOSED`),
 * so nothing is stored unchecked, and an agent exports none.
 */
export const BUILT_IN_LAYER_MEMBERS: ReadonlySet<string> = new Set([
  'schemaVersion',
  'scope',
  'engineProfile',
  'approvals',
  'models',
  'budgets',
  'rollout',
  'operator',
  'memory',
  'retention',
  'locks',
  'lockedBy',
  'updated',
  'catalog',
  'toolDisclosure',
  'snapshots',
  'triggers',
  'websites',
  'repositories',
  'quotas',
  'toolResults',
  'interactions',
  'recovery',
  'sandbox',
  'ui',
  'access',
]);

/**
 * Dimensions of a section this build holds that only an edition may let an
 * admin set (`CONTROL_PLANE_POLICY_EDITION_DIMENSIONS`): without it a save
 * changing one is refused (`CP_NOT_EXPOSED`) and the editor is given no
 * ceiling for it, so it shows no field. What a layer already holds still
 * resolves. `interactions.waitS`: how long a run waits for a person, whose
 * default is the deployment's (`HARNESS_WAIT_FOR_PERSON_DAYS`).
 */
export const EDITION_DIMENSIONS: ReadonlySet<string> = new Set([
  'interactions.waitS',
]);
