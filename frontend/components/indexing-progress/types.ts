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
  status: 'indexing' | 'paused';
  phase: 'active' | 'complete';
  total: number;
  pending: number;
  done: number;
  failed: number;
  skipped: number;
  percentage: number;
  etaSeconds: number | null;
  connectors: ConnectorProgress[];
}
