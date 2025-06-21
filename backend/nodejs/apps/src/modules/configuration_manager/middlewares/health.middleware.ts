import { Response, NextFunction } from 'express';
import { Kafka } from 'kafkajs';
import Redis from 'ioredis';
import { MongoClient } from 'mongodb';
import { Database } from 'arangojs';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import net from 'net';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { Logger } from '../../../libs/services/logger.service';
import axios from 'axios';
import { MONGO_DB_NAME } from '../../../libs/enums/db.enum';

const logger = Logger.getInstance({
  service: 'Health Middleware',
});

// // ✅ Qdrant Configuration

const isBrokerReachable = async (broker: string): Promise<boolean> => {
  const [host, port] = broker.split(':');
  if (!port) {
    throw new BadRequestError('Port number not given');
  }
  return new Promise((resolve) => {
    const socket = net.createConnection(
      { host, port: parseInt(port), timeout: 3000 },
      () => {
        socket.end();
        resolve(true);
      },
    );

    socket.on('error', () => {
      resolve(false);
    });
  });
};

export const checkKafkaHealth = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  try {
    let brokers = req.body.brokers;

    // Ensure brokers is always an array
    if (typeof brokers === 'string') {
      brokers = [brokers];
    }

    if (!Array.isArray(brokers) || brokers.length === 0) {
      throw new BadRequestError(
        "Invalid brokers list: Must be a non-empty array of 'host:port' strings.",
      );
    }

    // Validate each broker
    const reachableBrokers = await Promise.all(
      brokers.map(async (broker) => ({
        broker,
        reachable: await isBrokerReachable(broker),
      })),
    );

    const workingBrokers = reachableBrokers
      .filter(({ reachable }) => reachable)
      .map(({ broker }) => broker);

    const failedBrokers = reachableBrokers
      .filter(({ reachable }) => !reachable)
      .map(({ broker }) => broker);

    if (workingBrokers.length === 0) {
      throw new BadRequestError(
        `No reachable Kafka brokers. Failed brokers: ${failedBrokers.join(', ')}`,
      );
    }

    if (failedBrokers.length > 0) {
      logger.warn(
        `⚠️ Some Kafka brokers are unreachable: ${failedBrokers.join(', ')}`,
      );
      const warningMessage = `Unreachable-Brokers detected : "${failedBrokers.join(', ')}"`;
      res.setHeader('warning', warningMessage);
    }

    logger.info('Connecting to Kafka brokers:', workingBrokers);

    const kafka = new Kafka({ brokers: workingBrokers });
    const kafkaAdmin = kafka.admin();
    await kafkaAdmin.connect();

    // Try listing topics to confirm the connection
    await kafkaAdmin.listTopics();
    await kafkaAdmin.disconnect();

    logger.info('✅ Kafka is active');
    req.body.brokers = workingBrokers;
    next();
  } catch (error) {
    logger.error('❌ Kafka connection error:', error);
    next(error);
  }
};

export const checkRedisHealth = async (
  req: AuthenticatedUserRequest,
  _res: Response,
  next: NextFunction,
) => {
  try {
    const { host, port, password, tls } = req.body;

    // Validate required fields
    if (!host || !port) {
      return next(new BadRequestError('Missing Redis host or port.'));
    }

    // Construct Redis URL
    let redisUrl = `redis://`;
    if (password) redisUrl += `:${password}@`;
    redisUrl += `${host}:${port}`;
    if (tls) redisUrl = redisUrl.replace('redis://', 'rediss://');

    // Create Redis client
    const redisClient = new Redis(redisUrl, {
      connectTimeout: 5000, // Timeout in 5 seconds
      maxRetriesPerRequest: 1, // Retry only once
      retryStrategy: (times) => {
        if (times >= 2) return null; // Stop retrying after 2 attempts
        return Math.min(times * 500, 3000); // Retry delay (500ms, 1000ms)
      },
    });

    // Wait for the connection to be established or fail
    await new Promise<void>((resolve, reject) => {
      redisClient.once('ready', () => {
        redisClient.quit(); // Close connection after checking
        resolve();
      });

      redisClient.once('error', (error) => {
        redisClient.quit(); // Ensure client is closed
        reject(new BadRequestError('Failed to connect to Redis', error));
      });
    });

    next(); // Move to next middleware if Redis is healthy
  } catch (error) {
    next(error); // Pass error to Express error handler
  }
};

// 🛠 Qdrant Health Check
export const checkQdrantHealth = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  try {
    let { port, host, apiKey } = req.body;

    if (!host || !port) {
      throw new BadRequestError('Missing host and port');
    }

    port = Number(port);
    if (isNaN(port) || port <= 0) {
      throw new BadRequestError(
        `Invalid port: ${port}. Expected a valid port number.`,
      );
    }

    // ✅ Check if the host is localhost (no API key needed)
    const isLocal = host.includes('localhost') || host === '127.0.0.1';

    if (isLocal && apiKey) {
      logger.warn("API key provided for localhost, but it's not required.");
      res.setHeader('warning', 'localhost does not require an API key');
      apiKey = undefined; // Remove API key if localhost
    }

    // ✅ Ensure proper protocol
    const protocol = isLocal ? 'http' : 'https';
    const httpUrl = `${protocol}://${host}:${port}/healthz`;

    logger.info(`🔍 Checking Qdrant health at: ${httpUrl} (HTTP) `);
    try {
      const httpResponse = await axios.get(httpUrl);
      if (httpResponse.status !== 200) {
        throw new BadRequestError('Qdrant HTTP health check failed');
      }
    } catch (axiosError: any) {
      if (axiosError.code === 'ECONNREFUSED') {
        throw new BadRequestError(
          `Cannot connect to Qdrant at ${httpUrl}. Connection refused.`,
        );
      } else if (axiosError.code === 'ENOTFOUND') {
        throw new BadRequestError(
          `Invalid host: ${host}. Could not resolve hostname.`,
        );
      } else if (axiosError.message.includes('Parse Error: Expected HTTP/')) {
        throw new BadRequestError(
          `Invalid response from ${httpUrl}. Ensure the port is correct and the service is running.`,
        );
      } else {
        throw new BadRequestError(
          `Qdrant request failed: ${axiosError.message}`,
        );
      }
    }

    logger.info('✅ Qdrant is active and reachable via both HTTP.');
    next();
  } catch (error) {
    next(error);
  }
};

// 🛠 MongoDB Health Check

export const checkMongoHealth = async (
  req: AuthenticatedUserRequest,
  _res: Response,
  next: NextFunction,
) => {
  const mongoURI = req.body.uri;
  const dbName = MONGO_DB_NAME;

  if (!mongoURI || !dbName) {
    throw new BadRequestError('Missing MongoDB URI or Database Name');
  }

  const client = new MongoClient(mongoURI); // Removed outdated options

  try {
    await client.connect();

    const db = client.db(dbName);
    const admin = db.admin();
    const status = await admin.command({ ping: 1 });
    if (status.ok !== 1) {
      throw new BadRequestError('Database is not responding');
    }
    await client.close();
    next();
  } catch (error: any) {
    if (
      error.message.includes('Authentication failed') ||
      error.message.includes('bad auth')
    ) {
      return next(
        new BadRequestError(
          'MongoDB authentication failed. Check your uri and password.',
        ),
      );
    } else if (error.message.includes('ECONNREFUSED')) {
      return next(
        new BadRequestError(
          'Could not connect to MongoDB. Ensure the server is running and the URI is correct.',
        ),
      );
    } else if (error.message.includes('ENOTFOUND')) {
      return next(
        new BadRequestError(
          'Invalid MongoDB URI. Hostname could not be resolved.',
        ),
      );
    } else {
      return next(
        new BadRequestError(`MongoDB connection error: ${error.message}`),
      );
    }
  } finally {
    await client.close().catch(() => {}); // Ensure client closes even if an error occurs
  }
};

// 🛠 ArangoDB Health Check
export const checkArangoHealth = async (
  req: AuthenticatedUserRequest,
  _res: Response,
  next: NextFunction,
) => {
  const { url, username, password } = req.body;
  const arangoDB = new Database({ url, timeout: 2000 });
  if (username && password) {
    arangoDB.useBasicAuth(username, password);
  }
  try {
    await arangoDB.exists();
    next();
  } catch (error: any) {
    if (error.message.includes('Fetch failed')) {
      return next(
        new BadRequestError(
          `Failed to connect to ArangoDB at ${url}. Ensure the server is running and accessible.`,
        ),
      );
    } else if (error.message.includes('401')) {
      return next(
        new BadRequestError(
          'ArangoDB authentication failed. Check your username and password.',
        ),
      );
    } else if (error.message.includes('ECONNREFUSED')) {
      return next(
        new BadRequestError(
          `Connection refused. Ensure ArangoDB is running at ${url}.`,
        ),
      );
    } else if (error.message.includes('ENOTFOUND')) {
      return next(
        new BadRequestError(
          `Invalid ArangoDB URL: ${url}. Hostname could not be resolved.`,
        ),
      );
    } else {
      return next(
        new BadRequestError(`ArangoDB connection error: ${error.message}`),
      );
    }
  }
};
