import { Thing, otherFn } from "./barrel/index";
import { ShapeImpl } from "./iface";
import { pick } from "./over";

export function useAll(): number {
  const t = new Thing();
  const s = new ShapeImpl();
  pick("x");
  pick(1);
  return t.name().length + otherFn() + s.area();
}
