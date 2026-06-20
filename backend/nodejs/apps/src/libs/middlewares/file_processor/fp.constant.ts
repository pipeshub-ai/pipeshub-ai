export enum FileProcessingType {
  BUFFER = 'buffer',
  JSON = 'json',
}

export enum FileProcessingMode {
  SINGLE = 'single',
  MULTIPLE = 'multiple',
}

// Stable codes describing why a single file was rejected during processing.
// Sent to the client so it can localize/group failures (see frontend
// upload-store / upload-progress-tracker).
export enum FileRejectionReason {
  EXCEEDS_SIZE_LIMIT = 'EXCEEDS_SIZE_LIMIT',
  UNSUPPORTED_TYPE = 'UNSUPPORTED_TYPE',
  DUPLICATE_NAME = 'DUPLICATE_NAME',
}

