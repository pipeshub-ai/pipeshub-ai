import mongoose, { Schema, Model } from 'mongoose';
import { IProjectDocument } from '../types/project.interfaces';
import {
  PROJECT_CHAT_SHARING_VALUES,
  PROJECT_DESCRIPTION_MAX_LENGTH,
  PROJECT_INSTRUCTIONS_MAX_LENGTH,
  PROJECT_MEMBER_ROLE_VALUES,
  PROJECT_NAME_MAX_LENGTH,
  PROJECT_PRINCIPAL_TYPE_VALUES,
  PROJECT_VISIBILITY_VALUES,
  DEFAULT_PROJECT_CHAT_SHARING,
  DEFAULT_PROJECT_VISIBILITY,
} from '../constants/constants';

/**
 * A project workspace: groups chat/agent conversations under shared
 * instructions, a knowledge scope, a selected tool set, and its own hidden
 * Collection (`linkedKnowledgeBaseId`) for uploaded files. The hidden
 * Collection is created and owned by `ProjectKnowledgeBaseService`, not
 * this schema — Mongo only stores the pointer.
 */
const projectMemberSchema = new Schema(
  {
    principalType: {
      type: String,
      enum: PROJECT_PRINCIPAL_TYPE_VALUES,
      default: 'user',
      required: true,
    },
    principalId: { type: Schema.Types.ObjectId, required: true },
    role: {
      type: String,
      enum: PROJECT_MEMBER_ROLE_VALUES,
      default: 'viewer',
      required: true,
    },
    addedBy: { type: Schema.Types.ObjectId, required: true },
    addedAt: { type: Date, default: Date.now },
  },
  { _id: false },
);

const appliedFilterNodeSchema = new Schema(
  {
    id: { type: String, required: true },
    name: { type: String, required: true },
    nodeType: { type: String, required: true },
    connector: { type: String, required: true },
  },
  { _id: false },
);

const projectSchema = new Schema<IProjectDocument>(
  {
    orgId: { type: Schema.Types.ObjectId, required: true, index: true },
    userId: { type: Schema.Types.ObjectId, required: true, index: true },
    name: {
      type: String,
      required: true,
      trim: true,
      maxlength: PROJECT_NAME_MAX_LENGTH,
    },
    description: { type: String, maxlength: PROJECT_DESCRIPTION_MAX_LENGTH },
    icon: { type: String },
    color: { type: String },
    instructions: { type: String, maxlength: PROJECT_INSTRUCTIONS_MAX_LENGTH },
    knowledgeScope: {
      apps: [{ type: String }],
      kb: [{ type: String }],
    },
    appliedFilters: {
      apps: [appliedFilterNodeSchema],
      kb: [appliedFilterNodeSchema],
    },
    tools: { type: [String], default: [] },
    linkedKnowledgeBaseId: { type: String, default: null },
    visibility: {
      type: String,
      enum: PROJECT_VISIBILITY_VALUES,
      default: DEFAULT_PROJECT_VISIBILITY,
    },
    chatSharing: {
      type: String,
      enum: PROJECT_CHAT_SHARING_VALUES,
      default: DEFAULT_PROJECT_CHAT_SHARING,
    },
    members: { type: [projectMemberSchema], default: [] },
    isPinned: { type: Boolean, default: false },
    isArchived: { type: Boolean, default: false },
    archivedBy: { type: Schema.Types.ObjectId },
    isDeleted: { type: Boolean, default: false },
    deletedBy: { type: Schema.Types.ObjectId },
    lastActivityAt: { type: Number, default: Date.now },
    metadata: { type: Map, of: Schema.Types.Mixed },
  },
  { timestamps: true, collection: 'projects' },
);

projectSchema.index({
  orgId: 1,
  userId: 1,
  isDeleted: 1,
  isArchived: 1,
  isPinned: -1,
  lastActivityAt: -1,
});
projectSchema.index({ orgId: 1, 'members.principalId': 1, isDeleted: 1 });
projectSchema.index({ orgId: 1, visibility: 1, isDeleted: 1 });
projectSchema.index({ orgId: 1, userId: 1, name: 1 });

export const Project: Model<IProjectDocument> =
  mongoose.model<IProjectDocument>('Project', projectSchema);

export default Project;
