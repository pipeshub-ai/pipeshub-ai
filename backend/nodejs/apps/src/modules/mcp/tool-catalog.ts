/**
 * Every tool `/mcp` can serve, read from the package's own registrations plus the code-graph
 * and entity tools this app adds (the entity tools with every action, since some deployment
 * serves each), so the admin list and the write-tool check never drift from what the server
 * mounts.
 */

import { z } from 'zod';
import {
  CODE_GRAPH_TOOL_NAMES,
  registerCodeGraphTools,
} from './code-graph.tools';
import {
  ALL_ENTITY_CAPABILITIES,
  ENTITY_TOOL_NAMES,
  registerEntityTools,
} from './entity.tools';
import { PUBLIC_MCP_TOOLS } from './constants/allowed-tools';

/** One argument of a tool, flattened from its input schema for people to read. */
export interface McpAccessToolArgument {
  name: string;
  /** `string`, `number`, `integer`, `boolean`, `object`, `enum`, `<type>[]`, or `a|b` for a union. */
  type: string;
  required: boolean;
  description?: string;
  /** The allowed values, for an enum (or an array of one). */
  values?: string[];
}

/** The hints a tool declares about what it does (MCP tool annotations). */
export interface McpAccessToolHints {
  readOnly: boolean;
  destructive: boolean;
  idempotent: boolean;
  openWorld: boolean;
}

export interface McpAccessTool {
  name: string;
  title: string;
  /** Left out while an admin does not allow write tools. */
  writes: boolean;
  /** What the AI model reads before it picks the tool. */
  description: string;
  arguments: McpAccessToolArgument[];
  hints: McpAccessToolHints;
  /** OAuth scopes an app or access token needs to call it; empty for one that calls nothing. */
  scopes: string[];
}

interface RegisteredTool {
  title?: string;
  description?: string;
  inputSchema?: unknown;
  annotations?: {
    title?: string;
    readOnlyHint?: boolean;
    destructiveHint?: boolean;
    idempotentHint?: boolean;
    openWorldHint?: boolean;
  };
  remove(): void;
}

type RegisteredTools = Record<string, RegisteredTool>;

/** The SDK keeps registrations on a private field; the package itself reads it the same way. */
export function registeredTools(server: unknown): RegisteredTools {
  return (
    (server as { _registeredTools?: RegisteredTools })._registeredTools ?? {}
  );
}

const noop = (): void => undefined;

interface JsonSchema {
  type?: string | string[];
  enum?: unknown[];
  items?: JsonSchema;
  anyOf?: JsonSchema[];
  oneOf?: JsonSchema[];
  description?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
}

function typeOf(schema: JsonSchema): string {
  if (schema.enum) return 'enum';
  const union = schema.anyOf ?? schema.oneOf;
  if (union)
    return [...new Set(union.map(typeOf).filter((t) => t !== 'null'))].join(
      '|',
    );
  if (schema.type === 'array')
    return `${schema.items ? typeOf(schema.items) : 'any'}[]`;
  if (Array.isArray(schema.type))
    return schema.type.filter((t) => t !== 'null').join('|');
  return schema.type ?? 'any';
}

function valuesOf(schema: JsonSchema): string[] | undefined {
  const values = schema.enum ?? schema.items?.enum;
  return values?.map(String);
}

/** A tool's arguments, read off the JSON Schema its zod input publishes to MCP clients. */
export function toolArguments(inputSchema: unknown): McpAccessToolArgument[] {
  if (!inputSchema) return [];
  let schema: JsonSchema;
  try {
    schema = z.toJSONSchema(inputSchema as z.ZodType, {
      io: 'input',
      unrepresentable: 'any',
    }) as JsonSchema;
  } catch {
    return [];
  }
  const required = new Set(schema.required ?? []);
  return Object.entries(schema.properties ?? {}).map(([name, property]) => {
    const values = valuesOf(property);
    return {
      name,
      type: typeOf(property),
      required: required.has(name),
      ...(property.description ? { description: property.description } : {}),
      ...(values?.length ? { values } : {}),
    };
  });
}

/**
 * The scopes a tool needs. The package states its own; the code tools need
 * code:read, and the entity tools a search scope (semantic:read, or
 * semantic:write, which the query service accepts too), as the MCP endpoint
 * checks them before mounting the tools.
 */
function toolScopes(
  name: string,
  packageScopes: (tool: string) => readonly string[] | undefined,
): string[] {
  if ((CODE_GRAPH_TOOL_NAMES as readonly string[]).includes(name))
    return ['code:read'];
  if ((ENTITY_TOOL_NAMES as readonly string[]).includes(name))
    return ['semantic:read'];
  return [...(packageScopes(name) ?? [])];
}

async function buildCatalog(): Promise<McpAccessTool[]> {
  const { createMCPServer } =
    await import('@pipeshub-ai/mcp/esm/mcp-server/server.js');
  const { requiredOAuthScopes } =
    await import('@pipeshub-ai/mcp/esm/mcp-server/oauth-scopes.js');
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
      // The catalog lists what a request's server can mount, so it applies the same allowlist.
      allowedTools: [...PUBLIC_MCP_TOOLS],
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
    registerEntityTools(
      server,
      { aiBackend: '', authorization: '' },
      ALL_ENTITY_CAPABILITIES,
    );
    return registeredTools(server);
  };
  const all = mount();
  // The package's `read` scope is what it serves when writing is not allowed.
  const readOnly = mount(['read']);
  return Object.entries(all).map(([name, tool]) => ({
    name,
    title: tool.title ?? tool.annotations?.title ?? name,
    writes: !(name in readOnly),
    description: tool.description ?? '',
    arguments: toolArguments(tool.inputSchema),
    hints: {
      readOnly: tool.annotations?.readOnlyHint === true,
      destructive: tool.annotations?.destructiveHint === true,
      idempotent: tool.annotations?.idempotentHint === true,
      openWorld: tool.annotations?.openWorldHint === true,
    },
    scopes: toolScopes(name, requiredOAuthScopes),
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
