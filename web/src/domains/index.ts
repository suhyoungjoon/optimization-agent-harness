import type { DomainAdapter } from "./types";
import { dispatchAdapter } from "./dispatch";

export const adapters: Record<string, DomainAdapter> = {
  dispatch: dispatchAdapter,
};
