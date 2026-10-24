export interface Shape {
  area(): number;
}

export class ShapeImpl implements Shape {
  area(): number {
    return 1;
  }
}
