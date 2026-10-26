import { MongoPictureRepository } from './mongo';
import type { PictureRepository } from './types';

export * from './types';

let repo: PictureRepository | undefined;

export const getPictureRepository = (): PictureRepository =>
  (repo ??= new MongoPictureRepository());

/** Replaces the repository (tests, container wiring); no argument restores the default for the selected store. */
export const setPictureRepository = (r?: PictureRepository): void => {
  repo = r;
};
