export interface ConnectorProgress {
  connectorId: string;
  connectorName: string;
  total: number;
  pending: number;
  done: number;
  failed: number;
  skipped: number;
  percentage: number;
}

export interface ProgressSnapshot {
  orgId: string;
  status: 'indexing' | 'paused'; // derived from the indexer heartbeat
  phase: 'active' | 'complete';
  total: number;
  pending: number;
  done: number;
  failed: number;
  skipped: number;
  percentage: number; // (done + failed + skipped) / total * 100
  etaSeconds: number | null; // null when paused / rate<=0 / warming up
  connectors: ConnectorProgress[]; // counts only — no per-connector ETA
}
