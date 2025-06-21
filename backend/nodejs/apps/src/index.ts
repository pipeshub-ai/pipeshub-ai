import 'reflect-metadata';
import { config } from 'dotenv';
import { Logger } from './libs/services/logger.service';

// Loads environment variables
config();
import { Application } from './app';

const app = new Application();
const logger = Logger.getInstance();

const gracefulShutdown = async (signal: string) => {
  logger.info(`Received ${signal}. Starting graceful shutdown...`);
  try {
    await app.stop();
    process.exit(0);
  } catch (error) {
    Logger.getInstance().error('Error during shutdown:', error);
    process.exit(1);
  }
};

process.on('SIGTERM', () => gracefulShutdown('SIGTERM'));
process.on('SIGINT', () => gracefulShutdown('SIGINT'));

(async () => {
  try {
    await app.initialize();
    await app.start();
  } catch (error) {
    logger.error('Failed to start application:', error);
    process.exit(1);
  }
})();
