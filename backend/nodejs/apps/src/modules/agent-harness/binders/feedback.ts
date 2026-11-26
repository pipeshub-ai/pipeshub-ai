import { RunFeedbackService } from '../application/run-feedback';
import type { ExecutionStore } from '../ports/execution-store';
import {
  noRatedAnswerSink,
  type RatedAnswerSink,
} from '../ports/rated-answers';
import { boundOrNull, HarnessBinder } from './context';
import {
  AGENT_HARNESS_EXECUTION_STORE,
  AGENT_HARNESS_RATED_ANSWER_SINK,
  AGENT_HARNESS_RUN_FEEDBACK,
} from './tokens';

/** G-15: ratings linked to their run and agent version, passed to the rated-answer sink bound before this binder. */
export const bindRunFeedback: HarnessBinder = ({ container }) => {
  if (container.isBound(AGENT_HARNESS_RUN_FEEDBACK)) return;
  container
    .bind<RunFeedbackService>(AGENT_HARNESS_RUN_FEEDBACK)
    .toDynamicValue(
      (context) =>
        new RunFeedbackService(
          context.container.get<ExecutionStore>(AGENT_HARNESS_EXECUTION_STORE),
          boundOrNull<RatedAnswerSink>(
            context.container,
            AGENT_HARNESS_RATED_ANSWER_SINK,
          ) ?? noRatedAnswerSink,
        ),
    )
    .inSingletonScope();
};
