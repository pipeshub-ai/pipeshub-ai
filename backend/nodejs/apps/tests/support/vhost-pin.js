// Preload (node -r) for the S3 virtual-hosted leg of the storage integration
// tests. The S3 adapter reads and deletes a stored record only through
// <bucket>.s3.us-east-1.amazonaws.com URLs, and the SDK signs and sends to that
// host. This sends every connection to a *.amazonaws.com host to the local
// MinIO at STORAGE_IT_S3_VHOST_ADDR (host:port) instead, keeping the host name
// for TLS, so the adapter runs its real URL path without reaching AWS.
// The Python side does the same in backend/python/tests/integration/vhost_pin.py.
// Trust for MinIO's test certificate comes from NODE_EXTRA_CA_CERTS.
const net = require('net')

// Host names compare case-insensitively and may end in the root dot.
function isPinnedHost(host) {
  return typeof host === 'string' && host.toLowerCase().replace(/\.$/, '').endsWith('.amazonaws.com')
}

// net.Socket#connect takes ({host, port}, cb), (port, host, cb) or (path, cb);
// returns the arguments with a pinned host replaced by the target.
function pinConnectArgs(args, host, port) {
  const first = args[0]
  if (first && typeof first === 'object' && isPinnedHost(first.host)) {
    return [{ ...first, host, port }, ...args.slice(1)]
  }
  if (typeof first === 'number' && isPinnedHost(args[1])) {
    return [port, host, ...args.slice(2)]
  }
  return args
}

function install(target) {
  const sep = target.lastIndexOf(':')
  const host = target.slice(0, sep)
  const port = Number(target.slice(sep + 1))
  const connect = net.Socket.prototype.connect
  net.Socket.prototype.connect = function (...args) {
    return connect.apply(this, pinConnectArgs(args, host, port))
  }
}

if (process.env.STORAGE_IT_S3_VHOST_ADDR) install(process.env.STORAGE_IT_S3_VHOST_ADDR)

module.exports = { isPinnedHost, pinConnectArgs }
