import { Schema } from 'mongoose';
import { IUnavailableTool } from '../types/conversation.interfaces';

/** A tool the requester could not use on a turn (`IUnavailableTool`); written only through `sanitizeUnavailableTools`. */
export const unavailableToolSchema = new Schema<IUnavailableTool>(
  {
    kind: { type: String, enum: ['toolset', 'mcp'], required: true },
    instanceId: { type: String, required: true },
    displayName: { type: String, required: true },
    name: { type: String },
    problem: {
      type: String,
      enum: ['not_configured', 'not_authenticated', 'not_found'],
      required: true,
    },
    authMode: { type: String },
    sharedCredential: { type: Boolean, default: false },
    userCanConnect: { type: Boolean, default: false },
  },
  { _id: false },
);
