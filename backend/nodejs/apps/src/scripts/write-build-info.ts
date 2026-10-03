import fs from 'fs';
import {
  BUILD_INFO_FILE,
  GitRunner,
  readBuildInfoFromGit,
} from '../libs/utils/build-info.utils';

// Runs after `tsc`. A Docker build has no git checkout, so it writes nothing
// and the image's environment variables are the source instead.
export function writeBuildInfo(
  file: string = BUILD_INFO_FILE,
  now: Date = new Date(),
  git?: GitRunner,
): boolean {
  const info = readBuildInfoFromGit(git);
  if (!info.commitId) {
    // A file left by an earlier build would report the wrong commit.
    fs.rmSync(file, { force: true });
    return false;
  }
  fs.writeFileSync(
    file,
    `${JSON.stringify({ ...info, buildTime: now.toISOString() }, null, 2)}\n`,
  );
  return true;
}

if (require.main === module) {
  writeBuildInfo();
}
