/**
 * API Documentation Container
 * Dependency injection container for API documentation service
 */
import { Container } from 'inversify';
import { ApiDocsService } from './docs.service';
import { Logger } from '../../libs/services/logger.service';

let container: Container | null = null;

export class ApiDocsContainer {
  /**
   * Initialize the API docs container
   */
  static async initialize(): Promise<Container> {
    if (container) {
      return container;
    }

    container = new Container();

    // Bind Logger
    container
      .bind<Logger>('Logger')
      .toConstantValue(Logger.getInstance({ service: 'ApiDocsService' }));

    // Bind ApiDocsService
    container
      .bind<ApiDocsService>(ApiDocsService)
      .toSelf()
      .inSingletonScope();

    // Initialize the service
    const apiDocsService = container.get<ApiDocsService>(ApiDocsService);
    await apiDocsService.initialize();

    return container;
  }

  /**
   * Get the container instance
   */
  static getContainer(): Container {
    if (!container) {
      throw new Error('ApiDocsContainer not initialized');
    }
    return container;
  }

  /**
   * Dispose the container
   */
  static async dispose(): Promise<void> {
    if (container) {
      container.unbindAll();
      container = null;
    }
  }
}
