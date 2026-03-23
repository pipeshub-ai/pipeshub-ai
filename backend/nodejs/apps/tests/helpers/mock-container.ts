import { Container } from 'inversify';

/**
 * Creates a lightweight Inversify container with the given mock bindings.
 * Usage: createTestContainer({ 'Logger': mockLogger, 'ServiceName': mockService })
 */
export function createTestContainer(bindings: Record<string, any> = {}): Container {
  const container = new Container();
  for (const [key, value] of Object.entries(bindings)) {
    container.bind(key).toConstantValue(value);
  }
  return container;
}

/**
 * Rebinds a service in an existing container (useful for overriding in specific tests).
 */
export function rebindInContainer(container: Container, key: string, value: any): void {
  if (container.isBound(key)) {
    container.rebind(key).toConstantValue(value);
  } else {
    container.bind(key).toConstantValue(value);
  }
}
