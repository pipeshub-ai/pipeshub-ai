/**
 * Set by the Node MCP module on the requests its `pipeshub_chat` tool makes.
 * It can only narrow what a response shows (the answer-only audience), never
 * who the caller is: identity and `via` come from the verified credential.
 */
export const SURFACE_HEADER = 'x-pipeshub-surface';
