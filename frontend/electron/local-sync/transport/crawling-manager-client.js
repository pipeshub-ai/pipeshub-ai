const { buildCronFromSchedule } = require('../cron-from-schedule');

function trimTrailingSlash(value) {
  return String(value || '').replace(/\/$/, '');
}

/**
 * Register a repeating BullMQ job with the crawling manager so the backend
 * triggers scheduled Local FS resyncs. Mirrors CLI BackendClient.scheduleCrawlingManagerJob.
 */
async function scheduleCrawlingManagerJob({
  apiBaseUrl,
  accessToken,
  connectorDisplayType,
  connectorInstanceId,
  intervalMinutes,
  startTime,
  timezone,
}) {
  const base = trimTrailingSlash(apiBaseUrl);
  if (!base) throw new Error('apiBaseUrl required');
  if (!accessToken) throw new Error('accessToken required');
  if (!connectorDisplayType) throw new Error('connectorDisplayType required');
  if (!connectorInstanceId) throw new Error('connectorInstanceId required');
  if (!intervalMinutes) throw new Error('intervalMinutes required');

  const connSeg = encodeURIComponent(String(connectorDisplayType).trim().toLowerCase());
  const idSeg = encodeURIComponent(connectorInstanceId);
  const tz = String(timezone || 'UTC').trim().toUpperCase();
  const cron = buildCronFromSchedule({ startTime, intervalMinutes, timezone: tz });

  const url = `${base}/api/v1/crawlingManager/${connSeg}/${idSeg}/schedule`;
  const body = {
    scheduleConfig: {
      scheduleType: 'custom',
      isEnabled: true,
      timezone: tz,
      cronExpression: cron,
    },
    priority: 5,
    maxRetries: 3,
    timeout: 300000,
  };

  const resp = await fetch(url, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${accessToken}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });
  let parsed = null;
  try { parsed = await resp.json(); } catch { /* ignore */ }
  if (!resp.ok) {
    throw new Error(`crawlingManager schedule failed (${resp.status}): ${JSON.stringify(parsed)}`);
  }
  return { cron, response: parsed };
}

/** Tear down the scheduled job. */
async function unscheduleCrawlingManagerJob({
  apiBaseUrl,
  accessToken,
  connectorDisplayType,
  connectorInstanceId,
}) {
  const base = trimTrailingSlash(apiBaseUrl);
  if (!base || !accessToken || !connectorDisplayType || !connectorInstanceId) return null;
  const connSeg = encodeURIComponent(String(connectorDisplayType).trim().toLowerCase());
  const idSeg = encodeURIComponent(connectorInstanceId);
  const url = `${base}/api/v1/crawlingManager/${connSeg}/${idSeg}/schedule`;
  try {
    const resp = await fetch(url, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${accessToken}` },
    });
    return { ok: resp.ok, status: resp.status };
  } catch {
    return null;
  }
}

module.exports = { scheduleCrawlingManagerJob, unscheduleCrawlingManagerJob };
