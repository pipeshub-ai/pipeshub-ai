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
  git?: GitRunner,
): boolean {
  const info = readBuildInfoFromGit(git);
  if (info.commitId === null || info.commitId === '') {
    // A file left by an earlier build would report the wrong commit.
    fs.rmSync(file, { force: true });
    return false;
  }
  // Write a temporary file and rename it, so a build stopped mid-write
  // cannot leave half a JSON file behind.
  const tmp = `${file}.${String(process.pid)}.tmp`;
  try {
    fs.writeFileSync(tmp, `${JSON.stringify(info, null, 2)}\n`);
    fs.renameSync(tmp, file);
  } catch (err) {
    fs.rmSync(tmp, { force: true });
    throw err;
  }
  return true;
}

if (require.main === module) {
  writeBuildInfo();
}
