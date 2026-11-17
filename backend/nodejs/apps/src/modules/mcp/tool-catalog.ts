/**
 * Every tool `/mcp` can serve, read from the package's own registrations plus the code-graph
 * tools this app adds, so the admin list and the write-tool check never drift from what the
 * server mounts.
 */

import { registerCodeGraphTools } from './code-graph.tools';

export interface McpAccessTool {
  name: string;
  title: string;
  /** Left out while an admin does not allow write tools. */
  writes: boolean;
}

type RegisteredTools = Record<
  string,
  { title?: string; annotations?: { title?: string }; remove(): void }
>;

/** The SDK keeps registrations on a private field; the package itself reads it the same way. */
export function registeredTools(server: unknown): RegisteredTools {
  return (
    (server as { _registeredTools?: RegisteredTools })._registeredTools ?? {}
  );
}

const noop = (): void => undefined;

async function buildCatalog(): Promise<McpAccessTool[]> {
  const { createMCPServer } =
    await import('@pipeshub-ai/mcp/esm/mcp-server/server.js');
  const mount = (scopes?: Array<'read'>): RegisteredTools => {
    const { server } = createMCPServer({
      logger: {
        level: 'error',
        info: noop,
        debug: noop,
        warning: noop,
        error: noop,
      },
      dynamic: false,
      // Lists the citation tools, which a per-request server shows only to clients with views.
      apps: true,
      scopes,
      serverURL: 'http://127.0.0.1/api/v1',
      getSDK: () => {
        throw new Error('The tool catalog never calls a tool.');
      },
    });
    registerCodeGraphTools(server, {
      aiBackend: '',
      authorization: '',
    });
    return registeredTools(server);
  };
  const all = mount();
  // The package's `read` scope is what it serves when writing is not allowed.
  const readOnly = mount(['read']);
  return Object.entries(all).map(([name, tool]) => ({
    name,
    title: tool.title ?? tool.annotations?.title ?? name,
    writes: !(name in readOnly),
  }));
}

let catalog: Promise<McpAccessTool[]> | undefined;

export const McpToolCatalog = {
  /** Built once per process: the tools are fixed by the installed package. */
  list(): Promise<McpAccessTool[]> {
    catalog ??= buildCatalog().catch((error: unknown) => {
      catalog = undefined;
      throw error;
    });
    return catalog;
  },

  async isWriteTool(): Promise<(name: string) => boolean> {
    const writes = new Set(
      (await McpToolCatalog.list()).filter((t) => t.writes).map((t) => t.name),
    );
    return (name) => writes.has(name);
  },

  /** Forgets the catalog, so a test can build it from a stubbed package. */
  reset(): void {
    catalog = undefined;
  },
};
