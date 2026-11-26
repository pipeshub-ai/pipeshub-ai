import { Schema, model } from 'mongoose';

export interface SlackUserLinkDocument {
  orgId: string;
  workspaceId: string;
  slackUserId: string;
  teamId: string;
  userId: string;
  email: string;
  slackName: string;
  linkedAt: Date;
}

const slackUserLinkSchema = new Schema<SlackUserLinkDocument>(
  {
    orgId: { type: String, required: true },
    workspaceId: { type: String, required: true },
    slackUserId: { type: String, required: true },
    teamId: { type: String, required: true },
    userId: { type: String, required: true },
    email: { type: String, required: true },
    slackName: { type: String, default: '' },
    linkedAt: { type: Date, required: true },
  },
  { timestamps: true },
);

slackUserLinkSchema.index(
  { orgId: 1, workspaceId: 1, slackUserId: 1 },
  { unique: true, name: 'org_account' },
);
slackUserLinkSchema.index({ orgId: 1, userId: 1 });

export const SlackUserLinkModel = model<SlackUserLinkDocument>(
  'SlackUserLink',
  slackUserLinkSchema,
  'slack_user_links',
);
