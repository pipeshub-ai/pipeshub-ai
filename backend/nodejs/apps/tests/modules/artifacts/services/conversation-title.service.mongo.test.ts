import 'reflect-metadata'
import { expect } from 'chai'
import mongoose, { Types } from 'mongoose'
import { ConversationTitleService } from '../../../../src/modules/artifacts/services/conversation-title.service'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { readFilter } from '../../../../src/modules/enterprise_search/services/collaboration/access/conversation-access.filters'

const uri = process.env.PCC_MONGO_URI

;(uri ? describe : describe.skip)('ConversationTitleService against a real MongoDB (#3427 on PH-04)', function () {
  this.timeout(30_000)
  const org = new Types.ObjectId()
  const otherOrg = new Types.ObjectId()
  const [caller, owner, stranger] = [1, 2, 3].map(() => new Types.ObjectId())
  const team = new Types.ObjectId()
  const project = new Types.ObjectId()
  const ids: Record<string, Types.ObjectId> = {}

  const chat = async (name: string, fields: Record<string, unknown>) => {
    const doc = await ChatSession.create({ orgId: org, userId: owner, initiator: owner, title: name, ...fields })
    ids[name] = doc._id as Types.ObjectId
  }

  before(async () => {
    await mongoose.connect(uri as string)
    await ChatSession.init()
    await chat('mine', { userId: caller, initiator: caller })
    await chat('team share', { sharedWith: [{ principalType: 'team', teamId: String(team), accessLevel: 'read' }] })
    await chat('direct share', { isShared: true, sharedWith: [{ principalType: 'user', userId: caller, accessLevel: 'read' }] })
    await chat('someone else', { isShared: true, sharedWith: [{ principalType: 'user', userId: stranger, accessLevel: 'read' }] })
    await chat('project chat', { projectId: project, projectVisibility: 'project' })
    await chat('private project chat', { projectId: project, projectVisibility: 'private' })
    await chat('deleted', { userId: caller, initiator: caller, isDeleted: true })
    await chat('other org', { orgId: otherOrg, userId: caller, initiator: caller })
  })

  after(async () => {
    await ChatSession.deleteMany({ orgId: { $in: [org, otherOrg] } })
    await mongoose.disconnect()
  })

  const titlesFor = async (teamIds: string[], accessibleProjectIds: string[]) => {
    const filter = readFilter(
      { userId: String(caller), orgId: String(org), teamIds },
      { kind: 'any' },
      { accessibleProjectIds, includeOwned: true, includeShared: true },
    )
    const titles = await ConversationTitleService.batchTitles(Object.values(ids).map(String), filter)
    return [...titles.values()].sort()
  }

  it('names exactly the conversations the caller can read, team shares and projects included', async () => {
    expect(await titlesFor([String(team)], [String(project)])).to.deep.equal(
      ['direct share', 'mine', 'project chat', 'team share'].sort(),
    )
  })

  it('drops a team share once the caller leaves the team, and project chats without project access', async () => {
    expect(await titlesFor([], [])).to.deep.equal(['direct share', 'mine'])
  })
})
