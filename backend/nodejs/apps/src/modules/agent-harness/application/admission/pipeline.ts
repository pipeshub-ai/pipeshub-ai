/**
 * One decision of an admission (R2 CP11, 3.4): a step reads what the steps
 * before it decided from the context, adds its own, or refuses by throwing.
 * Submit, the next-run drain and the scheduler's firing run the same steps;
 * a path differs only in its list.
 */
export interface AdmissionStep<C> {
  readonly name: string;
  run(context: C): Promise<void>;
}

/** Steps in order; the first refusal ends the admission and no later step runs. */
export class AdmissionPipeline<C> {
  constructor(readonly steps: readonly AdmissionStep<C>[]) {}

  async run(context: C): Promise<void> {
    for (const step of this.steps) await step.run(context);
  }
}

/**
 * Independent steps run at once, for latency; a refusal still wins in list
 * order, whichever settles first, so the answer never depends on timing.
 */
export function concurrently<C>(
  name: string,
  steps: readonly AdmissionStep<C>[],
): AdmissionStep<C> {
  return {
    name,
    run: async (context) => {
      const results = await Promise.allSettled(
        steps.map((step) => step.run(context)),
      );
      for (const result of results) {
        if (result.status === 'rejected') throw result.reason;
      }
    },
  };
}
