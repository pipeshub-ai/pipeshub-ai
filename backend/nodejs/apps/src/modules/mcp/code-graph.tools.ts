/**
 * Code-graph tools for MCP clients (Claude Code, Cursor).
 *
 * The curated tools come from @pipeshub-ai/mcp, which has none for code; these
 * are registered beside them on the same per-request server. Each forwards the
 * caller's own bearer token to the query service's `/api/v1/code-graph`
 * routes, which check the OAuth scope and every record's ACL.
 */

import { z } from 'zod';
import { AIServiceCommand } from '../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../libs/enums/http-methods.enum';

interface ToolResult {
  [key: string]: unknown;
  content: Array<{ type: 'text'; text: string }>;
  isError?: boolean;
}

/** The slice of McpServer this module uses, so tests can pass a recorder. */
export interface ToolRegistrar {
  registerTool(
    name: string,
    config: {
      description: string;
      inputSchema?: z.ZodRawShape;
      annotations?: Record<string, unknown>;
    },
    handler: (args: Record<string, unknown>) => Promise<ToolResult>,
  ): unknown;
}

export interface CodeGraphToolDeps {
  aiBackend: string;
  authorization: string;
}

const READ_ONLY = {
  readOnlyHint: true,
  destructiveHint: false,
  idempotentHint: true,
  openWorldHint: false,
};

const TIMEOUT_MS = 60_000;

const repo = z
  .string()
  .min(1)
  .optional()
  .describe(
    'Repository as `owner/name` (or just `name` when unique), from pipeshub_code_repos. ' +
      'Optional when `record_id` is given.',
  );
const includeTests = z
  .boolean()
  .default(false)
  .describe('Include test files and test symbols. False by default.');
const filePath = z
  .string()
  .min(1)
  .optional()
  .describe('Repo-relative file path, copied from an earlier result.');
const recordId = z
  .string()
  .min(1)
  .optional()
  .describe(
    "A code file's `recordId` from a pipeshub_search hit; use it instead of `file_path`.",
  );
const qualifiedName = z
  .string()
  .min(1)
  .optional()
  .describe(
    'Symbol name exactly as results show it, e.g. `function:parse_config` or ' +
      '`method:Client.fetch`. Copy it; the `function:`/`method:` prefix is not guessable.',
  );
const edgeTypes = z
  .array(z.string())
  .optional()
  .describe(
    'Relations to follow (CALLS, IMPORTS, INHERITS, CONTAINS). Omit for all.',
  );

const COVERAGE =
  'Cross-file CALLS/IMPORTS edges exist for Python, JavaScript and TypeScript only; ' +
  'a `coverage` field says when an answer may be incomplete, so confirm with ' +
  'pipeshub_search before relying on an empty result.';

async function call(
  deps: CodeGraphToolDeps,
  method: HttpMethod,
  path: string,
  body?: Record<string, unknown>,
): Promise<ToolResult> {
  // AIServiceCommand adds the current request id itself.
  const headers: Record<string, string> = {
    Authorization: deps.authorization,
    'Content-Type': 'application/json',
  };
  try {
    const response = await new AIServiceCommand<Record<string, unknown>>({
      uri: `${deps.aiBackend}/api/v1/code-graph${path}`,
      method,
      headers,
      body,
      timeoutMs: TIMEOUT_MS,
      maxAttempts: 1,
    }).execute();
    const data: Record<string, unknown> = response.data ?? {};
    const failed = response.statusCode >= 400 || 'error' in data;
    return {
      content: [{ type: 'text', text: JSON.stringify(data) }],
      ...(failed ? { isError: true } : {}),
    };
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    return {
      content: [
        {
          type: 'text',
          text: JSON.stringify({ error: `code graph unavailable: ${message}` }),
        },
      ],
      isError: true,
    };
  }
}

export const CODE_GRAPH_TOOL_NAMES = [
  'pipeshub_code_repos',
  'pipeshub_code_symbols',
  'pipeshub_code_neighbours',
  'pipeshub_code_read',
  'pipeshub_code_path',
] as const;

export function registerCodeGraphTools(
  server: ToolRegistrar,
  deps: CodeGraphToolDeps,
): void {
  server.registerTool(
    'pipeshub_code_repos',
    {
      description:
        'List the indexed code repositories you can read. Every other ' +
        'pipeshub_code_* tool takes one of these names as `repo`.',
      annotations: READ_ONLY,
    },
    async () => call(deps, HttpMethod.GET, '/repos'),
  );

  server.registerTool(
    'pipeshub_code_symbols',
    {
      description:
        'What a directory or file in an indexed repo holds. `select` a directory ' +
        '(lists its children), a file (the symbols it defines, with line ranges) ' +
        'or a glob (`src/**`). Results are ranked by how connected each symbol is.',
      inputSchema: {
        repo: repo
          .unwrap()
          .describe('Repository as `owner/name`, from pipeshub_code_repos.'),
        select: z
          .string()
          .min(1)
          .describe(
            'A directory, a file path, or a glob. Not a symbol or free text.',
          ),
        kinds: z
          .array(z.string())
          .optional()
          .describe(
            'Keep only these kinds, e.g. ["class","function","method"].',
          ),
        limit: z
          .number()
          .int()
          .min(1)
          .max(500)
          .optional()
          .describe('Maximum rows.'),
        include_tests: includeTests,
      },
      annotations: READ_ONLY,
    },
    async (args) => call(deps, HttpMethod.POST, '/query', args),
  );

  server.registerTool(
    'pipeshub_code_neighbours',
    {
      description:
        'Callers, callees, imports and inheritance of a file or symbol: what ' +
        'reaches it (`direction: "inbound"`) and what it reaches (`"outbound"`). Each row ' +
        `has repo, file_path, line range and qualified name. ${COVERAGE}`,
      inputSchema: {
        repo,
        file_path: filePath,
        record_id: recordId,
        qualified_name: qualifiedName,
        direction: z.enum(['inbound', 'outbound', 'any']).default('any'),
        edge_types: edgeTypes,
        depth: z
          .number()
          .int()
          .min(1)
          .max(3)
          .default(1)
          .describe('Hops to walk.'),
        limit: z.number().int().min(1).max(200).optional(),
        offset: z.number().int().min(0).default(0),
        include_tests: includeTests,
      },
      annotations: READ_ONLY,
    },
    async (args) => call(deps, HttpMethod.POST, '/neighbours', args),
  );

  server.registerTool(
    'pipeshub_code_read',
    {
      description:
        'Read indexed source: one symbol (`qualified_name`), a line range ' +
        '(`lines: "380-420"`), or a whole file (bounded by `max_lines`; the result ' +
        'says where it stopped). Reads the indexed copy, which can lag the repo.',
      inputSchema: {
        repo,
        file_path: filePath,
        record_id: recordId,
        qualified_name: qualifiedName,
        lines: z
          .string()
          .regex(/^\d+-\d+$/)
          .optional()
          .describe('A line range such as `40-80`.'),
        max_lines: z.number().int().min(1).max(2000).optional(),
        include_tests: includeTests,
      },
      annotations: READ_ONLY,
    },
    async (args) => call(deps, HttpMethod.POST, '/read', args),
  );

  server.registerTool(
    'pipeshub_code_path',
    {
      description:
        'How symbol A reaches symbol B in one repo, hop by hop with a sentence ' +
        'per hop. If only B reaches A, that path comes back with `reversed: true`. ' +
        `Edges across languages or over HTTP are not in the graph. ${COVERAGE}`,
      inputSchema: {
        repo: repo
          .unwrap()
          .describe('Repository as `owner/name`, from pipeshub_code_repos.'),
        file_path_a: z.string().min(1),
        qualified_name_a: z.string().min(1),
        file_path_b: z.string().min(1),
        qualified_name_b: z.string().min(1),
        max_depth: z
          .number()
          .int()
          .min(1)
          .max(8)
          .optional()
          .describe('Most hops to search.'),
        edge_types: edgeTypes,
        undirected: z
          .boolean()
          .default(false)
          .describe('Ignore direction: only for "are these related at all".'),
        include_tests: includeTests,
      },
      annotations: READ_ONLY,
    },
    async (args) => call(deps, HttpMethod.POST, '/path', args),
  );
}
