import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  isDesktopConnected,
  isLocalFsDesktopOnline,
  registerDesktopPresence,
  resolveDesktopPresence,
} from '../../../src/libs/services/desktop-presence.provider'

const makePresence = (online: boolean | null, connected: boolean | null = null) => ({
  isLocalFsDesktopOnline: sinon.stub().returns(online),
  isDesktopConnected: sinon.stub().returns(connected),
})

describe('desktop-presence.provider', () => {
  afterEach(() => {
    sinon.restore()
    registerDesktopPresence(null)
  })

  it('resolves null before anything is registered', () => {
    registerDesktopPresence(null)
    expect(resolveDesktopPresence()).to.be.null
    expect(isLocalFsDesktopOnline('org-1', 'user-1', 'conn-1')).to.be.null
  })

  it('resolves the registered presence and forwards the lookup', () => {
    const presence = makePresence(false)
    registerDesktopPresence(presence)
    expect(resolveDesktopPresence()).to.equal(presence)
    expect(isLocalFsDesktopOnline('org-1', 'user-1', 'conn-1')).to.equal(false)
    expect(presence.isLocalFsDesktopOnline.calledOnceWithExactly('org-1', 'user-1', 'conn-1')).to.be.true
  })

  it('passes a null answer through when the gateway is not ready', () => {
    registerDesktopPresence(makePresence(null))
    expect(isLocalFsDesktopOnline('org-1', 'user-1', 'conn-1')).to.be.null
  })

  it('forwards the connected lookup and answers null when nothing is registered', () => {
    registerDesktopPresence(null)
    expect(isDesktopConnected('org-1', 'user-1')).to.be.null
    const presence = makePresence(false, true)
    registerDesktopPresence(presence)
    expect(isDesktopConnected('org-1', 'user-1')).to.equal(true)
    expect(presence.isDesktopConnected.calledOnceWithExactly('org-1', 'user-1')).to.be.true
  })

  it('overwrites a previously registered presence', () => {
    const first = makePresence(true)
    const second = makePresence(false)
    registerDesktopPresence(first)
    registerDesktopPresence(second)
    expect(resolveDesktopPresence()).to.equal(second)
  })
})
