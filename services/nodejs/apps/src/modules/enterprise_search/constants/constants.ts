export const CONFIDENCE_LEVELS = {
  HIGH: 'High' as const,
  MEDIUM: 'Medium' as const,
  LOW: 'Low' as const,
  VERY_HIGH: 'Very High' as const,
  UNKNOWN: 'Unknown' as const,
} as const;

// Create a type from the object values
export type ConfidenceLevel =
  (typeof CONFIDENCE_LEVELS)[keyof typeof CONFIDENCE_LEVELS];

export const CONVERSATION_STATUS = {
  COMPLETE: 'Complete' as const,
  FAILED: 'Failed' as const,
  INPROGRESS: 'Inprogress' as const,
  NONE: 'None' as const,
} as const;

// Create a type from the object values
export type ConversationStatus =
  (typeof CONVERSATION_STATUS)[keyof typeof CONVERSATION_STATUS];

// Conversation fail reason type - this is now just informational since we store as string
export const FAIL_REASON_TYPE = {
  AI_SERVICE_UNAVAILABLE: 'ai_service_unavailable' as const,
  AI_SERVICE_ERROR: 'ai_service_error' as const,
  AI_API_ERROR: 'ai_api_error' as const,
  CONNECTION_ERROR: 'connection_error' as const,
  INTERNAL_ERROR: 'internal_error' as const,
  INVALID_REQUEST: 'invalid_request' as const,
  TIMEOUT: 'timeout' as const,
  PROCESSING_ERROR: 'processing_error' as const,
  UNKNOWN_ERROR: 'unknown_error' as const,
} as const;

// Create a type from the object values
export type FailReasonType =
  (typeof FAIL_REASON_TYPE)[keyof typeof FAIL_REASON_TYPE];
