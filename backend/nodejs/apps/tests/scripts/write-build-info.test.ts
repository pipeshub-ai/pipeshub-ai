import { expect } from 'chai'
import fs from 'fs'
import os from 'os'
import path from 'path'
import { GitRunner } from '../../src/libs/utils/build-info.utils'
import { writeBuildInfo } from '../../src/scripts/write-build-info'

const COMMIT = '84614464bb120e9f8e8c85420001260e709d99bb'

describe('scripts/write-build-info', () => {
  let dir: string
  let file: string

  beforeEach(() => {
    dir = fs.mkdtempSync(path.join(os.tmpdir(), 'write-build-info-'))
    file = path.join(dir, 'build-info.json')
  })

  afterEach(() => {
    fs.rmSync(dir, { recursive: true, force: true })
  })

  it('should write the tag, the commit and the build time', () => {
    const git: GitRunner = (args) => (args[0] === 'describe' ? 'v0.9.1' : COMMIT)
    const now = new Date('2026-09-30T10:12:00.000Z')

    expect(writeBuildInfo(file, now, git)).to.be.true

    expect(JSON.parse(fs.readFileSync(file, 'utf8'))).to.deep.equal({
      version: '0.9.1',
      commitId: COMMIT,
      buildTime: '2026-09-30T10:12:00.000Z',
    })
  })

  it('should write nothing and remove a stale file when git has no commit', () => {
    fs.writeFileSync(file, JSON.stringify({ commitId: 'stale' }))

    expect(writeBuildInfo(file, new Date(), () => null)).to.be.false

    expect(fs.existsSync(file)).to.be.false
  })
})
