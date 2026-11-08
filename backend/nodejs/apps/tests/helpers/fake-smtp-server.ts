import net from 'net';

/**
 * A real SMTP server on a loopback port that speaks just enough of the
 * protocol for nodemailer's verify(): greeting, EHLO, AUTH PLAIN/LOGIN, QUIT.
 * One username/password pair signs in; anything else gets 535.
 */
export interface FakeSmtpServer {
  port: number;
  /** AUTH attempts seen, as "user:pass". */
  logins: string[];
  close(): Promise<void>;
}

export async function startFakeSmtpServer(options: {
  user: string;
  pass: string;
  /** Answer the connection with this instead of a 220 greeting. */
  greeting?: string;
}): Promise<FakeSmtpServer> {
  const logins: string[] = [];
  const decode = (b64: string) => Buffer.from(b64, 'base64').toString('utf8');
  const server = net.createServer((socket) => {
    let pendingUser: string | null = null;
    let expecting: 'user' | 'pass' | null = null;
    const login = (user: string, pass: string) => {
      logins.push(`${user}:${pass}`);
      socket.write(
        user === options.user && pass === options.pass
          ? '235 2.7.0 Authentication successful\r\n'
          : '535 5.7.8 Authentication credentials invalid\r\n',
      );
    };
    socket.write(options.greeting ?? '220 fake.smtp ESMTP\r\n');
    if (options.greeting) {
      socket.end();
      return;
    }
    let buffer = '';
    socket.on('data', (chunk) => {
      buffer += chunk.toString('utf8');
      let index: number;
      while ((index = buffer.indexOf('\r\n')) >= 0) {
        const line = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        if (expecting === 'user') {
          pendingUser = decode(line);
          expecting = 'pass';
          socket.write(`334 ${Buffer.from('Password:').toString('base64')}\r\n`);
          continue;
        }
        if (expecting === 'pass') {
          expecting = null;
          login(pendingUser ?? '', decode(line));
          continue;
        }
        const [verb = '', ...rest] = line.split(' ');
        switch (verb.toUpperCase()) {
          case 'EHLO':
            socket.write('250-fake.smtp\r\n250 AUTH PLAIN LOGIN\r\n');
            break;
          case 'AUTH': {
            const [mechanism, initial] = rest;
            if (mechanism?.toUpperCase() === 'PLAIN' && initial) {
              const [, user = '', pass = ''] = decode(initial).split('\u0000');
              login(user, pass);
            } else {
              expecting = 'user';
              socket.write(`334 ${Buffer.from('Username:').toString('base64')}\r\n`);
            }
            break;
          }
          case 'QUIT':
            socket.end('221 Bye\r\n');
            break;
          default:
            socket.write('250 OK\r\n');
        }
      }
    });
    socket.on('error', () => {});
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as net.AddressInfo;
  return {
    port,
    logins,
    close: () => new Promise<void>((resolve) => server.close(() => resolve())),
  };
}

/** A loopback port nothing listens on. */
export async function closedPort(): Promise<number> {
  const server = net.createServer();
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as net.AddressInfo;
  await new Promise<void>((resolve) => server.close(() => resolve()));
  return port;
}
