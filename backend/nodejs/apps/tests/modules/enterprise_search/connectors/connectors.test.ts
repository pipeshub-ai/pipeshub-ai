import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { APP_TYPES } from '../../../../src/modules/enterprise_search/connectors/connectors'

describe('enterprise_search/connectors/connectors', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('APP_TYPES', () => {
    it('should define DRIVE', () => {
      expect(APP_TYPES.DRIVE).to.equal('drive')
    })

    it('should define GMAIL', () => {
      expect(APP_TYPES.GMAIL).to.equal('gmail')
    })

    it('should define ONEDRIVE', () => {
      expect(APP_TYPES.ONEDRIVE).to.equal('onedrive')
    })

    it('should define SHAREPOINT_ONLINE', () => {
      expect(APP_TYPES.SHAREPOINT_ONLINE).to.equal('sharepointOnline')
    })

    it('should define CONFLUENCE', () => {
      expect(APP_TYPES.CONFLUENCE).to.equal('confluence')
    })

    it('should define JIRA', () => {
      expect(APP_TYPES.JIRA).to.equal('jira')
    })

    it('should define SLACK', () => {
      expect(APP_TYPES.SLACK).to.equal('slack')
    })

    it('should define DROPBOX', () => {
      expect(APP_TYPES.DROPBOX).to.equal('dropbox')
    })

    it('should define OUTLOOK', () => {
      expect(APP_TYPES.OUTLOOK).to.equal('outlook')
    })

    it('should define WEB', () => {
      expect(APP_TYPES.WEB).to.equal('web')
    })

    it('should define LOCAL', () => {
      expect(APP_TYPES.LOCAL).to.equal('local')
    })

    it('should define NOTION', () => {
      expect(APP_TYPES.NOTION).to.equal('notion')
    })

    it('should define all expected app types', () => {
      const keys = Object.keys(APP_TYPES)
      expect(keys).to.include.members([
        'DRIVE', 'GMAIL', 'ONEDRIVE', 'SHAREPOINT_ONLINE',
        'BOOKSTACK', 'CONFLUENCE', 'JIRA', 'LINEAR',
        'SLACK', 'DROPBOX', 'OUTLOOK', 'SERVICENOW',
        'WEB', 'RSS', 'LOCAL', 'NOTION',
      ])
    })
  })
})
