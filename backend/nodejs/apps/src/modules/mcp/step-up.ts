/**
 * MCP authorization step-up: a call to a tool the token's scopes do not cover is answered with
 * HTTP 403 and `WWW-Authenticate: Bearer error="insufficient_scope"`, so a client that supports
 * it can ask the person for just those scopes and retry.
 */

type ToolScopes = (toolName: string) => readonly string[] | undefined;

const calledTools = (body: unknown): string[] => {
  const messages: unknown[] = Array.isArray(body) ? body : [body];
  return messages.flatMap((m) => {
    const msg = m as { method?: unknown; params?: { name?: unknown } } | null;
    return msg?.method === 'tools/call' && typeof msg.params?.name === 'string'
      ? [msg.params.name]
      : [];
  });
};

/** The scopes a tools/call in `body` needs and `held` lacks; empty when nothing is missing. */
export function missingToolScopes(
  body: unknown,
  held: readonly string[],
  required: ToolScopes,
): string[] {
  const missing = new Set<string>();
  for (const tool of calledTools(body)) {
    for (const scope of required(tool) ?? []) {
      if (!held.includes(scope)) missing.add(scope);
    }
  }
  return [...missing];
}

/**
 * The challenge for a step-up. `scope` names everything the client should ask for: what it has
 * plus what is missing, as the MCP authorization spec recommends, so the new token keeps the
 * access the old one had.
 */
export function insufficientScopeChallenge(
  held: readonly string[],
  missing: readonly string[],
  resourceMetadataUrl: string,
): string {
  const scope = [...new Set([...held, ...missing])].join(' ');
  return `Bearer error="insufficient_scope", scope="${scope}", resource_metadata="${resourceMetadataUrl}"`;
}
