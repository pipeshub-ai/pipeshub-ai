import { JobsApi } from '@/lib/harness-jobs/jobs-api';
import type { PersonJobsKey } from '@/lib/harness-jobs/swr-keys';
import type { PersonRuns } from '@/lib/contracts/harness/generated/ext/pipeshub/api/person-runs';

/** The Jobs page's reads: the caller's own jobs across their chats, newest first (`GET …/agent-harness/runs?kind=job`). */
export const fetchPersonJobs = ([, , { limit }]: PersonJobsKey): Promise<PersonRuns> => JobsApi.personJobs({ limit });

export const fetchMorePersonJobs = (cursor: string, limit: number): Promise<PersonRuns> => JobsApi.personJobs({ cursor, limit });
