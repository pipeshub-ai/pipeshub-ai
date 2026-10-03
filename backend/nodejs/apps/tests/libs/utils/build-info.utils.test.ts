import { expect } from 'chai'
import fs from 'fs'
import os from 'os'
import path from 'path'
import sinon from 'sinon'
import {
  GitRunner,
  getBuildInfo,
  parseBuildInfo,
  readBuildInfoFromGit,
  resolveBuildInfo,
} from '../../../src/libs/utils/build-info.utils'

const COMMIT = '84614464bb120e9f8e8c85420001260e709d99bb'

function gitAnswering(answers: Record<string, string | null>): sinon.SinonStub {
  return sinon.stub().callsFake((args: string[]) => answers[args[0] as string] ?? null)
}

describe('build-info.utils', () => {
  let dir: string
  let file: string

  beforeEach(() => {
    dir = fs.mkdtempSync(path.join(os.tmpdir(), 'build-info-'))
    file = path.join(dir, 'build-info.json')
  })

  afterEach(() => {
    fs.rmSync(dir, { recursive: true, force: true })
  })

  describe('resolveBuildInfo', () => {
    it('should read the values from the environment and never call git', () => {
      const git = gitAnswering({ describe: 'v9.9.9', 'rev-parse': 'other' })
      fs.writeFileSync(file, JSON.stringify({ version: '1.2.3', commitId: 'file' }))

      const info = resolveBuildInfo(
        { APP_VERSION: '0.9.1', GIT_COMMIT: COMMIT, BUILD_TIME: '2026-09-30T10:12:00Z' },
        file,
        git as GitRunner,
      )

      expect(info).to.deep.equal({
        version: '0.9.1',
        commitId: COMMIT,
        buildTime: '2026-09-30T10:12:00Z',
      })
      expect(git.called).to.be.false
    })

    it('should give null for empty variables and still not call git', () => {
      // What a plain `docker build .` produces: defined, but empty.
      const git = gitAnswering({ 'rev-parse': COMMIT })

      const info = resolveBuildInfo(
        { APP_VERSION: '', GIT_COMMIT: '', BUILD_TIME: '' },
        file,
        git as GitRunner,
      )

      expect(info).to.deep.equal({ version: null, commitId: null, buildTime: null })
      expect(git.called).to.be.false
    })

    it('should prefer the build file over git', () => {
      const git = gitAnswering({ describe: 'v9.9.9', 'rev-parse': 'later-pull' })
      fs.writeFileSync(
        file,
        JSON.stringify({ version: '0.9.1', commitId: COMMIT, buildTime: '2026-09-30T10:12:00Z' }),
      )

      const info = resolveBuildInfo({}, file, git as GitRunner)

      expect(info.commitId).to.equal(COMMIT)
      expect(info.buildTime).to.equal('2026-09-30T10:12:00Z')
      expect(git.called).to.be.false
    })

    it('should fall back to git when the build file is missing or not JSON', () => {
      const git = gitAnswering({ describe: 'v0.9.1', 'rev-parse': COMMIT })
      const expected = { version: '0.9.1', commitId: COMMIT, buildTime: null }

      expect(resolveBuildInfo({}, file, git as GitRunner)).to.deep.equal(expected)

      fs.writeFileSync(file, '{not json')
      expect(resolveBuildInfo({}, file, git as GitRunner)).to.deep.equal(expected)
    })
  })

  describe('readBuildInfoFromGit', () => {
    it('should strip the leading v from the tag', () => {
      const git = gitAnswering({ describe: 'v0.9.1', 'rev-parse': COMMIT })
      expect(readBuildInfoFromGit(git as GitRunner).version).to.equal('0.9.1')
    })

    it('should report the commit when the checkout has no tags', () => {
      const git = gitAnswering({ 'rev-parse': COMMIT })
      expect(readBuildInfoFromGit(git as GitRunner)).to.deep.equal({
        version: null,
        commitId: COMMIT,
        buildTime: null,
      })
    })

    it('should give null values when git fails', () => {
      expect(readBuildInfoFromGit(gitAnswering({}) as GitRunner)).to.deep.equal({
        version: null,
        commitId: null,
        buildTime: null,
      })
    })
  })

  describe('parseBuildInfo', () => {
    it('should return null for a value that is not an object', () => {
      for (const raw of [undefined, null, 'x', 42]) {
        expect(parseBuildInfo(raw)).to.be.null
      }
    })

    it('should keep string fields and drop every other type', () => {
      expect(
        parseBuildInfo({ version: ' 0.9.1 ', commitId: 123, buildTime: '', extra: 'x' }),
      ).to.deep.equal({ version: '0.9.1', commitId: null, buildTime: null })
    })
  })

  describe('getBuildInfo', () => {
    it('should resolve once and return the same object', () => {
      const first = getBuildInfo()
      expect(getBuildInfo()).to.equal(first)
      expect(first).to.have.all.keys('version', 'commitId', 'buildTime')
    })
  })
})
