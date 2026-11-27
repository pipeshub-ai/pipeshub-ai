/**
 * The usage telemetry plug-in: everything this build knows about collecting
 * and shipping usage data lives in this directory. The application does not
 * import it; it announces platform events and this plug-in turns them into the
 * metrics and events the collector receives.
 */
import { metricsBackend } from './metrics-backend';
import { recordAuthAttempt } from './modules/auth-metrics';
import type { RequestHandler } from 'express';
import type { PlatformPlugin, PluginContext } from '../../libs/plugins/types';
import type { McpCaller } from '../../libs/platform-events/events';
import { loadConfigurationManagerConfig } from '../../modules/configuration_manager/config/config';
import { KeyValueStoreService } from '../../libs/services/keyValueStore.service';
import { Logger } from '../../libs/services/logger.service';
import { recordEvent } from './event-buffer';
import { isTelemetryEnabled, TELEMETRY_COLLECTION_FLAG } from './flag';
import { clientTags, domainFromEmail, normalizeOrgId } from './identity';
import { startOrgMetricsRefresh } from './metrics.refresh.service';
import { recordServiceActivity } from './modules/activity-metrics';
import { recordClientRequest } from './modules/client-metrics';
import { setMetricCollectionEnabled } from './modules/collection-metrics';
import { dbMetrics } from './modules/db-metrics';
import { docstoreMetrics } from './modules/docstore-metrics';
import { messagingMetrics } from './modules/messaging-metrics';
import { metricsMiddleware } from './telemetry.middleware';
import { startTelemetry, TelemetryService } from './telemetry.service';

const logger = Logger.getInstance({ service: 'Telemetry' });

/** Event props every MCP event carries, named as the collector expects. */
function mcpProps(caller: McpCaller): Record<string, unknown> {
  return {
    orgId: caller.orgId,
    userId: caller.userId,
    domain: caller.emailDomain,
    auth_type: caller.authType,
    ...(caller.oauthClientId !== undefined
      ? { oauth_client_id: caller.oauthClientId }
      : {}),
    ...clientTags(caller),
  };
}

function subscribeProductEvents(ctx: PluginContext): void {
  ctx.onEvent('auth.attempted', (e) => {
    recordAuthAttempt(e.method, e.outcome);
  });
  ctx.onEvent('user.signed_in', (e) => {
    recordEvent('login', {
      orgId: e.orgId,
      userId: e.userId,
      email: e.email,
      domain: domainFromEmail(e.email),
      first_login: e.firstSignIn,
      auth_method: e.authMethod,
    });
  });
  ctx.onEvent('user.account_setup.completed', (e) => {
    recordEvent('signup', {
      orgId: e.orgId,
      userId: e.userId,
      email: e.email,
      domain: domainFromEmail(e.email),
    });
  });
  ctx.onEvent('org.created', (e) => {
    recordEvent('org_created', {
      orgId: e.orgId,
      userId: e.adminUserId,
      email: e.contactEmail,
      domain: domainFromEmail(e.contactEmail),
      fullName: e.adminFullName,
      channel: e.signupChannel,
      ...(e.signupReferrerDomain !== undefined
        ? { referrer_domain: e.signupReferrerDomain }
        : {}),
    });
  });
  ctx.onEvent('org.data_collection_setting.changed', (e) => {
    setMetricCollectionEnabled(normalizeOrgId(e.orgId), e.enabled);
    if (!e.enabled) {
      // Ships the opt-out itself: once the setting is stored the push loop stops.
      return TelemetryService.current()?.flush();
    }
    return undefined;
  });
  ctx.onEvent('http.request.completed', recordClientRequest);
  ctx.onEvent('personal_access_token.created', (e) => {
    recordEvent('pat_created', {
      orgId: e.orgId,
      userId: e.userId,
      domain: e.emailDomain,
      scope_count: e.scopeCount,
      expiry_days: e.expiryDays,
      ...clientTags(e),
    });
    recordServiceActivity('pat_created', {
      org: e.orgId,
      domain: e.emailDomain,
    });
  });
  ctx.onEvent('personal_access_token.knowledge_limit.changed', (e) => {
    recordEvent('pat_access_changed', {
      orgId: e.orgId,
      userId: e.userId,
      domain: e.emailDomain,
      limited: e.limited,
      item_count: e.itemCount,
      ...clientTags(e),
    });
  });
  ctx.onEvent('oauth.token.issued', (e) => {
    recordEvent('oauth_token_issued', {
      grant_type: e.grantType,
      client_id: e.clientId,
      ...clientTags(e),
    });
  });
  ctx.onEvent('mcp.client.connected', (e) => {
    recordEvent('mcp_connected', {
      ...mcpProps(e),
      client_name: e.clientName,
      client_version: e.clientVersion,
    });
    recordServiceActivity('mcp_connected', {
      org: e.orgId,
      domain: e.emailDomain,
    });
  });
  ctx.onEvent('mcp.tool.called', (e) => {
    recordEvent('mcp_tool_called', { ...mcpProps(e), tool: e.tool });
    recordServiceActivity('mcp_tool_called', {
      org: e.orgId,
      domain: e.emailDomain,
    });
  });
}

function subscribeStoreEvents(ctx: PluginContext): void {
  ctx.onEvent('db.query.completed', (e) => {
    dbMetrics.query.observe(
      { service: e.service, site: e.site, class: e.access },
      e.durationSeconds,
    );
  });
  ctx.onEvent('db.unit_of_work.completed', (e) => {
    dbMetrics.uow.observe(
      { service: e.service, outcome: e.outcome },
      e.durationSeconds,
    );
  });
  ctx.onEvent('db.unit_of_work.retried', (e) => {
    dbMetrics.retries.inc({ code: e.cause });
  });
  ctx.onEvent('db.unit_of_work.refused', () => {
    dbMetrics.orgContextMissing.inc();
  });
  ctx.onEvent('db.connection.acquired', (e) => {
    dbMetrics.poolWait.observe({}, e.waitSeconds);
  });
  ctx.onEvent('db.connection.session_role_leaked', () => {
    dbMetrics.roleLeak.inc();
  });

  ctx.onEvent('docstore.write.refused', (e) => {
    docstoreMetrics.fenceRefused.inc({ store: e.store, reason: e.reason });
  });
  ctx.onEvent('docstore.fence.checked', (e) => {
    docstoreMetrics.fenceCheck.observe({}, e.durationSeconds);
  });
  ctx.onEvent('docstore.row_policy.violated', () => {
    docstoreMetrics.rlsViolation.inc();
  });
  ctx.onEvent('docstore.transaction.failed', () => {
    docstoreMetrics.failedTransaction.inc();
  });
  ctx.onEvent('docstore.nul_characters.stripped', (e) => {
    docstoreMetrics.nulStripped.inc({ repo: e.repo, field: e.field }, e.count);
  });
  ctx.onEvent('docstore.shadow_read.compared', (e) => {
    docstoreMetrics.shadowReads.inc({ repo: e.repo, result: e.result });
  });
  ctx.onEvent('docstore.ttl.swept', (e) => {
    docstoreMetrics.ttlSwept.inc({ target: e.target }, e.removed);
  });
  ctx.onEvent('docstore.ttl.sweep_failed', (e) => {
    docstoreMetrics.ttlSweepFailed.inc({ target: e.target });
  });

  ctx.onEvent('consumer.inbox.failed', (e) => {
    messagingMetrics.inboxErrors.inc({ op: e.operation });
  });
  ctx.onEvent('consumer.delivery.skipped', (e) => {
    messagingMetrics.inboxSkipped.inc({ consumer: e.consumer });
  });
  ctx.onEvent('notification.event.refused', (e) => {
    messagingMetrics.notificationEventRefused.inc({ reason: e.reason });
  });
}

const telemetryPlugin: PlatformPlugin = {
  name: 'telemetry',
  register(ctx) {
    const active = isTelemetryEnabled();
    ctx.provideCapability('data-collection', { isActive: () => active });
    // The operational registry (agent harness, control plane), whatever the collection flag says, as before: its
    // backend is the one the flag picks.
    ctx.provideCapability('metrics', metricsBackend);
    if (!active) {
      logger.info(
        `Telemetry disabled (${TELEMETRY_COLLECTION_FLAG} is not 'true')`,
      );
      return;
    }

    ctx.useHttpMiddleware(metricsMiddleware() as RequestHandler);
    subscribeProductEvents(ctx);
    subscribeStoreEvents(ctx);

    let refreshTimer: NodeJS.Timeout | undefined;
    ctx.onStart(() => {
      startTelemetry(
        KeyValueStoreService.getInstance(loadConfigurationManagerConfig()),
      );
      refreshTimer = startOrgMetricsRefresh(logger);
    });
    ctx.onShutdown(async () => {
      if (refreshTimer !== undefined) clearInterval(refreshTimer);
      await TelemetryService.current()?.shutdown();
    });
  },
};

export default telemetryPlugin;
