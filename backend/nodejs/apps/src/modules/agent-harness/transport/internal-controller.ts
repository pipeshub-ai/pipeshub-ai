import { deploymentRegion } from '../domain/region';
import { NextFunction, Response } from 'express';
import { randomUUID } from 'node:crypto';
import {
  ExpectedRefusalError,
  HttpError,
  NotFoundError,
  ServiceUnavailableError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { ERROR_CODES } from '../contracts/generated/ext/pipeshub/errors';
import {
  STORE_UNAVAILABLE_MESSAGE,
  storeTransient,
} from '../../../libs/repositories/agent-harness/journal-startup/mongo';
import { dependencyHealth } from '../ops/dependency-health';
import { ExecutionService } from '../application/execution-service';
import { HarnessAuthority } from '../application/harness-authority';
import { AttemptClaims } from '../domain/authority';
import {
  HarnessConflictError,
  ConversationDeletedError,
  HarnessNotFoundError,
  RunNotCompletedError,
  HarnessUnauthorizedError,
  HarnessUnavailableError,
  JournalPayloadMissingError,
  SessionErasedError,
  JournalRejectedError,
  harnessWireCode,
} from '../domain/errors';
import {
  asWorkerNamedOrg,
  HarnessAttemptRequest,
  harnessUnauthorized,
  logAuthorityRefusal,
} from './internal-auth';
import { forEnumeratedOrg } from '../../../libs/context/orgContext';
import { CLAIM_JOB } from '../application/execution-service';
import { HarnessLogger, silentHarnessLogger } from '../application/harness-log';
import {
  claimNextRunSchema,
  readRunAuditSchema,
  claimRunSchema,
  commitRunBodySchema,
  readRunJournalSchema,
  releaseRunSchema,
  renewRunSchema,
} from './schemas';
import { ClaimedRun } from '../domain/execution';
import { workerRun } from '../domain/steering';
import { RunInputSeal } from '../application/run-input-seal';
import { claimingWorker } from '../domain/worker-compat';
import { advertisedNodeCapabilities } from '../domain/node-capabilities';
import type { NodeCapabilities } from '../contracts/generated/core/node-capabilities';
import { priorityClassOf, type PriorityClass } from '../domain/priority-class';

function requestBody(req: HarnessAttemptRequest): Record<string, unknown> {
  return req.body as Record<string, unknown>;
}

function runId(req: HarnessAttemptRequest): string {
  return claimRunSchema.shape.params.parse(req.params).runId;
}

/** Fails closed if a route was mounted without `requireAttemptToken`. */
export function attemptClaims(req: HarnessAttemptRequest): AttemptClaims {
  if (req.harnessAttempt === undefined) {
    throw new UnauthorizedError('Agent harness attempt token required');
  }
  return req.harnessAttempt;
}

export function transportError(error: unknown): unknown {
  if (error instanceof HarnessUnauthorizedError) {
    return harnessUnauthorized(error);
  }
  if (error instanceof RunNotCompletedError) {
    return new ExpectedRefusalError(
      harnessWireCode(error.code),
      error.message,
      ERROR_CODES[error.code].status,
    );
  }
  if (error instanceof ConversationDeletedError) {
    return new HttpError(
      harnessWireCode(error.code),
      error.message,
      ERROR_CODES[error.code].status,
    );
  }
  if (error instanceof HarnessNotFoundError) {
    return new NotFoundError('Run not found');
  }
  if (error instanceof HarnessConflictError) {
    return new ExpectedRefusalError(
      harnessWireCode(error.code),
      error.message,
      ERROR_CODES[error.code].status,
    );
  }
  if (error instanceof JournalRejectedError) {
    return new HttpError(
      harnessWireCode(`JOURNAL_${error.code}`),
      `${error.message} (event ${String(error.index)})`,
      HTTP_STATUS.UNPROCESSABLE_ENTITY,
    );
  }
  if (error instanceof HarnessUnavailableError) {
    // Retryable for the worker: capabilities not probed yet, or not durable.
    return new ServiceUnavailableError(error.message);
  }
  if (error instanceof SessionErasedError) {
    // The session was erased: the worker must stop, not retry.
    return new HttpError(
      harnessWireCode(error.code),
      error.message,
      HTTP_STATUS.GONE,
    );
  }
  if (error instanceof JournalPayloadMissingError) {
    // The run has been failed; the worker must stop, not retry.
    return new HttpError(
      harnessWireCode('JOURNAL_PAYLOAD_MISSING'),
      error.message,
      HTTP_STATUS.CONFLICT,
    );
  }
  if (storeTransient(error)) {
    dependencyHealth.report('mongo');
    return new ServiceUnavailableError(STORE_UNAVAILABLE_MESSAGE);
  }
  return error;
}

export class AgentHarnessInternalController {
  constructor(
    private readonly executions: ExecutionService,
    private readonly authority: HarnessAuthority,
    private readonly newAttemptId: () => string = randomUUID,
    private readonly logger: HarnessLogger = silentHarnessLogger,
    /** This Node's region (OWD-12): a worker is handed only runs of its own region. */
    private readonly region: string = deploymentRegion(),
    /** What this Node names on its claims (`nodeCapabilitiesOf`). */
    private readonly nodeCapabilities: Readonly<NodeCapabilities> = advertisedNodeCapabilities(),
  ) {}

  /** The attempt checks after the token (A-3, A-9) refuse here; log them like the middleware's. */
  private refused(req: HarnessAttemptRequest, error: unknown): unknown {
    if (error instanceof HarnessUnauthorizedError) {
      logAuthorityRefusal(this.logger, req, error);
    }
    return transportError(error);
  }

  /** The worker credential admitted the caller; the claim names org and worker. */
  claim = async (
    req: HarnessAttemptRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const input = requestBody(req);
      const body = claimRunSchema.shape.body.parse({
        orgId: input['orgId'],
        workerId: input['workerId'],
        leaseDurationMs: input['leaseDurationMs'],
        workerCapabilities: input['workerCapabilities'],
        workerRegion: input['workerRegion'],
      });
      const answer = await asWorkerNamedOrg(body.orgId, async () =>
        this.withAttemptToken(
          await this.executions.claim({
            orgId: body.orgId,
            runId: runId(req),
            workerId: body.workerId,
            attemptId: this.newAttemptId(),
            leaseDurationMs: body.leaseDurationMs,
            worker: claimingWorker(body.workerCapabilities, {
              worker: body.workerRegion,
              here: this.region,
            }),
          }),
          body.leaseDurationMs,
        ),
      );
      res.status(HTTP_STATUS.OK).json(answer);
    } catch (error) {
      next(this.refused(req, error));
    }
  };

  /** P9: a run's pinned grant and tool intents for the grant auditor; 404 for a run the org does not hold. */
  auditEvidence = async (
    req: HarnessAttemptRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const query = readRunAuditSchema.shape.query.parse(req.query);
      const evidence = await asWorkerNamedOrg(query.orgId, () =>
        this.executions.auditEvidence(query.orgId, String(req.params['runId'])),
      );
      if (evidence === null) throw new HarnessNotFoundError();
      res.setHeader('Cache-Control', 'no-store');
      res.status(HTTP_STATUS.OK).json(evidence);
    } catch (error) {
      next(this.refused(req, error));
    }
  };

  /** Claim-next: 204 when nothing is claimable for the caller's role. */
  claimNext = async (
    req: HarnessAttemptRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const body = claimNextRunSchema.shape.body.parse(requestBody(req));
      const claimed = await this.executions.claimNext({
        role: body.role,
        workerId: body.workerId,
        attemptId: this.newAttemptId(),
        leaseDurationMs: body.leaseDurationMs,
        worker: claimingWorker(body.workerCapabilities, {
          worker: body.workerRegion,
          here: this.region,
        }),
        ...(body.aboveClass !== undefined
          ? { aboveClass: body.aboveClass }
          : {}),
      });
      if (claimed === null) {
        res.status(HTTP_STATUS.NO_CONTENT).end();
        return;
      }
      // The per-run phase runs as the claimed run's org, which the cross-org pick found (never the request's).
      const answer = await forEnumeratedOrg(claimed.run.orgId, CLAIM_JOB, () =>
        this.withAttemptToken(claimed, body.leaseDurationMs),
      );
      res.status(HTTP_STATUS.OK).json(answer);
    } catch (error) {
      next(this.refused(req, error));
    }
  };

  /** Gives the attempt back to the queue (e.g. a fold this build cannot read). */
  release = async (
    req: HarnessAttemptRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const claims = attemptClaims(req);
      const input = requestBody(req);
      const body = releaseRunSchema.shape.body.parse({
        attemptId: input['attemptId'],
        fence: input['fence'],
        reason: input['reason'],
      });
      await this.authority.authorizeAttempt(claims, body);
      const run = await this.executions.release({
        orgId: claims.orgId,
        runId: claims.runId,
        workerId: claims.workerId,
        attemptId: body.attemptId,
        fence: body.fence,
        reason: body.reason,
      });
      res
        .status(HTTP_STATUS.OK)
        .json({ run: workerRun(RunInputSeal.withoutSealed(run)) });
    } catch (error) {
      next(this.refused(req, error));
    }
  };

  /**
   * A claim whose attempt token cannot be issued is released at once, so the
   * run is claimable again instead of owned until its lease expires.
   */
  private async withAttemptToken(
    claimed: ClaimedRun,
    leaseDurationMs: number,
  ): Promise<
    ClaimedRun & {
      attemptToken: unknown;
      nodeCapabilities: Readonly<NodeCapabilities>;
      priorityClass: PriorityClass;
      skippedConnects: string[];
      treeDepth: number;
    }
  > {
    try {
      const attemptToken = await this.authority.issueFor(
        claimed.run,
        leaseDurationMs,
      );
      // UX2: what this Node accepts, so a newer worker writes only shapes it will not refuse. The class rides
      // beside the run, which a worker reads strictly: an older worker ignores it (G-45).
      return {
        ...claimed,
        run: workerRun(claimed.run),
        attemptToken,
        nodeCapabilities: this.nodeCapabilities,
        priorityClass: priorityClassOf(claimed.run),
        // WP-64: items skipped anywhere in the run's tree; a worker reads only its own stream.
        skippedConnects: await this.executions.treeSkippedConnects(claimed.run),
        // ADR-RUN-01 D4: a child's budget is its root's taken down its depth (its deadline is its root's here).
        treeDepth: claimed.run.parent?.depth ?? 0,
      };
    } catch (error) {
      const run = claimed.run;
      if (run.attemptId !== null && run.workerId !== null) {
        await this.executions.release({
          orgId: run.orgId,
          runId: run.runId,
          workerId: run.workerId,
          attemptId: run.attemptId,
          fence: run.fence,
          reason: 'attempt_token_unavailable',
        });
      }
      throw new HarnessUnavailableError(
        `Attempt token could not be issued: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
  }

  /** Heartbeat: extends the lease and returns a token for the new lease. */
  renew = async (
    req: HarnessAttemptRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const claims = attemptClaims(req);
      const input = requestBody(req);
      const body = renewRunSchema.shape.body.parse({
        attemptId: input['attemptId'],
        fence: input['fence'],
        leaseDurationMs: input['leaseDurationMs'],
      });
      await this.authority.authorizeAttempt(claims, body);
      const run = await this.executions.renew({
        orgId: claims.orgId,
        runId: claims.runId,
        workerId: claims.workerId,
        attemptId: body.attemptId,
        fence: body.fence,
        leaseDurationMs: body.leaseDurationMs,
      });
      const attemptToken = await this.authority.issueFor(
        run,
        body.leaseDurationMs,
      );
      res.status(HTTP_STATUS.OK).json({
        run: workerRun(RunInputSeal.withoutSealed(run)),
        attemptToken,
      });
    } catch (error) {
      next(this.refused(req, error));
    }
  };

  commit = async (
    req: HarnessAttemptRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const claims = attemptClaims(req);
      // Tenant, run and worker authority come from the token only.
      const {
        orgId: _orgId,
        runId: _runId,
        workerId: _workerId,
        ...input
      } = requestBody(req);
      const body = commitRunBodySchema.parse(input);
      const prior = this.executions.priorCommit(
        claims.orgId,
        claims.runId,
        body.commandId,
      );
      // A refused attempt check is the answer: the replay read is then never awaited, and must not go unhandled.
      prior.catch(() => undefined);
      const run = await this.authority.authorizeAttempt(claims, body);
      const authority = {
        orgId: claims.orgId,
        runId: claims.runId,
        workerId: claims.workerId,
      };
      const receipt = await this.executions.commit(
        'status' in body
          ? {
              ...body,
              ...authority,
              events: body.events.map((event) => ({
                type: event.type,
                data: event.data ?? null,
              })),
            }
          : {
              ...body,
              ...authority,
              legacyEvents: body.legacyEvents.map((event) => ({
                type: event.type,
                data: event.data ?? null,
              })),
            },
        run,
        prior,
      );
      const { replayed, ...stored } = receipt;
      res.status(HTTP_STATUS.OK).json({
        receipt: stored,
        events: await this.executions.receiptEvents(claims.orgId, stored),
        replayed: replayed === true,
      });
    } catch (error) {
      next(this.refused(req, error));
    }
  };

  readJournal = async (
    req: HarnessAttemptRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const claims = attemptClaims(req);
      const query = readRunJournalSchema.shape.query.parse(req.query);
      const run = await this.authority.authorizeAttempt(claims, query);
      const events = await this.executions.readRunJournal(
        {
          orgId: claims.orgId,
          runId: claims.runId,
          workerId: claims.workerId,
          attemptId: query.attemptId,
          fence: query.fence,
          afterSeq: query.after,
          limit: query.limit,
          scope: query.scope,
        },
        run,
      );
      res.status(HTTP_STATUS.OK).json({ events });
    } catch (error) {
      next(this.refused(req, error));
    }
  };
}
