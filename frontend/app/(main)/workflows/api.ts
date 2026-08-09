import { apiClient } from '@/lib/api';
import type {
  CreateTriggerRequest,
  Workflow,
  WorkflowIR,
  WorkflowRun,
  WorkflowTrigger,
  WorkflowsListResponse,
  WorkflowRunsListResponse,
  WorkflowTriggerListResponse,
  WorkflowStatus,
  WorkflowEditResult,
  WorkflowTraceEntry,
} from './types';

export interface WorkflowVersionSummary {
  versionId: string;
  workflowId: string;
  versionNumber: number;
  sdkVersion: string;
  contentHash: string;
  hasBundleRef: boolean;
  createdAt: string;
  createdByUserId: string;
  ir: WorkflowIR | null;
  verifierVersion: number;
  verifiedAt: string | null;
  /** True when this version predates a verifier rule that would have
   * failed it (e.g. a missing-await or SDK-compile check added after this
   * version was generated). It is not re-verified automatically, so the
   * workflow keeps running the old code until it is regenerated. */
  needsRegeneration: boolean;
}

export interface WorkflowVersionSource {
  versionId: string;
  workflowId: string;
  source: string;
}

const BASE_URL = '/api/v1/workflows';
const LOG_PREFIX = '[WorkflowsApi]';

function logRequest(method: string, url: string, params?: unknown) {
  console.log(`${LOG_PREFIX} ${method} ${url}`, params ?? '');
}

function logResponse(method: string, url: string, status: number, data: unknown) {
  console.log(`${LOG_PREFIX} ${method} ${url} → ${status}`, data);
}

function logError(method: string, url: string, error: unknown) {
  console.error(`${LOG_PREFIX} ${method} ${url} FAILED`, error);
}

export const WorkflowsApi = {
  async listWorkflows(params: {
    page: number;
    limit: number;
    status?: WorkflowStatus;
    allUsers?: boolean;
    q?: string;
  }): Promise<WorkflowsListResponse> {
    const url = `${BASE_URL}/`;
    const queryParams = {
      limit: params.limit,
      offset: (params.page - 1) * params.limit,
      ...(params.status ? { status: params.status } : {}),
      ...(params.allUsers ? { all_users: true } : {}),
      ...(params.q ? { q: params.q } : {}),
    };
    logRequest('GET', url, queryParams);
    try {
      const resp = await apiClient.get<WorkflowsListResponse>(url, { params: queryParams });
      logResponse('GET', url, resp.status, { total: resp.data.total, count: resp.data.workflows?.length });
      return resp.data;
    } catch (err) {
      logError('GET', url, err);
      throw err;
    }
  },

  async listByConversation(conversationId: string): Promise<WorkflowsListResponse> {
    const url = `${BASE_URL}/`;
    const queryParams = { conversation_id: conversationId, all_users: true, limit: 50 };
    logRequest('GET', url, queryParams);
    try {
      const resp = await apiClient.get<WorkflowsListResponse>(url, { params: queryParams });
      logResponse('GET', url, resp.status, { total: resp.data.total, count: resp.data.workflows?.length });
      return resp.data;
    } catch (err) {
      logError('GET', url, err);
      throw err;
    }
  },

  async getWorkflow(workflowId: string): Promise<Workflow> {
    const url = `${BASE_URL}/${workflowId}`;
    logRequest('GET', url);
    try {
      const resp = await apiClient.get<Workflow>(url);
      logResponse('GET', url, resp.status, { name: resp.data.name, status: resp.data.status });
      return resp.data;
    } catch (err) {
      logError('GET', url, err);
      throw err;
    }
  },

  async listTriggers(workflowId: string): Promise<WorkflowTriggerListResponse> {
    const url = `${BASE_URL}/${workflowId}/triggers`;
    logRequest('GET', url);
    try {
      const resp = await apiClient.get<WorkflowTriggerListResponse>(url);
      logResponse('GET', url, resp.status, { count: resp.data.triggers?.length });
      return resp.data;
    } catch (err) {
      logError('GET', url, err);
      throw err;
    }
  },

  /**
   * Attaches a trigger. For a webhook trigger the response is the only place
   * `webhookSecret` ever appears — it is not readable afterwards, so it has to
   * be shown to the user straight away.
   */
  async createTrigger(
    workflowId: string,
    spec: CreateTriggerRequest
  ): Promise<WorkflowTrigger & { webhookSecret?: string; webhookPath?: string }> {
    const url = `${BASE_URL}/${workflowId}/triggers`;
    logRequest('POST', url, { kind: spec.kind });
    try {
      const resp = await apiClient.post<
        WorkflowTrigger & { webhookSecret?: string; webhookPath?: string }
      >(url, spec);
      logResponse('POST', url, resp.status, { triggerId: resp.data.triggerId });
      return resp.data;
    } catch (err) {
      logError('POST', url, err);
      throw err;
    }
  },

  async setTriggerEnabled(
    workflowId: string,
    triggerId: string,
    enabled: boolean
  ): Promise<WorkflowTrigger> {
    const url = `${BASE_URL}/${workflowId}/triggers/${triggerId}`;
    logRequest('PATCH', url, { enabled });
    try {
      const resp = await apiClient.patch<WorkflowTrigger>(url, { enabled });
      logResponse('PATCH', url, resp.status, { enabled: resp.data.enabled });
      return resp.data;
    } catch (err) {
      logError('PATCH', url, err);
      throw err;
    }
  },

  async deleteTrigger(workflowId: string, triggerId: string): Promise<void> {
    const url = `${BASE_URL}/${workflowId}/triggers/${triggerId}`;
    logRequest('DELETE', url);
    try {
      const resp = await apiClient.delete(url);
      logResponse('DELETE', url, resp.status, { triggerId });
    } catch (err) {
      logError('DELETE', url, err);
      throw err;
    }
  },

  async listRuns(
    workflowId: string,
    params: { limit: number; offset: number } = { limit: 20, offset: 0 }
  ): Promise<WorkflowRunsListResponse> {
    const url = `${BASE_URL}/${workflowId}/runs`;
    logRequest('GET', url, params);
    try {
      const resp = await apiClient.get<WorkflowRunsListResponse>(url, { params });
      logResponse('GET', url, resp.status, { total: resp.data.total, count: resp.data.runs?.length });
      return resp.data;
    } catch (err) {
      logError('GET', url, err);
      throw err;
    }
  },

  async getRun(workflowId: string, runId: string): Promise<WorkflowRun> {
    const url = `${BASE_URL}/${workflowId}/runs/${runId}`;
    logRequest('GET', url);
    try {
      const resp = await apiClient.get<WorkflowRun>(url);
      logResponse('GET', url, resp.status, { status: resp.data.status });
      return resp.data;
    } catch (err) {
      logError('GET', url, err);
      throw err;
    }
  },

  async getRunTrace(
    workflowId: string,
    runId: string
  ): Promise<{ run: WorkflowRun; traceEntries: WorkflowTraceEntry[] }> {
    const url = `${BASE_URL}/${workflowId}/runs/${runId}/trace`;
    logRequest('GET', url);
    try {
      const resp = await apiClient.get<{ run: WorkflowRun; traceEntries: WorkflowTraceEntry[] }>(url);
      logResponse('GET', url, resp.status, { runStatus: resp.data.run?.status, traceCount: resp.data.traceEntries?.length });
      return resp.data;
    } catch (err) {
      logError('GET', url, err);
      throw err;
    }
  },

  async answerRun(workflowId: string, runId: string, answer: string): Promise<WorkflowRun> {
    const url = `${BASE_URL}/${workflowId}/runs/${runId}/answer`;
    logRequest('POST', url, { answerLength: answer.length });
    try {
      const resp = await apiClient.post<WorkflowRun>(url, { answer });
      logResponse('POST', url, resp.status, { status: resp.data.status });
      return resp.data;
    } catch (err) {
      logError('POST', url, err);
      throw err;
    }
  },

  async runNow(workflowId: string): Promise<WorkflowRun> {
    const url = `${BASE_URL}/${workflowId}/run-now`;
    logRequest('POST', url);
    try {
      const resp = await apiClient.post<WorkflowRun>(url);
      logResponse('POST', url, resp.status, { runId: resp.data.runId, status: resp.data.status });
      return resp.data;
    } catch (err) {
      logError('POST', url, err);
      throw err;
    }
  },

  async dryRun(workflowId: string): Promise<WorkflowRun> {
    const url = `${BASE_URL}/${workflowId}/dry-run`;
    logRequest('POST', url);
    try {
      const resp = await apiClient.post<WorkflowRun>(url);
      logResponse('POST', url, resp.status, { runId: resp.data.runId, status: resp.data.status });
      return resp.data;
    } catch (err) {
      logError('POST', url, err);
      throw err;
    }
  },

  async pauseWorkflow(workflowId: string): Promise<Workflow> {
    const url = `${BASE_URL}/${workflowId}/pause`;
    logRequest('POST', url);
    try {
      const resp = await apiClient.post<Workflow>(url);
      logResponse('POST', url, resp.status, { status: resp.data.status });
      return resp.data;
    } catch (err) {
      logError('POST', url, err);
      throw err;
    }
  },

  async resumeWorkflow(workflowId: string): Promise<Workflow> {
    const url = `${BASE_URL}/${workflowId}/resume`;
    logRequest('POST', url);
    try {
      const resp = await apiClient.post<Workflow>(url);
      logResponse('POST', url, resp.status, { status: resp.data.status });
      return resp.data;
    } catch (err) {
      logError('POST', url, err);
      throw err;
    }
  },

  async cancelWorkflow(workflowId: string): Promise<Workflow> {
    const url = `${BASE_URL}/${workflowId}/cancel`;
    logRequest('POST', url);
    try {
      const resp = await apiClient.post<Workflow>(url);
      logResponse('POST', url, resp.status, { status: resp.data.status });
      return resp.data;
    } catch (err) {
      logError('POST', url, err);
      throw err;
    }
  },

  async deleteWorkflow(workflowId: string): Promise<void> {
    const url = `${BASE_URL}/${workflowId}`;
    logRequest('DELETE', url);
    try {
      const resp = await apiClient.delete(url);
      logResponse('DELETE', url, resp.status, 'deleted');
    } catch (err) {
      logError('DELETE', url, err);
      throw err;
    }
  },

  async promoteToAgent(workflowId: string): Promise<{ agentId: string }> {
    const url = `${BASE_URL}/${workflowId}/promote-to-agent`;
    logRequest('POST', url);
    try {
      const resp = await apiClient.post<{ agentId: string }>(url);
      logResponse('POST', url, resp.status, resp.data);
      return resp.data;
    } catch (err) {
      logError('POST', url, err);
      throw err;
    }
  },

  async listVersions(workflowId: string): Promise<{ versions: WorkflowVersionSummary[] }> {
    const url = `${BASE_URL}/${workflowId}/versions`;
    logRequest('GET', url);
    try {
      const resp = await apiClient.get<{ versions: WorkflowVersionSummary[] }>(url);
      logResponse('GET', url, resp.status, { count: resp.data.versions?.length });
      return resp.data;
    } catch (err) {
      logError('GET', url, err);
      throw err;
    }
  },

  async getVersionSource(workflowId: string, versionId: string): Promise<WorkflowVersionSource> {
    const url = `${BASE_URL}/${workflowId}/versions/${versionId}/source`;
    logRequest('GET', url);
    try {
      const resp = await apiClient.get<WorkflowVersionSource>(url);
      logResponse('GET', url, resp.status, { sourceLength: resp.data.source?.length });
      return resp.data;
    } catch (err) {
      logError('GET', url, err);
      throw err;
    }
  },

  /** Proposes edited code. Persists nothing — call `commitVersion` to apply. */
  async editWorkflow(workflowId: string, instructions: string): Promise<WorkflowEditResult> {
    const url = `${BASE_URL}/${workflowId}/edit`;
    logRequest('POST', url, { instructionsLength: instructions.length });
    try {
      const resp = await apiClient.post<WorkflowEditResult>(url, { instructions });
      logResponse('POST', url, resp.status, {
        baseVersionId: resp.data.baseVersionId,
        sourceLength: resp.data.source?.length,
      });
      return resp.data;
    } catch (err) {
      logError('POST', url, err);
      throw err;
    }
  },

  /**
   * Persists reviewed source as a new version and pins it. `baseVersionId`
   * (from `editWorkflow`, or the version currently open in the editor) makes
   * the pin conditional; the server answers 409 if someone else moved the
   * workflow in the meantime.
   */
  async commitVersion(
    workflowId: string,
    source: string,
    baseVersionId?: string | null
  ): Promise<WorkflowVersionSummary> {
    const url = `${BASE_URL}/${workflowId}/versions/commit`;
    logRequest('POST', url, { sourceLength: source.length, baseVersionId });
    try {
      const resp = await apiClient.post<WorkflowVersionSummary>(url, {
        source,
        baseVersionId: baseVersionId ?? null,
      });
      logResponse('POST', url, resp.status, { versionId: resp.data.versionId });
      return resp.data;
    } catch (err) {
      logError('POST', url, err);
      throw err;
    }
  },

  /** Rolls back (or forward) to an already-stored version by re-pinning it. */
  async activateVersion(
    workflowId: string,
    versionId: string
  ): Promise<WorkflowVersionSummary> {
    const url = `${BASE_URL}/${workflowId}/versions/${versionId}/activate`;
    logRequest('POST', url);
    try {
      const resp = await apiClient.post<WorkflowVersionSummary>(url, {});
      logResponse('POST', url, resp.status, { versionId: resp.data.versionId });
      return resp.data;
    } catch (err) {
      logError('POST', url, err);
      throw err;
    }
  },
};
