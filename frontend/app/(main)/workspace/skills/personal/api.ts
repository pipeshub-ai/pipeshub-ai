import { apiClient } from '@/lib/api';
import type {
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

  async updateSkill(name: string, payload: SkillWritePayload): Promise<SkillMetadata> {
    const { data } = await apiClient.put(`${BASE_URL}/${encodeURIComponent(name)}`, payload);
    return data;
  },

  async deprecateSkill(name: string, reason: string, replacedBy?: string | null): Promise<void> {
    await apiClient.post(`${BASE_URL}/${encodeURIComponent(name)}/deprecate`, {
      reason,
      replaced_by: replacedBy || null,
    });
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

  async approveCandidate(candidateId: string): Promise<SkillMetadata> {
    const { data } = await apiClient.post(`${BASE_URL}/candidates/${encodeURIComponent(candidateId)}/approve`);
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

  async finalizeImport(preview: ImportPreview, category?: string, subcategory?: string): Promise<SkillMetadata> {
    const { data } = await apiClient.post(`${BASE_URL}/import/finalize`, {
      content: preview.content,
      resources: preview.resources,
      category: category || null,
      subcategory: subcategory || null,
    });
    return data;
  },

  // ── Agent Builder skill picker ──

  async listAssignableSkills(): Promise<SkillMetadata[]> {
    const { data } = await apiClient.get(BASE_URL, { params: { status: 'active' } });
    return data?.skills ?? [];
  },
};
