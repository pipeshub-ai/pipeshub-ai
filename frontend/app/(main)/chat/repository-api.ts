/**
 * The GitHub repository a chat works on (WP-26 GIT-FLOW): the chat's own setting, and the GitHub connection's
 * repositories and branches the picker lists. The server checks every choice; the client only offers them.
 */
import { apiClient } from '@/lib/api';
import { ToolsetsApi, MAX_TOOLSETS_LIST_LIMIT } from '@/app/(main)/toolsets/api';
import { durableSessionBase, statusOf } from './api';

/** A chat's repository as `GET …/repository` answers it. */
export interface ChatRepositoryView {
  url: string;
  baseRef: string | null;
  sessionBranch: string;
  /** The chat's workspace holds its copy: the repository can no longer change. */
  cloned: boolean;
  /** The pull request the chat's engine opened; `state` as GitHub answered when it was opened. */
  pullRequest?: { number: number; url: string; state: string };
}

/** What the picker sends: on `PUT …/repository`, or on the first message of a new chat as `repository`. */
export interface RepositoryChoice {
  url: string;
  baseRef?: string;
  toolsetInstanceId: string;
}

export interface GithubConnection {
  instanceId: string;
  name: string;
}

export interface GithubRepository {
  fullName: string;
  url: string;
  private: boolean;
  defaultBranch: string;
  updatedAt: string;
}

export interface GithubRepositoryPage {
  repositories: GithubRepository[];
  page: number;
  hasMore: boolean;
}

export interface GithubBranch {
  name: string;
  default: boolean;
}

type Session = { agentId?: string; sessionId: string };

const QUIET = { suppressErrorToast: true } as const;

/** The harness's machine code of a refused request, if it has one. */
function refusalCode(error: unknown): string {
  const original = (error as { originalError?: { response?: { data?: unknown } } } | null)?.originalError;
  const body = original?.response?.data as { error?: { code?: unknown } } | undefined;
  return typeof body?.error?.code === 'string' ? body.error.code : `HTTP_${String(statusOf(error) ?? 0)}`;
}

const str = (value: unknown): string | null => (typeof value === 'string' && value !== '' ? value : null);

function toView(data: unknown): ChatRepositoryView | null {
  const view = (data ?? {}) as Record<string, unknown>;
  const url = str(view.url);
  if (!url) return null;
  const pr = view.pullRequest as Record<string, unknown> | undefined;
  return {
    url,
    baseRef: str(view.baseRef),
    sessionBranch: str(view.sessionBranch) ?? '',
    cloned: view.cloned === true,
    ...(pr && typeof pr.number === 'number' && str(pr.url)
      ? { pullRequest: { number: pr.number, url: str(pr.url) as string, state: str(pr.state) ?? 'open' } }
      : {}),
  };
}

/** `owner/name` of a GitHub https URL; the URL itself when it is not one. */
export function repositoryName(url: string): string {
  const match = /^https:\/\/github\.com\/([^/\s]+\/[^/\s]+?)(?:\.git)?\/?$/.exec(url.trim());
  return match ? match[1] : url;
}

export const RepositoryApi = {
  /** The chat's repository; null when it has none (204). */
  async get(session: Session): Promise<ChatRepositoryView | null> {
    const { data, status } = await apiClient.get<unknown>(`${durableSessionBase(session.agentId, session.sessionId)}/repository`, QUIET);
    return status === 204 ? null : toView(data);
  },

  /** Saves the chat's repository; the refusal's code when the server refuses it. */
  async put(session: Session, choice: RepositoryChoice): Promise<{ view: ChatRepositoryView } | { refused: string }> {
    try {
      const { data } = await apiClient.put<unknown>(`${durableSessionBase(session.agentId, session.sessionId)}/repository`, choice, QUIET);
      const view = toView(data);
      return view ? { view } : { refused: 'HTTP_0' };
    } catch (error) {
      return { refused: refusalCode(error) };
    }
  },

  /** Drops the chat's repository before its workspace holds a copy. */
  async remove(session: Session): Promise<{ ok: true } | { refused: string }> {
    try {
      await apiClient.delete(`${durableSessionBase(session.agentId, session.sessionId)}/repository`, QUIET);
      return { ok: true };
    } catch (error) {
      return { refused: refusalCode(error) };
    }
  },

  /** The person's GitHub connections that are signed in; only those can copy and push a repository. */
  async connections(): Promise<GithubConnection[]> {
    const { toolsets } = await ToolsetsApi.getMyToolsets({ toolsetType: 'github', limit: MAX_TOOLSETS_LIST_LIMIT });
    return toolsets
      .filter((row) => row.isAuthenticated && row.instanceId)
      .map((row) => ({ instanceId: row.instanceId as string, name: row.instanceName || row.displayName || row.name }));
  },

  async repositories(instanceId: string, search: string, page = 1): Promise<GithubRepositoryPage> {
    const { data } = await apiClient.get<Partial<GithubRepositoryPage>>(
      `/api/v1/toolsets/instances/${encodeURIComponent(instanceId)}/github/repositories`,
      { ...QUIET, params: { ...(search.trim() ? { search: search.trim() } : {}), page } },
    );
    return {
      repositories: Array.isArray(data?.repositories) ? data.repositories : [],
      page: typeof data?.page === 'number' ? data.page : page,
      hasMore: data?.hasMore === true,
    };
  },

  async branches(instanceId: string, fullName: string): Promise<GithubBranch[]> {
    const { data } = await apiClient.get<{ branches?: GithubBranch[] }>(
      `/api/v1/toolsets/instances/${encodeURIComponent(instanceId)}/github/branches`,
      { ...QUIET, params: { repository: fullName } },
    );
    return Array.isArray(data?.branches) ? data.branches : [];
  },
};
