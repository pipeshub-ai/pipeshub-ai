import 'reflect-metadata'
import { expect } from 'chai'
import { isLocalFsConnector } from '../../../../src/modules/knowledge_base/utils/local_fs_connector_name'

describe('isLocalFsConnector', () => {
  describe('canonical spellings', () => {
    const accepted = [
      'Local FS',
      'Local FileSystem',
      'LocalFileSystem',
      'localFs',
      'LOCALFS',
      'FolderSync',
      'folder_sync',
      'folder sync',
      'FOLDER SYNC',
    ]

    accepted.forEach((name) => {
      it(`accepts "${name}"`, () => {
        expect(isLocalFsConnector(name)).to.equal(true)
      })
    })
  })

  describe('whitespace and underscore tolerance', () => {
    it('strips a single internal space', () => {
      expect(isLocalFsConnector('Local FS')).to.equal(true)
    })

    it('strips multiple internal spaces', () => {
      expect(isLocalFsConnector('Local   FS')).to.equal(true)
    })

    it('strips internal underscores', () => {
      expect(isLocalFsConnector('local_fs')).to.equal(true)
    })

    it('strips mixed whitespace and underscores', () => {
      expect(isLocalFsConnector('local _ file _ system')).to.equal(true)
    })

    it('strips leading and trailing whitespace', () => {
      expect(isLocalFsConnector('  Local FS  ')).to.equal(true)
    })

    it('strips tab characters that match \\s', () => {
      expect(isLocalFsConnector('Local\tFS')).to.equal(true)
    })
  })

  describe('case insensitivity', () => {
    it('lower-case', () => {
      expect(isLocalFsConnector('localfilesystem')).to.equal(true)
    })

    it('UPPER-case', () => {
      expect(isLocalFsConnector('LOCALFILESYSTEM')).to.equal(true)
    })

    it('mIxEd-case', () => {
      expect(isLocalFsConnector('LoCaLfIlEsYsTeM')).to.equal(true)
    })
  })

  describe('rejects unrelated connectors', () => {
    const rejected = [
      'Google Drive',
      'OneDrive',
      'Dropbox',
      'Slack',
      'Confluence',
      'Local FS Extra',
      'NotLocalFs',
      'localfsx',
      'remoteFs',
      'fs',
      'local',
      '',
      ' ',
      'foldersyncing',
      'folder-sync',
    ]

    rejected.forEach((name) => {
      it(`rejects "${name}"`, () => {
        expect(isLocalFsConnector(name)).to.equal(false)
      })
    })
  })

  describe('does not strip non-whitespace separators', () => {
    it('hyphens are NOT collapsed (intentional)', () => {
      expect(isLocalFsConnector('local-fs')).to.equal(false)
    })

    it('dots are NOT collapsed', () => {
      expect(isLocalFsConnector('local.fs')).to.equal(false)
    })
  })

  describe('return type', () => {
    it('always returns a boolean', () => {
      expect(isLocalFsConnector('Local FS')).to.be.a('boolean')
      expect(isLocalFsConnector('xyz')).to.be.a('boolean')
    })
  })
})
