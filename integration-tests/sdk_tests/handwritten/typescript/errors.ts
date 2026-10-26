import { expect } from "vitest";
import { PipeshubError } from "../../models/errors/pipeshub-error.js";

/** Check that a call fails with the given HTTP status. */
export async function expectStatus(call: Promise<unknown>, status: number): Promise<void> {
  const error = await call.then(
    () => undefined,
    (e: unknown) => e,
  );
  expect(error, `expected HTTP ${status}, the call succeeded`).toBeInstanceOf(PipeshubError);
  expect((error as PipeshubError).statusCode, String(error)).toBe(status);
}
