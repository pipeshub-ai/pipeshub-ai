/**
 * AG-UI protocol helpers for the Node.js proxy layer — see the AG-UI
 * migration plan. Node intercepts a handful of event names to persist
 * conversations to Mongo, keying off the AG-UI names
 * (`RUN_FINISHED`/`RUN_ERROR`/`CUSTOM{name:"ask_user_question"}`), and
 * Node's own outbound frames (the early `connected` event, the
 * re-emitted `complete`) are reframed to `CUSTOM`/`RUN_FINISHED` too.
 *
 * AG-UI is the only supported wire protocol; the legacy SSE format has
 * been removed. `LEGACY_PROTOCOL` and the (now unreachable) legacy
 * formatting branches remain in this proxy layer purely as dead code to
 * minimize churn — no caller can select them anymore. See
 * `resolveProtocol()`.
 */

export const AGUI_PROTOCOL = 'agui' as const;
export const LEGACY_PROTOCOL = 'legacy' as const;
export type SSEProtocol = typeof AGUI_PROTOCOL | typeof LEGACY_PROTOCOL;

/**
 * AG-UI is the only supported protocol — always resolve to it regardless
 * of what the caller sends. Kept as a function (rather than inlining the
 * constant at call sites) so the protocol can be reintroduced as a real
 * negotiation later without touching every call site.
 */
export const resolveProtocol = (
  _body?: Record<string, unknown>,
  _query?: Record<string, unknown>,
): SSEProtocol => AGUI_PROTOCOL;

export const isAGUI = (protocol: SSEProtocol | undefined): boolean =>
  protocol === AGUI_PROTOCOL;

/**
 * One AG-UI event, hybrid-framed exactly like the Python side's
 * `protocol/agui.py::frame()` — `event:` line carries the AG-UI type
 * name (so this file's own line-based parsing, and the frontend's
 * `parseSSEBuffer`, keep working unchanged), `data:` carries the full
 * AG-UI JSON including `"type"`.
 */
export const frameAGUI = (
  type: string,
  fields: Record<string, unknown> = {},
): string => `event: ${type}\ndata: ${JSON.stringify({ type, ...fields })}\n\n`;

/** AG-UI type names this proxy layer needs to recognize on the wire. */
export const AGUIEventType = {
  RUN_STARTED: 'RUN_STARTED',
  RUN_FINISHED: 'RUN_FINISHED',
  RUN_ERROR: 'RUN_ERROR',
  STATE_SNAPSHOT: 'STATE_SNAPSHOT',
  CUSTOM: 'CUSTOM',
} as const;
