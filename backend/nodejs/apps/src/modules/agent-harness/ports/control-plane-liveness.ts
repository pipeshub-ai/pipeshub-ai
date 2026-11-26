/** One sweeper tick's view of when the control plane was last seen alive. */
export interface LivenessObservation {
  /** Store (DB) time of this observation, epoch ms. */
  now: number;
  /** When a sweeper last observed, on the same clock; null on the first ever tick. */
  previousSeenAt: number | null;
  /** Until when expiry is held off; 0 when never. */
  holdoffUntil: number;
}

/**
 * Shared across replicas, so a holdoff survives a leader change and a normal
 * handover between replicas is not mistaken for an outage.
 */
export interface ControlPlaneLiveness {
  /** Records "seen now" and returns what was recorded before. */
  observe(): Promise<LivenessObservation>;
  /** Extends the holdoff to at least `until` (store time). */
  holdOff(until: number): Promise<void>;
}
