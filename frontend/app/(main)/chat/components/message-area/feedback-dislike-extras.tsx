'use client';

/** What a build adds to the thumbs-down popover; `onChange` sets more fields of the rating's request body. */
export interface FeedbackDislikeExtrasProps {
  messageId?: string;
  /** A durable run wrote the answer. */
  runLinked: boolean;
  onChange: (fields: Record<string, unknown>) => void;
}

/** Editions plug in via config.ts; this build adds nothing. */
export function FeedbackDislikeExtras(_props: FeedbackDislikeExtrasProps) {
  return null;
}
