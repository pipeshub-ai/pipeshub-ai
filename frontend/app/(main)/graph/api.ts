import { apiClient } from '@/lib/api';
import { i18n } from '@/lib/i18n';
import type {
  CodeRepoInfo,
  ExpandRequest,
  ExpandResponse,
  ResolveRequest,
  ResolveResponse,
  RevalidateRequest,
  RevalidateResponse,
  SearchMatch,
  SearchRequest,
} from './types';

const BASE_URL = '/api/v1/graph';
const CODE_URL = '/api/v1/code-graph';

// The parser's filler kinds have no name of their own ("imports:@38ce6710"), so the picture names them by their file.
const FILLER_KINDS = new Set(['imports', 'statements', 'comment', 'header']);

/** A name a person can read for a node the server could only label with a hash. */
export function readableNode<N extends { kind?: string; file?: string; label?: string }>(node: N): N {
  if (!node.kind || !FILLER_KINDS.has(node.kind) || !node.file) return node;
  return { ...node, label: i18n.t('graph.ui.fillerNode', { kind: node.kind, file: node.file.split('/').pop() }) };
}

/** The explorer's reads. Access is decided by the server for the caller's own token; nothing here filters. */
export const GraphApi = {
  async expand(body: ExpandRequest, signal?: AbortSignal): Promise<ExpandResponse> {
    const { data } = await apiClient.post<ExpandResponse>(`${BASE_URL}/expand`, body, { signal });
    return { ...data, nodes: (data.nodes ?? []).map(readableNode) };
  },

  async resolve(body: ResolveRequest, signal?: AbortSignal): Promise<ResolveResponse> {
    const { data } = await apiClient.post<ResolveResponse>(`${BASE_URL}/resolve`, body, { signal });
    return data;
  },

  async repos(signal?: AbortSignal): Promise<CodeRepoInfo[]> {
    const { data } = await apiClient.get<{ repos?: CodeRepoInfo[] }>(`${CODE_URL}/repos`, { signal });
    return data?.repos ?? [];
  },

  /** Identifier search only: its hits carry the address (repo, file, qualified name) that resolve turns into a node. */
  async search(body: SearchRequest, signal?: AbortSignal): Promise<SearchMatch[]> {
    const { data } = await apiClient.post<{ matches?: SearchMatch[] }>(
      `${CODE_URL}/search`,
      { mode: 'identifier', limit: 20, ...body },
      { signal },
    );
    return (data?.matches ?? []).filter((m) => m.file_path && m.qualified_name);
  },

  async revalidate(body: RevalidateRequest, signal?: AbortSignal): Promise<RevalidateResponse> {
    const { data } = await apiClient.post<RevalidateResponse>(`${BASE_URL}/revalidate`, body, { signal });
    return data;
  },
};
