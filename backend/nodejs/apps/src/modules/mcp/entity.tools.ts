/**
 * Knowledge-graph entity tools for MCP clients: the in-product agent's entity
 * tools, as two tools with an `action` each.
 *
 * - `pipeshub_entities`: `search` (entities by name: departments, topics,
 *   record groups, people, organizations, record titles, named entities),
 *   `records` (an entity's readable records), `co_mentioned` (the named
 *   entities mentioned with one).
 * - `pipeshub_find_records`: `by_person` (records linked to a person by role),
 *   `by_value` (records mentioning a date range, amount, quantity or
 *   percentage), `by_entities` (passages inside the records naming people,
 *   companies or products).
 *
 * What a deployment serves differs (entity store, named-entity extraction,
 * the `ENTITY_SEARCH_TOOL` switch), so the query service reports it per caller
 * (`GET /search/entities/capabilities`) and only those actions are mounted: a
 * tool with none is not registered, and an action not offered is not in its
 * `action` enum. Each forwards the caller's own bearer token to the query
 * service, which checks the OAuth scope, every record's ACL and the connected
 * app's limit, and refuses an action its deployment does not serve.
 */

import { z } from 'zod';
import { AIServiceCommand } from '../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../libs/enums/http-methods.enum';
import { ToolRegistrar } from './code-graph.tools';

interface ToolResult {
  [key: string]: unknown;
  content: Array<{ type: 'text'; text: string }>;
  isError?: boolean;
}

export interface EntityToolDeps {
  aiBackend: string;
  authorization: string;
}

/** What the query service serves this caller; see `entity_tools.capabilities_of`. */
export interface EntityCapabilities {
  entitySearch: boolean;
  entityRecords: boolean;
  namedEntities: boolean;
  coMentions: boolean;
  recordsByValue: boolean;
  recordsByEntities: boolean;
  recordsByPerson: boolean;
}

export const NO_ENTITY_CAPABILITIES: EntityCapabilities = {
  entitySearch: false,
  entityRecords: false,
  namedEntities: false,
  coMentions: false,
  recordsByValue: false,
  recordsByEntities: false,
  recordsByPerson: false,
};

/** Every action, for the admin tool list: it names what `/mcp` can serve anywhere. */
export const ALL_ENTITY_CAPABILITIES: EntityCapabilities = {
  entitySearch: true,
  entityRecords: true,
  namedEntities: true,
  coMentions: true,
  recordsByValue: true,
  recordsByEntities: true,
  recordsByPerson: true,
};

export const ENTITIES_TOOL = 'pipeshub_entities';
export const FIND_RECORDS_TOOL = 'pipeshub_find_records';
export const ENTITY_TOOL_NAMES = [ENTITIES_TOOL, FIND_RECORDS_TOOL] as const;

export type EntitiesAction = 'search' | 'records' | 'co_mentioned';
export type FindRecordsAction = 'by_person' | 'by_value' | 'by_entities';

const READ_ONLY = {
  readOnlyHint: true,
  destructiveHint: false,
  idempotentHint: true,
  openWorldHint: false,
};

// Inside the MCP client's own wait; the linking model call is the slow part.
const TIMEOUT_MS = 60_000;

// What a search can be narrowed to; people and organizations come back only
// from a search of every type (`EntityType` in the published spec).
const SEARCH_ENTITY_TYPES = [
  'department',
  'category',
  'subcategory',
  'topic',
  'language',
  'record_group',
  'record',
] as const;
// What `records` lists for: any type a search returns.
const RECORD_ENTITY_TYPES = [
  ...SEARCH_ENTITY_TYPES,
  'person',
  'organization',
] as const;
const NAMED_ENTITY_TYPE = 'named_entity';
// The query service's linking.mentions.MAX_MENTIONS: names past it would not be looked up.
const MAX_MENTIONS = 5;
// The query service's people.ROLE_EDGES.
const PERSON_ROLES = [
  'assignee',
  'reporter',
  'creator',
  'author',
  'owner',
  'editor',
  'shared_with',
  'reviewer',
  'recipient',
  'lead',
  'mentioned',
  'involved',
] as const;

/** The actions each tool serves for these capabilities, in a fixed order. */
export function entityActions(caps: EntityCapabilities): {
  entities: EntitiesAction[];
  findRecords: FindRecordsAction[];
} {
  const entities: EntitiesAction[] = [];
  if (caps.entitySearch) entities.push('search');
  if (caps.entityRecords) entities.push('records');
  if (caps.coMentions) entities.push('co_mentioned');
  const findRecords: FindRecordsAction[] = [];
  if (caps.recordsByPerson) findRecords.push('by_person');
  if (caps.recordsByValue) findRecords.push('by_value');
  if (caps.recordsByEntities) findRecords.push('by_entities');
  return { entities, findRecords };
}

/** The capabilities left once an admin's turned-off tools are taken out, so
 *  neither the tool nor the instructions that name it are served. */
export function withoutDisabledEntityTools(
  caps: EntityCapabilities,
  disabledTools: readonly string[],
): EntityCapabilities {
  const out = { ...caps };
  if (disabledTools.includes(ENTITIES_TOOL)) {
    out.entitySearch = false;
    out.entityRecords = false;
    out.coMentions = false;
  }
  if (disabledTools.includes(FIND_RECORDS_TOOL)) {
    out.recordsByPerson = false;
    out.recordsByValue = false;
    out.recordsByEntities = false;
  }
  return out;
}

/** The tools these capabilities mount. */
export function entityToolNames(caps: EntityCapabilities): string[] {
  const { entities, findRecords } = entityActions(caps);
  return [
    ...(entities.length ? [ENTITIES_TOOL] : []),
    ...(findRecords.length ? [FIND_RECORDS_TOOL] : []),
  ];
}

// ---------------------------------------------------------------------------
// Guidance: the query service writes it with the in-product agent's tool
// names, which an MCP client cannot call. Each is rewritten to the MCP call
// that does the same; the arguments are already this tool's (snake_case).
// ---------------------------------------------------------------------------
const CALL_RENAMES: Array<[string, string]> = [
  ['knowledgegraph__search_entities', `${ENTITIES_TOOL}(action="search"`],
  [
    'knowledgegraph__find_records_by_entity',
    `${ENTITIES_TOOL}(action="records"`,
  ],
  [
    'knowledgegraph__find_co_mentioned_entities',
    `${ENTITIES_TOOL}(action="co_mentioned"`,
  ],
  [
    'knowledgegraph__find_records_by_person',
    `${FIND_RECORDS_TOOL}(action="by_person"`,
  ],
  [
    'knowledgegraph__find_records_by_value',
    `${FIND_RECORDS_TOOL}(action="by_value"`,
  ],
  [
    'knowledgegraph__search_by_entities',
    `${FIND_RECORDS_TOOL}(action="by_entities"`,
  ],
];

export function rewriteGuidance(text: string): string {
  let out = text
    // Read a record: the agent's fetch takes a list, the MCP read one id at a time.
    .replace(
      /knowledgegraph__fetch_record\(record_ids=\[([^\]]*)\](?:,\s*start_block=(\d+))?\)/g,
      (_m, ids: string, block?: string) => {
        const list = ids
          .split(',')
          .map((id) => id.trim())
          .filter(Boolean);
        const extra = block ? `, startBlock=${block}` : '';
        if (list.length === 1)
          return `pipeshub_get_record_content(mode="content", recordId=${list[0]}${extra})`;
        return `pipeshub_get_record_content(mode="content", recordId=...)${extra ? ` (${extra.slice(2)})` : ''} for each of [${list.join(', ')}]`;
      },
    )
    .replace(
      /knowledgegraph__navigate\(node_id=/g,
      'pipeshub_get_record_content(mode="navigate", nodeId=',
    )
    .replace(
      /\bknowledgegraph__navigate\b/g,
      'pipeshub_get_record_content(mode="navigate")',
    )
    // pipeshub_search has no entity scope: what it can still do is search the words.
    .replace(
      /knowledgegraph__search\(([^)]*?),?\s*entity_ids=\[[^\]]*\]\)/g,
      'pipeshub_search($1)',
    )
    .replace(/\bknowledgegraph__search\(/g, 'pipeshub_search(')
    .replace(/\bknowledgegraph__search\b(?!_)/g, 'pipeshub_search');
  for (const [internal, call] of CALL_RENAMES) {
    out = out
      .replace(new RegExp(`\\b${internal}\\(\\)`, 'g'), `${call})`)
      .replace(new RegExp(`\\b${internal}\\(`, 'g'), `${call}, `)
      .replace(new RegExp(`\\b${internal}\\b`, 'g'), `${call})`);
  }
  return out;
}

/** Rewrites the guidance fields of a JSON answer, not the entities or passages it returns. */
function rewriteFields(data: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = { ...data };
  for (const key of ['text', 'entities', 'message', 'note', 'incompleteNote']) {
    const value = out[key];
    if (typeof value === 'string') out[key] = rewriteGuidance(value);
  }
  for (const key of ['notes', 'kindNotes']) {
    if (Array.isArray(out[key]))
      out[key] = (out[key] as unknown[]).map((n) =>
        typeof n === 'string' ? rewriteGuidance(n) : n,
      );
  }
  return out;
}

function errorResult(message: string): ToolResult {
  return {
    content: [{ type: 'text', text: JSON.stringify({ error: message }) }],
    isError: true,
  };
}

async function post(
  deps: EntityToolDeps,
  path: string,
  body: Record<string, unknown>,
): Promise<ToolResult> {
  try {
    const response = await new AIServiceCommand<Record<string, unknown>>({
      uri: `${deps.aiBackend}/api/v1${path}`,
      method: HttpMethod.POST,
      headers: {
        Authorization: deps.authorization,
        'Content-Type': 'application/json',
      },
      body: dropUndefined(body),
      timeoutMs: TIMEOUT_MS,
      maxAttempts: 1,
    }).execute();
    const data: Record<string, unknown> = response.data ?? {};
    if (response.statusCode >= 400) {
      const detail = data.detail ?? data.message ?? data.error;
      return errorResult(
        rewriteGuidance(
          typeof detail === 'string'
            ? detail
            : `request failed (${response.statusCode})`,
        ),
      );
    }
    const shaped = rewriteFields(data);
    // A listing is the agent's rendered text: hand it over as text, not as a JSON string.
    const text =
      Object.keys(shaped).length === 1 && typeof shaped.text === 'string'
        ? shaped.text
        : JSON.stringify(shaped);
    return { content: [{ type: 'text', text }] };
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    return errorResult(`entity lookup unavailable: ${message}`);
  }
}

function dropUndefined(body: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(body).filter(([, value]) => value !== undefined),
  );
}

function missing(action: string, ...names: string[]): ToolResult {
  return errorResult(
    `action "${action}" needs ${names.map((n) => `\`${n}\``).join(' and ')}.`,
  );
}

// ---------------------------------------------------------------------------
// Capabilities
// ---------------------------------------------------------------------------
const CAPS_TIMEOUT_MS = 5_000;
const CAPS_TTL_MS = 60_000;
const CAPS_MAX_ENTRIES = 10_000;
// shortcut: per-process cache, so a flag turned on or off can take up to the
// TTL to change the listing on each replica; every route checks its own gate,
// so a stale listing can only offer an action that is then refused.
const capsCache = new Map<string, { caps: EntityCapabilities; at: number }>();

function asCapabilities(data: unknown): EntityCapabilities | undefined {
  if (!data || typeof data !== 'object') return undefined;
  const record = data as Record<string, unknown>;
  const caps = { ...NO_ENTITY_CAPABILITIES };
  for (const key of Object.keys(caps) as Array<keyof EntityCapabilities>) {
    const value = record[key];
    if (typeof value !== 'boolean') return undefined;
    caps[key] = value;
  }
  return caps;
}

/**
 * Which entity actions the caller is served, asked of the query service with
 * the caller's token and cached per caller. When it cannot answer, nothing is
 * served and nothing is cached: a deployment's flags must never be guessed,
 * since guessing on would list tools that deployment turned off.
 */
export async function callerEntityCapabilities(
  deps: EntityToolDeps,
  cacheKey: string,
): Promise<EntityCapabilities> {
  const hit = capsCache.get(cacheKey);
  if (hit && Date.now() - hit.at < CAPS_TTL_MS) return hit.caps;
  try {
    const response = await new AIServiceCommand<unknown>({
      uri: `${deps.aiBackend}/api/v1/search/entities/capabilities`,
      method: HttpMethod.GET,
      headers: { Authorization: deps.authorization },
      timeoutMs: CAPS_TIMEOUT_MS,
      maxAttempts: 1,
    }).execute();
    if (response.statusCode >= 400) return NO_ENTITY_CAPABILITIES;
    const caps = asCapabilities(response.data);
    if (!caps) return NO_ENTITY_CAPABILITIES;
    if (capsCache.size >= CAPS_MAX_ENTRIES) capsCache.clear();
    capsCache.set(cacheKey, { caps, at: Date.now() });
    return caps;
  } catch {
    return NO_ENTITY_CAPABILITIES;
  }
}

/** Forgets the cached capabilities, so a test can change what the service answers. */
export function resetEntityCapabilitiesCache(): void {
  capsCache.clear();
}

// ---------------------------------------------------------------------------
// Instructions
// ---------------------------------------------------------------------------
/** Appended to the server's instructions for a caller with at least one entity action. */
export function entityInstructions(caps: EntityCapabilities): string {
  const { entities, findRecords } = entityActions(caps);
  if (!entities.length && !findRecords.length) return '';
  const lines = ['', '', '## People, entities and values', ''];
  if (entities.includes('search'))
    lines.push(
      `- A named thing (a project, folder, channel, topic, customer, colleague, a document title): \`${ENTITIES_TOOL}\` \`action:"search"\` finds it and returns its \`entityId\` and \`entityType\`; \`action:"records"\` lists all its records (for "how many / all / every").`,
    );
  if (findRecords.includes('by_person'))
    lines.push(
      `- Records a person is assigned, reported, created, wrote, owns or leads ("my tickets": \`person:"me"\`): \`${FIND_RECORDS_TOOL}\` \`action:"by_person"\`.`,
    );
  if (findRecords.includes('by_value'))
    lines.push(
      `- A value rather than words ("deals over $50k", "incidents in March"): \`${FIND_RECORDS_TOOL}\` \`action:"by_value"\`.`,
    );
  if (entities.includes('co_mentioned'))
    lines.push(
      `- Who or what comes up with an entity ("who worked with Acme"): \`${ENTITIES_TOOL}\` \`action:"co_mentioned"\` with the entity ids ${entities.includes('search') ? '`action:"search"` returned' : 'an earlier answer gave'}.`,
    );
  if (findRecords.includes('by_entities'))
    lines.push(
      `- Content about specific people, companies or products, especially two together: \`${FIND_RECORDS_TOOL}\` \`action:"by_entities"\` (\`match:"all"\` for records naming every one).`,
    );
  lines.push(
    '',
    'Records come back with ids: read one with `pipeshub_get_record_content`. A listing ending in a `Continue with:` call is unfinished: make that call.',
  );
  return lines.join('\n');
}

// ---------------------------------------------------------------------------
// Tools
// ---------------------------------------------------------------------------
const limit = z
  .number()
  .int()
  .min(1)
  .max(50)
  .optional()
  .describe(
    'Results per page (default 20 for listings, 10 for search; max 50).',
  );
const cursor = z
  .string()
  .min(1)
  .max(4096)
  .optional()
  .describe(
    'From the previous page\'s "Continue with" call; pass it back unchanged.',
  );
const recordTypes = z
  .array(z.string().min(1))
  .max(20)
  .optional()
  .describe('Record types to keep, e.g. FILE, MAIL, TICKET, WEBPAGE.');
const strings = (
  max: number,
  description: string,
): z.ZodOptional<z.ZodArray<z.ZodString>> =>
  z.array(z.string().min(1)).max(max).optional().describe(description);

/**
 * Registers the tools these capabilities serve, with each input published as
 * a closed object as the code-graph tools are (see `registerCodeGraphTools`).
 */
export function registerEntityTools(
  server: ToolRegistrar,
  deps: EntityToolDeps,
  caps: EntityCapabilities,
): string[] {
  const closed: ToolRegistrar = {
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
  };
  return registerEntityToolDefinitions(closed, deps, caps);
}

/** The tool definitions, with each input as a plain shape. Returns the names registered. */
export function registerEntityToolDefinitions(
  server: ToolRegistrar,
  deps: EntityToolDeps,
  caps: EntityCapabilities,
): string[] {
  const { entities, findRecords } = entityActions(caps);
  const registered: string[] = [];
  if (entities.length) {
    registerEntities(server, deps, caps, entities);
    registered.push(ENTITIES_TOOL);
  }
  if (findRecords.length) {
    registerFindRecords(server, deps, findRecords);
    registered.push(FIND_RECORDS_TOOL);
  }
  return registered;
}

function registerEntities(
  server: ToolRegistrar,
  deps: EntityToolDeps,
  caps: EntityCapabilities,
  actions: EntitiesAction[],
): void {
  const named = (list: readonly string[]): string[] =>
    caps.namedEntities ? [...list, NAMED_ENTITY_TYPE] : [...list];
  const searchTypes = named(SEARCH_ENTITY_TYPES);
  const recordTypesOf = named(RECORD_ENTITY_TYPES);
  const shape: Record<string, z.ZodType> = {
    action: z
      .enum(actions as [EntitiesAction, ...EntitiesAction[]])
      .describe(
        [
          actions.includes('search') &&
            '`search` — entities by name or description; needs `query`.',
          actions.includes('records') &&
            '`records` — every readable record of one entity, newest first; needs `entity_id` and `entity_type` from `search`.',
          actions.includes('co_mentioned') &&
            '`co_mentioned` — the people, organizations, products ... mentioned in the records that mention one entity, most first; needs `entity_ids` (from `search`, or an earlier answer).',
        ]
          .filter(Boolean)
          .join(' '),
      ),
  };
  if (actions.includes('search')) {
    shape.query = z
      .string()
      .min(1)
      .max(500)
      .optional()
      .describe('search: the name or description to look up.');
    shape.entity_types = z
      .array(z.enum(searchTypes as [string, ...string[]]))
      .max(10)
      .optional()
      .describe(
        `search: types to keep (${searchTypes.join(', ')}). People and organizations come back only when this is omitted. Omit unless the user named the kind of thing; a name does not tell you its type.`,
      );
    if (caps.namedEntities)
      shape.named_entity_kinds = strings(
        10,
        'search: kinds of named entity to keep, e.g. organization, product, event, location.',
      );
  }
  if (actions.includes('records')) {
    shape.entity_id = z
      .string()
      .min(1)
      .max(256)
      .optional()
      .describe('records: an `entityId` from `search`.');
    shape.entity_type = z
      .enum(recordTypesOf as [string, ...string[]])
      .optional()
      .describe("records: that entity's `entityType`.");
    shape.record_types = recordTypes;
    shape.cursor = cursor;
  }
  if (actions.includes('co_mentioned')) {
    shape.entity_ids = strings(
      20,
      'co_mentioned: every id returned for the one entity (its aliases too).',
    );
    shape.kinds = strings(
      20,
      'co_mentioned: kinds to list, e.g. person, organization, product. Emails and phones only when named here.',
    );
  }
  shape.limit = limit;

  server.registerTool(
    ENTITIES_TOOL,
    {
      description:
        "Use to find a named thing in the org's knowledge graph — a department, topic, record " +
        'group (Jira project, Confluence space, Drive folder, Slack channel), person, customer ' +
        `organization or document title${caps.namedEntities ? ', or a product, event or place named in documents' : ''} — ` +
        'and what is connected to it. Only what you can read is returned; an entity you cannot ' +
        'reach and one with no readable records answer alike. Next: read a record with ' +
        'pipeshub_get_record_content, or search its content with pipeshub_search.',
      inputSchema: shape,
      annotations: { ...READ_ONLY, title: 'Entities' },
    },
    async (args) => {
      const action = args.action as EntitiesAction;
      if (!actions.includes(action))
        return errorResult(
          `action "${action}" is not available here; use one of ${actions.join(', ')}.`,
        );
      if (action === 'search') {
        if (typeof args.query !== 'string' || !args.query.trim())
          return missing(action, 'query');
        return post(deps, '/search/entities', {
          query: args.query,
          entityTypes: args.entity_types,
          namedEntityKinds: caps.namedEntities
            ? args.named_entity_kinds
            : undefined,
          limit:
            typeof args.limit === 'number'
              ? Math.min(args.limit, 25)
              : undefined,
        });
      }
      if (action === 'records') {
        if (!args.entity_id || !args.entity_type)
          return missing(action, 'entity_id', 'entity_type');
        return post(deps, '/search/entities/records', {
          entityId: args.entity_id,
          entityType: args.entity_type,
          recordTypes: args.record_types,
          limit: args.limit,
          cursor: args.cursor,
        });
      }
      if (!Array.isArray(args.entity_ids) || !args.entity_ids.length)
        return missing(action, 'entity_ids');
      return post(deps, '/search/entities/co-mentioned', {
        entityIds: args.entity_ids,
        kinds: args.kinds,
        limit: args.limit,
      });
    },
  );
}

function registerFindRecords(
  server: ToolRegistrar,
  deps: EntityToolDeps,
  actions: FindRecordsAction[],
): void {
  const shape: Record<string, z.ZodType> = {
    action: z
      .enum(actions as [FindRecordsAction, ...FindRecordsAction[]])
      .describe(
        [
          actions.includes('by_person') &&
            '`by_person` — records a person is linked to by role, newest first; needs `person`.',
          actions.includes('by_value') &&
            '`by_value` — records that mention a date range, amount, quantity or percentage, however written; needs at least one bound.',
          actions.includes('by_entities') &&
            '`by_entities` — the best passages inside the records that mention named people, organizations, products or places; needs `mentions`.',
        ]
          .filter(Boolean)
          .join(' '),
      ),
  };
  if (actions.includes('by_person')) {
    shape.person = z
      .string()
      .min(1)
      .max(320)
      .optional()
      .describe('by_person: a full name, an email, or "me".');
    shape.roles = z
      .array(z.enum(PERSON_ROLES))
      .max(PERSON_ROLES.length)
      .optional()
      .describe('by_person: roles to keep; omit for every role.');
  }
  if (actions.includes('by_value')) {
    shape.kinds = strings(
      20,
      'by_value: value kinds, e.g. date, currency, percentage, dimension.',
    );
    shape.name = z
      .string()
      .min(1)
      .max(200)
      .optional()
      .describe(
        'by_value: a company, person or product the records must also mention.',
      );
    shape.entity_ids = strings(
      20,
      'by_value: entity ids from pipeshub_entities the records must mention.',
    );
    shape.date_from = z
      .string()
      .max(64)
      .optional()
      .describe('by_value: earliest date mentioned, ISO 8601.');
    shape.date_to = z
      .string()
      .max(64)
      .optional()
      .describe('by_value: latest date mentioned, ISO 8601.');
    shape.amount_min = z
      .number()
      .optional()
      .describe('by_value: minimum amount.');
    shape.amount_max = z
      .number()
      .optional()
      .describe('by_value: maximum amount.');
    shape.currency = z
      .string()
      .max(16)
      .optional()
      .describe('by_value: ISO currency code, e.g. USD.');
    shape.quantity_min = z
      .number()
      .optional()
      .describe('by_value: minimum quantity in SI units.');
    shape.quantity_max = z
      .number()
      .optional()
      .describe('by_value: maximum quantity in SI units.');
    shape.dimension = z
      .string()
      .max(64)
      .optional()
      .describe(
        "by_value: the quantity's dimension, e.g. time, length, mass, data.",
      );
    shape.percent_min = z
      .number()
      .optional()
      .describe('by_value: minimum percentage as a fraction (0.2 is 20%).');
    shape.percent_max = z
      .number()
      .optional()
      .describe('by_value: maximum percentage as a fraction.');
    shape.source_ids = strings(
      50,
      'by_value: app or knowledge base ids to look in.',
    );
  }
  if (actions.includes('by_person') || actions.includes('by_value')) {
    shape.record_types = recordTypes;
    shape.cursor = cursor;
  }
  if (actions.includes('by_entities')) {
    shape.mentions = z
      .array(
        z.object({
          name: z.string().min(1).max(200),
          kind: z
            .string()
            .max(64)
            .optional()
            .describe(
              'person, organization, product, event, location, ... when known.',
            ),
        }),
      )
      .min(1)
      .max(MAX_MENTIONS)
      .optional()
      .describe(
        'by_entities: the names to look up, e.g. [{"name": "Acme Robotics", "kind": "organization"}].',
      );
    shape.match = z
      .enum(['any', 'all'])
      .optional()
      .describe(
        'by_entities: "all" keeps records that mention every name; default "any".',
      );
    shape.query = z
      .string()
      .max(1000)
      .optional()
      .describe(
        'by_entities: what to look for inside those records; the names when omitted.',
      );
  }
  shape.limit = limit;

  server.registerTool(
    FIND_RECORDS_TOOL,
    {
      description:
        'Use to list records by who they belong to or what they mention, rather than by their words: ' +
        [
          actions.includes('by_person') &&
            "a person's tickets, documents or mail",
          actions.includes('by_value') &&
            'amounts, dates, quantities or percentages',
          actions.includes('by_entities') &&
            'passages about named people, companies or products',
        ]
          .filter(Boolean)
          .join('; ') +
        '. Only records you can read are returned. Next: read one with pipeshub_get_record_content.',
      inputSchema: shape,
      annotations: { ...READ_ONLY, title: 'Find records' },
    },
    async (args) => {
      const action = args.action as FindRecordsAction;
      if (!actions.includes(action))
        return errorResult(
          `action "${action}" is not available here; use one of ${actions.join(', ')}.`,
        );
      if (action === 'by_person') {
        if (typeof args.person !== 'string' || !args.person.trim())
          return missing(action, 'person');
        return post(deps, '/search/records/by-person', {
          person: args.person,
          roles: args.roles,
          recordTypes: args.record_types,
          limit: args.limit,
          cursor: args.cursor,
        });
      }
      if (action === 'by_value') {
        return post(deps, '/search/records/by-value', {
          kinds: args.kinds,
          name: args.name,
          entityIds: args.entity_ids,
          dateFrom: args.date_from,
          dateTo: args.date_to,
          amountMin: args.amount_min,
          amountMax: args.amount_max,
          currency: args.currency,
          quantityMin: args.quantity_min,
          quantityMax: args.quantity_max,
          dimension: args.dimension,
          percentMin: args.percent_min,
          percentMax: args.percent_max,
          recordTypes: args.record_types,
          sourceIds: args.source_ids,
          limit: args.limit,
          cursor: args.cursor,
        });
      }
      if (!Array.isArray(args.mentions) || !args.mentions.length)
        return missing(action, 'mentions');
      return post(deps, '/search/records/by-entities', {
        mentions: args.mentions,
        match: args.match,
        query: args.query,
        limit: typeof args.limit === 'number' ? args.limit : undefined,
      });
    },
  );
}
