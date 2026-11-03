// Signing out does not reload the page in the desktop app and does not abort
// requests, so a response for the previous person can land after the reset.
// Every knowledge-base write that follows an `await` checks this first.
let generation = 0;

/** Captures the current session; the returned check turns false once it ends. */
export function kbSessionToken(): () => boolean {
  const captured = generation;
  return () => captured === generation;
}

/** Ends the current session, so every token taken before now reads as stale. */
export function endKbSession(): void {
  generation += 1;
}
