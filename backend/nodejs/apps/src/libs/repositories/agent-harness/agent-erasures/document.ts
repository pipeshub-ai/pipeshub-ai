/** An agent erasure's document as every store keeps it: no driver. */
export interface AgentErasureDocument {
  /** `orgId:agentKey`: one pending erasure per agent, however often it is asked for. */
  _id: string;
  orgId: string;
  agentKey: string;
  /** Absent on H4's entries, which were all written after the delete: they read as confirmed. */
  confirmed?: boolean;
  failures: number;
  requestedAt: number;
  nextAttemptAt: number;
  lastErrorType: string | null;
}
