import 'reflect-metadata'
import { expect } from 'chai'
import { Container } from 'inversify'
import { createProjectsRouter } from '../../../../src/modules/projects/routes/project.routes'
import { COLLAB_TYPES } from '../../../../src/modules/enterprise_search/services/collaboration/collab.types'
import { GUARD_MARK } from '../../../../src/modules/enterprise_search/services/collaboration/http/conversation-guards'
import { markingGuards } from '../../enterprise_search/helpers/guarded-chat'

function container(withGuards: boolean): Container {
  const c = new Container()
  c.bind('AuthMiddleware').toConstantValue({ authenticate: () => undefined })
  c.bind('AppConfig').toConstantValue({})
  c.bind(COLLAB_TYPES.FeatureFlags).toConstantValue({ isEnabled: async () => false })
  c.bind(COLLAB_TYPES.AuditWriter).toConstantValue({ record: async () => undefined })
  if (withGuards) {
    c.bind(COLLAB_TYPES.ConversationGuards).toConstantValue(markingGuards())
  }
  return c
}

describe('projects router', () => {
  it('does not boot without the conversation guards binding', () => {
    expect(() => createProjectsRouter(container(false))).to.throw()
  })

  it('guards the project conversation list with a list scope over both kinds', () => {
    const router = createProjectsRouter(container(true)) as unknown as { stack: Array<{ route?: { path: string; stack: Array<{ handle: Record<symbol, unknown> }> } }> }
    const layer = router.stack.find((l) => l.route?.path === '/:projectId/conversations')
    const marks = layer?.route?.stack.map((s) => s.handle[GUARD_MARK]).filter(Boolean)
    expect(marks).to.deep.equal([{ list: true, kind: 'any' }])
  })
})
