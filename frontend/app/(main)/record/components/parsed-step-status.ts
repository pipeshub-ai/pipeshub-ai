/**
 * Status for the "Parsed" step of a record's stage track, or null to hide the step.
 *
 * Records indexed by the in-process pipeline before it wrote `parsingStatus`
 * hold the field's default, NOT_STARTED, or no field at all. A record that is
 * searchable or empty was parsed, so its step reads COMPLETED. With no
 * `parsingStatus` and nothing to infer it from, the step is hidden rather than
 * shown as NOT_STARTED.
 */
export function parsedStepStatus(
  parsingStatus: string | null | undefined,
  indexingStatus: string | null | undefined,
): string | null {
  if (parsingStatus && parsingStatus !== 'NOT_STARTED') return parsingStatus;
  if (indexingStatus === 'COMPLETED' || indexingStatus === 'EMPTY') return 'COMPLETED';
  return parsingStatus || null;
}
