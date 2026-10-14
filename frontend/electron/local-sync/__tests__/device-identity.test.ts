import test from 'node:test';
import * as assert from 'node:assert/strict';
import * as crypto from 'crypto';
import {
  DeviceIdentityError,
  parseIoregPlatformUuid,
  parseRegMachineGuid,
  resolveDeviceIdentity,
  type ExecFileFn,
  type ReadFileFn,
} from '../persistence/device-identity';

const REG_OUTPUT = [
  '',
  'HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Cryptography',
  '    MachineGuid    REG_SZ    3F2504E0-4F89-11D3-9A0C-0305E82C3301',
  '',
].join('\r\n');

const IOREG_OUTPUT = [
  '+-o J316sAP  <class IOPlatformExpertDevice, id 0x100000254, registered, matched, active, busy 0 (0 ms), retain 44>',
  '  {',
  '    "IOPlatformSerialNumber" = "C02XXXXXXXXX"',
  '    "IOPlatformUUID" = "7A1C2B3D-1111-2222-3333-444455556666"',
  '  }',
].join('\n');

function sha256(value: string): string {
  return crypto.createHash('sha256').update(value).digest('hex');
}

const neverExec: ExecFileFn = async () => assert.fail('execFile must not be called');
const neverRead: ReadFileFn = async () => assert.fail('readFile must not be called');

function enoent(filePath: string): Error {
  return Object.assign(new Error(`ENOENT: no such file or directory, open '${filePath}'`), { code: 'ENOENT' });
}

test('parseRegMachineGuid reads the value after REG_SZ', () => {
  assert.equal(parseRegMachineGuid(REG_OUTPUT), '3F2504E0-4F89-11D3-9A0C-0305E82C3301');
  assert.equal(parseRegMachineGuid('ERROR: The system was unable to find the specified registry key or value.'), null);
  assert.equal(parseRegMachineGuid('    MachineGuid    REG_SZ    \r\n'), null);
});

test('parseIoregPlatformUuid reads IOPlatformUUID', () => {
  assert.equal(parseIoregPlatformUuid(IOREG_OUTPUT), '7A1C2B3D-1111-2222-3333-444455556666');
  assert.equal(parseIoregPlatformUuid('"IOPlatformSerialNumber" = "C02XXXXXXXXX"'), null);
});

test('win32 hashes the registry MachineGuid via reg.exe', async () => {
  const calls: Array<{ file: string; args: string[] }> = [];
  const identity = await resolveDeviceIdentity({
    platform: 'win32',
    windir: 'D:\\Windows',
    execFile: async (file, args) => {
      calls.push({ file, args });
      return REG_OUTPUT;
    },
    readFile: neverRead,
    hostname: () => 'WIN-HOST',
  });

  assert.deepEqual(calls, [{
    file: 'D:\\Windows\\System32\\reg.exe',
    args: ['QUERY', 'HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Cryptography', '/v', 'MachineGuid'],
  }]);
  assert.equal(identity.deviceId, sha256('3f2504e0-4f89-11d3-9a0c-0305e82c3301'));
  assert.equal(identity.deviceName, 'WIN-HOST');
});

test('win32 throws with no fallback when reg.exe fails', async () => {
  await assert.rejects(
    resolveDeviceIdentity({
      platform: 'win32',
      windir: 'C:\\Windows',
      execFile: async () => {
        throw new Error('spawn EACCES');
      },
      readFile: neverRead,
      hostname: () => 'WIN-HOST',
    }),
    (error: unknown) => {
      assert.ok(error instanceof DeviceIdentityError);
      assert.match(error.message, /reg\.exe/);
      assert.match(error.message, /spawn EACCES/);
      return true;
    },
  );
});

test('win32 throws when the output has no MachineGuid', async () => {
  await assert.rejects(
    resolveDeviceIdentity({
      platform: 'win32',
      execFile: async () => 'ERROR: The system was unable to find the specified registry key or value.',
      readFile: neverRead,
      hostname: () => 'WIN-HOST',
    }),
    /MachineGuid not found/,
  );
});

test('darwin hashes IOPlatformUUID via ioreg', async () => {
  const identity = await resolveDeviceIdentity({
    platform: 'darwin',
    execFile: async (file, args) => {
      assert.equal(file, '/usr/sbin/ioreg');
      assert.deepEqual(args, ['-rd1', '-c', 'IOPlatformExpertDevice']);
      return IOREG_OUTPUT;
    },
    readFile: neverRead,
    hostname: () => 'mac.local',
  });

  assert.equal(identity.deviceId, sha256('7a1c2b3d-1111-2222-3333-444455556666'));
  assert.equal(identity.deviceName, 'mac.local');
});

test('darwin throws with no fallback when ioreg fails', async () => {
  await assert.rejects(
    resolveDeviceIdentity({
      platform: 'darwin',
      execFile: async () => {
        throw new Error('ENOENT');
      },
      readFile: neverRead,
      hostname: () => 'mac.local',
    }),
    (error: unknown) => {
      assert.ok(error instanceof DeviceIdentityError);
      assert.match(error.message, /\/usr\/sbin\/ioreg/);
      return true;
    },
  );
});

test('linux prefers /etc/machine-id', async () => {
  const read: string[] = [];
  const identity = await resolveDeviceIdentity({
    platform: 'linux',
    execFile: neverExec,
    readFile: async (filePath) => {
      read.push(filePath);
      return 'abc123\n';
    },
    hostname: () => 'linux-box',
  });

  assert.deepEqual(read, ['/etc/machine-id']);
  assert.equal(identity.deviceId, sha256('abc123'));
  assert.equal(identity.deviceName, 'linux-box');
});

test('linux falls back to the dbus machine id when /etc/machine-id is missing or empty', async () => {
  for (const etc of [async () => { throw enoent('/etc/machine-id'); }, async () => '  \n']) {
    const identity = await resolveDeviceIdentity({
      platform: 'linux',
      execFile: neverExec,
      readFile: async (filePath) => (filePath === '/etc/machine-id' ? etc() : 'dbus-id\n'),
      hostname: () => 'linux-box',
    });
    assert.equal(identity.deviceId, sha256('dbus-id'));
  }
});

test('linux falls back to the hostname, then throws', async () => {
  const readFile: ReadFileFn = async (filePath) => {
    throw enoent(filePath);
  };

  const identity = await resolveDeviceIdentity({
    platform: 'linux',
    execFile: neverExec,
    readFile,
    hostname: () => 'Linux-Box',
  });
  assert.equal(identity.deviceId, sha256('linux-box'));

  await assert.rejects(
    resolveDeviceIdentity({ platform: 'linux', execFile: neverExec, readFile, hostname: () => '' }),
    (error: unknown) => {
      assert.ok(error instanceof DeviceIdentityError);
      assert.match(error.message, /\/etc\/machine-id/);
      assert.match(error.message, /\/var\/lib\/dbus\/machine-id/);
      return true;
    },
  );
});

test('unsupported platforms throw', async () => {
  await assert.rejects(
    resolveDeviceIdentity({ platform: 'freebsd', execFile: neverExec, readFile: neverRead, hostname: () => 'bsd' }),
    DeviceIdentityError,
  );
});

test('the same raw id always yields the same deviceId regardless of case and whitespace', async () => {
  const resolveWith = (raw: string) => resolveDeviceIdentity({
    platform: 'linux',
    execFile: neverExec,
    readFile: async () => raw,
    hostname: () => 'host',
  });

  const a = await resolveWith('ABCDEF0123');
  const b = await resolveWith('  abcdef0123\n');
  const c = await resolveWith('abcdef0123');
  assert.equal(a.deviceId, b.deviceId);
  assert.equal(b.deviceId, c.deviceId);
  assert.match(a.deviceId, /^[0-9a-f]{64}$/);
  assert.notEqual(a.deviceId, (await resolveWith('abcdef0124')).deviceId);
});
