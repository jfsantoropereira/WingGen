/** Tiny valid binary STL generator (box) for mock mode — no bundled binary asset needed. */

const HEADER_BYTES = 80;
const TRIANGLE_BYTES = 50;

type Vec3 = [number, number, number];

interface Triangle {
  normal: Vec3;
  vertices: [Vec3, Vec3, Vec3];
}

function quad(a: Vec3, b: Vec3, c: Vec3, d: Vec3, normal: Vec3): Triangle[] {
  return [
    { normal, vertices: [a, b, c] },
    { normal, vertices: [a, c, d] },
  ];
}

/**
 * Build a watertight axis-aligned box as binary STL (12 triangles).
 *
 * The box is centered on x (span), sits on z=0 (thickness up, Z-up like the
 * real exporter) and extends chord in +y.
 */
export function buildBoxStl(spanM: number, chordM: number, thicknessM: number): ArrayBuffer {
  const x0 = -spanM / 2;
  const x1 = spanM / 2;
  const y0 = 0;
  const y1 = chordM;
  const z0 = 0;
  const z1 = thicknessM;

  // Corners: n = near (y0), f = far (y1); b = bottom (z0), t = top (z1).
  const lnb: Vec3 = [x0, y0, z0];
  const rnb: Vec3 = [x1, y0, z0];
  const lfb: Vec3 = [x0, y1, z0];
  const rfb: Vec3 = [x1, y1, z0];
  const lnt: Vec3 = [x0, y0, z1];
  const rnt: Vec3 = [x1, y0, z1];
  const lft: Vec3 = [x0, y1, z1];
  const rft: Vec3 = [x1, y1, z1];

  const triangles: Triangle[] = [
    ...quad(lnb, lfb, rfb, rnb, [0, 0, -1]), // bottom
    ...quad(lnt, rnt, rft, lft, [0, 0, 1]), // top
    ...quad(lnb, rnb, rnt, lnt, [0, -1, 0]), // near (leading edge)
    ...quad(lfb, lft, rft, rfb, [0, 1, 0]), // far (trailing edge)
    ...quad(lnb, lnt, lft, lfb, [-1, 0, 0]), // left tip
    ...quad(rnb, rfb, rft, rnt, [1, 0, 0]), // right tip
  ];

  const buffer = new ArrayBuffer(HEADER_BYTES + 4 + triangles.length * TRIANGLE_BYTES);
  const view = new DataView(buffer);
  const headerText = 'WingGen Studio mock STL (box)';
  for (let i = 0; i < headerText.length && i < HEADER_BYTES; i += 1) {
    view.setUint8(i, headerText.charCodeAt(i));
  }
  view.setUint32(HEADER_BYTES, triangles.length, true);

  let offset = HEADER_BYTES + 4;
  for (const triangle of triangles) {
    for (const component of triangle.normal) {
      view.setFloat32(offset, component, true);
      offset += 4;
    }
    for (const vertex of triangle.vertices) {
      for (const component of vertex) {
        view.setFloat32(offset, component, true);
        offset += 4;
      }
    }
    view.setUint16(offset, 0, true);
    offset += 2;
  }
  return buffer;
}
