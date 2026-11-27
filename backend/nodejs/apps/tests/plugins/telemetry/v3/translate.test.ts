import { expect } from 'chai';
import { DeltaTracker } from '../../../../src/plugins/telemetry/v3/delta-tracker';
import {
  EVENTS,
  METRICS,
  MetricSample,
  translateEvent,
  translateMetrics,
} from '../../../../src/plugins/telemetry/v3/translate';

const sample = (
  name: string,
  type: 'counter' | 'gauge',
  labels: Record<string, string>,
  value: number,
): MetricSample => ({ name, type, labels, value });

describe('usage telemetry v3: translation', () => {
  describe('metrics', () => {
    it('adds a metric up over the organisation and domain it leaves out', () => {
      const out = translateMetrics([
        sample(
          'pipeshub_usage_sdk_requests_total',
          'counter',
          {
            service: 'node_api',
            channel: 'sdk',
            sdk_language: 'python',
            sdk_version: '1.2.3',
            outcome: 'success',
            org: 'org-1',
            domain: 'acme.com',
          },
          3,
        ),
        sample(
          'pipeshub_usage_sdk_requests_total',
          'counter',
          {
            service: 'node_api',
            channel: 'sdk',
            sdk_language: 'python',
            sdk_version: '1.2.3',
            outcome: 'success',
            org: 'org-2',
            domain: 'example.org',
          },
          4,
        ),
        sample(
          'pipeshub_usage_sdk_requests_total',
          'counter',
          {
            service: 'node_api',
            channel: 'mcp',
            sdk_language: 'other',
            sdk_version: '0.4.1',
            outcome: 'failure',
            org: 'org-2',
            domain: 'example.org',
          },
          1,
        ),
      ]);
      expect(out).to.deep.equal([
        {
          name: 'pipeshub.usage.sdk.requests',
          type: 'counter',
          labels: {
            channel: 'sdk',
            sdk_language: 'python',
            sdk_version: '1.2.3',
            outcome: 'success',
          },
          value: 7,
        },
        {
          name: 'pipeshub.usage.sdk.requests',
          type: 'counter',
          labels: {
            channel: 'mcp',
            sdk_language: 'other',
            sdk_version: '0.4.1',
            outcome: 'failure',
          },
          value: 1,
        },
      ]);
    });

    it('renames labels to the contract and reports users as one total', () => {
      const out = translateMetrics([
        sample(
          'pipeshub_org_users',
          'gauge',
          { org: 'org-1', domain: 'acme.com' },
          5,
        ),
        sample(
          'pipeshub_org_users',
          'gauge',
          { org: 'org-2', domain: 'example.org' },
          7,
        ),
        sample(
          'pipeshub_org_auth_methods',
          'gauge',
          { org: 'org-1', method: 'azureAd' },
          1,
        ),
        sample(
          'pipeshub_org_auth_methods',
          'gauge',
          { org: 'org-2', method: 'azureAd' },
          1,
        ),
        sample(
          'pipeshub_org_auth_methods',
          'gauge',
          { org: 'org-2', method: 'password' },
          1,
        ),
        sample(
          'pipeshub_orgs_total',
          'gauge',
          { account_type: 'business', domain: 'acme.com' },
          1,
        ),
        sample(
          'pipeshub_connector_active',
          'gauge',
          { connector: 'Google Drive', domain: 'acme.com' },
          2,
        ),
        sample(
          'pipeshub_usage_records',
          'gauge',
          {
            connector_type: 'DRIVE',
            indexing_status: 'COMPLETED',
            org: 'org-1',
            domain: 'acme.com',
          },
          40,
        ),
        sample(
          'pipeshub_usage_records',
          'gauge',
          {
            connector_type: 'DRIVE',
            indexing_status: 'COMPLETED',
            org: 'other',
            domain: 'other',
          },
          2,
        ),
        sample(
          'pipeshub_activity_total',
          'counter',
          {
            service: 'node_api',
            activity: 'pat_created',
            connector: 'none',
            status: 'ok',
            org: 'org-1',
            kb: 'none',
            domain: 'acme.com',
            mimetype: 'none',
          },
          3,
        ),
        sample(
          'pipeshub_install_info',
          'gauge',
          {
            graph_db: 'neo4j',
            vector_db: 'qdrant',
            message_broker: 'kafka',
            kv_store: 'redis',
          },
          1,
        ),
      ]);
      const byName = Object.fromEntries(
        out.map((s) => [`${s.name} ${JSON.stringify(s.labels)}`, s.value]),
      );
      expect(byName).to.deep.equal({
        'pipeshub.usage.users {"user_state":"total"}': 12,
        'pipeshub.usage.auth_methods {"auth_method":"azure_ad"}': 2,
        'pipeshub.usage.auth_methods {"auth_method":"password"}': 1,
        'pipeshub.usage.orgs {"account_type":"business"}': 1,
        'pipeshub.usage.connectors.active {"connector_type":"google_drive"}': 2,
        'pipeshub.usage.records {"connector_type":"drive","indexing_status":"completed"}': 42,
        'pipeshub.usage.activity {"activity":"pat_created","status":"ok","connector_type":"none"}': 3,
        'pipeshub.usage.install.info {"graph_db":"neo4j","vector_db":"qdrant","message_broker":"kafka","kv_store":"redis"}': 1,
      });
    });

    it('leaves out every metric the contract does not name', () => {
      const out = translateMetrics([
        sample(
          'pipeshub_http_requests_total',
          'counter',
          {
            service: 'node_api',
            route: '/x',
            method: 'GET',
            status: '200',
            org: 'org-1',
            domain: 'acme.com',
          },
          9,
        ),
        sample(
          'pipeshub_metric_collection_enabled',
          'gauge',
          { service: 'node_api', org: 'org-1' },
          1,
        ),
        sample('pipeshub_db_retries_total', 'counter', { code: '40001' }, 2),
        sample('docstore_ttl_swept_total', 'counter', { target: 'x' }, 2),
        // The right name with the wrong type is not the contract's metric either.
        sample(
          'pipeshub_orgs_total',
          'counter',
          { account_type: 'business' },
          1,
        ),
        sample('pipeshub_org_users', 'gauge', { org: 'org-1' }, Number.NaN),
      ]);
      expect(out).to.deep.equal([]);
    });

    it('never puts an organisation, a domain or a service in a v3 label', () => {
      const banned = [
        'org',
        'orgId',
        'domain',
        'service',
        'kb',
        'mimetype',
        'userId',
        'email',
      ];
      for (const rule of Object.values(METRICS)) {
        const labels = [
          ...Object.values(rule.labels),
          ...Object.keys(rule.constant ?? {}),
        ];
        expect(
          labels.filter((l) => banned.includes(l)),
          rule.to,
        ).to.deep.equal([]);
      }
    });
  });

  describe('events', () => {
    it('keeps what happened and drops who did it', () => {
      const out = translateEvent({
        event: 'login',
        timestamp: '2026-11-25T10:00:00.000Z',
        props: {
          orgId: 'org-1',
          userId: 'user-1',
          email: 'jane@acme.com',
          domain: 'acme.com',
          first_login: true,
          auth_method: 'samlSso',
        },
      });
      expect(out).to.deep.equal({
        name: 'user.logged_in',
        ts: '2026-11-25T10:00:00.000Z',
        attributes: { auth_method: 'saml', first_login: true },
      });
    });

    it('translates every event the contract has, with attributes of the right type', () => {
      const t = (event: string, props: Record<string, unknown>) =>
        translateEvent({ event, timestamp: 't', props });
      const who = {
        orgId: 'org-1',
        userId: 'user-1',
        email: 'jane@acme.com',
        domain: 'acme.com',
        fullName: 'Jane Doe',
      };
      expect(t('signup', who)).to.deep.equal({
        name: 'user.signed_up',
        ts: 't',
        attributes: {},
      });
      expect(
        t('org_created', {
          ...who,
          channel: 'web',
          referrer_domain: 'news.example',
        }),
      ).to.deep.equal({ name: 'org.created', ts: 't', attributes: {} });
      expect(
        t('connector_added', { ...who, connector: 'Slack', scope: 'team' })
          ?.attributes,
      ).to.deep.equal({ connector_type: 'slack', connector_scope: 'team' });
      expect(
        t('search_performed', {
          ...who,
          status_code: 200,
          num_queries: '2',
          search_type: 'search',
        })?.attributes,
      ).to.deep.equal({
        search_type: 'search',
        status_code: 200,
        num_queries: 2,
      });
      expect(
        t('chat_session_started', {
          ...who,
          mode: 'quick',
          search_type: 'web_search',
          streaming: true,
        })?.attributes,
      ).to.deep.equal({ mode: 'quick', search_type: 'web_search' });
      expect(
        t('agent_run', { ...who, has_tools: 'true', streaming: false })
          ?.attributes,
      ).to.deep.equal({ has_tools: true, streaming: false });
      expect(
        t('sdk.client.seen', {
          orgId: 'org-1',
          domain: 'acme.com',
          channel: 'mcp',
          mcp_server_version: '0.4.1',
        }),
      ).to.deep.equal({
        name: 'sdk.client.seen',
        ts: 't',
        attributes: { channel: 'mcp', mcp_server_version: '0.4.1' },
      });
      expect(
        t('mcp_connected', {
          orgId: 'org-1',
          userId: 'user-1',
          domain: 'acme.com',
          auth_type: 'oauth',
          oauth_client_id: 'c1',
          transport: 'http',
          client_name: 'claude',
          client_version: '1.2',
        }),
      ).to.deep.equal({
        name: 'mcp.client.connected',
        ts: 't',
        attributes: { mcp_client_name: 'claude', mcp_client_version: '1.2' },
      });
    });

    it('does not send an event the contract does not have', () => {
      for (const event of [
        'pat_created',
        'pat_access_changed',
        'oauth_token_issued',
        'mcp_tool_called',
        'answer_generated',
        'retrieve_performed',
        'opt_out',
      ]) {
        expect(
          translateEvent({ event, timestamp: 't', props: { orgId: 'org-1' } }),
          event,
        ).to.equal(undefined);
      }
    });

    it('skips an attribute that is missing or of the wrong type', () => {
      const out = translateEvent({
        event: 'search_performed',
        timestamp: 't',
        props: {
          status_code: 'two hundred',
          num_queries: 1.5,
          search_type: null,
        },
      });
      expect(out?.attributes).to.deep.equal({});
    });

    it('has no rule that carries a personal prop', () => {
      const personal = [
        'orgId',
        'userId',
        'email',
        'domain',
        'fullName',
        'user_agent',
        'oauth_client_id',
        'client_id',
        'referrer_domain',
      ];
      for (const rule of Object.values(EVENTS)) {
        expect(
          Object.keys(rule.attributes).filter((p) => personal.includes(p)),
          rule.to,
        ).to.deep.equal([]);
      }
    });
  });
});

describe('usage telemetry v3: counter deltas', () => {
  const counter = (value: number, outcome = 'success') => ({
    name: 'pipeshub.usage.sdk.requests',
    type: 'counter' as const,
    labels: { outcome },
    value,
  });
  const gauge = (value: number) => ({
    name: 'pipeshub.usage.orgs',
    type: 'gauge' as const,
    labels: {},
    value,
  });

  it('reports what was counted since the last cut, and gauges as they are', () => {
    const tracker = new DeltaTracker();
    expect(tracker.cut([counter(5), gauge(2)])).to.deep.equal([
      counter(5),
      gauge(2),
    ]);
    expect(tracker.cut([counter(8), gauge(2)])).to.deep.equal([
      counter(3),
      gauge(2),
    ]);
  });

  it('leaves out a counter that did not move', () => {
    const tracker = new DeltaTracker();
    tracker.cut([counter(5), counter(1, 'failure')]);
    expect(tracker.cut([counter(5), counter(2, 'failure')])).to.deep.equal([
      counter(1, 'failure'),
    ]);
    expect(tracker.cut([counter(5), counter(2, 'failure')])).to.deep.equal([]);
  });

  it('counts from zero again when the registry was reset', () => {
    const tracker = new DeltaTracker();
    tracker.cut([counter(50)]);
    expect(tracker.cut([counter(4)])).to.deep.equal([counter(4)]);
    // A series that is gone and comes back also starts from zero.
    tracker.cut([]);
    expect(tracker.cut([counter(6)])).to.deep.equal([counter(6)]);
  });

  it('moves the baseline without a batch when the counts went another way', () => {
    const tracker = new DeltaTracker();
    tracker.rebase([counter(100), gauge(3)]);
    expect(tracker.cut([counter(104), gauge(3)])).to.deep.equal([
      counter(4),
      gauge(3),
    ]);
  });
});
