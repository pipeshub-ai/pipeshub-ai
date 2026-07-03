import { expect } from 'chai';
import { recordActivity } from '../../../../../src/libs/services/telemetry/modules/activity-metrics';
import { metricsBackend } from '../../../../../src/libs/services/telemetry/metrics-backend';

describe('telemetry modules/activity-metrics', () => {
  it('should count an activity with full identity labels', async () => {
    recordActivity(
      'ORG_CREATED',
      'user-1',
      'org-1',
      'admin@acme.io',
      'Admin User',
      'req-123',
      '{"path":"/api/v1/org"}',
    );

    const text = await metricsBackend.serialize();
    expect(text).to.match(
      /app_activity_total\{activity="ORG_CREATED",userId="user-1",orgId="org-1",email="admin@acme\.io",fullName="Admin User",requestId="req-123",reqContext="\{\\"path\\":\\"\/api\/v1\/org\\"\}"\} 1/,
    );
  });

  it('should map missing user/org to "anonymous" and missing email/name/requestId to "unknown"', async () => {
    recordActivity('LOGIN_ATTEMPT');

    const text = await metricsBackend.serialize();
    expect(text).to.include(
      'app_activity_total{activity="LOGIN_ATTEMPT",userId="anonymous",orgId="anonymous",email="unknown",fullName="unknown",requestId="unknown",reqContext=""} 1',
    );
  });

  it('should treat empty strings the same as missing values', async () => {
    recordActivity('EMPTY_CASE', '', '', '', '', '');

    const text = await metricsBackend.serialize();
    expect(text).to.include(
      'app_activity_total{activity="EMPTY_CASE",userId="anonymous",orgId="anonymous",email="unknown",fullName="unknown",requestId="unknown",reqContext=""} 1',
    );
  });
});
