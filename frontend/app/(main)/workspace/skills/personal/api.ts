import { apiClient } from '@/lib/api';
import type {
  CommandShare,
  CommandShareTargets,
  ImportPreview,
  Skill,
  SkillCandidate,
  SkillMetadata,
  SkillSearchMatch,
  SkillUsage,
  SkillVersionSummary,
  SkillWritePayload,
} from './types';

const BASE_URL = '/api/v1/skills';

export const SkillsApi = {
  // ── Catalog ──

  async listSkills(params?: { category?: string; status?: string; q?: string }): Promise<SkillMetadata[]> {
    const { data } = await apiClient.get(BASE_URL, { params });
    return data?.skills ?? [];
  },

  async getCategories(): Promise<{ categories: string[]; tags: string[] }> {
    const { data } = await apiClient.get(`${BASE_URL}/categories`);
    return { categories: data?.categories ?? [], tags: data?.tags ?? [] };
  },

  async search(q: string, category?: string, limit = 10): Promise<SkillSearchMatch[]> {
    const { data } = await apiClient.get(`${BASE_URL}/search`, { params: { q, category, limit } });
    return data?.results ?? [];
  },

  async getSkill(name: string): Promise<Skill> {
    const { data } = await apiClient.get(`${BASE_URL}/${encodeURIComponent(name)}`);
    return data;
  },

  async exportSkill(name: string): Promise<string> {
    const { data } = await apiClient.get(`${BASE_URL}/${encodeURIComponent(name)}/export`, {
      responseType: 'text',
    });
    return data;
  },

  async createSkill(payload: SkillWritePayload): Promise<SkillMetadata> {
    const { data } = await apiClient.post(BASE_URL, payload);
    return data;
  },

    async updateSkill(
    name: string,
    payload: SkillWritePayload,
    expectedUpdatedAt?: string | number | null,
  ): Promise<SkillMetadata> {
    const headers: Record<string, string> = {};
    if (expectedUpdatedAt !== undefined) {
      const token = String(expectedUpdatedAt ?? '');
      if (token.length === 0) {
        throw new Error('Skill update requires a last-seen updatedAt (If-Match)');
      }
      headers['If-Match'] = token;
    }
    const { data } = await apiClient.put(`${BASE_URL}/${encodeURIComponent(name)}`, payload, {
      headers,
    });
    return data;
  },

  async deprecateSkill(name: string, reason: string, replacedBy?: string | null): Promise<void> {
    await apiClient.post(`${BASE_URL}/${encodeURIComponent(name)}/deprecate`, {
      reason,
      replaced_by: replacedBy || null,
    });
  },

  /** Reversible mute — hides the skill from every agent-facing surface without touching content/version. */
  async disableSkill(name: string): Promise<SkillMetadata> {
    const { data } = await apiClient.post(`${BASE_URL}/${encodeURIComponent(name)}/disable`);
    return data;
  },

  /** Reverses `disableSkill`. Never un-deprecates. */
  async enableSkill(name: string): Promise<SkillMetadata> {
    const { data } = await apiClient.post(`${BASE_URL}/${encodeURIComponent(name)}/enable`);
    return data;
  },

  async getUsage(name: string): Promise<SkillUsage> {
    const { data } = await apiClient.get(`${BASE_URL}/${encodeURIComponent(name)}/usage`);
    return data;
  },

  async deleteSkill(name: string, detach = false): Promise<void> {
    await apiClient.delete(`${BASE_URL}/${encodeURIComponent(name)}`, { params: { detach } });
  },

  // ── Version history ──

  async listVersions(name: string): Promise<SkillVersionSummary[]> {
    const { data } = await apiClient.get(`${BASE_URL}/${encodeURIComponent(name)}/versions`);
    return data?.versions ?? [];
  },

  async getVersion(name: string, version: string): Promise<Skill> {
    const { data } = await apiClient.get(
      `${BASE_URL}/${encodeURIComponent(name)}/versions/${encodeURIComponent(version)}`
    );
    return data;
  },

  // ── Sharing a /command (SLASH1 S4) ──

  async getCommandShareTargets(): Promise<CommandShareTargets> {
    const { data } = await apiClient.get(`${BASE_URL}/commands/share-targets`);
    return { org: data?.org === true, teams: Array.isArray(data?.teams) ? data.teams : [] };
  },

  async publishCommand(name: string, share: CommandShare): Promise<void> {
    await apiClient.post(`${BASE_URL}/${encodeURIComponent(name)}/publish`, {
      scope: share.scope,
      ...(share.scope === 'team' ? { teamId: share.teamId } : {}),
    });
  },

  async rollback(name: string, version: string): Promise<SkillMetadata> {
    const { data } = await apiClient.post(`${BASE_URL}/${encodeURIComponent(name)}/rollback`, { version });
    return data;
  },

  // ── Bundled resources ──

  async getResource(name: string, path: string): Promise<string> {
    const { data } = await apiClient.get(`${BASE_URL}/${encodeURIComponent(name)}/resource`, {
      params: { path },
      responseType: 'text',
    });
    return data;
  },

  async writeResource(name: string, path: string, content: string): Promise<void> {
    await apiClient.put(`${BASE_URL}/${encodeURIComponent(name)}/resource`, { path, content });
  },

  async removeResource(name: string, path: string): Promise<void> {
    await apiClient.delete(`${BASE_URL}/${encodeURIComponent(name)}/resource`, { params: { path } });
  },

  // ── Learning-loop candidates ──

  async getPendingCandidates(): Promise<SkillCandidate[]> {
    const { data } = await apiClient.get(`${BASE_URL}/candidates/pending`);
    return data?.candidates ?? [];
  },

  /** `edits` (UI-B4) approve the proposal as the reviewer changed it, in one version. */
  async approveCandidate(candidateId: string, edits?: object): Promise<SkillMetadata> {
    const url = `${BASE_URL}/candidates/${encodeURIComponent(candidateId)}/approve`;
    const { data } = edits ? await apiClient.post(url, edits) : await apiClient.post(url);
    return data;
  },

  /** UI-B4: the name people see (null: the id); the id never changes. Sends If-Match when the last save is known. */
  async renameSkill(
    name: string,
    displayName: string | null,
    expectedUpdatedAt?: string | number | null,
  ): Promise<{ name: string; displayName: string | null; changeNote: string | null }> {
    const token = expectedUpdatedAt === null || expectedUpdatedAt === undefined ? '' : String(expectedUpdatedAt);
    const { data } = await apiClient.post(
      `${BASE_URL}/${encodeURIComponent(name)}/rename`,
      { displayName },
      token ? { headers: { 'If-Match': token } } : undefined,
    );
    return data;
  },

  async rejectCandidate(candidateId: string): Promise<void> {
    await apiClient.post(`${BASE_URL}/candidates/${encodeURIComponent(candidateId)}/reject`);
  },

  // ── Package import (npm / URL / upload) — stateless preview + finalize ──

  async previewNpmImport(commandOrName: string): Promise<ImportPreview> {
    const { data } = await apiClient.post(`${BASE_URL}/import/npm/preview`, {
      command_or_name: commandOrName,
    });
    return data;
  },

  async previewUrlImport(url: string): Promise<ImportPreview> {
    const { data } = await apiClient.post(`${BASE_URL}/import/url/preview`, { url });
    return data;
  },

  async previewUploadImport(file: File): Promise<ImportPreview> {
    const form = new FormData();
    form.append('file', file);
    const { data } = await apiClient.post(`${BASE_URL}/import/upload/preview`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return data;
  },

  async finalizeImport(
    preview: ImportPreview,
    category?: string,
    subcategory?: string,
  ): Promise<SkillMetadata> {
    const { data } = await apiClient.post(`${BASE_URL}/import/finalize`, {
      content: preview.content,
      resources: preview.resources,
      category: category || null,
      subcategory: subcategory || null,
      name: preview.name || null,
    });
    return data;
  },

  // ── Agent Builder skill picker ──

  async listAssignableSkills(): Promise<SkillMetadata[]> {
    const { data } = await apiClient.get(BASE_URL, { params: { status: 'active' } });
    return data?.skills ?? [];
  },
};

/** PUT /skills/:name 409 whose body carries `currentUpdatedAt` — a concurrent edit, not a name clash. */
export function isSkillConflictError(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  const response = (error as { response?: { status?: number; data?: { detail?: unknown } } }).response;
  if (response?.status !== 409) return false;
  const detail = response.data?.detail;
  return Boolean(detail && typeof detail === 'object' && detail !== null && 'currentUpdatedAt' in detail);
}
