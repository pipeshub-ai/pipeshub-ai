import { injectable } from 'inversify';
import mongoose, { Connection, ConnectOptions } from 'mongoose';
import { ConnectionError } from '../errors/database.errors';
import { BadRequestError, InternalServerError } from '../errors/http.errors';
import { Logger } from './logger.service';

const logger = Logger.getInstance({
  service: 'MongoDB Service',
});

export interface MongooseConfig {
  uri: string;
  db: string;
  options?: ConnectOptions;
}

// List of all collections that need to be pre-created for DocumentDB compatibility
// DocumentDB does not support creating namespaces within multi-document transactions
const REQUIRED_COLLECTIONS = [
  'orgAuthConfig',
  'userGroups',
  'users',
  'userCredentials',
  'org',
  'org-logos',
  'user-dps',
  'userActivities',
  'oauthRefreshTokens',
  'oauthAccessTokens',
  'oauthApps',
  'authorizationCodes',
  'conversations',
  'agentconversations',
  'connectorsConfig',
  'citation',
  'citations',
  'documents',
  'mailInfo',
  'search',
  'token-references',
  'counters',
  'crawlingManagerConfig',
  'notifications',
];

@injectable()
export class MongoService {
  private connection: Connection | null = null;
  private isInitialized: boolean = false;

  constructor(private config: MongooseConfig) {
    // Set mongoose configuration options
    mongoose.set('strictQuery', true);
    mongoose.set('debug', process.env.NODE_ENV === 'development');
  }

  public async initialize(): Promise<void> {
    if (this.isInitialized) {
      logger.warn('MongoDB connection already initialized');
      return;
    }

    try {
      const { uri, db, options } = this.config;
      const defaultOptions = {
        serverSelectionTimeoutMS: 5000,
        socketTimeoutMS: 45000,
        family: 4,
        maxPoolSize: 10,
      };
      // Configure connection options
      const connectOptions: ConnectOptions = {
        dbName: db,
        ...(options ?? defaultOptions),
        autoCreate: true,
        autoIndex: true,
      };

      // Connect to MongoDB
      await mongoose.connect(uri, connectOptions);
      this.connection = mongoose.connection;

      // Set up connection event handlers
      this.setupConnectionHandlers();

      // Pre-create collections for compatibility with other dbs that do not support autocreate
      await this.ensureCollections();

      this.isInitialized = true;
      logger.info(`Connected to MongoDB database: ${db}`);
    } catch (error) {
      const err =
        error instanceof Error ? error : new Error('Unknown error occurred');
      logger.error('Failed to connect to MongoDB:', err.message);
      throw new ConnectionError('Failed to connect to MongoDB', err);
    }
  }

  private setupConnectionHandlers(): void {
    if (!this.connection) return;

    this.connection.on('connected', () => {
      logger.info('Mongoose connection established');
    });

    this.connection.on('disconnected', () => {
      logger.info('Mongoose connection disconnected');
    });

    this.connection.on('error', (error) => {
      logger.error('Mongoose connection error:', error);
    });

    // Handle process termination
    process.on('SIGINT', this.gracefulShutdown.bind(this));
    process.on('SIGTERM', this.gracefulShutdown.bind(this));
  }

  /**
   * Pre-creates all required collections for DocumentDB compatibility.
   * DocumentDB does not support creating collections within multi-document transactions,
   * so we ensure all collections exist before any transactions are attempted.
   */
  private async ensureCollections(): Promise<void> {
    if (!this.connection?.db) {
      logger.warn('Cannot ensure collections: database connection not available');
      return;
    }

    try {
      const db = this.connection.db;
      const existingCollections = await db.listCollections().toArray();
      const existingNames = new Set(existingCollections.map((c) => c.name));

      const creationPromises = REQUIRED_COLLECTIONS.filter(
        (name) => !existingNames.has(name),
      ).map(async (name) => {
        try {
          await db.createCollection(name);
          logger.info(`Created collection: ${name}`);
        } catch (error) {
          // Ignore "collection already exists" errors (code 48)
          // This can happen in race conditions
          if ((error as { code?: number }).code !== 48) {
            throw error;
          }
        }
      });

      await Promise.all(creationPromises);
      logger.info('All required collections ensured');
    } catch (error) {
      const err =
        error instanceof Error ? error : new Error('Unknown error occurred');
      logger.error('Failed to ensure collections:', err.message);
      // Don't throw - allow app to continue, collections might already exist
    }
  }

  private async gracefulShutdown(): Promise<void> {
    try {
      await this.destroy();
    } catch (error) {
      logger.error('Error during graceful shutdown:', error);
      process.exit(1);
    }
  }

  public async destroy(): Promise<void> {
    if (!this.isInitialized) {
      logger.warn('MongoDB connection not initialized');
      return;
    }

    try {
      await mongoose.disconnect();
      this.connection = null;
      this.isInitialized = false;
      logger.info('Disconnected from MongoDB');
    } catch (error) {
      const err =
        error instanceof Error ? error : new Error('Unknown error occurred');
      logger.error('Failed to disconnect from MongoDB:', err.message);
      throw new InternalServerError('Failed to disconnect from MongoDB', err);
    }
  }

  // Allowed Clean Database in Test Environment
  public async cleanDatabase(): Promise<void> {
    if (process.env.NODE_ENV !== 'test') {
      throw new BadRequestError(
        'Database cleaning is only allowed in test environment',
      );
    }

    if (!this.connection?.db) {
      throw new ConnectionError('MongoDB connection not initialized');
    }

    try {
      const db = this.connection.db;
      const collections = await db.listCollections().toArray();
      const deletePromises = collections.map(async ({ name }) => {
        if (name !== 'system.indexes') {
          await db.collection(name).deleteMany({});
        }
      });

      await Promise.all(deletePromises);
      logger.info('Test database cleaned successfully');
    } catch (error) {
      const err =
        error instanceof Error ? error : new Error('Unknown error occurred');
      logger.error('Failed to clean the database:', err.message);
      throw new InternalServerError('Failed to clean the database', err);
    }
  }

  public async healthCheck(): Promise<boolean> {
    if (!this.connection?.db) {
      return false;
    }

    try {
      await this.connection.db.command({ ping: 1 });
      return true;
    } catch (error) {
      logger.error('MongoDB health check failed:', error);
      return false;
    }
  }

  public getConnection(): Connection {
    if (!this.connection || !this.isInitialized) {
      throw new ConnectionError(
        'MongoDB connection not initialized. Call initialize() first.',
      );
    }
    return this.connection;
  }

  public isConnected(): boolean {
    return this.isInitialized && this.connection?.readyState === 1;
  }
}
