'use client';

export type FeedbackKind = 'issue' | 'feedback';

export interface CreateFeedbackResponse {
  outcome: 'sent' | 'pending' | 'failed' | 'no_smtp';
}
