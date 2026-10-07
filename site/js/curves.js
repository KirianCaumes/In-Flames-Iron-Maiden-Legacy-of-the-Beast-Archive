// Unity AnimationCurve / MinMaxCurve / MinMaxGradient evaluation.
// Curves are exported as { k: [[time, value, inSlope, outSlope], ...] } (non-weighted Hermite keys).

export function curveEval(c, t) {
  const k = c && c.k;
  if (!k || !k.length) return 0;
  if (t <= k[0][0] || k.length === 1) return k[0][1];
  const last = k[k.length - 1];
  if (t >= last[0]) return last[1];
  let i = 0;
  while (i < k.length - 2 && t > k[i + 1][0]) i++;
  const [t0, v0, , o0] = k[i];
  const [t1, v1, in1] = k[i + 1];
  // a null / infinite tangent is Unity's "constant" (stepped) key
  if (o0 == null || in1 == null || !isFinite(o0) || !isFinite(in1)) return v0;
  const dt = t1 - t0, s = (t - t0) / dt, s2 = s * s, s3 = s2 * s;
  return (2 * s3 - 3 * s2 + 1) * v0 + (s3 - 2 * s2 + s) * o0 * dt + (-2 * s3 + 3 * s2) * v1 + (s3 - s2) * in1 * dt;
}

// MinMaxCurve modes: 0 constant, 1 curve, 2 random between two curves, 3 random between two constants
export function mmc(m, t, r) {
  if (!m) return 0;
  switch (m.minMaxState) {
    case 0: return m.scalar;
    case 1: return m.scalar * curveEval(m.maxCurve, t);
    case 2: return m.scalar * (curveEval(m.minCurve, t) + (curveEval(m.maxCurve, t) - curveEval(m.minCurve, t)) * r);
    case 3: return (m.minScalar ?? 0) + (m.scalar - (m.minScalar ?? 0)) * r;
    default: return m.scalar;
  }
}

export function gradEval(g, t, out) {
  const c = g.c, a = g.a;
  if (t <= c[0][0]) { out[0] = c[0][1]; out[1] = c[0][2]; out[2] = c[0][3]; }
  else if (t >= c[c.length - 1][0]) { const l = c[c.length - 1]; out[0] = l[1]; out[1] = l[2]; out[2] = l[3]; }
  else {
    let i = 0;
    while (t > c[i + 1][0]) i++;
    const A = c[i], B = c[i + 1];
    if (g.mode === 1) { out[0] = B[1]; out[1] = B[2]; out[2] = B[3]; }   // fixed (stepped) gradient
    else {
      const s = (t - A[0]) / Math.max(1e-6, B[0] - A[0]);
      out[0] = A[1] + (B[1] - A[1]) * s; out[1] = A[2] + (B[2] - A[2]) * s; out[2] = A[3] + (B[3] - A[3]) * s;
    }
  }
  if (t <= a[0][0]) out[3] = a[0][1];
  else if (t >= a[a.length - 1][0]) out[3] = a[a.length - 1][1];
  else {
    let i = 0;
    while (t > a[i + 1][0]) i++;
    const s = (t - a[i][0]) / Math.max(1e-6, a[i + 1][0] - a[i][0]);
    out[3] = a[i][1] + (a[i + 1][1] - a[i][1]) * s;
  }
  return out;
}

// MinMaxGradient modes: 0 color, 1 gradient, 2 two colors, 3 two gradients, 4 random color
const _g1 = [0, 0, 0, 0], _g2 = [0, 0, 0, 0];
const col = (c, o) => { o[0] = c.r; o[1] = c.g; o[2] = c.b; o[3] = c.a; return o; };
export function mmg(m, t, r, out) {
  switch (m.minMaxState) {
    case 0: return col(m.maxColor, out);
    case 1: return gradEval(m.maxGradient, t, out);
    case 2: col(m.minColor, _g1); col(m.maxColor, _g2); break;
    case 3: gradEval(m.minGradient, t, _g1); gradEval(m.maxGradient, t, _g2); break;
    case 4: return gradEval(m.maxGradient, r, out);
    default: return col(m.maxColor, out);
  }
  for (let i = 0; i < 4; i++) out[i] = _g1[i] + (_g2[i] - _g1[i]) * r;
  return out;
}

export const clamp01 = x => Math.min(1, Math.max(0, x));
