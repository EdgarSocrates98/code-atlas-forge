import { Svc } from "./types";

export class Api implements Svc {
  ping() {}
}

export function boot(): Api {
  return new Api();
}
