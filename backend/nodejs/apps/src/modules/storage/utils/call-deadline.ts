/**
 * A deadline for a storage call on the paid-result sweep path. The SDK wrappers take no abort signal, so the call is raced
 * against a timer and its late answer is ignored. A call that does not answer in time ends as a failure (`TimeoutError`),
 * which a caller retries or fails on: it is never read as success or as "nothing there".
 */
export const PAID_RESULT_CALL_DEADLINE_MS = 30_000;

export async function withDeadline<T>(
  call: Promise<T>,
  ms: number,
  label: string,
): Promise<T> {
  let timer: NodeJS.Timeout | undefined;
  try {
    return await Promise.race([
      call,
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(
          () =>
            reject(
              Object.assign(new Error(`${label} did not answer in ${ms} ms`), {
                name: 'TimeoutError',
              }),
            ),
          ms,
        );
      }),
    ]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
    call.catch(() => undefined);
  }
}
