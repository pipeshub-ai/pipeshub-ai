import mongoose, { Document, Schema, Model, Types } from 'mongoose';
import {
  AGENT_CALL_EVENT_RETENTION_DAYS,
  type AgentTokenKind,
} from '../../../libs/repositories/agent-call-event/types';

/**
 * One call made with a bearer token (PAT, OAuth access token or delegated/service token).
 * Nothing the caller sent is kept: no body, query string, address or token value.
 * Successful GETs share one row per token, method, route and minute (`bucket`), counted in `count`.
 */
export interface IAgentCallEvent extends Document {
  orgId: Types.ObjectId;
  userId: Types.ObjectId;
  /** The stored token's id (the one token lists and revocation use), never the token, its hash or its `jti`. */
  tokenId: string;
  tokenKind: AgentTokenKind;
  oauthClientId?: string;
  userAgent?: string;
  channel?: string;
  method: string;
  /** The route template (`/api/v1/knowledgeBase/:kbId/records`), never the URL. */
  route: string;
  mcpTool?: string;
  statusCode: number;
  durationMs: number;
  scopeDenied: boolean;
  requestId?: string;
  count: number;
  /** The UTC minute of a rolled-up GET row; absent on every other row. */
  bucket?: Date;
  createdAt: Date;
}

const AgentCallEventSchema = new Schema<IAgentCallEvent>({
  orgId: { type: Schema.Types.ObjectId, ref: 'org', required: true },
  userId: { type: Schema.Types.ObjectId, ref: 'users', required: true },
  tokenId: { type: String, required: true, maxlength: 128 },
  tokenKind: {
    type: String,
    enum: ['pat', 'oauth', 'delegated'],
    required: true,
  },
  oauthClientId: { type: String, maxlength: 200 },
  userAgent: { type: String, maxlength: 80 },
  channel: { type: String, maxlength: 40 },
  method: { type: String, required: true, maxlength: 10 },
  route: { type: String, required: true, maxlength: 300 },
  mcpTool: { type: String, maxlength: 200 },
  statusCode: { type: Number, required: true },
  durationMs: { type: Number, required: true },
  scopeDenied: { type: Boolean, required: true, default: false },
  requestId: { type: String, maxlength: 64 },
  count: { type: Number, required: true, default: 1 },
  bucket: { type: Date },
  createdAt: { type: Date, required: true, default: Date.now },
});

// The reading order of the admin view: one organisation, newest first.
AgentCallEventSchema.index({ orgId: 1, createdAt: -1 });
AgentCallEventSchema.index({ orgId: 1, tokenId: 1, createdAt: -1 });
// One roll-up row per token, route and minute, so the upsert has a single target.
AgentCallEventSchema.index(
  { orgId: 1, tokenId: 1, method: 1, route: 1, bucket: 1 },
  { unique: true, partialFilterExpression: { bucket: { $exists: true } } },
);
AgentCallEventSchema.index(
  { createdAt: 1 },
  { expireAfterSeconds: AGENT_CALL_EVENT_RETENTION_DAYS * 24 * 60 * 60 },
);

export const AgentCallEvent: Model<IAgentCallEvent> =
  mongoose.model<IAgentCallEvent>(
    'agentCallEvent',
    AgentCallEventSchema,
    'agentCallEvents',
  );
