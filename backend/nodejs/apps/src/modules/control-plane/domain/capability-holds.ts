export type HoldReason =
  'unknown_toolset_type' | 'type_keyed_toolset' | 'invalid_object';

/** Something the plan will not write, and why: reported, never guessed. */
export interface Hold {
  readonly reason: HoldReason;
  readonly object: string;
}
