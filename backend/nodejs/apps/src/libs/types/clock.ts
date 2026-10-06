export interface IClock {
  now(): number;
}

export const systemClock: IClock = { now: () => Date.now() };

export class FixedClock implements IClock {
  constructor(private current: number) {}

  now(): number {
    return this.current;
  }

  advance(ms: number): void {
    this.current += ms;
  }

  set(ms: number): void {
    this.current = ms;
  }
}
