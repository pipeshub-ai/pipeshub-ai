import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { COLLAB_ERROR_CODES } from '../../../../src/modules/enterprise_search/services/collaboration/domain/errors'
import { CONVERSATION_ROUTES, ConversationRoute } from '../helpers/conversation-routes'
import { ACTORS, ActorName, Env, buildRouters, call, seed } from '../helpers/conversation-world'

/** Archive is per user for shared chats, so a recipient's `archiveSelf` grant archives for the recipient and never for the owner. */
describe('archive by a non-owner with read access is a per-user archive (flag on)', () => {
  let on: Env
  before(() => {
    on = buildRouters({ collab: true, stop: false })
  })
  afterEach(() => sinon.restore())

  const route = (id: string): ConversationRoute => CONVERSATION_ROUTES.find((r) => r.id === id)!

  for (const id of ['C15', 'A12']) {
    it(`${id}: B, C and P archive for themselves and the chat stays unarchived for everyone else`, async () => {
      for (const who of ['B', 'C', 'P'] as ActorName[]) {
        const s = seed()
        const out = await call(on, route(id), s, who)
        expect(out.error, `${id} ${who}`).to.equal(undefined)
        expect(out.status, `${id} ${who}`).to.equal(200)
        const stored = s.store.session(s.ids[route(id).kind])
        expect(stored?.isArchived ?? false, `${id} ${who} global flag`).to.equal(false)
        expect(((stored?.get('archivedFor') ?? []) as Types.ObjectId[]).map(String), `${id} ${who} archivedFor`).to.deep.equal([String(ACTORS[who].userId)])
      }
    })

    it(`${id}: a stranger and another org get 404 and nothing is written`, async () => {
      for (const who of ['D', 'E'] as ActorName[]) {
        const s = seed()
        const out = await call(on, route(id), s, who)
        expect(out.error?.statusCode, `${id} ${who}`).to.equal(404)
        expect(out.error?.code, `${id} ${who}`).to.equal(COLLAB_ERROR_CODES.NOT_FOUND)
        expect(s.store.writes, `${id} ${who} writes`).to.deep.equal([])
      }
    })
  }

  for (const id of ['C16', 'A13']) {
    it(`${id}: a recipient cannot clear the owner's archive of a shared chat`, async () => {
      const s = seed({ isArchived: true, archivedBy: ACTORS.A.userId })
      const out = await call(on, route(id), s, 'B')
      expect(out.error?.statusCode, id).to.equal(400)
      expect(s.store.session(s.ids[route(id).kind])?.isArchived, `${id} still archived`).to.equal(true)
    })
  }
})
