/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import { FakePool } from '../db/fake-pool'
import { PgRuntime, configurePg } from '../../../src/libs/db'
import { PgOrgMetricsJobRepository } from '../../../src/libs/repositories/org-metrics-job/pg'

describe('PgOrgMetricsJobRepository', () => {
  const rows: Record<string, any[]> = {
    gauge_user_counts: [{ org_id: 'a', n: '3' }],
    gauge_live_orgs: [{ org_id: 'a', account_type: 'business', domain: 'x.test' }],
    gauge_auth_configs: [{ org_id: 'a', auth_steps: [{ order: 1 }] }],
  }
  let pool: FakePool

  beforeEach(() => {
    process.env.PG_JOBS_URL = 'in-test'
    pool = new FakePool((text) => ({ rows: rows[/identity\.(gauge_\w+)\(\)/.exec(text)?.[1] ?? ''] }))
    configurePg(new PgRuntime({ pool: new FakePool(), jobsPool: pool, defaultService: 'jobs', sleep: async () => {} }))
  })
  afterEach(() => {
    delete process.env.PG_JOBS_URL
    configurePg(undefined)
  })

  const sent = () => pool.calls.map((c) => c.text).filter((t) => /gauge_/.test(t))

  it('reads the gauges through the functions and maps the rows', async () => {
    const repo = new PgOrgMetricsJobRepository()
    expect(await repo.countUsersByOrg()).to.deep.equal([{ _id: 'a', count: 3 }])
    expect(await repo.listLiveOrgs()).to.deep.equal([{ _id: 'a', accountType: 'business', domain: 'x.test' }])
    expect(await repo.listLiveAuthConfigs()).to.deep.equal([{ orgId: 'a', authSteps: [{ order: 1 }] }])
    expect(sent().map((t) => /identity\.(gauge_\w+)\(\)/.exec(t)?.[1])).to.deep.equal(Object.keys(rows))
    expect(sent().join('\n')).to.not.match(/identity\.(users|orgs|org_auth_config)\b/)
  })
})
