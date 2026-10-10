import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { Project } from '../../../../src/modules/projects/schema/project.schema'
import { COLLAB_ERROR_CODES } from '../../../../src/modules/enterprise_search/services/collaboration/domain/errors'
import { AGENT_KEY, CONVERSATION_ROUTES, ConversationRoute } from '../helpers/conversation-routes'
import { invokeRoute, RouteOutcome } from '../helpers/route-invoker'
import { ACTORS, ActorName, Env, Seed, buildRouters, call, seed } from '../helpers/conversation-world'

type Kind = 'chat' | 'agent'
const route = (id: string): ConversationRoute => CONVERSATION_ROUTES.find((r) => r.id === id)!
const ARCHIVE: Record<Kind, string> = { chat: 'C15', agent: 'A12' }
const UNARCHIVE: Record<Kind, string> = { chat: 'C16', agent: 'A13' }

interface ListBody {
  conversations: Array<{ _id: unknown }>
  sharedWithMeConversations?: Array<{ _id: unknown }>
}

/** Ids a user sees on the main list or the archive list of `kind`; the chat sidebar needs `source=shared` to list what others shared. */
async function listed(env: Env, kind: Kind, list: 'main' | 'archive', who: ActorName, source: 'owned' | 'shared' = 'owned'): Promise<string[]> {
  const a = ACTORS[who]
  if (!(Project.find as unknown as sinon.SinonStub).restore) sinon.stub(Project, 'find').returns({ lean: () => Promise.resolve([]) } as never)
  const url =
    kind === 'chat'
      ? list === 'main' ? `/?source=${source}` : '/show/archives'
      : list === 'main' ? `/${AGENT_KEY}/conversations` : `/${AGENT_KEY}/conversations/show/archives`
  const out: RouteOutcome = await invokeRoute(env[kind], { method: 'get', url, user: { userId: a.userId, orgId: a.orgId, email: `${who}@example.com` } })
  expect(out.error, `${list} list of ${who}`).to.equal(undefined)
  const body = out.body as ListBody
  const rows = [...body.conversations, ...(kind === 'agent' && list === 'main' ? (body.sharedWithMeConversations ?? []) : [])]
  return rows.map((r) => String(r._id))
}

const sessionOf = (s: Seed, kind: Kind) => s.store.session(s.ids[kind])!
const archivedForOf = (s: Seed, kind: Kind): string[] => ((sessionOf(s, kind).get('archivedFor') ?? []) as Types.ObjectId[]).map(String)

describe('PR-5f: per-user archive', () => {
  let on: Env
  let off: Env
  before(() => {
    on = buildRouters({ collab: true, stop: false })
    off = buildRouters({ collab: false, stop: false })
  })
  afterEach(() => sinon.restore())

  for (const kind of ['chat', 'agent'] as Kind[]) {
    describe(`${kind}`, () => {
      const archive = route(ARCHIVE[kind])
      const unarchive = route(UNARCHIVE[kind])
      const main = (who: ActorName, s: Seed, env = on) => listed(env, kind, 'main', who, who === 'A' ? 'owned' : 'shared').then((ids) => ids.includes(s.ids[kind]))
      const archives = (who: ActorName, s: Seed, env = on) => listed(env, kind, 'archive', who).then((ids) => ids.includes(s.ids[kind]))

      it('CL-03: a recipient archives a shared chat for themself only; the chat is not global-archived and rev is unchanged', async () => {
        const s = seed()
        const rev = sessionOf(s, kind).rev
        const out = await call(on, archive, s, 'C')
        expect(out.error, 'archive').to.equal(undefined)
        expect(out.status).to.equal(200)
        expect(archivedForOf(s, kind)).to.deep.equal([String(ACTORS.C.userId)])
        expect(sessionOf(s, kind).isArchived).to.equal(false)
        expect(sessionOf(s, kind).rev, 'rev').to.equal(rev)

        expect(await main('C', s), 'C main').to.equal(false)
        expect(await archives('C', s), 'C archive').to.equal(true)
        expect(await main('A', s), 'A main').to.equal(true)
        expect(await archives('A', s), 'A archive').to.equal(false)
        expect(await main('B', s), 'B main').to.equal(true)
        expect(await archives('B', s), 'B archive').to.equal(false)
      })

      it('user A archiving a shared chat does not hide it for user B', async () => {
        const s = seed()
        expect((await call(on, archive, s, 'A')).error).to.equal(undefined)
        expect(archivedForOf(s, kind)).to.deep.equal([String(ACTORS.A.userId)])
        expect(sessionOf(s, kind).isArchived, 'owner on a shared chat is per user too').to.equal(false)
        expect(await main('A', s)).to.equal(false)
        expect(await archives('A', s)).to.equal(true)
        expect(await main('B', s)).to.equal(true)
        expect(await archives('B', s)).to.equal(false)
      })

      it('each user has their own archive list', async () => {
        const s = seed()
        await call(on, archive, s, 'A')
        await call(on, archive, s, 'B')
        expect(archivedForOf(s, kind).sort()).to.deep.equal([String(ACTORS.A.userId), String(ACTORS.B.userId)].sort())
        expect(await archives('A', s)).to.equal(true)
        expect(await archives('B', s)).to.equal(true)
        expect(await archives('C', s)).to.equal(false)
        expect(await main('C', s)).to.equal(true)
      })

      it('unarchive pulls only the caller and the chat returns to their main list', async () => {
        const s = seed({ archivedFor: [ACTORS.B.userId, ACTORS.C.userId] })
        expect((await call(on, unarchive, s, 'B')).error).to.equal(undefined)
        expect(archivedForOf(s, kind)).to.deep.equal([String(ACTORS.C.userId)])
        expect(await main('B', s)).to.equal(true)
        expect(await archives('B', s)).to.equal(false)
        expect(await archives('C', s)).to.equal(true)
      })

      it('archiving twice, or unarchiving what the caller has not archived, is a 400', async () => {
        const s = seed({ archivedFor: [ACTORS.B.userId] })
        expect((await call(on, archive, s, 'B')).error?.statusCode).to.equal(400)
        expect((await call(on, unarchive, s, 'C')).error?.statusCode).to.equal(400)
        expect(archivedForOf(s, kind)).to.deep.equal([String(ACTORS.B.userId)])
      })

      it('a project viewer of a project-visible chat archives per user', async () => {
        const s = seed({ sharedWith: [], isShared: false })
        expect((await call(on, archive, s, 'P')).error).to.equal(undefined)
        expect(archivedForOf(s, kind)).to.deep.equal([String(ACTORS.P.userId)])
        expect(sessionOf(s, kind).isArchived).to.equal(false)
      })

      it('a stranger still gets 404 and nothing is written', async () => {
        const s = seed()
        const out = await call(on, archive, s, 'D')
        expect(out.error?.statusCode).to.equal(404)
        expect(out.error?.code).to.equal(COLLAB_ERROR_CODES.NOT_FOUND)
        expect(s.store.writes).to.deep.equal([])
      })

      it('the owner on an unshared chat keeps the global isArchived', async () => {
        const s = seed({ sharedWith: [], isShared: false, projectId: undefined, projectVisibility: undefined })
        expect((await call(on, archive, s, 'A')).error).to.equal(undefined)
        expect(sessionOf(s, kind).isArchived).to.equal(true)
        expect(String(sessionOf(s, kind).archivedBy)).to.equal(String(ACTORS.A.userId))
        expect(archivedForOf(s, kind)).to.deep.equal([])
        expect(await main('A', s)).to.equal(false)
        expect(await archives('A', s)).to.equal(true)

        expect((await call(on, unarchive, s, 'A')).error).to.equal(undefined)
        expect(sessionOf(s, kind).isArchived).to.equal(false)
        expect(await main('A', s)).to.equal(true)
        expect(await archives('A', s)).to.equal(false)
      })

      it('an owner archive made before the first share lists for the owner only, and the owner can unarchive it', async () => {
        const s = seed({ isArchived: true, archivedBy: ACTORS.A.userId })
        expect(await archives('A', s)).to.equal(true)
        expect(await archives('B', s)).to.equal(false)
        expect((await call(on, unarchive, s, 'A')).error).to.equal(undefined)
        expect(sessionOf(s, kind).isArchived).to.equal(false)
        expect(archivedForOf(s, kind)).to.deep.equal([])
      })
    })
  }

  describe('flag off', () => {
    for (const kind of ['chat', 'agent'] as Kind[]) {
      const archive = route(ARCHIVE[kind])
      const unarchive = route(UNARCHIVE[kind])

      it(`${kind}: archive and unarchive are global and owner-only, and archivedFor is never written`, async () => {
        const s = seed()
        const denied = await call(off, archive, s, 'C')
        expect(denied.error?.statusCode).to.equal(404)
        expect(s.store.writes).to.deep.equal([])

        expect((await call(off, archive, s, 'A')).error).to.equal(undefined)
        expect(sessionOf(s, kind).isArchived, 'a shared chat is still globally archived').to.equal(true)
        expect(archivedForOf(s, kind)).to.deep.equal([])
        expect((await call(off, unarchive, s, 'A')).error).to.equal(undefined)
        expect(sessionOf(s, kind).isArchived).to.equal(false)
      })

      it(`${kind}: lists use isArchived and ignore archivedFor`, async () => {
        const s = seed({ archivedFor: [ACTORS.A.userId] })
        const globallyArchived = s.store.addSession({
          ...sessionOf(s, kind).toObject(),
          _id: new Types.ObjectId(),
          archivedFor: [],
          isArchived: true,
          archivedBy: ACTORS.A.userId,
        })
        const archivedId = String(globallyArchived._id)
        expect(await listed(off, kind, 'main', 'A')).to.include(s.ids[kind]).and.not.include(archivedId)
        expect(await listed(off, kind, 'archive', 'A')).to.include(archivedId).and.not.include(s.ids[kind])
      })
    }
  })
})
