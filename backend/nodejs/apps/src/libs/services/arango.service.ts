import { injectable } from 'inversify';
import { Database } from 'arangojs';
import { InternalServerError } from '../errors/http.errors';
import { Logger } from './logger.service';
import { ConnectionError } from '../errors/database.errors';

const logger = Logger.getInstance({
  service: 'ArangoDB Service',
});

export interface ArangoConfig {
  url: string;
  db: string;
  username?: string;
  password?: string;
}

@injectable()
export class ArangoService {
  private db: Database | null = null;
  private isInitialized: boolean = false;
  private config: ArangoConfig;

  constructor(config: ArangoConfig) {
    this.config = {
      ...config,
    };
  }

  async initialize(): Promise<void> {
    if (this.isInitialized) {
      logger.warn('ArangoDB connection already initialized');
      return;
    }

    try {
      const { url, username, password } = this.config;
      const databaseName = this.config.db;
      // First connect to _system database to be able to create our target database
      this.db = new Database({
        url,
        timeout: 2000,
      });

      if (username && password) {
        this.db.useBasicAuth(username, password);
      }

      // Test the connection
      await this.db.exists();

      // Check if database exists and create it if it doesn't
      const sysDb = this.db;
      const dbExists = await sysDb.database(databaseName).exists();

      if (!dbExists) {
        logger.info(`Database ${databaseName} does not exist, creating it...`);
        await sysDb.createDatabase(databaseName);
        logger.info(`Database ${databaseName} created successfully`);
      }
      // Now reconnect to the specific database
      this.db = new Database({
        url,
        databaseName,
        timeout: 2000,
      });

      if (username && password) {
        this.db.useBasicAuth(username, password);
      }

      // Setup graceful shutdown handlers
      process.on('SIGINT', this.gracefulShutdown.bind(this));
      process.on('SIGTERM', this.gracefulShutdown.bind(this));

      this.isInitialized = true;
      // Set up connection event handlers
      this.setupConnectionHandlers();
      logger.info(`Connected to ArangoDB database: ${databaseName}`);
    } catch (error) {
      const err =
        error instanceof Error ? error : new Error('Unknown error occurred');
      logger.error(`Failed to connect to ArangoDB: ${err.message}`);
      throw new ConnectionError('Failed to connect to ArangoDB', err);
    }
  }

  private setupConnectionHandlers(): void {
    if (!this.db) return;

    // Log that the connection is "established"
    logger.info('ArangoDB connection established');

    // Set up a periodic health check to simulate disconnection detection.
    setInterval(async () => {
      await this.healthCheck();
    }, 60000); // check every 60 seconds
  }

  private async gracefulShutdown(): Promise<void> {
    try {
      await this.destroy();
    } catch (error) {
      logger.error('Error during graceful shutdown:', error);
    }
  }

  public async destroy(): Promise<void> {
    if (!this.isInitialized || !this.db) {
      logger.warn('ArangoDB connection not initialized');
      return;
    }

    try {
      // Since arangojs is HTTP–based, we simply nullify our reference.
      this.db = null;
      this.isInitialized = false;
      logger.info('Disconnected from ArangoDB');
    } catch (error) {
      const err =
        error instanceof Error ? error : new Error('Unknown error occurred');
      logger.error(`Failed to disconnect from ArangoDB: ${err.message}`);
      throw new InternalServerError('Failed to disconnect from ArangoDB', err);
    }
  }

  public async cleanDatabase(): Promise<void> {
    if (process.env.NODE_ENV !== 'test') {
      throw new InternalServerError(
        'Database cleaning is only allowed in test environment',
      );
    }
    if (!this.isInitialized || !this.db) {
      throw new InternalServerError('ArangoDB connection not initialized');
    }

    try {
      const collections = await this.db.listCollections();
      for (const collectionInfo of collections) {
        if (!collectionInfo.isSystem) {
          const collection = this.db.collection(collectionInfo.name);
          await collection.truncate();
        }
      }
      logger.info('Test database cleaned successfully');
    } catch (error) {
      const err =
        error instanceof Error ? error : new Error('Unknown error occurred');
      logger.error(`Failed to clean the database: ${err.message}`);
      throw new InternalServerError('Failed to clean the database', err);
    }
  }

  public async healthCheck(): Promise<boolean> {
    if (!this.isInitialized || !this.db) {
      return false;
    }
    try {
      await this.db.version();
      return true;
    } catch (error) {
      logger.error('ArangoDB health check failed:', error);
      return false;
    }
  }

  public getConnection(): Database {
    if (!this.isInitialized || !this.db) {
      throw new InternalServerError(
        'ArangoDB connection not initialized. Call initialize() first.',
      );
    }
    return this.db;
  }

  public async isConnected(): Promise<boolean> {
    return this.isInitialized && (await this.healthCheck());
  }
}
