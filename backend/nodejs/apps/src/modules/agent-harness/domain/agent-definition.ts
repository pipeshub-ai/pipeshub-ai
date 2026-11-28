import { z } from 'zod/v3';
import { MAX_CHILDREN_PER_RUN } from './child-runs';
import { agentGatesSchema } from './run-gates';
import { agentEnvironmentSchema } from './agent-environment';

const text = z.string().min(1);

/** MCPNOTE-1: why a server's tools could not be named (`x.pipeshub.run.notice` `reason`). */
export const UNLISTED_MCP_REASONS = [
  'sign_in',
  'unreachable',
  'not_approved',
  'no_tools',
  'flagged',
] as const;
export type UnlistedMcpReason = (typeof UNLISTED_MCP_REASONS)[number];
/** At most this many held tools of one server are named (`x.pipeshub.run.notice` `tools`). */
export const MAX_HELD_NAMED = 20;

/** A sub-agent role as `roleModels` and `models.roleCostCeilings` key it (the `min-map` key pattern). */
export const ROLE_KEY = /^[a-z0-9][a-z0-9_.-]{0,63}$/;
export const MAX_ROLE_MODELS = 64;

const definitionToolSchema = z.object({
  name: text,
  fullName: text,
  // The tool's descriptor digest as the authority bound it (CPS); absent from an older one, or with nothing to bind.
  descriptorDigest: z
    .string()
    .regex(/^sha256:[0-9a-f]{64}$/)
    .nullish(),
});

/**
 * What an agent grants a run by one actor, as the agent authority (Python)
 * reports it for that actor's own credential. Node keeps it on the run as the
 * upper bound the claim resolves the RunSpec from (REPORT 6.1.1). Names and
 * ids only: no prompts, no credentials. Unknown fields are dropped so a newer
 * authority does not break an older Node.
 */
export const agentRunDefinitionSchema = z.object({
  agentKey: text,
  definitionVersion: z.number().int().positive(),
  models: z.array(
    z.object({
      modelKey: text,
      modelName: text,
      provider: text,
      isReasoning: z.boolean(),
    }),
  ),
  defaultReasoningEffort: z.string().nullable(),
  toolsets: z.array(
    z.object({
      name: text,
      instanceId: z.string().nullable(),
      tools: z.array(definitionToolSchema),
    }),
  ),
  mcpServers: z.array(
    z.object({
      instanceId: text,
      name: text,
      typeId: z.string().nullable(),
      tools: z.array(definitionToolSchema),
      // MCPNOTE-1: why the assistant's server got no tool, as its listing at admission recorded; absent from an
      // older authority, and for a server with tools.
      unavailable: z
        .object({
          reason: z.enum(UNLISTED_MCP_REASONS),
          signInHere: z.boolean().optional(),
        })
        .optional()
        .catch(undefined),
      // ADR-OWD-07 D-LAZY-2: granted whole, its tools named and pinned at the run's first use of it; sent only when
      // admission asked for a worker that lists at first use (`mcpFirstUse`).
      atFirstUse: z.boolean().optional().catch(undefined),
      // D-MCP-5: the server's tools the description scanner held back from the run, by name; absent: none.
      held: z
        .array(z.string().min(1).max(256))
        .max(MAX_HELD_NAMED)
        .optional()
        .catch(undefined),
    }),
  ),
  knowledge: z.array(
    z.object({
      connectorId: text,
      isKb: z.boolean(),
      // The source's limit to some of its record groups and records (#4255); absent from an older authority, and
      // for a whole source. Empty lists limit it to nothing. Pinned as `sourceLimits` (SYNC4 S12).
      limit: z
        .object({
          recordGroups: z.array(z.string()).max(200),
          records: z.array(z.string()).max(200),
        })
        .optional(),
    }),
  ),
  skills: z.array(
    z.object({
      name: text,
      version: z.string().nullable(),
      // Content digest of the granted version (P7.1); absent from an older authority.
      bundleDigest: z
        .string()
        .regex(/^sha256:[0-9a-f]{64}$/)
        .nullish(),
    }),
  ),
  webSearch: z.string().nullable(),
  // Child runs this agent may delegate to per run (P6.1); absent from an older authority: none.
  maxChildren: z.number().int().min(0).max(MAX_CHILDREN_PER_RUN).optional(),
  // The agent's engine profile narrowing (E4a), as stored: admission parses it
  // against the org ceiling and refuses it there. Absent: the agent runs on the loop.
  engineProfile: z.unknown().optional(),
  // The org cloud account (`cloud/<id>`) the agent's editor pinned for its engine runs (AG1).
  modelAccountId: z.string().min(1).max(300).optional(),
  // The agent's owner by user id (CPD-7): what they put on the agent is shared with whoever runs it.
  // Absent from an older authority, or when the creator cannot be read: nothing is shared through it.
  owner: z.string().min(1).max(128).optional(),
  // The model each sub-agent role calls (MA1); `default` is every role without its own entry.
  // Absent from an older authority: every child calls its parent's model.
  roleModels: z
    .record(
      z.string().regex(ROLE_KEY),
      z.object({ modelKey: text, modelName: text, provider: text }),
    )
    .refine((roles) => Object.keys(roles).length <= MAX_ROLE_MODELS)
    .optional(),
  // The agent's own run gates (WP-31 D: plan approval, checks), held to every run of it. A stored value the
  // authority cannot read arrives as one this refuses, so the agent is refused, never run without them.
  gates: agentGatesSchema.optional(),
  // C20.18 (WP-100): the agent's environment for its engine runs, pinned as `x.pipeshub.environment`. A stored value
  // the authority cannot read arrives as one this refuses, so the agent is refused, never run without its setup.
  environment: agentEnvironmentSchema.optional(),
});

export type AgentRunDefinition = z.infer<typeof agentRunDefinitionSchema>;

/** Whether the authority bound any granted tool to its descriptor: the run then pins version-2 digests. */
export const bindsDescriptors = (
  definition: AgentRunDefinition | null,
): boolean =>
  definition !== null &&
  [...definition.toolsets, ...definition.mcpServers].some((owner) =>
    owner.tools.some((tool) => typeof tool.descriptorDigest === 'string'),
  );
