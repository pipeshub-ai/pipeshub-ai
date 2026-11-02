/** A throw-away MongoDB replica set (one member) for the migrator tests. `MIGRATE_TEST_MONGOD` names the `mongod` binary. */
import { ChildProcess, spawn } from 'node:child_process';
import { existsSync, mkdirSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { MongoClient } from 'mongodb';
import { freePort } from '../../libs/db/pg-harness';

export const MONGOD = process.env.MIGRATE_TEST_MONGOD ?? '';
export const haveMongod = MONGOD !== '' && existsSync(MONGOD);

export interface TestMongo {
  uri: string;
  port: number;
  client: MongoClient;
  stop(): Promise<void>;
}

export async function startMongo(work: string, replSet = true): Promise<TestMongo> {
  const root = join(work, `mongo-${process.pid}-${Date.now()}`);
  mkdirSync(root, { recursive: true });
  const port = await freePort(Number(process.env.PG_TEST_PORT_BASE ?? 61000) + 150 + Math.floor(Math.random() * 40));
  const proc: ChildProcess = spawn(
    MONGOD,
    ['--dbpath', root, '--port', String(port), '--bind_ip', '127.0.0.1', '--wiredTigerCacheSizeGB', '0.25', '--setParameter', 'enableTestCommands=1', ...(replSet ? ['--replSet', 'rs0'] : [])],
    { stdio: 'ignore' },
  );
  let client: MongoClient | undefined;
  for (let i = 0; i < 100; i++) {
    try {
      client = await new MongoClient(`mongodb://127.0.0.1:${port}/?directConnection=true`, { serverSelectionTimeoutMS: 500, appName: 'test-harness', driverInfo: { name: 'mongosh' } }).connect();
      break;
    } catch {
      await new Promise((r) => setTimeout(r, 200));
    }
  }
  const stop = async (): Promise<void> => {
    await client?.close().catch(() => undefined);
    // a mongod that has already exited never emits 'exit' again
    if (proc.exitCode === null && proc.signalCode === null) {
      const exited = new Promise((r) => proc.once('exit', r));
      proc.kill('SIGTERM');
      await exited;
    }
    rmSync(root, { recursive: true, force: true });
  };
  try {
    if (!client) throw new Error('mongod did not start');
    if (replSet) {
      await client.db('admin').command({ replSetInitiate: { _id: 'rs0', members: [{ _id: 0, host: `127.0.0.1:${port}` }] } });
      for (let i = 0; i < 100; i++) {
        const h = await client.db('admin').command({ hello: 1 });
        if (h.isWritablePrimary) break;
        await new Promise((r) => setTimeout(r, 200));
      }
    }
  } catch (e) {
    await stop();
    throw e;
  }
  return { uri: `mongodb://127.0.0.1:${port}/?directConnection=true`, port, client, stop };
}
