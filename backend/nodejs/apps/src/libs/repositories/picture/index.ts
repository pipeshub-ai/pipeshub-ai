import { MongoPictureRepository } from './mongo';
import type { PictureRepository } from './types';
import { pickStore } from '../store-select';

export * from './types';

let repo: PictureRepository | undefined;

export const getPictureRepository = (): PictureRepository =>
  (repo ??= pickStore<PictureRepository>('picture', {
    mongo: () => new MongoPictureRepository(),
  }));

/** Replaces the repository (tests, container wiring); no argument restores the default for the selected store. */
export const setPictureRepository = (r?: PictureRepository): void => {
  repo = r;
};
