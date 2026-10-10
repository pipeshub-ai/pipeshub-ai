import 'reflect-metadata'
import { expect } from 'chai'
import { readFileSync } from 'fs'
import { join } from 'path'
import { Container } from 'inversify'
import yaml from 'js-yaml'
import { createAuthzRouter } from '../../../src/modules/authz/routes/authz.routes'
import { COLLAB_TYPES } from '../../../src/modules/enterprise_search/services/collaboration/collab.types'
import { markingGuards } from '../enterprise_search/helpers/guarded-chat'

type Json = Record<string, any>
const spec = yaml.load(readFileSync(join(__dirname, '..', '..', '..', 'src', 'modules', 'api-docs', 'pipeshub-openapi.yaml'), 'utf8')) as Json

describe('OpenAPI: authz user routes and projectChatAccess (PH07-20, PH07-21)', () => {
  const container = new Container()
  container.bind('AuthMiddleware').toConstantValue({ authenticate: (_q: unknown, _s: unknown, n: () => void) => n() })
  container.bind(COLLAB_TYPES.FeatureFlags).toConstantValue({ isEnabled: async () => true })
  container.bind(COLLAB_TYPES.ConversationGuards).toConstantValue(markingGuards())
  container.bind(COLLAB_TYPES.AuthzController).toConstantValue({ explain: () => undefined, preview: () => undefined })
  const mounted = (createAuthzRouter(container).stack as Array<{ route?: { path: string; methods: Record<string, boolean> } }>)
    .filter((l) => l.route)
    .map((l) => ({ method: Object.keys(l.route!.methods)[0]!, path: l.route!.path }))

  it('documents every route the router mounts, under /authz, with the read scope', () => {
    expect(mounted).to.have.length(2)
    for (const { method, path } of mounted) {
      const op = spec.paths[`/authz${path}`]?.[method]
      expect(op, `${method} /authz${path}`).to.not.equal(undefined)
      expect((op.security as Json[]).flatMap((s) => s.oauth2 ?? [])).to.deep.equal(['conversation:read'])
      expect(op.tags).to.deep.equal(['Authorization'])
    }
  })

  it('documents the failure modes a caller must handle', () => {
    const preview = spec.paths['/authz/explain/preview'].post.responses
    expect(preview['403'].description).to.include('CONVERSATION_OWNER_ONLY')
    expect(preview['404']).to.not.equal(undefined)
    expect(preview['503']).to.not.equal(undefined)
    expect(spec.paths['/authz/explain'].get.responses['503']).to.not.equal(undefined)
  })

  it('declares the response shapes the controller sends', () => {
    const { schemas } = spec.components
    expect(schemas.AuthzExplainResponse.required).to.have.members(['role', 'via'])
    expect(schemas.AuthzExplainPath.properties.ref.nullable).to.equal(true)
    expect(schemas.AuthzPreviewResponse.required).to.have.members(['gains', 'loses', 'becomesReadOnly', 'truncated'])
  })

  it('has unique operation ids', () => {
    for (const id of ['explainAccess', 'previewAccessChange']) {
      const same = Object.values(spec.paths).flatMap((item) => Object.values(item as Json)).filter((o) => (o as Json)?.operationId === id)
      expect(same, id).to.have.length(1)
    }
  })

  it('PATCH /projects/{projectId} accepts and returns projectChatAccess', () => {
    expect(spec.components.schemas.UpdateProjectRequest.properties.projectChatAccess.$ref).to.equal('#/components/schemas/ProjectChatAccess')
    expect(spec.components.schemas.Project.properties.projectChatAccess.$ref).to.equal('#/components/schemas/ProjectChatAccess')
    expect(spec.components.schemas.ProjectChatAccess.enum).to.deep.equal(['viewer', 'editor'])
  })
})
