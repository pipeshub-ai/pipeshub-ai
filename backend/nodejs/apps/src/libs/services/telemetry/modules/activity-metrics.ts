import { metricsBackend } from '../metrics-backend';

const activityCounter = metricsBackend.createCounter({
  name: 'app_activity_total',
  help: 'Total number of domain activities recorded',
  labelNames: [
    'activity',
    'userId',
    'orgId',
    'email',
    'fullName',
    'requestId',
    'reqContext',
  ],
});

export function recordActivity(
  activity: string,
  userId?: string,
  orgId?: string,
  email?: string,
  fullName?: string,
  requestId?: string,
  reqContext?: string,
): void {
  activityCounter.inc({
    activity,
    userId: userId == null || userId === '' ? 'anonymous' : userId,
    orgId: orgId == null || orgId === '' ? 'anonymous' : orgId,
    email: email == null || email === '' ? 'unknown' : email,
    fullName: fullName == null || fullName === '' ? 'unknown' : fullName,
    requestId: requestId == null || requestId === '' ? 'unknown' : requestId,
    reqContext: reqContext ?? '',
  });
}
