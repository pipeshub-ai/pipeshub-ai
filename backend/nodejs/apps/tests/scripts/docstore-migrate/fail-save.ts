/** Makes the first save of a job's progress that matches `when` fail (in this process); returns the undo. */
import {
  JobState,
  type Progress,
} from '../../../src/scripts/docstore-migrate/state';

export function failSaveOnce(when: (p: Progress) => boolean): () => void {
  const orig = JobState.prototype.checkpoint;
  let failed = false;
  JobState.prototype.checkpoint = function (this: JobState): Promise<void> {
    if (!failed && when(this.progress)) {
      failed = true;
      return Promise.reject(new Error('injected: the save failed'));
    }
    return orig.call(this);
  };
  return () => {
    JobState.prototype.checkpoint = orig;
  };
}
