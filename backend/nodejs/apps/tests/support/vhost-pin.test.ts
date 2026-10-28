import { expect } from 'chai'

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { isPinnedHost, pinConnectArgs } = require('./vhost-pin.js')

describe('storage integration vhost pin', () => {
  it('pins every spelling of an AWS host name', () => {
    for (const host of [
      'bucket.s3.us-east-1.amazonaws.com',
      'BUCKET.S3.US-EAST-1.AMAZONAWS.COM',
      'bucket.s3.us-east-1.amazonaws.com.',
      'Bucket.S3.us-east-1.AmazonAWS.com.',
    ]) {
      expect(isPinnedHost(host), host).to.equal(true)
    }
  })

  it('leaves other hosts alone', () => {
    for (const host of ['127.0.0.1', 'localhost', 'amazonaws.com.evil.test', 'notamazonaws.com', undefined, 80]) {
      expect(isPinnedHost(host), String(host)).to.equal(false)
    }
  })

  it('rewrites the object form of connect and keeps the other options', () => {
    const cb = () => undefined
    const out = pinConnectArgs([{ host: 'B.S3.US-EAST-1.AMAZONAWS.COM.', port: 443, servername: 'x' }, cb], '127.0.0.1', 19143)
    expect(out).to.deep.equal([{ host: '127.0.0.1', port: 19143, servername: 'x' }, cb])
  })

  it('rewrites the positional form connect(port, host, cb)', () => {
    const cb = () => undefined
    expect(pinConnectArgs([443, 'b.s3.us-east-1.amazonaws.com', cb], '127.0.0.1', 19143)).to.deep.equal([19143, '127.0.0.1', cb])
  })

  it('does not touch connections to other hosts, paths or a bare callback', () => {
    for (const args of [[{ host: 'localhost', port: 27017 }], [27017, 'localhost'], ['/tmp/x.sock'], [() => undefined]]) {
      expect(pinConnectArgs(args, '127.0.0.1', 19143)).to.deep.equal(args)
    }
  })
})
