import 'reflect-metadata'
import { expect } from 'chai'
import {
  CHATS_NEED_MONGO,
  defaultConversationEdition,
} from '../../../../../src/modules/enterprise_search/services/conversation/conversation-edition'

// Chats in this version read and write MongoDB themselves, so the default edition refuses to start on another
// document store rather than start healthy with every chat waiting on a MongoDB that is not there.
describe('the default conversation edition and the document store', () => {
  const saved = { store: process.env.DOC_STORE, mongoRepos: process.env.DOC_STORE_MONGO_REPOS }
  const bind = () => defaultConversationEdition.bind({} as never, {} as never, {} as never)

  afterEach(() => {
    for (const [name, value] of [['DOC_STORE', saved.store], ['DOC_STORE_MONGO_REPOS', saved.mongoRepos]] as const) {
      if (value === undefined) delete process.env[name]
      else process.env[name] = value
    }
  })

  for (const store of ['postgres', 'shadow', ' Postgres ']) {
    it(`refuses to start with DOC_STORE=${JSON.stringify(store)}, saying what to set and where to read why`, () => {
      process.env.DOC_STORE = store
      expect(bind).to.throw(Error, CHATS_NEED_MONGO)
    })
  }

  it('refuses PostgreSQL even with every repository kept on MongoDB: the chats do not read through them', () => {
    process.env.DOC_STORE = 'postgres'
    process.env.DOC_STORE_MONGO_REPOS = '*'
    expect(bind).to.throw(Error, CHATS_NEED_MONGO)
  })

  for (const store of [undefined, 'mongo', '']) {
    it(`starts with DOC_STORE=${JSON.stringify(store)}, and binds nothing`, () => {
      if (store === undefined) delete process.env.DOC_STORE
      else process.env.DOC_STORE = store
      expect(bind()).to.equal(undefined)
    })
  }

  it('says it in words an operator can act on', () => {
    expect(CHATS_NEED_MONGO).to.equal(
      'Chats need MongoDB storage in this version. Set DOC_STORE=mongo. ' +
        'See "Document store on PostgreSQL" in deployment/docker-compose/postgres/README.md.',
    )
  })
})
