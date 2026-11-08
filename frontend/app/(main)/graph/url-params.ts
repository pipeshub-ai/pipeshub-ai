import type { Direction, NodeId } from './types';

// The URL holds ids and the filters, never a name, a path or a label: a link must
// not carry what the person opening it may not be allowed to read.

/**
 * How a link from elsewhere names where to start: a record, or a repo and file, and optionally
 * a symbol. It is read once and resolved by the server, then the URL is rewritten to `node=`
 * (an id), so a shared link never carries a path or a name.
 */
export interface GraphEntry {
  recordId?: string;
  repo?: string;
  filePath?: string;
  qualifiedName?: string;
}

export interface GraphUrlState {
  node: NodeId | null;
  direction: Direction;
  edgeTypes: string[];
  includeTests: boolean;
}

const NODE_ID = /^block:[A-Za-z0-9_-]{1,190}$/;
const EDGE_TYPE = /^[A-Z_]{1,40}$/;
const DIRECTIONS: Direction[] = ['inbound', 'outbound', 'any'];

const RECORD_ID = /^[A-Za-z0-9_-]{1,200}$/;
const text = (value: string | null, max: number): string | undefined =>
  value && value.length <= max && !/[\u0000-\u001f]/.test(value) ? value : undefined;

/** The entry a link carries, or null. Needs a record id, or a repo and a file. */
export function parseGraphEntry(params: URLSearchParams): GraphEntry | null {
  const record = params.get('record');
  if (record && RECORD_ID.test(record)) return { recordId: record };
  const repo = text(params.get('repo'), 300);
  const filePath = text(params.get('file'), 1000);
  if (!repo || !filePath) return null;
  const qualifiedName = text(params.get('symbol'), 1000);
  return { repo, filePath, ...(qualifiedName ? { qualifiedName } : {}) };
}

/** `?repo=` with no file: open the page with search scoped to that repository. */
export function parseRepoScope(params: URLSearchParams): string | null {
  return params.get('file') ? null : (text(params.get('repo'), 300) ?? null);
}

export const DEFAULT_URL_STATE: GraphUrlState = { node: null, direction: 'any', edgeTypes: [], includeTests: false };

export const isNodeId = (value: unknown): value is NodeId => typeof value === 'string' && NODE_ID.test(value);

/** Anything malformed falls back to the default; a bad link never throws or half-applies. */
export function parseGraphUrl(params: URLSearchParams): GraphUrlState {
  const node = params.get('node');
  const direction = params.get('dir') as Direction;
  return {
    node: isNodeId(node) ? node : null,
    direction: DIRECTIONS.includes(direction) ? direction : 'any',
    edgeTypes: (params.get('types') ?? '').split(',').filter((t) => EDGE_TYPE.test(t)).slice(0, 20),
    includeTests: params.get('tests') === '1',
  };
}

/**
 * Where this history entry sits on the trail. The browser's Back and Forward name a centre, and an id is not a
 * position (A, B, A has A twice), so each entry carries its place; a link without one is a new step.
 */
export function parseTrailPosition(params: URLSearchParams): number | undefined {
  const at = params.get('at');
  return at !== null && /^\d{1,3}$/.test(at) ? Number(at) : undefined;
}

export function serializeGraphUrl(state: GraphUrlState, at?: number): URLSearchParams {
  const params = new URLSearchParams();
  if (isNodeId(state.node)) {
    params.set('node', state.node);
    if (at !== undefined && Number.isInteger(at) && at >= 0) params.set('at', String(at));
  }
  if (state.direction !== 'any') params.set('dir', state.direction);
  const types = state.edgeTypes.filter((t) => EDGE_TYPE.test(t));
  if (types.length) params.set('types', types.join(','));
  if (state.includeTests) params.set('tests', '1');
  return params;
}
