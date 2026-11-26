/* eslint-disable @typescript-eslint/require-await -- This deterministic adapter implements the asynchronous store port without I/O. */
import { StoreClock, systemStoreClock } from '../domain/execution';
import {
  ControlPlaneLiveness,
  LivenessObservation,
} from '../ports/control-plane-liveness';

export class InMemoryControlPlaneLiveness implements ControlPlaneLiveness {
  private seenAt: number | null = null;
  private holdoffUntil = 0;

  constructor(private readonly clock: StoreClock = systemStoreClock) {}

  async observe(): Promise<LivenessObservation> {
    const now = this.clock.now();
    const observation = {
      now,
      previousSeenAt: this.seenAt,
      holdoffUntil: this.holdoffUntil,
    };
    this.seenAt = now;
    return observation;
  }

  async holdOff(until: number): Promise<void> {
    this.holdoffUntil = Math.max(this.holdoffUntil, until);
  }
}
