/**
 * BGUI-8: a sub-agent its coordinator gave part of the request has a goal that quotes the person's request ahead of
 * its own task (`harness_core/kernel/agent/user_request.py`, `with_user_request`). Its brief, its title on the Jobs
 * page and in a request's view, and how its goal started read the task alone, never the quote or the headers.
 */
import { expect } from 'chai';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { DELEGATED_TASK_HEADER, delegatedTask, jobTitle } from '../../../src/modules/agent-harness/domain/job-brief';

const QUOTED =
  "## The user's request (verbatim, for context: work only on your delegated task)\n\n" +
  'Close the quarter: summarise the board notes, file the ticket and draft the investor update.\n\n' +
  `${DELEGATED_TASK_HEADER}\n\nDraft the investor update`;

describe('BGUI-8: a sub-agent handed part of the request is named by its own task', () => {
  it('reads the task after the header, and a goal without one as it is', () => {
    expect(delegatedTask(QUOTED)).to.equal('Draft the investor update');
    expect(delegatedTask('Summarise the release notes.')).to.equal('Summarise the release notes.');
    expect(delegatedTask(null)).to.equal(null);
    expect(delegatedTask(`${DELEGATED_TASK_HEADER}\n\n `)).to.equal(`${DELEGATED_TASK_HEADER}\n\n `);
  });

  it("titles the job by its task, never by the quoted request's header", () => {
    expect(jobTitle(QUOTED)).to.equal('Draft the investor update');
  });

  it('keeps the header the Python kernel writes', () => {
    const kernel = readFileSync(
      path.resolve(__dirname, '../../../../../python/harness_core/kernel/agent/user_request.py'),
      'utf8',
    );
    expect(kernel).to.include(`_DELEGATED_TASK_HEADER = "${DELEGATED_TASK_HEADER}"`);
  });
});
