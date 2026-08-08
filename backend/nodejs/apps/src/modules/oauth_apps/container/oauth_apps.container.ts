import { Container } from 'inversify';
import { ConfigurationManagerConfig } from '../../configuration_manager/config/config';

export class OAuthAppsContainer {
  private static instance: Container;

  static async initialize(
    _configurationManagerConfig: ConfigurationManagerConfig,
  ): Promise<Container> {
    this.instance = new Container();
    return this.instance;
  }

  static getInstance(): Container {
    if (!this.instance) {
      throw new Error('OAuthAppsContainer not initialized');
    }
    return this.instance;
  }

  static async dispose(): Promise<void> {
    if (this.instance) {
      this.instance = null!;
    }
  }
}
