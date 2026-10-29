/**
 * Code-graph tools for MCP clients (Claude Code, Cursor).
 *
 * The curated tools come from @pipeshub-ai/mcp, which has none for code; these
 * are registered beside them on the same per-request server. Each forwards the
 * caller's own bearer token to the query service's `/api/v1/code-graph`
 * routes, which check the OAuth scope (`code:read`) and every record's ACL.
 * The controller registers them only for callers who can use them.
 */

import { z } from 'zod';
import { AIServiceCommand } from '../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../libs/enums/http-methods.enum';
import { integerCheck } from '../../libs/utils/zod-formats';

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
      inputSchema?: z.ZodRawShape | z.ZodObject;
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

/**
 * Appended to the server's instructions for a caller with indexed code. The
 * package's own instructions are about documents only, so without this an
 * agent working in a checkout reads PipesHub as unrelated to its code.
 */
export const CODE_GRAPH_INSTRUCTIONS = `

## Code repositories

This caller has indexed code repositories. For a question about code in one
of them (where a feature or behaviour is implemented, what calls what, how A
reaches B, what a change breaks) use the \`pipeshub_code_*\` tools rather
than \`pipeshub_search\` or reading files one by one:

1. \`pipeshub_code_repos\` lists the repositories; pass one as \`repo\`.
2. Find the code: \`pipeshub_code_semantic_search\` when you know what it
   does but not its name, \`pipeshub_code_search\` for a name or a string.
3. Follow it: \`pipeshub_code_neighbours\` (callers, callees),
   \`pipeshub_code_path\` (A to B), \`pipeshub_code_impact\` (what a change
   breaks), \`pipeshub_code_outline\` and \`pipeshub_code_read\` to read.
`;

const repo = z
  .string()
  .min(1)
  .optional()
  .describe(
    'Repo `owner/name` from pipeshub_code_repos; optional with `record_id`.',
  );
const includeTests = z.boolean().default(false).describe('Include test files.');
const filePath = z
  .string()
  .min(1)
  .optional()
  .describe('Repo-relative path from an earlier result.');
const recordId = z
  .string()
  .min(1)
  .optional()
  .describe(
    "A code file's `recordId` from pipeshub_search, instead of `file_path`.",
  );
const qualifiedName = z
  .string()
  .min(1)
  .optional()
  .describe(
    'Name exactly as a result gave it, e.g. `method:Client.fetch`; the prefix is not guessable.',
  );
const edgeTypes = z
  .array(z.string())
  .optional()
  .describe('CALLS, IMPORTS, INHERITS, CONTAINS; omit for all.');

const repoRequired = repo
  .unwrap()
  .describe('Repo `owner/name` from pipeshub_code_repos.');

// "40-80", and the "[40, 80]" models often send instead: a turn lost to a
// validation error costs more than accepting it.
const LINE_RANGE = /^\[?\s*\d+\s*[-,]\s*\d+\s*\]?$/;

const COVERAGE =
  'The graph can miss calls made through callbacks or dynamic dispatch, and a ' +
  '`coverage` field says when an answer may be incomplete: confirm an empty result ' +
  'with pipeshub_code_search (`mode: "text"`) before relying on it.';

// The query service writes its guidance with the in-product agent's tool
// names; over MCP those tools have other names, which an agent cannot call.
// Only guidance is rewritten: returned source can hold the same words.
const GUIDANCE_FIELDS = ['note', 'hint', 'error', 'next'] as const;
const MCP_TOOL_NAMES: Record<string, string> = {
  get_neighbour: 'pipeshub_code_neighbours',
  read_code: 'pipeshub_code_read',
  search_code: 'pipeshub_code_search',
  find_symbol_path: 'pipeshub_code_path',
  query_code_graph: 'pipeshub_code_symbols',
  search_semantic: 'pipeshub_code_semantic_search',
};
const INTERNAL_TOOL_NAMES = new RegExp(
  `\\b(${Object.keys(MCP_TOOL_NAMES).join('|')})\\b`,
  'g',
);

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
    for (const key of GUIDANCE_FIELDS) {
      const value = data[key];
      if (typeof value === 'string') {
        data[key] = value.replace(
          INTERNAL_TOOL_NAMES,
          (name) => MCP_TOOL_NAMES[name] ?? name,
        );
      }
    }
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
  'pipeshub_code_search',
  'pipeshub_code_semantic_search',
  'pipeshub_code_outline',
  'pipeshub_code_impact',
  'pipeshub_code_entry_points',
] as const;

const REPOS_CHECK_TIMEOUT_MS = 5_000;
const REPOS_CHECK_TTL_MS = 60_000;
const REPOS_CHECK_MAX_ENTRIES = 10_000;
// shortcut: per-process cache, so a newly connected repo can take up to the TTL
// to show its tools on each replica; move to Redis if that lag matters.
const reposCheckCache = new Map<string, { has: boolean; at: number }>();

/**
 * Whether the caller can read at least one indexed repo, so a client in an org
 * without a code connector is not shown nine tools that can only fail. Asks
 * the same `/repos` route pipeshub_code_repos does, cached per caller. When the
 * query service cannot answer, counts as yes: the tools then report the outage
 * themselves instead of silently vanishing.
 */
export async function callerHasCodeRepos(
  deps: CodeGraphToolDeps,
  cacheKey: string,
): Promise<boolean> {
  const hit = reposCheckCache.get(cacheKey);
  if (hit && Date.now() - hit.at < REPOS_CHECK_TTL_MS) return hit.has;
  try {
    const response = await new AIServiceCommand<{ repos?: unknown[] }>({
      uri: `${deps.aiBackend}/api/v1/code-graph/repos`,
      method: HttpMethod.GET,
      headers: { Authorization: deps.authorization },
      timeoutMs: REPOS_CHECK_TIMEOUT_MS,
      maxAttempts: 1,
    }).execute();
    if (response.statusCode === 401 || response.statusCode === 403)
      return false;
    if (response.statusCode >= 400) return true;
    const has = (response.data?.repos?.length ?? 0) > 0;
    if (reposCheckCache.size >= REPOS_CHECK_MAX_ENTRIES)
      reposCheckCache.clear();
    reposCheckCache.set(cacheKey, { has, at: Date.now() });
    return has;
  } catch {
    return true;
  }
}

/**
 * Registers the tools with the server. Each tool's input is published as a
 * closed object (`additionalProperties: false`), as it was under zod 3; zod 4
 * only writes that for output schemas. Unknown arguments are still dropped
 * rather than refused.
 */
export function registerCodeGraphTools(
  server: ToolRegistrar,
  deps: CodeGraphToolDeps,
): void {
  registerCodeGraphToolDefinitions(
    {
      registerTool: (name, config, handler) =>
        server.registerTool(
          name,
          config.inputSchema
            ? {
                ...config,
                inputSchema: z
                  .object(config.inputSchema as z.ZodRawShape)
                  .meta({ additionalProperties: false }),
              }
            : config,
          handler,
        ),
    },
    deps,
  );
}

/** The tool definitions, with each input as a plain shape. */
export function registerCodeGraphToolDefinitions(
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
        repo: repoRequired,
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
        limit: z.number().int().min(1).max(500).optional(),
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
        'has repo, file_path, line range and qualified name. For "who calls X" this is more ' +
        'exact than grep: calls are resolved to the definition they reach, so a same-named ' +
        `method on another type is left out and a renamed import is followed. ${COVERAGE}`,
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
        // zod 4 would publish an `int()` without a maximum as capped at 2^53; keep the
        // published schema as it was, a plain non-negative integer.
        offset: z
          .number()
          .check(integerCheck())
          .min(0)
          .default(0)
          .meta({ type: 'integer' }),
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
          .regex(LINE_RANGE)
          .optional()
          .describe('A string such as "40-80", not an array.'),
        max_lines: z.number().int().min(1).max(2000).optional(),
        include_tests: includeTests,
      },
      annotations: READ_ONLY,
    },
    async (args) =>
      call(deps, HttpMethod.POST, '/read', {
        ...args,
        ...(typeof args.lines === 'string'
          ? { lines: args.lines.match(/\d+/g)?.join('-') }
          : {}),
      }),
  );

  server.registerTool(
    'pipeshub_code_path',
    {
      description:
        'How symbol A reaches symbol B in one repo, hop by hop with a sentence ' +
        'per hop. If only B reaches A, that path comes back with `reversed: true`. ' +
        `Edges across languages or over HTTP are not in the graph. ${COVERAGE}`,
      inputSchema: {
        repo: repoRequired,
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

  server.registerTool(
    'pipeshub_code_search',
    {
      description:
        'Find code without knowing its file. `mode: "identifier"` (default) lists every ' +
        'definition with that name. `mode: "text"` or `"regex"` finds lines in file bodies ' +
        '(a string, a call site, a config key); these need a repo-relative `path` and read ' +
        'at most 400 files.',
      inputSchema: {
        repo: repoRequired,
        pattern: z
          .string()
          .min(1)
          .max(200)
          .describe('A symbol name, text, or a regex.'),
        mode: z.enum(['identifier', 'text', 'regex']).default('identifier'),
        path: z
          .string()
          .min(1)
          .optional()
          .describe(
            'Directory, file or glob to search. Required for text and regex.',
          ),
        limit: z.number().int().min(1).max(200).optional(),
        include_tests: includeTests,
      },
      annotations: READ_ONLY,
    },
    async (args) => call(deps, HttpMethod.POST, '/search', args),
  );

  server.registerTool(
    'pipeshub_code_semantic_search',
    {
      description:
        'Find the code behind a feature, a behaviour or something on screen when you do not ' +
        "know its names: 'where a failed job is retried', 'what decides which config value " +
        "wins'. Ranks symbols by meaning, so near-synonyms " +
        'match. Results are addresses for pipeshub_code_read, pipeshub_code_neighbours and ' +
        'pipeshub_code_impact. For a name you already know, pipeshub_code_search is exact.',
      inputSchema: {
        repo: repoRequired,
        query: z
          .string()
          .min(1)
          .max(500)
          .describe('What the code does, in plain words.'),
        limit: z
          .number()
          .int()
          .min(1)
          .max(50)
          .optional()
          .describe('Maximum symbols, default 15.'),
        include_tests: includeTests,
      },
      annotations: READ_ONLY,
    },
    async (args) => call(deps, HttpMethod.POST, '/semantic-search', args),
  );

  server.registerTool(
    'pipeshub_code_outline',
    {
      description:
        "A file's classes, functions and methods with signatures, decorators, first doc " +
        'line and line ranges — no bodies. Read before pipeshub_code_read to pick what to read.',
      inputSchema: {
        repo,
        file_path: filePath,
        record_id: recordId,
        include_tests: includeTests,
      },
      annotations: READ_ONLY,
    },
    async (args) => call(deps, HttpMethod.POST, '/outline', args),
  );

  server.registerTool(
    'pipeshub_code_impact',
    {
      description:
        'What changing a symbol can break: everything that calls, imports or builds on it, ' +
        'followed backwards up to `depth` hops, grouped by file, in one call instead of a ' +
        `grep per caller. ${COVERAGE}`,
      inputSchema: {
        repo,
        file_path: filePath,
        record_id: recordId,
        qualified_name: qualifiedName,
        depth: z
          .number()
          .int()
          .min(1)
          .max(5)
          .optional()
          .describe('Hops back, default 2.'),
        include_tests: includeTests,
      },
      annotations: READ_ONLY,
    },
    async (args) => call(deps, HttpMethod.POST, '/impact', args),
  );

  server.registerTool(
    'pipeshub_code_entry_points',
    {
      description:
        'Where control enters a repo: functions nothing in it calls (routes, CLIs, jobs, ' +
        `hooks), conventional names and files first. ${COVERAGE}`,
      inputSchema: {
        repo: repoRequired,
        limit: z.number().int().min(1).max(200).optional(),
        include_tests: includeTests,
      },
      annotations: READ_ONLY,
    },
    async (args) => call(deps, HttpMethod.POST, '/entry-points', args),
  );
}
