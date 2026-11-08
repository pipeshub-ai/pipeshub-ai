import type { RecordDetailsResponse, StageStateSummary } from '@/app/(main)/knowledge-base/types';
import { parsedStepStatus } from './parsed-step-status';

export interface StageTrackStep {
  key: string;
  label: string;
  status: string | null | undefined;
  hint?: string;
}

type Translate = (key: string, options?: Record<string, unknown>) => string;

const CLASSIFY_STAGE = 'classify';
const ENTITIES_STAGE = 'entities';

/** Tooltip text for a stage: its attempt count plus the failure or skip reason, if any. */
function stageHint(state: StageStateSummary | undefined, t: Translate): string | undefined {
  if (!state) return undefined;
  return [t('recordView.stageAttempt', { n: state.attempt }), state.reason?.trim()]
    .filter(Boolean)
    .join(' · ');
}

/**
 * Steps of a record's stage track, in pipeline order.
 *
 * Entity extraction has no record-level status field, so its step reads the
 * `entities` stage state. With no such entry (older records, or a backend that
 * sends no `stageStates`) the step is hidden rather than shown as NOT_STARTED.
 */
export function buildStageTrackSteps(
  record: Pick<RecordDetailsResponse['record'], 'parsingStatus' | 'indexingStatus' | 'extractionStatus'>,
  stageStates: StageStateSummary[] | null | undefined,
  t: Translate,
): StageTrackStep[] {
  const states = Array.isArray(stageStates) ? stageStates : [];
  const classify = states.find((s) => s?.stage === CLASSIFY_STAGE);
  const entities = states.find((s) => s?.stage === ENTITIES_STAGE);

  const parsedStatus = parsedStepStatus(record.parsingStatus, record.indexingStatus);
  return [
    ...(parsedStatus ? [{ key: 'parsed', label: t('recordView.stageParsed'), status: parsedStatus }] : []),
    { key: 'searchable', label: t('recordView.stageSearchable'), status: record.indexingStatus },
    {
      key: 'classified',
      label: t('recordView.stageClassified'),
      status: record.extractionStatus,
      hint: stageHint(classify, t),
    },
    ...(entities
      ? [
          {
            key: 'entities',
            label: t('recordView.stageEntities'),
            status: entities.status,
            hint: stageHint(entities, t),
          },
        ]
      : []),
  ];
}
