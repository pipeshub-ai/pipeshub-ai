/**
 * AG-UI protocol helpers for the Node.js proxy layer — see the AG-UI
 * migration plan. Node intercepts a handful of event names to persist
 * conversations to Mongo (today: `complete`/`error`/`ask_user_question`);
 * in `agui` mode the SAME interceptions key off the AG-UI names instead
 * (`RUN_FINISHED`/`RUN_ERROR`/`CUSTOM{name:"ask_user_question"}`), and
 * Node's own outbound frames (the early `connected` event, the
 * re-emitted `complete`) are reframed to `CUSTOM`/`RUN_FINISHED` too.
 *
 * Protocol is negotiated EXPLICITLY on the request body Node hand-builds
 * for the Python backend (`aiPayload.protocol`) — header passthrough
 * never reaches Python, since Node constructs that request itself. See
 * `resolveProtocol()`.
 */

export const AGUI_PROTOCOL = 'agui' as const;
export const LEGACY_PROTOCOL = 'legacy' as const;
export type SSEProtocol = typeof AGUI_PROTOCOL | typeof LEGACY_PROTOCOL;

/**
 * Negotiate the wire protocol for one request: an explicit `protocol`
 * field/query param wins, defaulting to legacy for every existing client
 * (Slack bot, internal/service-account routes, any API caller that never
 * opts in). The only recognized non-legacy value is `"agui"` — anything
 * else (a typo, an unrelated truthy value) collapses to legacy rather
 * than erroring.
 */
export const resolveProtocol = (
  body: Record<string, unknown> | undefined,
  query: Record<string, unknown> | undefined,
): SSEProtocol => {
  const value = body?.['protocol'] ?? query?.['protocol'];
  return value === AGUI_PROTOCOL ? AGUI_PROTOCOL : LEGACY_PROTOCOL;
};

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
