import 'reflect-metadata';
import { expect } from 'chai';
import { Cluster, Redis } from 'ioredis';

import {
  buildRedisClient,
  clusterAwareScan,
  parseRedisNodes,
} from '../../../src/libs/services/redisClientFactory';
import { RedisConfig } from '../../../src/libs/types/redis.types';

const standaloneConfig: RedisConfig = {
  host: 'localhost',
  port: 6379,
  mode: 'standalone',
};

const clusterConfig: RedisConfig = {
  host: 'localhost',
  port: 7000,
  mode: 'cluster',
  nodes: [
    { host: '127.0.0.1', port: 7000 },
    { host: '127.0.0.1', port: 7001 },
  ],
};

describe('redisClientFactory', () => {
  describe('buildRedisClient', () => {
    it('returns a Redis instance in standalone mode', () => {
      const client = buildRedisClient(standaloneConfig, { lazyConnect: true });
      try {
        expect(client).to.be.instanceOf(Redis);
        expect(client).to.not.be.instanceOf(Cluster);
      } finally {
        // Disconnect without trying to reach a real server.
        (client as Redis).disconnect();
      }
    });

    it('returns a Cluster instance in cluster mode', () => {
      const client = buildRedisClient(clusterConfig, { lazyConnect: true });
      try {
        expect(client).to.be.instanceOf(Cluster);
      } finally {
        (client as Cluster).disconnect();
      }
    });

    it('throws when cluster mode is set but nodes are empty', () => {
      const bad: RedisConfig = {
        host: 'localhost',
        port: 7000,
        mode: 'cluster',
        nodes: [],
      };
      expect(() => buildRedisClient(bad, { lazyConnect: true })).to.throw(
        /requires nodes/i,
      );
    });

    it('defaults to standalone when mode is unset', () => {
      const noMode: RedisConfig = { host: 'localhost', port: 6379 };
      const client = buildRedisClient(noMode, { lazyConnect: true });
      try {
        expect(client).to.be.instanceOf(Redis);
      } finally {
        (client as Redis).disconnect();
      }
    });
  });

  describe('clusterAwareScan (shape only)', () => {
    // Real cluster behaviour is verified by integration tests behind
    // RUN_CLUSTER_TESTS=1. Here we just confirm the helper iterates every
    // node returned by `.nodes('master')` on a Cluster-shaped stub.
    it('iterates every master node and de-dupes results (scan)', async () => {
      let masterCalls = 0;
      const fakeNode = (keys: string[]) => ({
        scan: async (cursor: string) => {
          masterCalls += 1;
          return ['0', keys];
        },
      });
      const fakeCluster = {
        nodes: (role: string) => {
          expect(role).to.equal('master');
          return [
            fakeNode(['a', 'b']),
            fakeNode(['b', 'c']),
          ];
        },
      };
      const out = await clusterAwareScan(fakeCluster as any, 'pattern');
      expect(out.sort()).to.deep.equal(['a', 'b', 'c']);
      expect(masterCalls).to.equal(2);
    });
  });

  describe('parseRedisNodes', () => {
    it('parses host:port pairs and defaults the port', () => {
      expect(parseRedisNodes('a:7000, b:7001 ,c')).to.deep.equal([
        { host: 'a', port: 7000 },
        { host: 'b', port: 7001 },
        { host: 'c', port: 6379 },
      ]);
    });

    it('strips brackets from IPv6 literals', () => {
      expect(parseRedisNodes('[::1]:6379,fe80::1:7000')).to.deep.equal([
        { host: '::1', port: 6379 },
        { host: 'fe80::1', port: 7000 },
      ]);
    });

    it('throws on a non-numeric port', () => {
      expect(() => parseRedisNodes('host:abc')).to.throw(/non-numeric port/);
    });
  });
});
