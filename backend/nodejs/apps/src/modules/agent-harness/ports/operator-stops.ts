import type { OperatorControl } from '../domain/operator-controls';

/**
 * The operator stops in force (ADR-OPS-01), as admission, child runs, engine
 * eligibility and the sweeper read them: an extension point. The built-in
 * source holds only the deployment's env switch (`AGENT_HARNESS_KILL_SWITCH`);
 * a binding made before the built-in binder supplies more.
 */
export interface OperatorStops {
  /** Every control in force, the deployment's env switch included. */
  controls(): Promise<OperatorControl[]>;
  /** A session or agent stop admission refuses a turn for (the narrowest), or null. */
  admissionStop(turn: {
    orgId: string;
    sessionId: string;
    agentKey?: string | null;
  }): Promise<OperatorControl | null>;
  /** `kind@version` of every engine version stopped for `orgId`. */
  stoppedEngines(orgId: string): Promise<ReadonlySet<string>>;
  egressStopped(orgId: string): Promise<boolean>;
  /** The sweeper's pass: applies every control in force to the open runs it covers; how many it controlled. */
  enforceAll(): Promise<number>;
}

/** Whether an operator stops every harness turn of the org (an org or instance stop), as the rollout reads it. */
export type OrgStopReader = (orgId: string) => Promise<boolean>;
