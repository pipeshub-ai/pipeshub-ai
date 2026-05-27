import 'reflect-metadata'
import { expect } from 'chai'
import {
  createLocalFsConnectorUploadMulter,
  isLocalFsConnector,
} from '../../src/utils/local-fs-utils'

describe('local-fs-utils', () => {
  describe('createLocalFsConnectorUploadMulter', () => {
    it('returns a Multer instance with standard handlers', () => {
      const upload = createLocalFsConnectorUploadMulter({
        maxFileSizeBytes: 2048,
        maxFiles: 7,
      })
      expect(upload.any).to.be.a('function')
      expect(upload.array).to.be.a('function')
      expect(upload.single).to.be.a('function')
    })
  })

  describe('isLocalFsConnector', () => {
    describe('canonical spellings', () => {
      const accepted = ['Local FS', 'localFs', 'LOCALFS']

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

      it('does not accept localfilesystem aliases via separators', () => {
        expect(isLocalFsConnector('local _ file _ system')).to.equal(false)
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
        expect(isLocalFsConnector('localfs')).to.equal(true)
      })

      it('UPPER-case', () => {
        expect(isLocalFsConnector('LOCALFS')).to.equal(true)
      })

      it('mIxEd-case', () => {
        expect(isLocalFsConnector('LoCaLfS')).to.equal(true)
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
        'FolderSync',
        'folder_sync',
        'folder sync',
        'FOLDER SYNC',
        'folder-sync',
        'Local FileSystem',
        'LocalFileSystem',
        'localfilesystem',
        'LOCALFILESYSTEM',
        'LoCaLfIlEsYsTeM',
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
})
