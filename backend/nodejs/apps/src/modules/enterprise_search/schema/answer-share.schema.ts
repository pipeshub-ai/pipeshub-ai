import { Schema, Types } from 'mongoose';

/** Who an answer is shared with in place: everyone in the chat, or the people listed in `consentTo`. */
export const ANSWER_SHARE_SCOPES = ['everyone', 'people'] as const;
export type AnswerShareScope = (typeof ANSWER_SHARE_SCOPES)[number];

/** `open` until shared or dismissed; a request for an older version, or by someone no longer the same member, is void. */
export const SHARE_REQUEST_STATES = [
  'open',
  'shared',
  'dismissed',
  'void',
] as const;
export type ShareRequestState = (typeof SHARE_REQUEST_STATES)[number];

/** The asker's consent to show one version of an answer to people who can't open what it used. */
export interface AnswerConsent {
  by: Types.ObjectId;
  at: Date;
  /** The `labelVer` shared: a new version of the answer is not covered. */
  ver: string;
  scope: AnswerShareScope;
  /** 'earlier': the owner's one-step consent to answers written before labels. */
  how?: 'earlier';
}

/** Stores an `AnswerConsent`. */
export const answerConsentSchema = new Schema(
  {
    by: { type: Schema.Types.ObjectId, required: true },
    at: { type: Date, required: true },
    // The `labelVer` shared: a new version of the answer is not covered.
    ver: { type: String, required: true },
    scope: { type: String, enum: ANSWER_SHARE_SCOPES, required: true },
    // 'earlier': the owner's one-step consent to answers written before labels.
    how: { type: String, enum: ['earlier'] },
  },
  { _id: false },
);

/** A person a share with people covers, in the membership they had when it was made. */
export interface AnswerConsentEntry {
  userId: Types.ObjectId;
  memberSince: string;
}

/** Stores an `AnswerConsentEntry`. */
export const answerConsentEntrySchema = new Schema(
  {
    userId: { type: Schema.Types.ObjectId, required: true },
    memberSince: { type: String, required: true },
  },
  { _id: false },
);

/** A member's ask to see one version of an answer: one per person per version, ever. */
export interface ShareRequest {
  by: Types.ObjectId;
  at: Date;
  ver: string;
  state: ShareRequestState;
  /** The asking member's membership when they asked: a removal and re-add voids the request. */
  memberSince?: string;
}

/** Stores a `ShareRequest`. */
export const shareRequestSchema = new Schema(
  {
    by: { type: Schema.Types.ObjectId, required: true },
    at: { type: Date, required: true },
    ver: { type: String, required: true },
    state: { type: String, enum: SHARE_REQUEST_STATES, required: true },
    // The asking member's membership when they asked: a removal and re-add voids the request.
    memberSince: { type: String },
  },
  { _id: false },
);
