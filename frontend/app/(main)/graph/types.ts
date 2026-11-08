// Wire types of POST /api/v1/graph/expand and /revalidate (pipeshub-openapi.yaml, "Code Graph").

export type GraphSource = 'code';
export type Direction = 'inbound' | 'outbound' | 'any';

/** `block:<key>` for a code symbol. Ids are opaque to the client; only the prefix is ever read. */
export type NodeId = string;

export interface GraphNode {
  id: NodeId;
  kind?: string;
  label?: string;
  recordId?: string;
  file?: string;
  line?: number;
}

export interface GraphEdge {
  id: string;
  from: NodeId;
  to: NodeId;
  type: string;
  /** EXTRACTED is drawn solid, INFERRED dashed. */
  confidence?: string;
  line?: number;
}

export type GraphStatus =
  | 'never_built'
  | 'building'
  | 'ready'
  | 'stale'
  | 'waiting_on_indexing'
  | 'retrying'
  | 'failed'
  | 'too_large'
  | 'unknown';

export interface ExpandRequest {
  source: GraphSource;
  /** 1 to 20 anchors. */
  nodeIds: NodeId[];
  direction?: Direction;
  edgeTypes?: string[];
  includeTests?: boolean;
  /** 1 to 100; at most this many new nodes come back. */
  limit?: number;
  /** `page.nextCursor` of the previous answer, with every other field unchanged. */
  cursor?: string;
}

export interface ExpandResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** `total` is exact only when `page.truncated` is false. */
  anchors: { id: NodeId; total: number }[];
  page: { truncated: boolean; incomplete: boolean; nextCursor?: string };
  graphStatus: GraphStatus;
}

export interface RevalidateRequest {
  source: GraphSource;
  /** 1 to 5,000. Send includeTests as the view shows it, or shown test nodes read as revoked. */
  nodeIds: NodeId[];
  includeTests?: boolean;
}

export interface RevalidateResponse {
  visible: NodeId[];
}

/** Turns an address (a record, or a repo and a file path, and optionally a symbol) into a node id. */
export interface ResolveRequest {
  source: GraphSource;
  /** Needs recordId, or repo and filePath. */
  recordId?: string;
  repo?: string;
  filePath?: string;
  /** Without it, the file's first definition. */
  qualifiedName?: string;
  /** A hit in test code needs this true. */
  includeTests?: boolean;
}

export interface ResolveResponse {
  nodeId: NodeId;
}

/** A repository the caller can read (GET /api/v1/code-graph/repos). */
export interface CodeRepoInfo {
  repo: string;
  source: string;
  connector_id: string;
  graph_status: GraphStatus;
}

/** One identifier-search hit (POST /api/v1/code-graph/search, mode identifier). */
export interface SearchMatch {
  file_path: string;
  qualified_name: string;
  kind?: string;
  repo?: string;
  start_line?: number;
}

export interface SearchRequest {
  repo: string;
  pattern: string;
  include_tests?: boolean;
  limit?: number;
}

export const MAX_EXPAND_ANCHORS = 20;
export const MAX_EXPAND_LIMIT = 100;
export const MAX_REVALIDATE_IDS = 5000;
