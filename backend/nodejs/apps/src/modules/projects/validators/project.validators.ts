import { z } from 'zod';
import {
  PROJECT_CHAT_SHARING_VALUES,
  PROJECT_DESCRIPTION_MAX_LENGTH,
  PROJECT_INSTRUCTIONS_MAX_LENGTH,
  PROJECT_MEMBER_ROLE_VALUES,
  PROJECT_MEMBERS_BATCH_MAX,
  PROJECT_NAME_MAX_LENGTH,
  PROJECT_PRINCIPAL_TYPE_VALUES,
  PROJECT_VISIBILITY_VALUES,
} from '../constants/constants';

const OBJECT_ID_REGEX = /^[0-9a-fA-F]{24}$/;
const objectId = (label: string): z.ZodString =>
  z.string().regex(OBJECT_ID_REGEX, { message: `Invalid ${label} format` });

/**
 * Team ids come from the graph, not Mongo: a UUID, or `all_<orgId>` for the
 * synthetic every-member team. A user id is always an ObjectId.
 */
const TEAM_ID_REGEX =
  /^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|all_[0-9a-fA-F]{24})$/;
const PRINCIPAL_ID_REGEX = new RegExp(
  `${OBJECT_ID_REGEX.source}|${TEAM_ID_REGEX.source}`,
);
const principalIdSchema = (label: string): z.ZodString =>
  z.string().regex(PRINCIPAL_ID_REGEX, { message: `Invalid ${label} format` });

/**
 * A user row needs an ObjectId and a team row a team id. Runs even when other
 * fields failed (`when`), so a bad id is still reported beside a bad role.
 */
function principalMatchesType(
  label: string,
  read: (value: unknown) => { id: unknown; type: unknown },
  path: (string | number)[],
): readonly [
  (value: unknown, ctx: z.RefinementCtx) => void,
  { when: () => boolean },
] {
  return [
    (value: unknown, ctx: z.RefinementCtx) => {
      const { id, type } = read(value);
      if (typeof id !== 'string' || !PRINCIPAL_ID_REGEX.test(id)) return;
      const pattern = type === 'team' ? TEAM_ID_REGEX : OBJECT_ID_REGEX;
      if (!pattern.test(id)) {
        ctx.addIssue({
          code: 'invalid_format',
          format: 'regex',
          pattern: pattern.source,
          // Zod prefixes the path in place, so each issue needs its own copy.
          path: [...path],
          message: `Invalid ${label} format`,
          input: id,
        });
      }
    },
    { when: () => true },
  ] as const;
}

const pageSchema = z.preprocess(
  (arg) => (arg === undefined || arg === '' ? undefined : Number(arg)),
  z.number().int({ message: 'page must be a whole number' }).min(1).default(1),
);
const limitSchema = z.preprocess(
  (arg) => (arg === undefined || arg === '' ? undefined : Number(arg)),
  z
    .number()
    .int({ message: 'limit must be a whole number' })
    .min(1)
    .max(100)
    .default(20),
);

/**
 * The color is rendered into a style prop, so only a plain color value is
 * stored: a hex code, a CSS color keyword, or a theme variable reference.
 */
const COLOR_REGEX =
  /^(#[0-9a-fA-F]{3,4}|#[0-9a-fA-F]{6}|#[0-9a-fA-F]{8}|[a-zA-Z]{1,30}|var\(--[a-zA-Z0-9-]{1,40}\))$/;

const appOrKbIdSchema = z.string().min(1).max(200);

/** Most ids a project may list, across every key: what one chat request may carry. */
const PROJECT_SCOPE_MAX_IDS = 200;

/**
 * What a project may search: whole apps and collections (`apps`, `kb`), and
 * record groups, folders or records with everything under them.
 */
const knowledgeScopeSchema = z
  .object({
    apps: z.array(appOrKbIdSchema).optional(),
    kb: z.array(appOrKbIdSchema).optional(),
    recordGroups: z.array(appOrKbIdSchema).optional(),
    records: z.array(appOrKbIdSchema).optional(),
  })
  .refine(
    (scope) =>
      Object.values(scope).reduce<number>(
        (count, ids) => count + (Array.isArray(ids) ? ids.length : 0),
        0,
      ) <= PROJECT_SCOPE_MAX_IDS,
    { message: `A project may list at most ${PROJECT_SCOPE_MAX_IDS} sources` },
  )
  .optional();

/** Max tool fullNames a project can persist — mirrors PROJECT_MEMBERS_BATCH_MAX's role as a sanity bound, not a real product limit. */
const PROJECT_TOOLS_MAX = 200;
/** Tool fullName, same format the composer sends as `agentStreamTools` (toolset + MCP tools). */
const toolFullNameSchema = z.string().min(1).max(200);
const toolsSchema = z.array(toolFullNameSchema).max(PROJECT_TOOLS_MAX).optional();

/**
 * One entry of the display mirror. The store requires id, name and node type,
 * so an empty one is refused here rather than failing at save; the connector
 * name is for the icon only and may be unknown.
 */
const appliedFilterNodeSchema = z.object({
  id: z.string().min(1, { message: 'Id is required.' }),
  name: z.string().min(1, { message: 'Name is required.' }),
  nodeType: z.string().min(1, { message: 'Node type is required.' }),
  connector: z.string(),
});

const appliedFiltersSchema = z
  .object({
    apps: z.array(appliedFilterNodeSchema).optional(),
    kb: z.array(appliedFilterNodeSchema).optional(),
    recordGroups: z.array(appliedFilterNodeSchema).optional(),
    records: z.array(appliedFilterNodeSchema).optional(),
  })
  .optional();

/**
 * Zod's `max` counts code points, but the Mongo schema's `maxlength` (and the
 * browser's `maxLength`) count UTF-16 units, so text with emoji near the limit
 * passed here and then failed the save with a 500. Count the way the schema does.
 */
function maxUtf16(max: number): (value: string, ctx: z.RefinementCtx) => void {
  return (value, ctx) => {
    if (value.length > max) {
      ctx.addIssue({
        code: 'too_big',
        origin: 'string',
        maximum: max,
        inclusive: true,
        input: value,
      });
    }
  };
}

const projectBodyFieldsSchema = {
  name: z
    .string()
    .trim()
    .min(1, { message: 'Project name is required' })
    .max(PROJECT_NAME_MAX_LENGTH)
    .superRefine(maxUtf16(PROJECT_NAME_MAX_LENGTH)),
  description: z
    .string()
    .max(PROJECT_DESCRIPTION_MAX_LENGTH)
    .superRefine(maxUtf16(PROJECT_DESCRIPTION_MAX_LENGTH))
    .optional(),
  icon: z.string().max(100).optional(),
  color: z
    .string()
    .max(50)
    .regex(COLOR_REGEX, {
      message: 'Color must be a hex code, a CSS color name or a theme variable',
    })
    .optional(),
  instructions: z
    .string()
    .max(PROJECT_INSTRUCTIONS_MAX_LENGTH)
    .superRefine(maxUtf16(PROJECT_INSTRUCTIONS_MAX_LENGTH))
    .optional(),
  knowledgeScope: knowledgeScopeSchema,
  appliedFilters: appliedFiltersSchema,
  tools: toolsSchema,
};

export const createProjectSchema = z.object({
  body: z.object(projectBodyFieldsSchema),
});

export const updateProjectSchema = z.object({
  params: z.object({ projectId: objectId('project ID') }),
  body: z
    .object({
      ...projectBodyFieldsSchema,
      visibility: z.enum(PROJECT_VISIBILITY_VALUES).optional(),
      chatSharing: z.enum(PROJECT_CHAT_SHARING_VALUES).optional(),
    })
    .partial(),
});

export const projectIdParamsSchema = z.object({
  params: z.object({ projectId: objectId('project ID') }),
});

export const listProjectsQuerySchema = z.object({
  query: z.object({
    page: pageSchema,
    limit: limitSchema,
    search: z.string().max(200).optional(),
    scope: z.enum(['mine', 'shared', 'all']).optional().default('mine'),
    includeArchived: z
      .enum(['true', 'false'])
      .optional()
      .transform((v) => v === 'true'),
    isArchived: z
      .enum(['true', 'false'])
      .optional()
      .transform((v) => (v === undefined ? undefined : v === 'true')),
  }),
});

export const listProjectConversationsQuerySchema = z.object({
  params: z.object({ projectId: objectId('project ID') }),
  query: z.object({
    page: pageSchema,
    limit: limitSchema,
  }),
});

export const upsertProjectMembersSchema = z.object({
  params: z.object({ projectId: objectId('project ID') }),
  body: z.object({
    members: z
      .array(
        z
          .object({
            principalId: principalIdSchema('principal ID'),
            principalType: z.enum(PROJECT_PRINCIPAL_TYPE_VALUES).optional(),
            role: z.enum(PROJECT_MEMBER_ROLE_VALUES),
          })
          .superRefine(
            ...principalMatchesType(
              'principal ID',
              (member) => {
                const m = member as {
                  principalId?: unknown;
                  principalType?: unknown;
                };
                return { id: m.principalId, type: m.principalType };
              },
              ['principalId'],
            ),
          ),
      )
      .min(1, { message: 'At least one member is required' })
      .max(PROJECT_MEMBERS_BATCH_MAX, {
        message: `At most ${PROJECT_MEMBERS_BATCH_MAX} members may be updated per request`,
      })
      // Two rows for one principal used to save whichever came last, silently.
      .superRefine((members, ctx) => {
        const seen = new Set<string>();
        members.forEach((member, index) => {
          const key = `${member.principalType ?? 'user'}:${member.principalId}`;
          if (seen.has(key)) {
            ctx.addIssue({
              code: 'custom',
              path: [index, 'principalId'],
              message: 'Each member may appear only once per request',
              input: member.principalId,
            });
          }
          seen.add(key);
        });
      }),
  }),
});

export const removeProjectMemberParamsSchema = z
  .object({
    params: z.object({
      projectId: objectId('project ID'),
      memberUserId: principalIdSchema('member ID'),
    }),
    query: z.object({
      principalType: z.enum(PROJECT_PRINCIPAL_TYPE_VALUES).optional(),
    }),
  })
  .superRefine(
    ...principalMatchesType(
      'member ID',
      (request) => {
        const r = request as {
          params?: { memberUserId?: unknown };
          query?: { principalType?: unknown };
        };
        return { id: r.params?.memberUserId, type: r.query?.principalType };
      },
      ['params', 'memberUserId'],
    ),
  );
