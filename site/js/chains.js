// Pocket-watch chains. In the game they are Rigidbodies linked by a SpringJoint (to the hand anchor) and
// HingeJoints, simulated by PhysX. Here: a Verlet rope with the same link lengths, gravity and damping.
import * as THREE from 'three';

const GRAVITY = 9.81, DOWN = new THREE.Vector3(0, -1, 0);
const _m = new THREE.Matrix4(), _p = new THREE.Vector3(), _q = new THREE.Quaternion(), _q2 = new THREE.Quaternion(),
  _s = new THREE.Vector3(), _d = new THREE.Vector3(), _dk = new THREE.Vector3();

export class Chains {
  constructor(model, vfx, nodeByIndex) {
    this.model = model;
    model.updateMatrixWorld(true);
    this.chains = vfx.chains.map(c => {
      const anchor = nodeByIndex[c.anchor];
      const segs = c.links.map(l => nodeByIndex[l.node]).filter(Boolean);
      if (!anchor || segs.length !== c.links.length) return null;
      const anchorInv = anchor.matrixWorld.clone().invert();
      const rel = segs.map(s => anchorInv.clone().multiply(s.matrixWorld));       // rest pose relative to the hand
      const pts = segs.map(s => model.worldToLocal(s.getWorldPosition(new THREE.Vector3())));
      const lastQ = segs.at(-1).getWorldQuaternion(new THREE.Quaternion());
      const tipLen = 0.22 * segs.at(-1).getWorldScale(new THREE.Vector3()).y;
      pts.push(pts.at(-1).clone().add(new THREE.Vector3(0, -tipLen, 0).applyQuaternion(lastQ)));
      const lens = pts.slice(1).map((p, i) => Math.max(0.01, p.distanceTo(pts[i])));
      segs.forEach(s => model.attach(s));
      return { anchor, segs, rel, pts, prev: pts.map(p => p.clone()), lens, kin: rel.map(() => new THREE.Matrix4()), root: new THREE.Vector3() };
    }).filter(Boolean);
  }

  update(dt, simulate) {
    const model = this.model;
    model.updateMatrixWorld(true);
    const sub = 3, h = Math.min(dt, 1 / 20) / sub;
    model.getWorldQuaternion(_q2).invert();
    for (const c of this.chains) {
      const kin = c.kin;                       // the links where the hand alone would put them
      c.rel.forEach((r, i) => kin[i].multiplyMatrices(c.anchor.matrixWorld, r));
      const root = model.worldToLocal(c.root.setFromMatrixPosition(kin[0]));
      if (!simulate) {                         // rigid, as authored
        kin.forEach((k, i) => {
          k.decompose(_p, _q, _s);
          c.segs[i].position.copy(model.worldToLocal(_p)); c.segs[i].quaternion.copy(_q).premultiply(_q2);
          c.pts[i].copy(c.segs[i].position); c.prev[i].copy(c.pts[i]);
        });
        kin.at(-1).decompose(_p, _q, _s);
        c.pts.at(-1).copy(c.pts.at(-2)).add(_d.copy(DOWN).applyQuaternion(_q).multiplyScalar(c.lens.at(-1)));
        c.prev.at(-1).copy(c.pts.at(-1));
        continue;
      }
      for (let s = 0; s < sub; s++) {
        c.pts[0].copy(root); c.prev[0].copy(root);
        for (let i = 1; i < c.pts.length; i++) {
          const p = c.pts[i], q = c.prev[i];
          _d.subVectors(p, q).multiplyScalar(0.985);
          q.copy(p); p.add(_d); p.y -= GRAVITY * h * h;
        }
        for (let it = 0; it < 6; it++) {
          for (let i = 0; i < c.lens.length; i++) {
            const a = c.pts[i], b = c.pts[i + 1];
            _d.subVectors(b, a);
            const len = _d.length() || 1e-6, diff = (len - c.lens[i]) / len;
            if (i === 0) b.addScaledVector(_d, -diff);
            else { a.addScaledVector(_d, 0.5 * diff); b.addScaledVector(_d, -0.5 * diff); }
          }
          c.pts[0].copy(root);
        }
      }
      // each link keeps its authored twist; its local -Y axis follows the rope
      for (let i = 0; i < c.segs.length; i++) {
        kin[i].decompose(_p, _q, _s);
        _q.premultiply(_q2);
        _dk.copy(DOWN).applyQuaternion(_q);
        _d.subVectors(c.pts[i + 1], c.pts[i]).normalize();
        c.segs[i].quaternion.setFromUnitVectors(_dk, _d).multiply(_q);
        c.segs[i].position.copy(c.pts[i]);
      }
    }
  }
}
