// Text colour that stays readable on a given background.
//
// Scenes often get a per-cut `backgroundColor` while the text colour comes
// from the theme (e.g. white text from a dark playbook on a light paper
// background). `readableOn` keeps the requested colour when it already has
// WCAG AA contrast (4.5:1) against the background, and otherwise switches to
// near-black or white, whichever contrasts more. Non-hex backgrounds
// ("transparent", gradients, rgba) are left alone because the real backdrop
// is unknown.

const DARK = "#0F172A";
const LIGHT = "#FFFFFF";
const MIN_CONTRAST = 4.5;

function parseHex(color: string | undefined): [number, number, number] | null {
  if (!color) return null;
  const m = color.trim().match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (!m) return null;
  let hex = m[1];
  if (hex.length === 3) {
    hex = hex
      .split("")
      .map((c) => c + c)
      .join("");
  }
  return [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16)) as [
    number,
    number,
    number,
  ];
}

export function relativeLuminance(color: string | undefined): number | null {
  const rgb = parseHex(color);
  if (!rgb) return null;
  const [r, g, b] = rgb.map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrastRatio(
  a: string | undefined,
  b: string | undefined,
): number | null {
  const la = relativeLuminance(a);
  const lb = relativeLuminance(b);
  if (la === null || lb === null) return null;
  const [hi, lo] = la > lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

export function readableOn(
  background: string | undefined,
  preferred: string,
): string {
  const ratio = contrastRatio(background, preferred);
  if (ratio === null || ratio >= MIN_CONTRAST) return preferred;
  const dark = contrastRatio(background, DARK) ?? 0;
  const light = contrastRatio(background, LIGHT) ?? 0;
  return dark >= light ? DARK : LIGHT;
}
