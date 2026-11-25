import { MODEL_ROLES } from '../domain/model-roles';
import { engineProfileNarrowingSchema } from '../contracts/generated/core/engine-profile-narrowing';
import { contract } from '../contracts/zod-contract';
import { z } from 'zod';
import { PRIORITY_CLASSES } from '../domain/priority-class';
import { requestFiltersSchema } from '../../../libs/validators/request-filters';
import { keywordSearchBudgetSchema } from '../../../libs/validators/keyword-search-budget';
import { RELEASE_DEFAULTS } from '../contracts/generated/core/policy-data';
import { MAX_LEASE_DURATION_MS } from '../domain/authority';
import { MAX_FOLLOWUPS_WAIT_MS } from '../domain/followups';
import { MAX_STREAM_ID_LENGTH } from '../projection/stream-id';
import { CHAT_QUERY_MAX_CHARS } from '../domain/admission';
import { COMPACT_FOCUS_MAX_CHARS } from '../domain/compact-requests';
import { APPROVAL_BATCH_MAX_ITEMS } from '../domain/approval-pattern';
import { sessionRepositorySchema as sessionRepositoryBody } from '../domain/repository';
import { workerCapabilitiesSchema } from '../contracts/generated/core/worker-capabilities';
import { labelsSchema } from '../contracts/generated/core/labels';
import { childCompletedEventSchema } from '../contracts/generated/core/events/child-completed';
import {
  CUT_SHORT_REASONS,
  SUSPEND_REASONS,
  VISIBILITIES,
  YIELD_REASONS,
} from '../contracts/generated/core/enums';
import {
  EXECUTION_STATUSES,
  RUN_OUTCOMES,
  WORKSPACE_MOUNTS,
} from '../contracts/generated/ext/pipeshub/enums';
import {
  MAX_DENY_REASON_CHARS,
  MAX_EDITED_ARGS_BYTES,
  NODE_AUTHORED_YIELD_REASONS,
} from '../domain/execution';
import { canonicalJsonBytes } from '../contracts/canonical-json';
import { pipeshubRunSpecExtensionSchema } from '../contracts/generated/ext/pipeshub/run-spec';
import { jobBriefSchema } from '../contracts/generated/ext/pipeshub/api/job-brief';
import {
  CONFLICT_POLICIES,
  MAX_WORKSPACE_FILES,
  WORKER_COMMIT_REASONS,
  WRITER_LEASE_MAX_MS,
  WRITER_LEASE_MIN_MS,
} from '../domain/workspace';

const runStatus = z.enum(EXECUTION_STATUSES);
const WORKER_COMMIT_STATUSES = runStatus.exclude(['queued']);
const runOutcome = z.enum(RUN_OUTCOMES);
const machineError = z.string().regex(/^[a-z][a-z0-9_]{0,63}$/);
/** The contract's `ContentRef` (common.schema.json); its ref bound is every receiver's. */
export const contentRefSchema =
  childCompletedEventSchema.shape.data.shape.resultRef;

export const boundedId = z
  .string()
  .min(1)
  .max(128)
  .regex(/^[A-Za-z0-9._:-]+$/);
const positiveDuration = z.number().int().positive().max(MAX_LEASE_DURATION_MS);

export const runParamsSchema = z.object({
  params: z.object({ runId: boundedId }),
});

/**
 * What the claiming worker can run (RU-2). Read leniently: a newer worker's
 * advertisement may carry members this Node does not know yet. Absent: a
 * worker older than the rule, which gets no v2 run.
 */
const workerCapabilities = contract(
  workerCapabilitiesSchema.strip(),
).optional();

/** The worker's region (OWD-12, PIPESHUB_REGION); absent from a worker older than regions. */
const workerRegion = z.string().regex(/^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/);

export const claimRunSchema = runParamsSchema.extend({
  body: z
    .object({
      orgId: boundedId,
      workerId: boundedId,
      leaseDurationMs: positiveDuration,
      workerCapabilities,
      workerRegion: workerRegion.optional(),
    })
    .strict(),
});

/** Claim-next (D20): the queue is the Mongo run collection, not the broker. */
export const claimNextRunSchema = z.object({
  body: z
    .object({
      workerId: boundedId,
      leaseDurationMs: positiveDuration,
      role: z.enum(['inline', 'dispatched']),
      workerCapabilities,
      workerRegion: workerRegion.optional(),
      // G-45: a full worker that will preempt a run of this class asks only for a higher one.
      aboveClass: z.enum(PRIORITY_CLASSES).optional(),
    })
    .strict(),
});

export const releaseRunSchema = runParamsSchema.extend({
  body: z
    .object({
      attemptId: boundedId,
      fence: z.number().int().nonnegative(),
      reason: machineError,
    })
    .strict(),
});

export const renewRunSchema = runParamsSchema.extend({
  body: z
    .object({
      attemptId: boundedId,
      fence: z.number().int().nonnegative(),
      leaseDurationMs: positiveDuration,
    })
    .strict(),
});

const immutableObjectRef = z
  .object({
    objectId: z.string().min(1).max(512),
    contentHash: z.string().min(1).max(128),
    sizeBytes: z.number().int().nonnegative(),
  })
  .strict();

const dispatchCommand = z
  .object({
    commandId: boundedId,
    runId: boundedId,
    kind: z.enum(['run_ready', 'run_resume', 'run_cancel']),
  })
  .strict();

const legacyEvent = z
  .object({
    type: boundedId,
    data: z.unknown(),
  })
  .strict();

const LEGACY_TERMINAL_EVENTS: Partial<Record<string, string>> = {
  completed: 'run_completed',
  failed: 'run_failed',
  cancelled: 'run_cancelled',
  needs_reconciliation: 'run_needs_reconciliation',
};

/**
 * The most events one commit carries: the contract's value, which a worker
 * reads too and splits a larger step by, so no commit is refused for its size.
 */
export const MAX_COMMIT_EVENTS = RELEASE_DEFAULTS.journal.maxCommitEvents;

/** Durable v1 flat commit; still accepted from workers not yet upgraded. */
const legacyCommitBody = z
  .object({
    commandId: boundedId,
    attemptId: boundedId,
    fence: z.number().int().nonnegative(),
    expectedRevision: z.number().int().nonnegative(),
    // A worker can advance an accepted run, but it cannot put work back into
    // the dispatcher-owned `queued` state.
    status: WORKER_COMMIT_STATUSES,
    outcome: runOutcome.optional(),
    checkpoint: immutableObjectRef.optional(),
    events: z.array(legacyEvent).max(MAX_COMMIT_EVENTS),
    outbox: z.array(dispatchCommand).max(100),
  })
  .strict()
  .superRefine((body, context) => {
    const eventType = LEGACY_TERMINAL_EVENTS[body.status];
    if (
      eventType !== undefined &&
      !body.events.some((event) => event.type === eventType)
    ) {
      context.addIssue({
        code: 'custom',
        path: ['events'],
        message: `${body.status} commits require a ${eventType} event`,
      });
    }
  });

// Shape only; the generated per-type schemas and the writer allowlist are
// applied by JournalService with the run's envelope.
const journalEventInput = z
  .object({
    type: z.string().min(1).max(128),
    schemaVersion: z.number().int().min(1),
    critical: z.boolean(),
    visibility: z.enum(VISIBILITIES),
    labels: z.record(z.string(), z.unknown()).optional(),
    data: z.record(z.string(), z.unknown()),
  })
  .strict();

/** Harness major 2: which `run.*` event Node journals for the transition. */
const transitionDetail = z.discriminatedUnion('kind', [
  z
    .object({
      kind: z.literal('suspend'),
      reason: z.enum(SUSPEND_REASONS),
      interactionId: boundedId.optional(),
    })
    .strict(),
  z
    .object({
      kind: z.literal('yield'),
      reason: z.enum(YIELD_REASONS).exclude(NODE_AUTHORED_YIELD_REASONS),
      notBefore: z.iso.datetime(),
      checkpointRef: z.string().min(1).max(512).optional(),
    })
    .strict(),
  z
    .object({
      kind: z.literal('quarantine'),
      attempts: z.number().int().positive(),
      lastErrorType: machineError.optional(),
    })
    .strict(),
  z
    .object({
      kind: z.literal('fail'),
      message: z.string().min(1).max(2_000).optional(),
      // `mcp_tool_withdrawn`: the servers' labels, so the web app names them in the reader's language (WP-84).
      servers: z.array(z.string().min(1).max(200)).min(1).max(16).optional(),
      // `out_of_memory`: the sandbox's memory limit, so the web app names it (R31GVISO-FU-3).
      memoryLimitMb: z.number().int().min(1).max(1_048_576).optional(),
    })
    .strict(),
]);

const runTransition = z
  .object({
    expectedRevision: z.number().int().nonnegative().optional(),
    // A v2 yield puts the run back in the queue; only v2 batched commits may.
    status: runStatus,
    outcome: runOutcome.optional(),
    checkpoint: immutableObjectRef.optional(),
    errorType: machineError.optional(),
    detail: transitionDetail.optional(),
    leavesInput: z.boolean().optional(),
    /** An answered completion whose work a limit cut short (BUDGETEND). */
    truncatedBy: z.enum(CUT_SHORT_REASONS).optional(),
    /**
     * A child run's completion: its result document and label (P6.1). The
     * ref is the contract's `ContentRef`, as `child.completed` carries it: a
     * hand copy capped at 4,096 characters refused every inline result over
     * about 3 KB (CHILDREF).
     */
    childResult: z
      .object({
        resultRef: contract(contentRefSchema),
        labels: contract(labelsSchema),
      })
      .strict()
      .optional(),
  })
  .strict();

/** REPORT 6.1.4: one fenced batch per worker commit. */
const batchedCommitBody = z
  .object({
    commandId: boundedId,
    attemptId: boundedId,
    fence: z.number().int().nonnegative(),
    events: z.array(journalEventInput).max(MAX_COMMIT_EVENTS).default([]),
    legacyEvents: z.array(legacyEvent).max(MAX_COMMIT_EVENTS).default([]),
    transition: runTransition.optional(),
    workspaceHeadCAS: z
      .object({
        workspaceId: boundedId,
        expectedRevision: z.number().int().nonnegative(),
        revision: z.number().int().positive(),
      })
      .strict()
      .optional(),
    outbox: z.array(dispatchCommand).max(100).default([]),
  })
  .strict()
  .superRefine((body, context) => {
    if (body.transition === undefined || body.legacyEvents.length === 0) return;
    const eventType = LEGACY_TERMINAL_EVENTS[body.transition.status];
    if (
      eventType !== undefined &&
      !body.legacyEvents.some((event) => event.type === eventType)
    ) {
      context.addIssue({
        code: 'custom',
        path: ['legacyEvents'],
        message: `${body.transition.status} commits require a ${eventType} event`,
      });
    }
  });

export const commitRunBodySchema = z.union([
  batchedCommitBody,
  legacyCommitBody,
]);

export const commitRunSchema = runParamsSchema.extend({
  body: commitRunBodySchema,
});

const toolIdText = z.string().min(1).max(320);

/** A child's grant request (P6.3): every dimension optional, each a subset of the parent's. */
const childGrantRequest = z
  .object({
    tools: z.array(toolIdText).max(500).optional(),
    sandboxProfile: z.enum(['agent-code', 'none']).optional(),
    egress: z
      .string()
      .regex(/^(?:none|allowlist:[A-Za-z0-9_-]{1,64})$/)
      .optional(),
    mounts: z.array(z.enum(WORKSPACE_MOUNTS)).max(16).optional(),
    knowledge: z
      .object({
        apps: z.array(boundedId).max(500).optional(),
        kb: z.array(boundedId).max(500).optional(),
      })
      .strict()
      .optional(),
    skills: z.array(z.string().min(1).max(64)).max(200).optional(),
    access: z.array(boundedId).max(50).optional(),
    maxTurns: z.number().int().positive().max(1_000).optional(),
    maxTokens: z
      .number()
      .int()
      .positive()
      .max(Number.MAX_SAFE_INTEGER)
      .optional(),
    maxChildren: z.number().int().nonnegative().max(1_000).optional(),
  })
  .strict();

/** Child admission (P6.1): a parent's attempt asks for one child per tool invocation. */
export const spawnChildRunSchema = runParamsSchema.extend({
  body: z
    .object({
      attemptId: boundedId,
      fence: z.number().int().nonnegative(),
      invocationId: boundedId,
      taskId: boundedId.optional(),
      goal: z.string().min(1).max(CHAT_QUERY_MAX_CHARS),
      grants: childGrantRequest.default({}),
      /** What the child runs as (K-L): pinned as the child's `x.pipeshub.subAgent`. */
      subAgent: contract(pipeshubRunSpecExtensionSchema.shape.subAgent),
      /** The parent's label when it asked (UX2): pinned as the child's `x.pipeshub.inheritedLabel`. */
      inheritedLabel: contract(
        pipeshubRunSpecExtensionSchema.shape.inheritedLabel,
      ),
      /** The conversation grants the parent held (FLOWASK): pinned as the child's `x.pipeshub.inheritedGrants`. */
      inheritedGrants: contract(
        pipeshubRunSpecExtensionSchema.shape.inheritedGrants,
      ),
      /** The child's task in its parts (BGUI-10), kept on its input for its brief. */
      task: contract(jobBriefSchema.shape.task.optional()),
      /**
       * R31HOT2 (ADR-OWD-07 D-LAZY-5): the first-use pins of the MCP servers the parent has listed, by instance, each
       * tool by the server's own name at its descriptor digest; pinned narrowed to what the child holds.
       */
      mcpPins: z
        .record(
          boundedId,
          z
            .record(
              z.string().min(1).max(256),
              z.string().regex(/^sha256:[0-9a-f]{64}$/),
            )
            .refine((pin) => Object.keys(pin).length <= 500, {
              message: 'at most 500 tools per server',
            }),
        )
        .refine((pins) => Object.keys(pins).length <= 50, {
          message: 'at most 50 servers',
        })
        .optional(),
      /** An engine child (E1); absent: the loop. */
      executor: z
        .object({
          kind: z.literal('engine'),
          profile: contract(engineProfileNarrowingSchema.optional()),
        })
        .strict()
        .optional(),
    })
    .strict(),
});

/** P9: the grant auditor's read of a run (worker credential; ids only). */
export const readRunAuditSchema = runParamsSchema.extend({
  query: z.object({ orgId: boundedId }).strict(),
});

export const readLiveLayerSchema = z.object({
  params: z.object({ orgId: boundedId }).strict(),
  query: z
    .object({ since: z.coerce.number().int().nonnegative().optional() })
    .strict(),
});

export const readRunJournalSchema = runParamsSchema.extend({
  query: z
    .object({
      attemptId: boundedId,
      fence: z.coerce.number().int().nonnegative(),
      after: z.coerce.number().int().nonnegative().default(0),
      limit: z.coerce.number().int().positive().max(500).default(200),
      scope: z.enum(['stream', 'run']).default('stream'),
    })
    .strict(),
});

const contentAttemptQuery = z
  .object({
    attemptId: boundedId,
    fence: z.coerce.number().int().nonnegative(),
  })
  .strict();

/** Sealed content (DP-1): the body is the raw bytes, never JSON. */
export const writeRunContentSchema = runParamsSchema.extend({
  query: contentAttemptQuery,
});

/**
 * Reads a `sealed:` ref only (about 150 characters); an `inline:` ref carries
 * its bytes and is never sent here, so this bound is not the contract's.
 */
export const readRunContentSchema = runParamsSchema.extend({
  query: contentAttemptQuery.extend({ ref: z.string().min(1).max(2048) }),
});

/** Workspace ids are the core's partition ids: `ws:{sessionId}` and its forks (P3.2). */
const workspaceId = z
  .string()
  .min(1)
  .max(256)
  .regex(/^[A-Za-z0-9._:-]+$/);
const revisionNumber = z.number().int().nonnegative();
/** Path rules are the domain's (422 with a reason), not a schema's 400. */
const workspacePath = z.string().min(1).max(4096);
const attemptFields = {
  attemptId: boundedId,
  fence: z.number().int().nonnegative(),
};
const workspaceParams = z.object({ runId: boundedId, workspaceId });

const workspaceFileSchema = z
  .object({
    path: workspacePath,
    objectId: z.string().min(1).max(2048),
    contentHash: z.string().min(1).max(256),
    sizeBytes: z.number().int().nonnegative(),
    mimeType: z.string().min(1).max(255),
    mode: z.number().int().nonnegative().optional(),
  })
  .strict();

export const workspaceHeadSchema = z.object({
  params: workspaceParams,
  query: contentAttemptQuery,
});

export const workspaceRevisionSchema = z.object({
  params: workspaceParams.extend({
    revision: z.coerce.number().int().nonnegative(),
  }),
  query: contentAttemptQuery,
});

export const workspaceCommitSchema = z.object({
  params: workspaceParams,
  body: z
    .object({
      ...attemptFields,
      baseRevision: revisionNumber,
      reason: z.enum(WORKER_COMMIT_REASONS),
      upserts: z.array(workspaceFileSchema).max(MAX_WORKSPACE_FILES),
      deletes: z.array(workspacePath).max(MAX_WORKSPACE_FILES),
    })
    .strict(),
});

export const workspaceForkSchema = z.object({
  params: workspaceParams,
  body: z
    .object({
      ...attemptFields,
      atRevision: revisionNumber,
      forkId: workspaceId,
    })
    .strict(),
});

export const workspaceMergeSchema = z.object({
  params: workspaceParams,
  body: z
    .object({
      ...attemptFields,
      forkId: workspaceId,
      mergeKey: boundedId.optional(),
      onConflict: z.enum(CONFLICT_POLICIES).optional(),
    })
    .strict(),
});

export const workspaceWriterSchema = z.object({
  params: workspaceParams,
  body: z
    .object({
      ...attemptFields,
      ttlMs: z.number().int().min(WRITER_LEASE_MIN_MS).max(WRITER_LEASE_MAX_MS),
    })
    .strict(),
});

export const workspaceWriterReleaseSchema = z.object({
  params: workspaceParams,
  body: z.object(attemptFields).strict(),
});

export const workspaceRollbackSchema = z.object({
  params: workspaceParams,
  body: z.object({ ...attemptFields, toRevision: revisionNumber }).strict(),
});

export const workspaceKeySchema = runParamsSchema.extend({
  body: z.object(attemptFields).strict(),
});

/**
 * E2: the egress proxy asks for the attempt its sandbox is bound to, with the proof the broker attached to that
 * sandbox's binding (R38IDENT-FU-7); Node binds the rest.
 */
export const attemptGatewayTokenSchema = runParamsSchema.extend({
  body: z
    .object({
      orgId: boundedId,
      attemptId: boundedId,
      bindingProof: z.string().max(64),
    })
    .strict(),
});

/** E3: the run comes from the path; everything else from the presented gateway token. */
export const modelGrantSchema = runParamsSchema;

/** WP-63: what a run's sandbox did at an allowed website; the bounds are `egress.allowed`'s `site`. */
const siteReportSchema = z
  .object({
    path: z.string().regex(/^\/[!-"$->@-~]{0,120}$/),
    access: z.enum(['read', 'read-write', 'host-level']),
    use: z.enum(['read', 'write']),
  })
  .strict();

/** SEC2 O-2: ids and codes only; the bounds are `gateway.refused`'s. */
export const gatewayRefusalSchema = runParamsSchema.extend({
  body: z
    .object({
      gateway: z.enum(['egress', 'model']),
      reason: z.string().regex(/^[a-z0-9][a-z0-9_.-]{0,63}$/),
      status: z.number().int().min(400).max(599),
      host: z
        .string()
        .regex(/^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$/)
        .optional(),
      param: z
        .string()
        .regex(/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/)
        .optional(),
      site: siteReportSchema.optional(),
    })
    .strict()
    .refine((body) => body.site === undefined || body.host !== undefined, {
      message: 'a site report names its host',
    }),
});

/** OB-03: a destination the egress proxy let the run reach; the bounds are `egress.allowed`'s. */
export const egressAllowedSchema = runParamsSchema.extend({
  body: z
    .object({
      host: z.string().regex(/^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$/),
      via: z.enum(['http', 'intercepted', 'tunnel', 'database']),
      site: siteReportSchema.optional(),
    })
    .strict(),
});

const fallbackModel = z
  .object({
    provider: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,199}$/),
    model: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,199}$/),
  })
  .strict();

/** G-48 (WP-100): the model gateway moved an engine run's call to an alternate; the bounds are `model.fallback`'s. */
export const modelFallbackReportSchema = runParamsSchema.extend({
  body: z
    .object({
      from: fallbackModel,
      to: fallbackModel,
      reason: z.enum(['5xx', 'timeout', '429_exhausted', 'content_filter']),
    })
    .strict(),
});

/** WP-103: a credential shape a gateway hid from the run's model call; the bounds are `gateway.secret_hidden`'s. */
export const secretHiddenReportSchema = runParamsSchema.extend({
  body: z
    .object({
      gateway: z.enum(['egress', 'model']),
      kind: z.string().regex(/^[a-z0-9][a-z0-9_.-]{0,63}$/),
    })
    .strict(),
});

const egressBytes = z.number().int().min(0).max(Number.MAX_SAFE_INTEGER);

/** C6.12: an unusual egress volume of the run; the bounds are `egress.volume_alert`'s. */
export const egressVolumeAlertSchema = runParamsSchema.extend({
  body: z
    .object({
      kind: z.enum(['run_threshold', 'org_baseline', 'run_cap']),
      direction: z.enum(['up', 'down', 'total']),
      bytes: egressBytes,
      thresholdBytes: egressBytes,
      baselineBytes: egressBytes.optional(),
      alias: z.enum([
        'model-gateway',
        'tool-gateway',
        'tunnel',
        'intercepted',
        'database',
        'other',
        'repository',
      ]),
    })
    .strict(),
});

const publicRunParams = z.object({
  agentKey: boundedId,
  sessionId: z.string().min(1).max(128),
  runId: boundedId,
});

const publicChatRunParams = publicRunParams.omit({ agentKey: true });

export const getSessionRunSchema = z.object({
  params: publicRunParams,
});

/** `lastEventId` stands in for the `Last-Event-ID` header where a client cannot set one. */
const streamQuery = z
  .object({
    lastEventId: z.string().min(1).max(MAX_STREAM_ID_LENGTH).optional(),
  })
  .strict();

export const streamSessionRunSchema = z.object({
  params: publicRunParams,
  query: streamQuery,
});

export const controlSessionRunSchema = z.object({
  params: publicRunParams,
  body: z
    .object({
      /** Absent (UX3): act on the run as it is now; Stop needs no read first. */
      expectedRevision: z.number().int().nonnegative().optional(),
      idempotencyKey: boundedId,
    })
    .strict(),
});

export const getChatSessionRunSchema = z.object({
  params: publicChatRunParams,
});

/** AP-05: a person's reason for a denial, bounded as the worker bounds what the model reads. */
const denyReason = z
  .string()
  .trim()
  .min(1)
  .max(MAX_DENY_REASON_CHARS)
  .optional();

/** Whether `value` is JSON a canonical encoding takes (no non-finite numbers, no reserved keys), within `limit` bytes. */
const canonicalWithin = (value: unknown, limit: number): boolean => {
  try {
    return canonicalJsonBytes(value).length <= limit;
  } catch {
    return false;
  }
};

/** AP-06: the arguments a person approves in place of the call's own: a JSON object, bounded as the worker offers it. */
const editedArgs = z
  .record(z.string(), z.unknown())
  .refine((value) => canonicalWithin(value, MAX_EDITED_ARGS_BYTES), {
    message: `Edited arguments are JSON of at most ${MAX_EDITED_ARGS_BYTES} bytes`,
  })
  .optional();

const onlyDenialsHaveReasons = (body: {
  decision: string;
  reason?: string | undefined;
}): boolean => body.reason === undefined || body.decision === 'denied';

/** A person's decision on an interaction a run waits on (P2.6c). */
export const decideSessionInteractionSchema = z.object({
  params: publicRunParams.extend({ interactionId: boundedId }),
  body: z
    .object({
      /** `answered`: an unknown effect happened (UX3); nothing else takes it. */
      decision: z.enum(['approved', 'denied', 'answered']),
      idempotencyKey: boundedId,
      /** UX1: `conversation` approves this tool and destination for the rest of the conversation. */
      scope: z.enum(['once', 'conversation']).optional(),
      /** BUGS-B: main's "Don't ask again", saving the answer as the person's own rule for the tool. */
      remember: z.boolean().optional(),
      /** AP-05: why the person declined; the run is told it. */
      reason: denyReason,
      /** AP-06: the arguments the person approves instead of the call's own, where the card offers editing. */
      edits: editedArgs,
    })
    .strict()
    .refine(onlyDenialsHaveReasons, {
      message: 'Only a denial carries a reason',
      path: ['reason'],
    })
    .refine(
      (body) =>
        body.edits === undefined ||
        (body.decision === 'approved' &&
          body.scope !== 'conversation' &&
          body.remember !== true),
      {
        message: 'Only an approval of this one call carries edited arguments',
        path: ['edits'],
      },
    )
    .refine(
      (body) =>
        body.remember !== true ||
        (body.decision !== 'answered' && body.scope !== 'conversation'),
      {
        message:
          'Only an approval or a denial of this one call can be remembered',
        path: ['remember'],
      },
    )
    .refine(
      (body) => body.scope !== 'conversation' || body.decision === 'approved',
      {
        message: 'Only an approval can hold for the conversation',
        path: ['scope'],
      },
    )
    .refine(
      (body) => body.scope === undefined || body.decision !== 'answered',
      {
        message: 'An answer about an unknown effect has no scope',
        path: ['scope'],
      },
    ),
});

export const decideChatSessionInteractionSchema =
  decideSessionInteractionSchema.extend({
    params: publicChatRunParams.extend({ interactionId: boundedId }),
  });

/** DECIDE409: the most calls one "Allow all" decides; a batch card never holds more. */
export const MAX_BATCH_DECISIONS = 100;

/** DECIDE409: "Allow all N" / "Deny all": one decision on several of the run's interactions. */
export const decideSessionInteractionsSchema = z.object({
  params: publicRunParams,
  body: z
    .object({
      interactionIds: z
        .array(boundedId)
        .min(1)
        .max(MAX_BATCH_DECISIONS)
        .refine((ids) => new Set(ids).size === ids.length, {
          message: 'Each interaction is named once',
        }),
      // `answered` is an unknown effect's own, decided one at a time from its card.
      decision: z.enum(['approved', 'denied']),
      idempotencyKey: boundedId,
      scope: z.enum(['once', 'conversation']).optional(),
      /** BUGS-B: named so a batch asking for "Don't ask again" gets its 409, not a bare 400; never saved. */
      remember: z.boolean().optional(),
      /** AP-05: "Deny all" with one reason, told to the run for each call. */
      reason: denyReason,
    })
    .strict()
    .refine(onlyDenialsHaveReasons, {
      message: 'Only a denial carries a reason',
      path: ['reason'],
    })
    .refine(
      (body) => body.scope !== 'conversation' || body.decision === 'approved',
      {
        message: 'Only an approval can hold for the conversation',
        path: ['scope'],
      },
    ),
});

export const decideChatSessionInteractionsSchema =
  decideSessionInteractionsSchema.extend({ params: publicChatRunParams });

/**
 * G-13: one decision on calls with the same pattern across runs and sessions,
 * from the inbox. The server reads each run's session and agent itself; the
 * client names only the calls and the pattern it showed.
 */
export const decideMatchingApprovalsSchema = z.object({
  body: z
    .object({
      pattern: z.string().regex(/^p1:[0-9a-f]{64}$/),
      decision: z.enum(['approved', 'denied']),
      items: z
        .array(
          z.object({ runId: boundedId, interactionId: boundedId }).strict(),
        )
        .min(1)
        .max(APPROVAL_BATCH_MAX_ITEMS)
        .refine(
          (items) =>
            new Set(items.map((i) => `${i.runId}\0${i.interactionId}`)).size ===
            items.length,
          { message: 'Each call is named once' },
        ),
      idempotencyKey: boundedId,
      reason: denyReason,
    })
    .strict()
    .refine(onlyDenialsHaveReasons, {
      message: 'Only a denial carries a reason',
      path: ['reason'],
    }),
});

/** UX5: one command's full log, from the activity panel. */
export const commandLogSchema = z.object({
  params: publicRunParams.extend({ commandId: boundedId }),
});

export const chatCommandLogSchema = z.object({
  params: publicChatRunParams.extend({ commandId: boundedId }),
});

/** A model's tool call id: longer than a run id can be. */
const toolCallId = z
  .string()
  .min(1)
  .max(256)
  .regex(/^[A-Za-z0-9._:-]+$/);

/** ACTRESULTS: one step's result, when the activity panel opens it. */
export const activityStepSchema = z.object({
  params: publicRunParams.extend({ toolCallId }),
});

export const chatActivityStepSchema = z.object({
  params: publicChatRunParams.extend({ toolCallId }),
});

export const followupsWaitQuery = z
  .object({
    waitMs: z.coerce
      .number()
      .int()
      .min(0)
      .max(MAX_FOLLOWUPS_WAIT_MS)
      .default(0),
  })
  .strict();

export const runFollowupsSchema = z.object({
  params: publicRunParams,
  query: followupsWaitQuery,
});

export const chatRunFollowupsSchema = z.object({
  params: publicChatRunParams,
  query: followupsWaitQuery,
});

const publicSessionParams = publicRunParams.omit({ runId: true });
const publicChatSessionParams = publicSessionParams.omit({ agentKey: true });
const pathRevision = z.coerce
  .number()
  .int()
  .min(0)
  .max(Number.MAX_SAFE_INTEGER);

/** The session workspace's version list (U3): newest first, paged by `before`. */
export const listSessionWorkspaceVersionsSchema = z.object({
  params: publicSessionParams,
  query: z
    .object({
      limit: z.coerce.number().int().min(1).max(100).optional(),
      before: pathRevision.pipe(z.number().min(1)).optional(),
    })
    .strict(),
});

export const listChatSessionWorkspaceVersionsSchema =
  listSessionWorkspaceVersionsSchema.extend({
    params: publicChatSessionParams,
  });

/** SLASH2 `/diff` and `/undo`: the workspace's changes over the chat (default) or its last writing turn. */
export const sessionWorkspaceDiffSchema = z.object({
  params: publicSessionParams,
  query: z.object({ scope: z.enum(['chat', 'turn']).default('chat') }).strict(),
});

export const chatSessionWorkspaceDiffSchema = sessionWorkspaceDiffSchema.extend(
  {
    params: publicChatSessionParams,
  },
);

/** A workspace path as a query names it: canonical relative form is checked by the domain. */
const workspacePathQuery = z.string().min(1).max(1024);
const workspaceFolderQuery = workspacePathQuery.transform((path) =>
  path.replace(/\/+$/, ''),
);
/** `/open`'s preview bound and `/download`'s whole file (SyncLimits: 100 MiB a file). */
export const WORKSPACE_FILE_MAX_PREVIEW_BYTES = 10 * 1024 * 1024;

/** SLASH3 `/files`: a revision's files under a folder (head by default). */
export const sessionWorkspaceFilesSchema = z.object({
  params: publicSessionParams,
  query: z
    .object({
      revision: pathRevision.optional(),
      prefix: workspaceFolderQuery.optional(),
    })
    .strict(),
});
export const chatSessionWorkspaceFilesSchema =
  sessionWorkspaceFilesSchema.extend({ params: publicChatSessionParams });

/** SLASH3 `/open` and `/download <file>`: one file's bytes, never rendered as a page. */
export const sessionWorkspaceFileSchema = z.object({
  params: publicSessionParams,
  query: z
    .object({
      path: workspacePathQuery,
      revision: pathRevision.optional(),
      download: z.enum(['0', '1']).optional(),
      maxBytes: z.coerce
        .number()
        .int()
        .min(1)
        .max(WORKSPACE_FILE_MAX_PREVIEW_BYTES)
        .optional(),
    })
    .strict(),
});
export const chatSessionWorkspaceFileSchema = sessionWorkspaceFileSchema.extend(
  { params: publicChatSessionParams },
);

/** SLASH3 `/download [folder]`: a revision's files under a folder as a streamed zip. */
export const sessionWorkspaceArchiveSchema = sessionWorkspaceFilesSchema;
export const chatSessionWorkspaceArchiveSchema =
  chatSessionWorkspaceFilesSchema;

/** SLASH3 `/run`: one shell line, bounded (§3.5 Limits); its timeout up to run_code's own wall bound. */
export const SANDBOX_COMMAND_MAX_CHARS = 8 * 1024;
export const SANDBOX_COMMAND_DEFAULT_TIMEOUT_S = 60;
export const SANDBOX_COMMAND_MAX_TIMEOUT_S = 600;
/**
 * The chat's mode, model and tools as its next message would send them: admission routes and grants a `/run` as
 * it would that turn, so it may run code exactly where the agent may (SD2).
 */
const sandboxCommandBody = z
  .object({
    chatMode: z.string().min(1).max(64).default('auto'),
    modelKey: z.string().max(256).optional(),
    modelName: z.string().max(256).optional(),
    modelProvider: z.string().max(128).optional(),
    modelFriendlyName: z.string().max(256).optional(),
    tools: z.array(z.string().min(1).max(512)).max(1024).optional(),
    agentCapabilities: z
      .object({
        internalSearch: z.boolean(),
        webSearch: z.boolean(),
        deepSearch: z.boolean().optional(),
      })
      .strict()
      .optional(),
    command: z.string().trim().min(1).max(SANDBOX_COMMAND_MAX_CHARS),
    timeoutS: z
      .number()
      .int()
      .min(1)
      .max(SANDBOX_COMMAND_MAX_TIMEOUT_S)
      .default(SANDBOX_COMMAND_DEFAULT_TIMEOUT_S),
    idempotencyKey: boundedId,
  })
  .strict();
export const runSessionCommandSchema = z.object({
  params: publicSessionParams,
  body: sandboxCommandBody,
});
export const runChatSessionCommandSchema = z.object({
  params: publicChatSessionParams,
  body: sandboxCommandBody,
});

/** SLASH3 `/upload`: one request's bounds (SyncLimits: 100 MiB a file); a bigger body is refused before it is read. */
export const UPLOAD_MAX_FILE_BYTES = 100 * 1024 * 1024;
export const UPLOAD_MAX_REQUEST_BYTES = 512 * 1024 * 1024;
/** The multipart fields beside the files; `paths` is a JSON list, one per file, in order. */
export const uploadFieldsSchema = z
  .object({
    baseRevision: z.coerce.number().int().nonnegative(),
    idempotencyKey: boundedId,
    folder: z.string().max(1024).optional(),
    paths: z.string().max(2_000_000),
    replace: z.enum(['true', 'false']).default('false'),
  })
  .strict();
export const uploadSessionFilesSchema = z.object({
  params: publicSessionParams,
});
export const uploadChatSessionFilesSchema = z.object({
  params: publicChatSessionParams,
});

/** SLASH3 `/engine`: this chat's coding engine, `none`, or null (back to the person's choice). */
const sessionEngineBody = z
  .object({
    engine: z
      .string()
      .regex(/^[a-z0-9][a-z0-9_.-]{0,63}$/)
      .nullable(),
  })
  .strict();
export const putSessionCodingEngineSchema = z.object({
  params: publicSessionParams,
  body: sessionEngineBody,
});
export const putChatSessionCodingEngineSchema = z.object({
  params: publicChatSessionParams,
  body: sessionEngineBody,
});

export const putSessionRepositorySchema = z.object({
  params: publicSessionParams,
  body: sessionRepositoryBody,
});
export const putChatSessionRepositorySchema = z.object({
  params: publicChatSessionParams,
  body: sessionRepositoryBody,
});

/** SLASH3 `/sandbox` and its "End now": the session's parameters only. */
export const sessionSandboxSchema = z.object({ params: publicSessionParams });
export const chatSessionSandboxSchema = z.object({
  params: publicChatSessionParams,
});

/** Restore an earlier version as a new one, on top of the head the person saw (U3). */
export const restoreSessionWorkspaceVersionSchema = z.object({
  params: publicSessionParams.extend({ revision: pathRevision }),
  body: z
    .object({
      baseRevision: z.number().int().nonnegative(),
      idempotencyKey: boundedId,
    })
    .strict(),
});

export const restoreChatSessionWorkspaceVersionSchema =
  restoreSessionWorkspaceVersionSchema.extend({
    params: publicChatSessionParams.extend({ revision: pathRevision }),
  });

export const streamChatSessionRunSchema = streamSessionRunSchema.extend({
  params: publicChatRunParams,
});

export const controlChatSessionRunSchema = controlSessionRunSchema.extend({
  params: publicChatRunParams,
});

const attachment = z
  .object({
    recordId: z.string().min(1).max(512),
    recordName: z.string().max(1024).optional(),
    mimeType: z.string().max(255).optional(),
    extension: z.string().max(64).optional(),
    virtualRecordId: z.string().max(512).optional(),
    source: z.enum(['upload', 'paste-text']).optional(),
  })
  .strict();

const inputArtifact = z
  .object({
    artifactId: z.string().min(1).max(512),
    version: z.number().int().positive().optional(),
  })
  .strict();

/**
 * The /command a chat turn's query was expanded from (SLASH1). Only the chat
 * routes set it, from the server's expansion; a run admission body never
 * carries it (`runInputSchema` is strict).
 */
export const runCommandSchema = z
  .object({
    name: z
      .string()
      .max(64)
      .regex(/^[a-z0-9]+(-[a-z0-9]+)*$/),
    version: z.string().min(1).max(64),
    contentDigest: z.string().regex(/^sha256:[0-9a-f]{64}$/),
    args: z
      .record(z.string(), z.string().max(4000))
      .refine((args) => Object.keys(args).length <= 20, 'at most 20 arguments'),
    typed: z.string().min(1).max(CHAT_QUERY_MAX_CHARS),
    owner: z.enum(['you', 'team', 'org', 'pipeshub']),
    authorId: z.string().min(1).max(128).optional(),
    authorFormer: z.boolean().optional(),
    teamId: z.string().min(1).max(128).optional(),
    files: z.number().int().min(0).max(1_000_000).optional(),
  })
  .strict();

/** What a run's input may carry; `admissionBodySchema` adds the admission fields. */
export const runInputSchema = z
  .object({
    query: z.string().min(1).max(CHAT_QUERY_MAX_CHARS),
    recordIds: z.array(z.string().min(1).max(512)).max(500).default([]),
    // The keys a chat turn accepts (#4492): unknown, internal-only and mistyped filters are refused.
    filters: requestFiltersSchema.default({}),
    appliedFilters: z.record(z.string(), z.unknown()).optional(),
    attachments: z.array(attachment).max(20).default([]),
    /** E1b: conversation artifacts an engine run starts from, under `in/`. */
    inputArtifacts: z.array(inputArtifact).max(20).optional(),
    tools: z.array(z.string().min(1).max(512)).max(1024).optional(),
    agentCapabilities: z
      .object({
        internalSearch: z.boolean(),
        webSearch: z.boolean(),
        deepSearch: z.boolean().optional(),
      })
      .strict()
      .optional(),
    modelKey: z.string().max(256).optional(),
    modelName: z.string().max(256).optional(),
    modelProvider: z.string().max(128).optional(),
    modelFriendlyName: z.string().max(256).optional(),
    reasoningEffort: z.string().max(64).optional(),
    /** A1: `users/<you>/<provider>` (your own key) or `cloud/<id>` (an org cloud account); engine runs only. */
    modelAccountId: z.string().min(1).max(300).optional(),
    chatMode: z.string().min(1).max(64).default('auto'),
    timezone: z.string().max(128).optional(),
    currentTime: z.string().max(128).optional(),
    /** Today's loop's measurement frames (`retrieval_context`, `run_usage`) on the stream (SYNC2-F1). */
    includeRetrievalContext: z.boolean().optional(),
    /** How long each knowledge search waits for its keyword matches (the chat routes' field, one schema). */
    keywordSearchBudget: keywordSearchBudgetSchema.optional(),
  })
  .strict();

export const admissionBodySchema = runInputSchema.extend({
  idempotencyKey: boundedId,
  /** Add to the session's running task only; never start a run (T2). */
  steer: z.boolean().optional(),
  /** Regenerate (UX3): ask the question of the conversation's last answer again, replacing that answer. */
  regenerate: z.object({ messageId: boundedId }).strict().optional(),
  /**
   * G-13: the run whose question this message answers (the inbox's Questions tab). The turn continues on that
   * run's model, mode, model account and tools wherever the body names none.
   */
  answers: boundedId.optional(),
  /** GIT-FLOW (WP-26): the new conversation's GitHub repository; an existing one's is set with `PUT …/repository`. */
  repository: sessionRepositoryBody.optional(),
  deadlineMs: z
    .number()
    .int()
    .min(60_000)
    .max(24 * 60 * 60 * 1000)
    .default(RELEASE_DEFAULTS.runs.deadlineMs),
});

export const createSessionRunSchema = z.object({
  params: z.object({ agentKey: boundedId }),
  body: admissionBodySchema,
});

export const submitSessionRunSchema = z.object({
  params: z.object({
    agentKey: boundedId,
    sessionId: z.string().min(1).max(128),
  }),
  body: admissionBodySchema,
});

export const createChatSessionRunSchema = z.object({
  body: admissionBodySchema,
});

export const submitChatSessionRunSchema = z.object({
  params: z.object({
    sessionId: z.string().min(1).max(128),
  }),
  body: admissionBodySchema,
});

/** P6.5: a caller's reservation key (the model gateway's completion id, the worker's call id). */
const budgetKey = z
  .string()
  .min(1)
  .max(128)
  .regex(/^[A-Za-z0-9._:~-]+$/);
const budgetAmount = z
  .object({
    usdMicros: z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER),
    tokens: z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER),
  })
  .strict();
const usageTokens = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER);
/** A call's usage as the settler reports it (OWD-8: the ledger is the usage authority). */
const budgetUsage = z
  .object({
    inputTokens: usageTokens,
    outputTokens: usageTokens,
    cacheReadTokens: usageTokens,
    cacheWriteTokens: usageTokens,
  })
  .strict();
/** What the worker's attempt routes add to every call; the token already names the attempt. */
const attemptEcho = {
  attemptId: boundedId.optional(),
  fence: z.number().int().nonnegative().optional(),
};

export const reserveBudgetSchema = runParamsSchema.extend({
  body: z
    .object({
      key: budgetKey,
      requested: budgetAmount,
      minimum: budgetAmount,
      ttlS: z.number().int().positive().max(3600),
      opening: budgetAmount.optional(),
      ...attemptEcho,
    })
    .strict(),
});

export const settleBudgetSchema = z.object({
  params: z.object({ runId: boundedId, key: budgetKey }),
  body: z
    .object({
      actual: budgetAmount.optional(),
      usage: budgetUsage.optional(),
      /** WP-66: `actual` is the gateway's estimate of a cut call; this much more stays held for `holdTtlS`. */
      hold: budgetAmount.optional(),
      holdTtlS: z.number().int().positive().max(3600).optional(),
      // GA-15: what the call was for, so cost is reported per role; absent: primary.
      modelRole: z.enum(MODEL_ROLES).optional(),
      ...attemptEcho,
    })
    .strict()
    .refine(
      (body) => (body.hold === undefined) === (body.holdTtlS === undefined),
      {
        message: 'A hold names its time, and only a hold does',
      },
    ),
});

export const readBudgetSchema = runParamsSchema.extend({
  query: z
    .object({
      attemptId: boundedId.optional(),
      fence: z.coerce.number().int().nonnegative().optional(),
    })
    .strict(),
});

/** The caller's spend over the conversation (SLASH2 C1, `/usage`). */
export const sessionUsageSchema = z.object({ params: publicSessionParams });
export const chatSessionUsageSchema = z.object({
  params: publicChatSessionParams,
});
/** `/compact [focus]` (SLASH2 C2): what the person asked the summary to keep, if anything. */
const compactBody = z
  .object({ focus: z.string().max(COMPACT_FOCUS_MAX_CHARS).optional() })
  .strict();
export const compactSessionSchema = z.object({
  params: publicSessionParams,
  body: compactBody,
});
export const compactChatSessionSchema = z.object({
  params: publicChatSessionParams,
  body: compactBody,
});

/** ADR-UI-01 D2: the caller opened the conversation; the mark is the server's time, so the body names nothing. */
export const markSessionSeenSchema = z.object({
  params: publicSessionParams,
  body: z.object({}).strict().optional(),
});
export const markChatSessionSeenSchema = z.object({
  params: publicChatSessionParams,
  body: z.object({}).strict().optional(),
});

/** ADR-GRANT-01 D8: a conversation's grants; one by its id. */
export const sessionGrantsSchema = z.object({ params: publicSessionParams });
export const chatSessionGrantsSchema = z.object({
  params: publicChatSessionParams,
});
const grantId = z.string().regex(/^cg_[0-9a-f]{40}$/);
export const revokeSessionGrantSchema = z.object({
  params: publicSessionParams.extend({ grantId }),
});
export const revokeChatSessionGrantSchema = z.object({
  params: publicChatSessionParams.extend({ grantId }),
});
