import { describe, it, expect, beforeEach, vi } from 'vitest';
import { apiClient } from '@/lib/api';
import { SkillsApi } from '../api';
import type { SkillWritePayload } from '../types';

vi.mock('@/lib/api', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

const mockedGet = vi.mocked(apiClient.get);
const mockedPost = vi.mocked(apiClient.post);
const mockedPut = vi.mocked(apiClient.put);
const mockedDelete = vi.mocked(apiClient.delete);

const BASE_URL = '/api/v1/skills';

describe('SkillsApi', () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedPost.mockReset();
    mockedPut.mockReset();
    mockedDelete.mockReset();
  });

  describe('listSkills', () => {
    it('hits the base URL and returns the skills array', async () => {
      mockedGet.mockResolvedValueOnce({ data: { skills: [{ name: 'pdf-extractor' }] } });
      const result = await SkillsApi.listSkills();
      expect(mockedGet).toHaveBeenCalledWith(BASE_URL, { params: undefined });
      expect(result).toEqual([{ name: 'pdf-extractor' }]);
    });

    it('forwards filter params', async () => {
      mockedGet.mockResolvedValueOnce({ data: { skills: [] } });
      await SkillsApi.listSkills({ category: 'documents', status: 'active', q: 'pdf' });
      expect(mockedGet).toHaveBeenCalledWith(BASE_URL, {
        params: { category: 'documents', status: 'active', q: 'pdf' },
      });
    });

    it('defaults to an empty array when the response has no skills field', async () => {
      mockedGet.mockResolvedValueOnce({ data: {} });
      const result = await SkillsApi.listSkills();
      expect(result).toEqual([]);
    });
  });

  describe('listAssignableSkills', () => {
    it('restricts the catalog to active skills for the Agent Builder picker', async () => {
      mockedGet.mockResolvedValueOnce({ data: { skills: [{ name: 'pdf-extractor', status: 'active' }] } });
      const result = await SkillsApi.listAssignableSkills();
      expect(mockedGet).toHaveBeenCalledWith(BASE_URL, { params: { status: 'active' } });
      expect(result).toEqual([{ name: 'pdf-extractor', status: 'active' }]);
    });
  });

  describe('getSkill', () => {
    it('encodes the skill name into the URL path', async () => {
      mockedGet.mockResolvedValueOnce({ data: { name: 'pdf extractor/v2' } });
      await SkillsApi.getSkill('pdf extractor/v2');
      expect(mockedGet).toHaveBeenCalledWith(`${BASE_URL}/pdf%20extractor%2Fv2`);
    });
  });

  describe('exportSkill', () => {
    it('requests plain text and returns the raw markdown', async () => {
      mockedGet.mockResolvedValueOnce({ data: '---\nname: pdf-extractor\n---\nbody' });
      const result = await SkillsApi.exportSkill('pdf-extractor');
      expect(mockedGet).toHaveBeenCalledWith(`${BASE_URL}/pdf-extractor/export`, { responseType: 'text' });
      expect(result).toBe('---\nname: pdf-extractor\n---\nbody');
    });
  });

  describe('createSkill', () => {
    it('POSTs the full write payload to the base URL', async () => {
      const payload: SkillWritePayload = { name: 'pdf-extractor', description: 'd', body: 'b' };
      mockedPost.mockResolvedValueOnce({ data: { name: 'pdf-extractor' } });
      await SkillsApi.createSkill(payload);
      expect(mockedPost).toHaveBeenCalledWith(BASE_URL, payload);
    });
  });

  describe('updateSkill', () => {
    it('PUTs to the skill-specific URL', async () => {
      const payload: SkillWritePayload = { name: 'ignored', description: 'd2', body: 'b2' };
      mockedPut.mockResolvedValueOnce({ data: { name: 'pdf-extractor' } });
      await SkillsApi.updateSkill('pdf-extractor', payload);
      expect(mockedPut).toHaveBeenCalledWith(`${BASE_URL}/pdf-extractor`, payload);
    });
  });

  describe('deleteSkill', () => {
    it('defaults detach to false when unset', async () => {
      mockedDelete.mockResolvedValueOnce({ data: { status: 'success' } });
      await SkillsApi.deleteSkill('pdf-extractor');
      expect(mockedDelete).toHaveBeenCalledWith(`${BASE_URL}/pdf-extractor`, { params: { detach: false } });
    });

    it('forwards detach=true when the caller confirmed unassignment', async () => {
      mockedDelete.mockResolvedValueOnce({ data: { status: 'success' } });
      await SkillsApi.deleteSkill('pdf-extractor', true);
      expect(mockedDelete).toHaveBeenCalledWith(`${BASE_URL}/pdf-extractor`, { params: { detach: true } });
    });
  });

  describe('getUsage', () => {
    it('returns the usage payload for the safe-delete confirmation UI', async () => {
      const usage = { usedByAgents: [{ id: 'a1', name: 'Support Bot' }], requiredBySkills: [] };
      mockedGet.mockResolvedValueOnce({ data: usage });
      const result = await SkillsApi.getUsage('pdf-extractor');
      expect(mockedGet).toHaveBeenCalledWith(`${BASE_URL}/pdf-extractor/usage`);
      expect(result).toEqual(usage);
    });
  });

  describe('deprecateSkill', () => {
    it('POSTs reason and replacedBy to the deprecate endpoint', async () => {
      mockedPost.mockResolvedValueOnce({ data: { status: 'success' } });
      await SkillsApi.deprecateSkill('pdf-extractor', 'superseded', 'pdf-extractor-v2');
      expect(mockedPost).toHaveBeenCalledWith(`${BASE_URL}/pdf-extractor/deprecate`, {
        reason: 'superseded',
        replaced_by: 'pdf-extractor-v2',
      });
    });
  });

  describe('versions', () => {
    it('listVersions calls the versions endpoint', async () => {
      mockedGet.mockResolvedValueOnce({ data: { versions: [{ version: '1.0.0' }] } });
      const result = await SkillsApi.listVersions('pdf-extractor');
      expect(mockedGet).toHaveBeenCalledWith(`${BASE_URL}/pdf-extractor/versions`);
      expect(result).toEqual([{ version: '1.0.0' }]);
    });

    it('rollback posts the target version', async () => {
      mockedPost.mockResolvedValueOnce({ data: { name: 'pdf-extractor', version: '1.0.0' } });
      await SkillsApi.rollback('pdf-extractor', '1.0.0');
      expect(mockedPost).toHaveBeenCalledWith(`${BASE_URL}/pdf-extractor/rollback`, { version: '1.0.0' });
    });
  });

  describe('resources', () => {
    it('writeResource PUTs path and content', async () => {
      mockedPut.mockResolvedValueOnce({ data: { status: 'success' } });
      await SkillsApi.writeResource('pdf-extractor', 'scripts/run.py', "print('hi')");
      expect(mockedPut).toHaveBeenCalledWith(`${BASE_URL}/pdf-extractor/resource`, {
        path: 'scripts/run.py',
        content: "print('hi')",
      });
    });

    it('removeResource DELETEs with the path as a query param', async () => {
      mockedDelete.mockResolvedValueOnce({ data: { status: 'success' } });
      await SkillsApi.removeResource('pdf-extractor', 'scripts/run.py');
      expect(mockedDelete).toHaveBeenCalledWith(`${BASE_URL}/pdf-extractor/resource`, {
        params: { path: 'scripts/run.py' },
      });
    });
  });

  describe('candidates', () => {
    it('getPendingCandidates returns the candidates array', async () => {
      mockedGet.mockResolvedValueOnce({ data: { candidates: [{ id: 'c1' }] } });
      const result = await SkillsApi.getPendingCandidates();
      expect(mockedGet).toHaveBeenCalledWith(`${BASE_URL}/candidates/pending`);
      expect(result).toEqual([{ id: 'c1' }]);
    });

    it('approveCandidate posts to the approve endpoint', async () => {
      mockedPost.mockResolvedValueOnce({ data: { name: 'pdf-extractor' } });
      await SkillsApi.approveCandidate('c1');
      expect(mockedPost).toHaveBeenCalledWith(`${BASE_URL}/candidates/c1/approve`);
    });

    it('rejectCandidate posts to the reject endpoint', async () => {
      mockedPost.mockResolvedValueOnce({ data: { status: 'success' } });
      await SkillsApi.rejectCandidate('c1');
      expect(mockedPost).toHaveBeenCalledWith(`${BASE_URL}/candidates/c1/reject`);
    });
  });

  describe('import', () => {
    it('previewNpmImport posts the raw command_or_name field', async () => {
      mockedPost.mockResolvedValueOnce({ data: { name: 'pdf-extractor' } });
      await SkillsApi.previewNpmImport('npm install pdf-extractor');
      expect(mockedPost).toHaveBeenCalledWith(`${BASE_URL}/import/npm/preview`, {
        command_or_name: 'npm install pdf-extractor',
      });
    });

    it('previewUrlImport posts the url field', async () => {
      mockedPost.mockResolvedValueOnce({ data: { name: 'pdf-extractor' } });
      await SkillsApi.previewUrlImport('https://example.com/skill.zip');
      expect(mockedPost).toHaveBeenCalledWith(`${BASE_URL}/import/url/preview`, {
        url: 'https://example.com/skill.zip',
      });
    });

    it('finalizeImport posts content/resources/category/subcategory', async () => {
      mockedPost.mockResolvedValueOnce({ data: { name: 'pdf-extractor' } });
      const preview = {
        name: 'pdf-extractor',
        description: 'd',
        version: '1.0.0',
        content: '---\nname: pdf-extractor\n---\nbody',
        resources: { 'scripts/run.py': "print('hi')" },
        warnings: [],
        skippedBinaryResources: [],
        sourceLabel: 'npm:pdf-extractor@1.0.0',
      };
      await SkillsApi.finalizeImport(preview, 'documents', 'pdf');
      expect(mockedPost).toHaveBeenCalledWith(`${BASE_URL}/import/finalize`, {
        content: preview.content,
        resources: preview.resources,
        category: 'documents',
        subcategory: 'pdf',
      });
    });
  });
});
