import { performance } from 'perf_hooks';
import { HarnessRun, journalStreamId } from '../domain/execution';
import { JournalEvent } from '../domain/journal';
import { JournalPreparer, JournalStore } from '../ports/journal-store';
import {
  elapsedMs,
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';

/** Python's `MAX_REFUSALS_PER_RUN` (`harness_core.gateway.refusal_reports`). */
export const MAX_GATEWAY_REFUSALS_PER_RUN = 32;
/** Python's `MAX_ALLOWED_PER_RUN`: destinations the egress proxy let a run reach. */
export const MAX_EGRESS_ALLOWED_PER_RUN = 64;
/** Python's `MAX_VOLUME_ALERTS_PER_RUN`: every kind and direction an egress volume alert can have. */
export const MAX_EGRESS_VOLUME_ALERTS_PER_RUN = 8;
/** Python's `MAX_FALLBACKS_PER_RUN`: a run moves at most twice, each once per reason. */
export const MAX_MODEL_FALLBACKS_PER_RUN = 8;
/** Python's `MAX_HIDDEN_PER_RUN`: one per gateway and kind, every forbidden kind and the policy's own. */
export const MAX_SECRETS_HIDDEN_PER_RUN = 16;
const READ_PAGE = 500;

export type GatewayName = 'egress' | 'model';
/** How the egress proxy let a request through (`egress.allowed`'s `via`). */
export type EgressVia = 'http' | 'intercepted' | 'tunnel' | 'database';

/**
 * WP-63: what a run's sandbox did at an allowed website, as its activity line
 * shows it: the path (no query, tokens masked), how the site is allowed and
 * whether the request read or wrote.
 */
export interface SiteReport {
  path: string;
  access: 'read' | 'read-write' | 'host-level';
  use: 'read' | 'write';
}

/** OB-03: a destination the egress proxy let the run's sandbox reach; a bare host, and an allowed website's path. */
export interface EgressAllowed {
  host: string;
  via: EgressVia;
  site?: SiteReport;
}
export type RefusalOutcome = 'recorded' | 'duplicate' | 'capped';

/** C6.12: what an egress volume alert is about (`egress.volume_alert`'s `kind`). */
export type EgressVolumeAlertKind =
  'run_threshold' | 'org_baseline' | 'run_cap';
export type EgressVolumeDirection = 'up' | 'down' | 'total';
export type EgressAlias =
  | 'model-gateway'
  | 'tool-gateway'
  | 'tunnel'
  | 'intercepted'
  | 'database'
  | 'other'
  /** WP-63 part A: what the worker fetched for the run outside its sandboxes (repository_fetch). */
  | 'repository';

/** C6.12: a run's sandboxes moved an unusual amount through the egress proxy; counts and an alias only. */
export interface EgressVolumeAlert {
  kind: EgressVolumeAlertKind;
  direction: EgressVolumeDirection;
  bytes: number;
  thresholdBytes: number;
  baselineBytes?: number;
  alias: EgressAlias;
}

/** G-48 (WP-100): the model gateway moved an engine run's call to an alternate model its pin names. */
export interface ModelFallbackReport {
  from: { provider: string; model: string };
  to: { provider: string; model: string };
  reason: '5xx' | 'timeout' | '429_exhausted' | 'content_filter';
}

/**
 * WP-103: a gateway hid a credential shape from the run's model context (a public page's documentation example)
 * and let the call go on; the kind of shape only, never its value.
 */
export interface SecretHiddenReport {
  gateway: GatewayName;
  kind: string;
}

type GatewayEventType =
  | 'gateway.refused'
  | 'egress.allowed'
  | 'egress.volume_alert'
  | 'model.fallback'
  | 'gateway.secret_hidden';

/** What a gateway reports: ids and codes only (the route's schema bounds each). */
export interface GatewayRefusal {
  gateway: GatewayName;
  reason: string;
  status: number;
  host?: string;
  param?: string;
  site?: SiteReport;
}

const samePath = (data: Record<string, unknown>, site?: SiteReport): boolean =>
  (data['site'] as SiteReport | undefined)?.path === site?.path;
const sameUse = (data: Record<string, unknown>, site?: SiteReport): boolean =>
  (data['site'] as SiteReport | undefined)?.use === site?.use;

/**
 * A sandbox gateway's decision on a run's request, through the same audited,
 * sealed journal as every run event, so it is chained in the org's audit
 * stream (OB-03): a refusal (SEC2 O-2) as `gateway.refused`, one per run,
 * gateway, reason and host, at most MAX_GATEWAY_REFUSALS_PER_RUN; a
 * destination the egress proxy let through as `egress.allowed`, one per run,
 * host and route, at most MAX_EGRESS_ALLOWED_PER_RUN; an unusual egress volume
 * (C6.12) as `egress.volume_alert`, one per run, kind and direction, at most
 * MAX_EGRESS_VOLUME_ALERTS_PER_RUN. Two replicas reporting
 * the same decision at once may both append; the reports are deduplicated in
 * each gateway first, so that stays rare and bounded by the cap.
 */
export class GatewayRefusalJournal<Tx> {
  constructor(
    private readonly store: JournalStore<Tx>,
    private readonly journals: JournalPreparer & {
      resolve(events: JournalEvent[]): Promise<JournalEvent[]>;
    },
    private readonly transaction: <T>(
      work: (tx: Tx) => Promise<T>,
    ) => Promise<T>,
    private readonly logger: HarnessLogger = silentHarnessLogger,
  ) {}

  async record(
    run: HarnessRun,
    attemptId: string,
    refusal: GatewayRefusal,
  ): Promise<RefusalOutcome> {
    const started = performance.now();
    const prior = await this.prior(run);
    const outcome = prior.some(({ data = {} }) => {
      const { gateway, reason, host } = data;
      return (
        gateway === refusal.gateway &&
        reason === refusal.reason &&
        host === refusal.host &&
        samePath(data, refusal.site)
      );
    })
      ? 'duplicate'
      : prior.length >= MAX_GATEWAY_REFUSALS_PER_RUN
        ? 'capped'
        : await this.append(run, attemptId, 'gateway.refused', {
            ...refusal,
            attemptId,
          });
    this.logger.info(
      'gateway_refusal.journaled',
      harnessLogMeta('gateway_refusal.journaled', {
        orgId: run.orgId,
        sessionId: run.sessionId,
        runId: run.runId,
        attemptId,
        gateway: refusal.gateway,
        reason: refusal.reason,
        outcome,
        ms: elapsedMs(started),
      }),
    );
    return outcome;
  }

  /** OB-03: a destination the egress proxy let the run's sandbox reach. */
  async recordAllowed(
    run: HarnessRun,
    attemptId: string,
    allowed: EgressAllowed,
  ): Promise<RefusalOutcome> {
    const started = performance.now();
    const prior = await this.prior(run, 'egress.allowed');
    const outcome = prior.some(
      ({ data = {} }) =>
        data['host'] === allowed.host &&
        data['via'] === allowed.via &&
        samePath(data, allowed.site) &&
        sameUse(data, allowed.site),
    )
      ? 'duplicate'
      : prior.length >= MAX_EGRESS_ALLOWED_PER_RUN
        ? 'capped'
        : await this.append(run, attemptId, 'egress.allowed', {
            ...allowed,
            attemptId,
          });
    this.logger.info(
      'egress_allowed.journaled',
      harnessLogMeta('egress_allowed.journaled', {
        orgId: run.orgId,
        sessionId: run.sessionId,
        runId: run.runId,
        attemptId,
        via: allowed.via,
        outcome,
        ms: elapsedMs(started),
      }),
    );
    return outcome;
  }

  /** C6.12: the egress proxy saw an unusual volume from or to the run's sandboxes. */
  async recordVolumeAlert(
    run: HarnessRun,
    attemptId: string,
    alert: EgressVolumeAlert,
  ): Promise<RefusalOutcome> {
    const started = performance.now();
    const prior = await this.prior(run, 'egress.volume_alert');
    const outcome = prior.some(
      ({ data = {} }) =>
        data['kind'] === alert.kind && data['direction'] === alert.direction,
    )
      ? 'duplicate'
      : prior.length >= MAX_EGRESS_VOLUME_ALERTS_PER_RUN
        ? 'capped'
        : await this.append(run, attemptId, 'egress.volume_alert', {
            ...alert,
            attemptId,
          });
    this.logger.warn(
      'egress_volume_alert.journaled',
      harnessLogMeta('egress_volume_alert.journaled', {
        orgId: run.orgId,
        sessionId: run.sessionId,
        runId: run.runId,
        attemptId,
        kind: alert.kind,
        direction: alert.direction,
        alias: alert.alias,
        outcome,
        ms: elapsedMs(started),
      }),
    );
    return outcome;
  }

  /**
   * G-48 (WP-100): the model gateway moved the run's call to an alternate model, journaled as the run's
   * `model.fallback` (the kernel's own event for a loop run), so the chat says which model answered; one per route
   * moved from, to and reason.
   */
  async recordFallback(
    run: HarnessRun,
    attemptId: string,
    fallback: ModelFallbackReport,
  ): Promise<RefusalOutcome> {
    const started = performance.now();
    const prior = await this.prior(run, 'model.fallback');
    const same = (
      a: unknown,
      b: { provider: string; model: string },
    ): boolean => {
      const ref = (a ?? {}) as Record<string, unknown>;
      return ref['provider'] === b.provider && ref['model'] === b.model;
    };
    const outcome = prior.some(
      ({ data = {} }) =>
        same(data['from'], fallback.from) &&
        same(data['to'], fallback.to) &&
        data['reason'] === fallback.reason,
    )
      ? 'duplicate'
      : prior.length >= MAX_MODEL_FALLBACKS_PER_RUN
        ? 'capped'
        : await this.append(
            run,
            attemptId,
            'model.fallback',
            {
              from: { ...fallback.from },
              to: { ...fallback.to },
              reason: fallback.reason,
            },
            'owner',
          );
    this.logger.warn(
      'model_fallback.journaled',
      harnessLogMeta('model_fallback.journaled', {
        orgId: run.orgId,
        sessionId: run.sessionId,
        runId: run.runId,
        attemptId,
        reason: fallback.reason,
        outcome,
        ms: elapsedMs(started),
      }),
    );
    return outcome;
  }

  /**
   * WP-103: a gateway hid a credential shape from the run's model call and let it go on, journaled as the run's
   * `gateway.secret_hidden` (audit), one per gateway and kind.
   */
  async recordSecretHidden(
    run: HarnessRun,
    attemptId: string,
    hidden: SecretHiddenReport,
  ): Promise<RefusalOutcome> {
    const started = performance.now();
    const prior = await this.prior(run, 'gateway.secret_hidden');
    const outcome = prior.some(
      ({ data = {} }) =>
        data['gateway'] === hidden.gateway && data['kind'] === hidden.kind,
    )
      ? 'duplicate'
      : prior.length >= MAX_SECRETS_HIDDEN_PER_RUN
        ? 'capped'
        : await this.append(run, attemptId, 'gateway.secret_hidden', {
            gateway: hidden.gateway,
            kind: hidden.kind,
            attemptId,
          });
    this.logger.warn(
      'gateway_secret_hidden.journaled',
      harnessLogMeta('gateway_secret_hidden.journaled', {
        orgId: run.orgId,
        sessionId: run.sessionId,
        runId: run.runId,
        attemptId,
        gateway: hidden.gateway,
        kind: hidden.kind,
        outcome,
        ms: elapsedMs(started),
      }),
    );
    return outcome;
  }

  private async append(
    run: HarnessRun,
    attemptId: string,
    type: GatewayEventType,
    data: Record<string, unknown>,
    visibility: 'audit' | 'owner' = 'audit',
  ): Promise<'recorded'> {
    const prepared = await this.journals.prepare({
      orgId: run.orgId,
      sessionId: run.sessionId,
      streamId: journalStreamId(run),
      runId: run.runId,
      attemptId,
      writer: 'node',
      events: [
        {
          type,
          schemaVersion: 1,
          critical: false,
          visibility,
          data,
        },
      ],
    });
    await this.transaction((tx) => this.store.append(prepared, tx));
    return 'recorded';
  }

  /** The run's events of `type` (`gateway.refused` unless named), oldest first. */
  async prior(
    run: HarnessRun,
    type: GatewayEventType = 'gateway.refused',
  ): Promise<JournalEvent[]> {
    const found: JournalEvent[] = [];
    let after = 0;
    for (;;) {
      const page = await this.store.readRun(
        run.orgId,
        run.runId,
        after,
        READ_PAGE,
      );
      found.push(...page.filter((event) => event.type === type));
      if (page.length < READ_PAGE) return this.journals.resolve(found);
      after = page[page.length - 1]?.seq ?? after;
    }
  }
}
