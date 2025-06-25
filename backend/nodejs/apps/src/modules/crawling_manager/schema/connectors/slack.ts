import { Schema } from 'mongoose';

export interface ISlackSettings {
  excludedChannels?: string[];
  excludedWorkspaces?: string[];
  includePrivateChannels?: boolean;
  includeDMs?: boolean;
  includeThreads?: boolean;
}

// Slack Settings Schema
// @ts-ignore - Temporarily ignoring unused declaration
export const SlackSettingsSchema = new Schema<ISlackSettings>(
  {
    excludedChannels: [{ type: String }],
    excludedWorkspaces: [{ type: String }],
    includePrivateChannels: { type: Boolean, default: false },
    includeDMs: { type: Boolean, default: false },
    includeThreads: { type: Boolean, default: true },
  },
  { _id: false },
);
