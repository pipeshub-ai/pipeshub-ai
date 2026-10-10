export interface DecisionLogEntry {
  /** User id only; no names, emails or message content. */
  subject: string;
  action: string;
  resource: string;
  decision: 'allow' | 'deny';
  role: string;
  /** `type:ref` per granting path, e.g. `team:<teamId>`. */
  via: readonly string[];
  aclVersion: number | undefined;
  requestId: string;
  /** Contract error code on a deny. */
  code?: string;
}

export interface IDecisionLog {
  record(entry: DecisionLogEntry): void;
}

export interface DecisionLogSink {
  write(entry: DecisionLogEntry): void;
}

export const NOOP_DECISION_LOG: IDecisionLog = { record: () => undefined };

/** ADR-001: every deny is logged, allows are sampled. A failing sink never fails the request. */
export class SampledDecisionLog implements IDecisionLog {
  constructor(
    private readonly sink: DecisionLogSink,
    private readonly allowSampleRate: number,
    private readonly random: () => number = Math.random,
  ) {}

  record(entry: DecisionLogEntry): void {
    if (entry.decision === 'allow' && this.random() >= this.allowSampleRate) {
      return;
    }
    try {
      this.sink.write(entry);
    } catch {
      // Audit output is best-effort; authorization must not depend on it.
    }
  }
}
