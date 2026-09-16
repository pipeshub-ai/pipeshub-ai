import { Document, Types } from 'mongoose';
import {
  IAppliedFilterNode,
  IChatAttachmentRef,
} from '../../enterprise_search/types/conversation.interfaces';

export type ProjectMemberRole = 'viewer' | 'editor';
export type ProjectPrincipalType = 'user' | 'team';
export type ProjectVisibility = 'private' | 'org';
/** Owner-controlled default for whether new/existing chats in this project are visible to project members. */
export type ProjectChatSharing = 'private' | 'members';
/** Effective access role computed for the requesting user, or 'none' for an unauthorized caller. */
export type ProjectRole = 'owner' | 'editor' | 'viewer' | 'none';

export interface IProjectMember {
  principalType: ProjectPrincipalType;
  /** userId (principalType 'user') — team principals are deferred (see plan "Deferred" section). */
  principalId: Types.ObjectId;
  role: ProjectMemberRole;
  addedBy: Types.ObjectId;
  addedAt: Date;
}

/** One project-attached file. Reuses the chat-attachment ref shape (graph record + blob) — no parallel file store. */
export interface IProjectFileRef extends IChatAttachmentRef {
  sizeBytes?: number;
  uploadedBy: Types.ObjectId;
  uploadedAt: Date;
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
  files: IProjectFileRef[];
  visibility: ProjectVisibility;
  chatSharing: ProjectChatSharing;
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

/** Assembled once per AI call site and merged into the outgoing `aiPayload` by `applyProjectContext`. */
export interface ProjectContext {
  projectId: string;
  instructions?: string;
  knowledgeScope?: IProjectKnowledgeScope;
  attachments?: IChatAttachmentRef[];
}

/** Bounds enforced on project file uploads (mirrors the plan's storage caps). */
export const PROJECT_FILE_LIMITS = {
  MAX_FILES: 20,
  MAX_FILE_SIZE_BYTES: 5 * 1024 * 1024,
  MAX_TOTAL_SIZE_BYTES: 25 * 1024 * 1024,
} as const;

/** Sentinel accepted by `?projectId=` on conversation-list endpoints to mean "no project". */
export const PROJECT_ID_UNASSIGNED = 'unassigned';
