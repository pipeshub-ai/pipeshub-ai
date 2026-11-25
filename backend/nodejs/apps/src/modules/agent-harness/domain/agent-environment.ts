import { z } from 'zod/v3';
import { pipeshubRunSpecExtensionSchema } from '../contracts/generated/ext/pipeshub/run-spec';

/**
 * C20.18 / G-17 (WP-100): the environment an agent's editor defined for its engine runs (agent builder: a setup
 * script, non-secret variables, the folders the setup fills; or a repository's
 * devcontainer.json imported into the agent). The agent authority reports it with the run definition; admission pins
 * it as `x.pipeshub.environment` on an engine run and a code_task parent (its engine child inherits it), and only a
 * worker that reads it (`environment_pin`) claims such a run. The worker sets it up, or restores its post-setup
 * snapshot, in each fresh sandbox before the person's workspace exists.
 */
export const ENVIRONMENT_PIN_FEATURE = 'environment_pin' as const;

export const environmentSchema =
  pipeshubRunSpecExtensionSchema.shape.environment.unwrap();
export type PinnedEnvironment = z.infer<typeof environmentSchema>;

/** An environment as the authority reports it: a stored one it cannot read arrives invalid and refuses the agent. */
export const agentEnvironmentSchema = environmentSchema;

/** Whether the run hands work to an engine (an engine run, or a loop run admitted to hand off through code_task). */
export const pinsEnvironment = (
  environment: PinnedEnvironment | null | undefined,
  handsToEngine: boolean,
): environment is PinnedEnvironment =>
  handsToEngine && environment !== undefined && environment !== null;
