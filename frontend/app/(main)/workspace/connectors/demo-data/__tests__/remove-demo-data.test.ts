import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ConnectorsApi } from '../../api';
import { UsersApi } from '@/app/(main)/workspace/users/api';
import type { User } from '@/app/(main)/workspace/users/types';
import { findSampleAccounts, removeDemoData } from '../remove-demo-data';

vi.mock('../../api', () => ({
  ConnectorsApi: { deleteConnectorInstance: vi.fn(), getConnectorInstance: vi.fn() },
}));
vi.mock('@/app/(main)/workspace/users/api', () => ({
  UsersApi: { listUsers: vi.fn(), deleteUser: vi.fn() },
}));

const deleteConnectorInstance = vi.mocked(ConnectorsApi.deleteConnectorInstance);
const getConnectorInstance = vi.mocked(ConnectorsApi.getConnectorInstance);

/** What the API client rejects with: a processed error carrying the HTTP status. */
function apiError(statusCode: number, message = 'failed') {
  return { type: statusCode === 404 ? 'NOT_FOUND' : 'CONFLICT', message, statusCode };
}
const listUsers = vi.mocked(UsersApi.listUsers);
const deleteUser = vi.mocked(UsersApi.deleteUser);

function user(email: string, userId: string, name?: string): User {
  return { id: `g-${userId}`, userId, email, name, hasLoggedIn: true, isActive: true } as User;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('findSampleAccounts', () => {
  it('returns accounts on the reserved demo domain, by their Mongo id', async () => {
    listUsers.mockResolvedValue({
      users: [
        user('alice@acme-demo.example', 'm1', 'Alice Chen'),
        user('bob@acme-demo.example', 'm2', 'Bob Okafor'),
        // The search is a substring match, so filter precisely afterwards.
        user('someone@acme-demo.example.com', 'm3'),
        user('admin@company.com', 'm4'),
      ],
      totalCount: 4,
    });

    const accounts = await findSampleAccounts('admin@company.com');

    expect(accounts).toEqual([
      { userId: 'm1', email: 'alice@acme-demo.example', name: 'Alice Chen' },
      { userId: 'm2', email: 'bob@acme-demo.example', name: 'Bob Okafor' },
    ]);
  });

  it('never offers to delete the person doing the removal', async () => {
    listUsers.mockResolvedValue({
      users: [user('alice@acme-demo.example', 'm1'), user('bob@acme-demo.example', 'm2')],
      totalCount: 2,
    });

    const accounts = await findSampleAccounts('Alice@Acme-Demo.example');

    expect(accounts.map((a) => a.email)).toEqual(['bob@acme-demo.example']);
  });
});

describe('removeDemoData', () => {
  const alice = { userId: 'm1', email: 'alice@acme-demo.example' };
  const bob = { userId: 'm2', email: 'bob@acme-demo.example' };

  it('deletes the connector first, then the chosen accounts', async () => {
    const order: string[] = [];
    deleteConnectorInstance.mockImplementation(async (id) => {
      order.push(`connector:${id}`);
      return {} as never;
    });
    deleteUser.mockImplementation(async (id) => {
      order.push(`user:${id}`);
    });

    const result = await removeDemoData(['demo'], [alice, bob]);

    expect(order).toEqual(['connector:demo', 'user:m1', 'user:m2']);
    expect(result.failedAccounts).toEqual([]);
  });

  it('touches no account when the connector cannot be deleted', async () => {
    deleteConnectorInstance.mockRejectedValue(new Error('an agent still uses it'));

    await expect(removeDemoData(['demo'], [alice])).rejects.toThrow('an agent still uses it');
    expect(deleteUser).not.toHaveBeenCalled();
  });

  it('picks up after a partial failure: a connector already being deleted counts as done', async () => {
    // Deleting is not repeatable; the retry of an accepted delete answers 409.
    deleteConnectorInstance.mockImplementation(async (id) => {
      if (id === 'demo-1') throw apiError(409, 'Connector deletion is already in progress');
      return {} as never;
    });
    getConnectorInstance.mockResolvedValue({ status: 'DELETING' } as never);
    deleteUser.mockResolvedValue(undefined);

    const result = await removeDemoData(['demo-1', 'demo-2'], [alice]);

    expect(deleteConnectorInstance).toHaveBeenCalledWith('demo-2');
    expect(deleteUser).toHaveBeenCalledWith('m1');
    expect(result.failedAccounts).toEqual([]);
  });

  it('treats a connector that is already gone as done', async () => {
    deleteConnectorInstance.mockRejectedValueOnce(apiError(404)).mockResolvedValueOnce({} as never);

    await removeDemoData(['gone', 'demo-2'], []);

    expect(deleteConnectorInstance).toHaveBeenCalledWith('demo-2');
    expect(getConnectorInstance).not.toHaveBeenCalled();
  });

  it('still stops on a 409 that is a refusal, such as an agent using the connector', async () => {
    deleteConnectorInstance.mockRejectedValue(apiError(409, 'An agent still uses this connector'));
    getConnectorInstance.mockResolvedValue({ status: 'IDLE' } as never);

    await expect(removeDemoData(['demo-1'], [alice])).rejects.toMatchObject({ statusCode: 409 });
    expect(deleteUser).not.toHaveBeenCalled();
  });

  it('reports accounts it could not delete instead of failing the whole removal', async () => {
    deleteConnectorInstance.mockResolvedValue({} as never);
    deleteUser.mockImplementation(async (id) => {
      if (id === 'm1') throw new Error('403');
    });

    const result = await removeDemoData(['demo'], [alice, bob]);

    expect(result.failedAccounts).toEqual([alice]);
    expect(deleteUser).toHaveBeenCalledWith('m2');
  });
});
