/**
 * WP-63: allowed websites reach the worker in the org's live layer, by who
 * set them. The deployment's cap comes from `HARNESS_ALLOWED_WEBSITES_CAP`.
 */
import { expect } from 'chai';
import {
  liveLayerOf,
  liveLayerSchema,
  type PolicyLayer,
  type WebsitePolicy,
} from '../../../src/modules/control-plane';
import { deploymentTunnels, deploymentWebsiteCap } from '../../../src/modules/agent-harness/domain/website-settings';
import { ORG_A } from './support/cp-objects';
import { aLayer } from './support/resolution-fixtures';

describe('WP-63: allowed websites in the live layer', () => {
  it('C11.04: the deployment’s, the org’s and each agent’s sections travel apart, valid against the contract', () => {
    const org: WebsitePolicy = { sites: [{ host: 'github.com', access: 'read' }] };
    const agent: WebsitePolicy = { sites: [] };
    const cap: WebsitePolicy = { sites: [{ host: '*.example.com', access: 'read' }] };
    const layer = liveLayerOf({
      orgId: ORG_A,
      revision: 3,
      killSwitch: false,
      websitesCap: cap,
      layers: new Map<string, PolicyLayer>([
        [`org:${ORG_A}`, aLayer('org', { websites: org }, ORG_A)],
        ['agent:a7', aLayer('agent', { websites: agent }, 'a7')],
        ['agent:b2', aLayer('agent', { budgets: { maxCostUsd: 1 } }, 'b2')],
      ]),
    });
    expect(layer.websites).to.deep.equal({ deployment: cap, org, agents: { a7: agent } });
    expect(liveLayerSchema.safeParse(layer).success).to.equal(true);
    expect(layer.layers.some((scoped) => 'websites' in scoped.layer)).to.equal(false);
  });

  it('C11.04: nothing set anywhere: no websites or repositories member, so runs reach no site and clone from the defaults', () => {
    const layer = liveLayerOf({ orgId: ORG_A, revision: 1, killSwitch: false, layers: new Map() });
    expect(layer).to.not.have.property('websites');
    expect(layer).to.not.have.property('repositories');
  });

  it('HARNESS_ALLOWED_WEBSITES_CAP: read by default, read-write and host-level when named, none for nothing', () => {
    expect(deploymentWebsiteCap({})).to.equal(null);
    expect(deploymentWebsiteCap({ HARNESS_ALLOWED_WEBSITES_CAP: 'none' })).to.deep.equal({ sites: [] });
    expect(
      deploymentWebsiteCap({ HARNESS_ALLOWED_WEBSITES_CAP: 'github.com, *.example.com=read-write,mirror.example.org=host-level' }),
    ).to.deep.equal({
      sites: [
        { host: 'github.com', access: 'read' },
        { host: '*.example.com', access: 'read-write', readWriteConfirmed: true },
        { host: 'mirror.example.org', access: 'read-write', readWriteConfirmed: true, opaque: true },
      ],
    });
    expect(() => deploymentWebsiteCap({ HARNESS_ALLOWED_WEBSITES_CAP: 'https://github.com' })).to.throw(/not a host name/);
    expect(() => deploymentWebsiteCap({ HARNESS_ALLOWED_WEBSITES_CAP: 'github.com=write' })).to.throw(/access is/);
  });

  it('EGRESS_CONNECT_TUNNELS: off unless the operator turns it on', () => {
    expect(deploymentTunnels({})).to.equal(false);
    expect(deploymentTunnels({ EGRESS_CONNECT_TUNNELS: 'true' })).to.equal(true);
  });
});
