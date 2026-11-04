import { expect } from 'chai'
import { DocStoreNotReady, docStore, mongoFallbacks, pickStore } from '../../../src/libs/repositories/store-select'
import { createUnitOfWork } from '../../../src/libs/repositories/doc-store'
import { MongoUnitOfWork } from '../../../src/libs/repositories/mongo'
import { PgUnitOfWork } from '../../../src/libs/repositories/pg'
import { setUserRepository } from '../../../src/libs/repositories/user'
import { setGroupRepository } from '../../../src/libs/repositories/group'
import { setOrgRepository } from '../../../src/libs/repositories/org'
import { setIdentityPort } from '../../../src/libs/repositories/identity'
import { setActivityRepository } from '../../../src/libs/repositories/activity'
import { setOrgAuthConfigRepository } from '../../../src/libs/repositories/org-auth-config'
import { setPictureRepository } from '../../../src/libs/repositories/picture'
import { setResetLinkRepository } from '../../../src/libs/repositories/reset-link'
import { setOrgMetricsJobRepository } from '../../../src/libs/repositories/org-metrics-job'
import { setDelegationRepository } from '../../../src/libs/repositories/delegation'
import { setConversationRepository } from '../../../src/libs/repositories/conversation'
import { setSemanticSearchRepository } from '../../../src/libs/repositories/semantic-search'
import { setConnectorsConfigRepository } from '../../../src/libs/repositories/connectors-config'
import { setDocumentRepository } from '../../../src/libs/repositories/document'
import { setIdentityLookup } from '../../../src/libs/repositories/identity-lookup'
import { setMailLogRepository } from '../../../src/libs/repositories/mail-log'
import { setNotificationRepository } from '../../../src/libs/repositories/notification'
import { setOAuthAppRepository } from '../../../src/libs/repositories/oauth-app'
import { setOAuthTokenRepository } from '../../../src/libs/repositories/oauth-token'
import { setOutboxRepository } from '../../../src/libs/repositories/outbox'
import { setProjectRepository } from '../../../src/libs/repositories/project'
import { setSlackThreadRepository } from '../../../src/libs/repositories/slack-thread'

/** Booting under postgres builds (and caches) every repository: the rest of the run must get the Mongo ones back. */
const forgetRepositories = (): void => {
  setUserRepository()
  setGroupRepository()
  setOrgRepository()
  setIdentityPort()
  setActivityRepository()
  setOrgAuthConfigRepository()
  setPictureRepository()
  setResetLinkRepository()
  setOrgMetricsJobRepository()
  setDelegationRepository()
  setConversationRepository()
  setSemanticSearchRepository()
  setConnectorsConfigRepository()
  setDocumentRepository()
  setIdentityLookup()
  setMailLogRepository()
  setNotificationRepository()
  setOAuthAppRepository()
  setOAuthTokenRepository()
  setOutboxRepository()
  setProjectRepository()
  setSlackThreadRepository()
}

describe('DOC_STORE switch', () => {
  const saved = process.env.DOC_STORE
  afterEach(() => {
    if (saved === undefined) delete process.env.DOC_STORE
    else process.env.DOC_STORE = saved
    forgetRepositories()
  })

  it('defaults to mongo and normalises case and spaces', () => {
    delete process.env.DOC_STORE
    expect(docStore()).to.equal('mongo')
    process.env.DOC_STORE = ' Postgres '
    expect(docStore()).to.equal('postgres')
  })

  it('refuses a value it does not know; shadow is Mongo with comparison reads', () => {
    process.env.DOC_STORE = 'both'
    expect(() => docStore()).to.throw(/DOC_STORE/)
    process.env.DOC_STORE = 'shadow'
    expect(docStore()).to.equal('mongo')
  })

  it('picks the Mongo implementation by default and the PostgreSQL one when it exists', () => {
    delete process.env.DOC_STORE
    expect(pickStore('x', { mongo: () => 'm', postgres: () => 'p' })).to.equal('m')
    process.env.DOC_STORE = 'postgres'
    expect(pickStore('x', { mongo: () => 'm', postgres: () => 'p' })).to.equal('p')
  })

  it('refuses to start a repository that has no PostgreSQL implementation yet, rather than reading Mongo', () => {
    process.env.DOC_STORE = 'postgres'
    expect(() => pickStore('user', { mongo: () => 'm' })).to.throw(DocStoreNotReady, /user repository/)
  })

  describe('DOC_STORE_MONGO_REPOS (testing aid while repositories are ported)', () => {
    const savedList = process.env.DOC_STORE_MONGO_REPOS
    afterEach(() => {
      if (savedList === undefined) delete process.env.DOC_STORE_MONGO_REPOS
      else process.env.DOC_STORE_MONGO_REPOS = savedList
      mongoFallbacks.clear()
    })

    it('keeps the named repositories on Mongo, others on their PostgreSQL implementation', () => {
      process.env.DOC_STORE = 'postgres'
      process.env.DOC_STORE_MONGO_REPOS = 'user, conversation'
      expect(pickStore('conversation', { mongo: () => 'm', postgres: () => 'p' })).to.equal('m')
      expect(pickStore('user', { mongo: () => 'm' })).to.equal('m')
      expect(pickStore('notification', { mongo: () => 'm', postgres: () => 'p' })).to.equal('p')
      expect(() => pickStore('org', { mongo: () => 'm' })).to.throw(DocStoreNotReady)
      expect([...mongoFallbacks].sort()).to.deep.equal(['conversation', 'user'])
    })

    it('"*" means every repository that has no PostgreSQL implementation, and only those', () => {
      process.env.DOC_STORE = 'postgres'
      process.env.DOC_STORE_MONGO_REPOS = '*'
      expect(pickStore('org', { mongo: () => 'm' })).to.equal('m')
      expect(pickStore('conversation', { mongo: () => 'm', postgres: () => 'p' })).to.equal('p')
      expect([...mongoFallbacks]).to.deep.equal(['org'])
    })

    it('boot waits for the schema of each repository that is not kept on Mongo', () => {
      const { schemaRequirements } = require('../../../src/libs/repositories/doc-store') as typeof import('../../../src/libs/repositories/doc-store')
      delete process.env.DOC_STORE_MONGO_REPOS
      const all = ['content', 'conversation', 'identity', 'integrations', 'oauth', 'platform', 'platform_data']
      expect(Object.keys(schemaRequirements()).sort()).to.deep.equal(all)
      process.env.DOC_STORE_MONGO_REPOS = '*'
      expect(Object.keys(schemaRequirements()).sort()).to.deep.equal(all) // every repository has a PostgreSQL implementation: none is kept
      process.env.DOC_STORE_MONGO_REPOS = 'conversation'
      expect(Object.keys(schemaRequirements()).sort()).to.deep.equal(all) // saved searches still need their tables
      process.env.DOC_STORE_MONGO_REPOS = 'document,project'
      expect(Object.keys(schemaRequirements()).sort()).to.deep.equal(all.filter((b) => b !== 'content'))
      process.env.DOC_STORE_MONGO_REPOS = 'oauth-app,oauth-token,identity-lookup,delegation'
      expect(Object.keys(schemaRequirements()).sort()).to.deep.equal(all.filter((b) => b !== 'oauth'))
      process.env.DOC_STORE_MONGO_REPOS = 'slack-thread'
      expect(Object.keys(schemaRequirements()).sort()).to.deep.equal(all.filter((b) => b !== 'integrations'))
      process.env.DOC_STORE_MONGO_REPOS = 'identity,credential,user,group,org,org-auth-config,activity,picture,reset-link,org-metrics-job'
      expect(Object.keys(schemaRequirements())).to.include('identity') // an OAuth app takes its slug number from the identity tables
      process.env.DOC_STORE_MONGO_REPOS = 'conversation,semantic-search,document,project,notification,mail-log,outbox,connectors-config,oauth-app,oauth-token,identity-lookup,delegation,slack-thread,identity,credential,user,group,org,org-auth-config,activity,picture,reset-link,org-metrics-job'
      expect(Object.keys(schemaRequirements())).to.deep.equal(['platform'])
    })

    it('does nothing under DOC_STORE=mongo', () => {
      delete process.env.DOC_STORE
      process.env.DOC_STORE_MONGO_REPOS = '*'
      expect(pickStore('conversation', { mongo: () => 'm', postgres: () => 'p' })).to.equal('m')
      expect(mongoFallbacks.size).to.equal(0)
    })
  })

  it('needs a MongoDB connection only on Mongo, or while a repository is kept there', () => {
    const { usesMongo } = require('../../../src/libs/repositories/store-select') as typeof import('../../../src/libs/repositories/store-select')
    delete process.env.DOC_STORE
    expect(usesMongo()).to.equal(true)
    process.env.DOC_STORE = 'postgres'
    delete process.env.DOC_STORE_MONGO_REPOS
    expect(usesMongo()).to.equal(false)
    process.env.DOC_STORE_MONGO_REPOS = 'outbox'
    expect(usesMongo()).to.equal(true)
    delete process.env.DOC_STORE_MONGO_REPOS
  })

  it('builds the unit of work of the selected store', () => {
    delete process.env.DOC_STORE
    expect(createUnitOfWork(true)).to.be.instanceOf(MongoUnitOfWork)
    process.env.DOC_STORE = 'postgres'
    expect(createUnitOfWork(true)).to.be.instanceOf(PgUnitOfWork)
  })
})

describe('DOC_STORE=postgres at boot', () => {
  const saved = process.env.DOC_STORE
  afterEach(() => {
    if (saved === undefined) delete process.env.DOC_STORE
    else process.env.DOC_STORE = saved
    forgetRepositories()
  })

  it('lists every repository directory in the registry, so none can be forgotten', () => {
    const { readdirSync, existsSync } = require('node:fs') as typeof import('node:fs')
    const { join } = require('node:path') as typeof import('node:path')
    const { REPOSITORIES } = require('../../../src/libs/repositories/all') as typeof import('../../../src/libs/repositories/all')
    const root = join(__dirname, '../../../src/libs/repositories')
    const dirs = readdirSync(root).filter((d) => existsSync(join(root, d, 'index.ts')))
    expect(Object.keys(REPOSITORIES).sort()).to.deep.equal(dirs.sort())
  })

  it('every repository has a PostgreSQL implementation: the check passes, and "*" keeps none on Mongo', () => {
    const { assertAllRepositoriesReady } = require('../../../src/libs/repositories/all') as typeof import('../../../src/libs/repositories/all')
    const { mongoFallbacks } = require('../../../src/libs/repositories/store-select') as typeof import('../../../src/libs/repositories/store-select')
    process.env.DOC_STORE = 'postgres'
    forgetRepositories()
    try {
      assertAllRepositoriesReady()
      process.env.DOC_STORE_MONGO_REPOS = '*'
      forgetRepositories()
      assertAllRepositoriesReady()
      expect([...mongoFallbacks]).to.deep.equal([])
    } finally {
      delete process.env.DOC_STORE_MONGO_REPOS
      mongoFallbacks.clear()
    }
  })

  it('refuses a split of the OAuth repositories across the stores', () => {
    const { assertCoherentSplit } = require('../../../src/libs/repositories/doc-store') as typeof import('../../../src/libs/repositories/doc-store')
    const saved = process.env.DOC_STORE_MONGO_REPOS
    try {
      delete process.env.DOC_STORE_MONGO_REPOS
      expect(() => assertCoherentSplit()).to.not.throw()
      process.env.DOC_STORE_MONGO_REPOS = 'oauth-token'
      expect(() => assertCoherentSplit()).to.throw(/together/)
      process.env.DOC_STORE_MONGO_REPOS = 'oauth-app,oauth-token,identity-lookup'
      expect(() => assertCoherentSplit()).to.not.throw()
    } finally {
      if (saved === undefined) delete process.env.DOC_STORE_MONGO_REPOS
      else process.env.DOC_STORE_MONGO_REPOS = saved
    }
  })

  it('refuses to start without a jobs login unless the outbox stays on Mongo', () => {
    const { assertJobsPool } = require('../../../src/libs/repositories/doc-store') as typeof import('../../../src/libs/repositories/doc-store')
    const saved = process.env.DOC_STORE_MONGO_REPOS
    try {
      delete process.env.DOC_STORE_MONGO_REPOS
      expect(() => assertJobsPool(false)).to.throw(/PG_JOBS_URL/)
      expect(() => assertJobsPool(true)).to.not.throw()
      process.env.DOC_STORE_MONGO_REPOS = 'outbox'
      expect(() => assertJobsPool(false)).to.not.throw()
    } finally {
      if (saved === undefined) delete process.env.DOC_STORE_MONGO_REPOS
      else process.env.DOC_STORE_MONGO_REPOS = saved
    }
  })

  it('treats a blank DOC_STORE as mongo', () => {
    process.env.DOC_STORE = ''
    expect(docStore()).to.equal('mongo')
  })
})
