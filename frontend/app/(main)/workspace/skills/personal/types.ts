// ========================================
// Skill page types — mirrors backend/python/app/api/routes/skills.py response
// shapes 1:1 so the frontend never has to reshape payloads.
// ========================================

export type SkillStatus = 'active' | 'deprecated' | 'candidate';
export type SkillSourceType = 'builtin' | 'manual' | 'imported' | 'learned';

export interface SkillMetadata {
  name: string;
  description: string;
  version: string;
  category: string | null;
  subcategory: string | null;
  tags: string[];
  status: SkillStatus;
  source: SkillSourceType;
  license: string | null;
  compatibility: string | null;
  allowedTools: string[] | null;
  related: string[];
  requires: string[];
  concepts: string[];
  deprecatedReason: string | null;
  replacedBy: string | null;
  createdAt: string | number | null;
  updatedAt: string | number | null;
  packName: string | null;
  packVersion: string | null;
}

/** Full skill: metadata + Tiptap-edited markdown body + bundled resource paths. */
export interface Skill extends SkillMetadata {
  body: string;
  resources: Record<string, string[]>;
}

export interface SkillWritePayload {
  name?: string;
  description: string;
  body: string;
  category?: string | null;
  subcategory?: string | null;
  tags?: string[];
  license?: string | null;
  compatibility?: string | null;
  allowedTools?: string[] | null;
  related?: string[];
  requires?: string[];
  concepts?: string[];
}

export interface SkillVersionSummary {
  version: string;
  updatedBy: string | null;
  createdAt: string | number | null;
  summary: string | null;
}

export interface SkillUsage {
  usedByAgents: { id: string; name: string }[];
  requiredBySkills: string[];
}

export interface SkillSearchMatch {
  skill: SkillMetadata;
  relevance: number;
  matchReason: string | null;
}

export interface SkillCandidate {
  candidate_id: string;
  name: string;
  description?: string;
  body?: string;
  category?: string | null;
  subcategory?: string | null;
  tags?: string[];
  source_trajectory_summary?: string;
  source?: string;
  created_at?: string | number | null;
  status?: string;
  [key: string]: unknown;
}

export type ImportSourceTab = 'npm' | 'url' | 'upload';

export interface ImportPreview {
  name: string;
  description: string;
  version: string;
  content: string;
  resources: Record<string, string>;
  warnings: string[];
  skippedBinaryResources: string[];
  sourceLabel: string;
}

// ── UI-only state ──

/** null = create mode, string = edit mode (skill name) */
export type EditingSkillTarget = string | null;

export type EditorTab = 'content' | 'metadata' | 'resources' | 'versions';
