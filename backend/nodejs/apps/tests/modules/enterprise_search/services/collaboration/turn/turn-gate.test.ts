import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ConversationBusyError, DuplicateMessageError, RunLostError } from '../../../../../../src/modules/enterprise_search/services/collaboration/domain/errors'
import { setConversationContext, trackLease } from '../../../../../../src/modules/enterprise_search/services/collaboration/http/conversation-context'
import { LeaseHandle, LeaseLostError } from '../../../../../../src/modules/enterprise_search/services/collaboration/leases/lease.types'
import { openTurnGate, outcomeForError } from '../../../../../../src/modules/enterprise_search/services/collaboration/turn/turn-gate'
import { outcomeForStatus } from '../../../../../../src/modules/enterprise_search/services/collaboration/turn/turn-lifecycle'
import { stampTurnRow } from '../../../../../../src/modules/enterprise_search/services/collaboration/turn/turn-run'
import { oid } from '../../../controller/chat-test-harness'

const fakeLease = (): LeaseHandle & { release: sinon.SinonStub; startHeartbeat: sinon.SinonStub } =>
  ({
    runId: 'run-1',
    sessionId: 's',
    renew: sinon.stub().resolves(true),
    release: sinon.stub().resolves(),
    ownershipFilter: () => ({}),
    startHeartbeat: sinon.stub(),
    stopHeartbeat: sinon.stub(),
  }) as never

describe('turn gate', () => {
  it('with the flag off there is no lease and settling does nothing', async () => {
    const req: any = {}
    setConversationContext(req, { caller: { userId: 'u', orgId: 'o', teamIds: [] }, collab: false })
    const gate = openTurnGate(req, () => undefined)
    expect(gate.lease).to.equal(undefined)
    await gate.settle('completed')
  })

  it('with the flag on it claims the lease, starts the heartbeat with onLost, and releases once', async () => {
    const req: any = {}
    const lease = fakeLease()
    trackLease(lease)
    setConversationContext(req, { caller: { userId: 'u', orgId: 'o', teamIds: [] }, collab: true, lease })
    const onLost = sinon.stub()

    const gate = openTurnGate(req, onLost)
    await gate.settle('failed')
    await gate.settle('completed')

    expect(gate.lease).to.equal(lease)
    expect(lease.startHeartbeat.calledOnceWithExactly(onLost)).to.equal(true)
    expect(lease.release.calledOnceWithExactly('Failed', { restorePrevious: false })).to.equal(true)
  })

  it('maps how a turn threw to its outcome', () => {
    expect(outcomeForError(new DuplicateMessageError('m', false))).to.equal('duplicate')
    expect(outcomeForError(new LeaseLostError('r'))).to.equal('stopped')
    expect(outcomeForError(new RunLostError())).to.equal('stopped')
    expect(outcomeForError(new ConversationBusyError({ userId: 'u', startedAt: new Date() }))).to.equal('failed')
  })

  it('maps a saved status to its outcome', () => {
    expect([outcomeForStatus('Failed'), outcomeForStatus('Stopped'), outcomeForStatus('Complete'), outcomeForStatus(undefined)]).to.deep.equal(['failed', 'stopped', 'completed', 'completed'])
  })

  it('stamps rows with asker, question and run, and leaves them alone without a run', () => {
    const asker = oid()
    const question = oid()
    const row = { messageType: 'bot_response', content: 'x' } as never
    expect(stampTurnRow(row)).to.equal(row)
    expect(stampTurnRow(row, { requestedBy: asker, inReplyTo: question })).to.deep.equal({ messageType: 'bot_response', content: 'x', requestedBy: asker, inReplyTo: question })
    expect(stampTurnRow(row, { lease: fakeLease() })).to.deep.equal({ messageType: 'bot_response', content: 'x', runId: 'run-1' })
  })
})
