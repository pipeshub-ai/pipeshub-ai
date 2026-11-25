/**
 * Node's equivalent of `pytest.mark.xfail(strict=True)` for the security audit's proof tests
 * (one finding per case). The body asserts the SECURE behaviour; today it fails, so the case
 * passes. Once the fix lands the body passes and this case fails, naming the finding: turn it into a
 * plain `it` so it stays in the gate as the regression test.
 */
export function knownHole(
  findingId: string,
  title: string,
  body: () => Promise<void> | void,
): Mocha.Test {
  return it(`[${findingId} known hole] ${title}`, async () => {
    let holeReason: unknown;
    try {
      await body();
    } catch (error) {
      holeReason = error;
    }
    // Only a failed assertion proves the hole: a setup error must not pass for one.
    if (
      holeReason !== undefined &&
      (holeReason as { name?: unknown }).name !== 'AssertionError'
    ) {
      throw holeReason instanceof Error ? holeReason : new Error('The body threw a value that is not an Error');
    }
    if (holeReason === undefined) {
      throw new Error(
        `${findingId} no longer reproduces: the secure behaviour holds. Make this a plain it() and close the finding.`,
      );
    }
  });
}
