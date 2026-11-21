import * as fs from 'fs';
import { failpoint, registry } from '../failpoints';

// fs.writeSync: stdout pipes are async on macOS and would lose lines on SIGKILL.
const out = (line: string): void => {
  fs.writeSync(1, `${line}\n`);
};

const path = process.argv[2];
if (!path) throw new Error('usage: kill9-child <failpoint-file>');
registry.installFile(path);
try {
  for (let step = 1; step <= 3; step++) {
    out(`before ${step}`);
    failpoint('node.admit.after_commit_before_response');
    out(`after ${step}`);
  }
} finally {
  out('finally');
}
