import { Document, Types } from 'mongoose';
import { IAppliedFilterNode } from '../../enterprise_search/types/conversation.interfaces';

export type ProjectMemberRole = 'viewer' | 'editor';
export type ProjectPrincipalType = 'user' | 'team';
export type ProjectVisibility = 'private' | 'org';
/** Owner-controlled default for whether new/existing chats in this project are visible to project members. */
export type ProjectChatSharing = 'private' | 'members';
/** Ceiling on the chat role a project member inherits (H2). */
export type ProjectChatAccess = 'viewer' | 'editor';
/** Effective access role computed for the requesting user, or 'none' for an unauthorized caller. */
export type ProjectRole = 'owner' | 'editor' | 'viewer' | 'none';

export interface IProjectMember {
  principalType: ProjectPrincipalType;
  /** The userId for a 'user' row. Team rows written before teams were string-keyed carry a legacy ObjectId here; read them, never write them. */
  principalId?: Types.ObjectId;
  /** Graph team key (UUID or `all_<orgId>`) of a 'team' row. */
  teamId?: string;
  role: ProjectMemberRole;
  addedBy: Types.ObjectId;
  addedAt: Date;
}

/** Raw id-array shape — identical to the `filters` object already sent to the AI backend (see es_validators `filtersSchema`). */
export interface IProjectKnowledgeScope {
  apps?: string[];
  kb?: string[];
}

/** Display-friendly mirror of `knowledgeScope`, for rendering scope chips without a round trip. */
export interface IProjectAppliedFilters {
  apps?: IAppliedFilterNode[];
  kb?: IAppliedFilterNode[];
}

export interface IProject {
  orgId: Types.ObjectId;
  /** Owner. Ownership never transfers in V1. */
  userId: Types.ObjectId;
  name: string;
  description?: string;
  icon?: string;
  color?: string;
  /** Injected into the system prompt as a dedicated `project_instructions` section — see prompt_builder.py. */
  instructions?: string;
  knowledgeScope?: IProjectKnowledgeScope;
  appliedFilters?: IProjectAppliedFilters;
  /** Tool fullNames (toolset + MCP tools, same format as the composer's `agentStreamTools`) available to this project in agent mode. */
  tools: string[];
  /** Hidden Collection (KB) holding this project's uploaded files — see `ProjectKnowledgeBaseService.ensureLinkedKb`. Created lazily on first upload. */
  linkedKnowledgeBaseId?: string | null;
  visibility: ProjectVisibility;
  chatSharing: ProjectChatSharing;
  /** H2 ceiling for inherited chat access; absent on legacy rows = 'viewer'. */
  projectChatAccess?: ProjectChatAccess;
  /** Bumped on every ACL change; absent on legacy rows = 0 (`.lean()` skips defaults). */
  aclVersion?: number;
  members: IProjectMember[];
  isPinned: boolean;
  isArchived: boolean;
  archivedBy?: Types.ObjectId;
  isDeleted: boolean;
  deletedBy?: Types.ObjectId;
  lastActivityAt: number;
  metadata?: Map<string, unknown>;
  createdAt?: Date;
  updatedAt?: Date;
}

export interface IProjectDocument extends Document, IProject {}

/** Result of an access check against a project — role drives what the caller may do. */
export interface ProjectAccess {
  role: ProjectRole;
  project: IProjectDocument;
}

/** Assembled once per AI call site and merged into the outgoing `aiPayload` by `applyProjectScope`. */
export interface ProjectContext {
  projectId: string;
  instructions?: string;
  knowledgeScope?: IProjectKnowledgeScope;
  /** Tool fullNames the project scope permits in agent mode. */
  tools: string[];
  /** The project's own hidden Collection, if one has been created — always added to the effective `filters.kb`. */
  linkedKnowledgeBaseId?: string | null;
}

/** Sentinel accepted by `?projectId=` on conversation-list endpoints to mean "no project". */
export const PROJECT_ID_UNASSIGNED = 'unassigned';
