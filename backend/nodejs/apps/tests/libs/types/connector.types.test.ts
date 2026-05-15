import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  ConnectorId,
  ConnectorNames,
  ConnectorIdToNameMap,
} from '../../../src/libs/types/connector.types'

describe('libs/types/connector.types', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('ConnectorId enum', () => {
    it('should have GOOGLE_WORKSPACE', () => {
      expect(ConnectorId.GOOGLE_WORKSPACE).to.equal('googleWorkspace')
    })

    it('should have ATLASSIAN', () => {
      expect(ConnectorId.ATLASSIAN).to.equal('atlassian')
    })

    it('should have ONEDRIVE', () => {
      expect(ConnectorId.ONEDRIVE).to.equal('onedrive')
    })

    it('should have SHAREPOINT_ONLINE', () => {
      expect(ConnectorId.SHAREPOINT_ONLINE).to.equal('sharepointOnline')
    })

    it('should have GMAIL', () => {
      expect(ConnectorId.GMAIL).to.equal('gmail')
    })

    it('should have CALENDAR', () => {
      expect(ConnectorId.CALENDAR).to.equal('calendar')
    })

    it('should have DRIVE', () => {
      expect(ConnectorId.DRIVE).to.equal('drive')
    })

    it('should have JIRA', () => {
      expect(ConnectorId.JIRA).to.equal('jira')
    })

    it('should have CONFLUENCE', () => {
      expect(ConnectorId.CONFLUENCE).to.equal('confluence')
    })

    it('should have NOTION', () => {
      expect(ConnectorId.NOTION).to.equal('notion')
    })

    it('should have BITBUCKET', () => {
      expect(ConnectorId.BITBUCKET).to.equal('bitbucket')
    })

    it('should have OUTLOOK', () => {
      expect(ConnectorId.OUTLOOK).to.equal('outlook')
    })

    it('should have OUTLOOK_CALENDAR', () => {
      expect(ConnectorId.OUTLOOK_CALENDAR).to.equal('outlookCalendar')
    })

    it('should have MICROSOFT_TEAMS', () => {
      expect(ConnectorId.MICROSOFT_TEAMS).to.equal('microsoftTeams')
    })

    it('should have SLACK', () => {
      expect(ConnectorId.SLACK).to.equal('slack')
    })

    it('should have DROPBOX', () => {
      expect(ConnectorId.DROPBOX).to.equal('dropbox')
    })

    it('should have BOX', () => {
      expect(ConnectorId.BOX).to.equal('box')
    })

    it('should have LINEAR', () => {
      expect(ConnectorId.LINEAR).to.equal('linear')
    })

    it('should have UPLOAD', () => {
      expect(ConnectorId.UPLOAD).to.equal('upload')
    })

    it('should have RSS', () => {
      expect(ConnectorId.RSS).to.equal('rss')
    })

    it('should have LOCAL_FS', () => {
      expect(ConnectorId.LOCAL_FS).to.equal('localfs')
    })

    it('should have exactly 21 connector IDs', () => {
      const values = Object.values(ConnectorId).filter(
        (v) => typeof v === 'string',
      )
      expect(values).to.have.lengthOf(21)
    })
  })

  describe('ConnectorNames enum', () => {
    it('should have GOOGLE_WORKSPACE as "Google Workspace"', () => {
      expect(ConnectorNames.GOOGLE_WORKSPACE).to.equal('Google Workspace')
    })

    it('should have ATLASSIAN as "Atlassian"', () => {
      expect(ConnectorNames.ATLASSIAN).to.equal('Atlassian')
    })

    it('should have SLACK as "Slack"', () => {
      expect(ConnectorNames.SLACK).to.equal('Slack')
    })

    it('should have UPLOAD as "Uploaded Files"', () => {
      expect(ConnectorNames.UPLOAD).to.equal('Uploaded Files')
    })

    it('should have LOCAL_FS as "Local FS"', () => {
      expect(ConnectorNames.LOCAL_FS).to.equal('Local FS')
    })

    it('should have exactly 21 connector names', () => {
      const values = Object.values(ConnectorNames).filter(
        (v) => typeof v === 'string',
      )
      expect(values).to.have.lengthOf(21)
    })
  })

  describe('ConnectorIdToNameMap', () => {
    it('should be an object', () => {
      expect(ConnectorIdToNameMap).to.be.an('object')
    })

    it('should map every ConnectorId to a ConnectorNames value', () => {
      const connectorIdValues = Object.values(ConnectorId).filter(
        (v) => typeof v === 'string',
      )
      connectorIdValues.forEach((id) => {
        expect(ConnectorIdToNameMap[id as ConnectorId]).to.be.a('string')
      })
    })

    it('should map googleWorkspace to Google Workspace', () => {
      expect(ConnectorIdToNameMap[ConnectorId.GOOGLE_WORKSPACE]).to.equal('Google Workspace')
    })

    it('should map jira to Jira', () => {
      expect(ConnectorIdToNameMap[ConnectorId.JIRA]).to.equal('Jira')
    })

    it('should map upload to Uploaded Files', () => {
      expect(ConnectorIdToNameMap[ConnectorId.UPLOAD]).to.equal('Uploaded Files')
    })

    it('should map localfs to Local FS', () => {
      expect(ConnectorIdToNameMap[ConnectorId.LOCAL_FS]).to.equal('Local FS')
    })

    it('should have an entry for every ConnectorId', () => {
      const idCount = Object.values(ConnectorId).filter(
        (v) => typeof v === 'string',
      ).length
      expect(Object.keys(ConnectorIdToNameMap)).to.have.lengthOf(idCount)
    })
  })
})
