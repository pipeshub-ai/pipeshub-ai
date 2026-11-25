// ========================================
// Skill page types — mirrors backend/python/app/api/routes/skills.py response
// shapes 1:1 so the frontend never has to reshape payloads.
// ========================================

import type { CommandArgument } from '@/lib/commands/render';

export type SkillStatus = 'active' | 'deprecated' | 'candidate' | 'disabled';
/** SLASH1 S4: who a /command is shared with. */
export type SkillScope = 'personal' | 'team' | 'org';

/** A command's sharing: the editor's "Who can use it". */
export interface CommandShare {
  scope: SkillScope;
  teamId: string | null;
}

/** Where the caller may share a command: the teams they own, and the org when they are its admin. */
export interface CommandShareTargets {
  org: boolean;
  teams: { id: string; name: string }[];
}
export type SkillSourceType = 'builtin' | 'manual' | 'imported' | 'learned';

export interface SkillMetadata {
  name: string;
  /** UI-B4: the name people see; null shows `name`. */
  displayName?: string | null;
  /** UI-B4: the current version's change note. */
  changeNote?: string | null;
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
  /** SLASH1: `user` lists it in the chat composer's / menu; `model` lets the agent load it itself. */
  invocation: { user: boolean; model: boolean };
  /** A /command's declared arguments. */
  arguments: CommandArgument[];
  /** SLASH1 S4: who it is shared with; a team command's team, and a shared command's author. */
  scope?: SkillScope;
  teamId?: string | null;
  team?: string | null;
  author?: string | null;
  /** The author's user id (S4), so a reader can tell their own shared command from someone else's. */
  authorId?: string | null;
  authorFormer?: boolean;
  /** ADR-SEC-01: an imported skill's standing; absent for skills written in PipesHub or shipped with it. */
  review?: SkillTrustStanding | null;
}

/** What the content scanner found in a skill (SK-06): a reason for an admin to read it, never proof of an attack. */
export interface SkillFinding {
  code: string;
  path: string;
  line: number;
  excerpt: string;
}

/** Why an imported skill the content scanner flags is held back from agents until its content changes. */
export const CONTENT_FLAGGED = 'skill_content_flagged';

/** Whether agents may use an imported skill's current content, and why not. */
export interface SkillTrustStanding {
  state: 'trusted' | 'pending' | 'denied' | 'untrusted';
  reasonCode?: string;
  /** How it is trusted: the content scanner passed it, a publisher's signature, or an admin's approval. */
  route?: 'scan' | 'signature' | 'approval' | 'first_party';
  digest: string;
  findings: SkillFinding[];
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
  invocation?: { user: boolean; model: boolean };
  arguments?: CommandArgument[];
  /** UI-B4: what this save changed, kept on the version and shown in its history. */
  changeNote?: string;
}

export interface SkillVersionSummary {
  version: string;
  /** The author's stored id; show `updatedByName`. */
  updatedBy: string | null;
  updatedByName?: string | null;
  updatedByFormer?: boolean;
  createdAt: string | number | null;
  summary: string | null;
  /** The live version, listed first. */
  current?: boolean;
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
  /** UI-B4: the agent whose run learned it (null: the assistant), and whether it was the caller's own run. */
  source_agent_key?: string | null;
  mine?: boolean;
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
  /** SEC-SC-04: the content scanner's findings, shown before the import. */
  findings?: SkillFinding[];
}

// ── UI-only state ──

/** null = create mode, string = edit mode (skill name) */
export type EditingSkillTarget = string | null;

export type EditorTab = 'content' | 'command' | 'metadata' | 'resources' | 'versions';
