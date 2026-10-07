// Re-simulation of the game's Unity (Shuriken) particle systems from their serialized data.
// Coordinates: the GLB is Unity space mirrored on X (x -> -x), so every Unity-space vector read from
// the data is mirrored the same way, and rotations about Y/Z change sign.
import * as THREE from 'three';
import { curveEval, mmc, mmg } from './curves.js';

const GRAVITY = 9.81;
const DEG = Math.PI / 180;
const v1 = new THREE.Vector3(), v2 = new THREE.Vector3(), v3 = new THREE.Vector3();
const q1 = new THREE.Quaternion(), e1 = new THREE.Euler(), m4 = new THREE.Matrix4(), m3 = new THREE.Matrix3();
const s1 = new THREE.Vector3(), c4 = [0, 0, 0, 0], g4 = [0, 0, 0, 0];
// scratch objects of the per-frame code (simulate, upload, alignQuat, Trail.update): nothing is allocated per frame
const _toLocal = new THREE.Matrix3(), _wp = new THREE.Vector3(), _wv = new THREE.Vector3();
const _qa = new THREE.Quaternion(), _ax = new THREE.Vector3(), _ay = new THREE.Vector3(), _az = new THREE.Vector3();
const _basis = new THREE.Matrix4(), _up = new THREE.Vector3(0, 1, 0);
const _head = new THREE.Vector3(), _side = new THREE.Vector3(), _c2 = [0, 0, 0, 0], _ca = [0, 0, 0, 0], _cb = [0, 0, 0, 0];
const WHITE = [1, 1, 1, 1];
const flipX = v => (v.x = -v.x, v);

// ------------------------------------------------------------------ shaders (Mobile/Particles/Additive & Alpha Blended)
const VERT = /* glsl */`
  attribute vec3 iPos; attribute vec3 iSize; attribute float iRot; attribute vec4 iColor; attribute vec4 iUV; attribute vec3 iVel;
  attribute vec4 meshColor;
  uniform int mode; uniform mat3 emitterRot; uniform float lengthScale; uniform float velocityScale; uniform vec2 pivot;
  uniform bool hasMeshColor; uniform float maxSize; uniform float tanHalfFov;
  varying vec2 vUv; varying vec4 vColor;
  void main() {
    vUv = iUV.xy + uv * iUV.zw;
    vColor = clamp(iColor, 0.0, 1.0) * (hasMeshColor ? meshColor : vec4(1.0));
    if (mode == 4) { gl_Position = projectionMatrix * viewMatrix * instanceMatrix * vec4(position, 1.0); return; }
    vec2 size = iSize.xy;
    vec4 mv = viewMatrix * vec4(iPos, 1.0);
    if (mode == 0 || mode == 5 || mode == 6) {
      // Renderer "Max Particle Size": billboards never exceed this fraction of the viewport
      float limit = maxSize * 2.0 * max(-mv.z, 1e-3) * tanHalfFov;
      float m = max(abs(size.x), abs(size.y));
      if (m > limit) size *= limit / m;
    }
    float c = cos(iRot), s = sin(iRot);
    vec2 q = (position.xy - pivot) * size;
    vec2 rq = vec2(c * q.x - s * q.y, s * q.x + c * q.y);
    vec3 p;
    if (mode == 0) { mv.xy += rq; gl_Position = projectionMatrix * mv; return; }        // view-facing billboard
    else if (mode == 5) { p = iPos + emitterRot * vec3(rq, 0.0); }                     // aligned to the emitter
    else if (mode == 6) {                                                              // aligned to velocity
      vec3 f = length(iVel) > 1e-5 ? normalize(iVel) : vec3(0.0, 0.0, 1.0);
      vec3 up = abs(f.y) > 0.99 ? vec3(1.0, 0.0, 0.0) : vec3(0.0, 1.0, 0.0);
      vec3 r = normalize(cross(up, f)); vec3 u = cross(f, r);
      p = iPos + r * rq.x + u * rq.y;
    } else if (mode == 2) { p = iPos + vec3(rq.x, 0.0, -rq.y); }                       // horizontal billboard
    else if (mode == 3) {                                                              // vertical billboard
      vec3 toCam = cameraPosition - iPos; toCam.y = 0.0; toCam = normalize(toCam);
      vec3 r = normalize(cross(vec3(0.0, 1.0, 0.0), toCam));
      p = iPos + r * rq.x + vec3(0.0, rq.y, 0.0);
    } else {                                                                           // stretched billboard
      vec3 v = iVel; float sp = length(v);
      vec3 axis = sp > 1e-5 ? v / sp : vec3(0.0, 1.0, 0.0);
      vec3 toCam = normalize(cameraPosition - iPos);
      vec3 side = cross(axis, toCam); side = length(side) > 1e-5 ? normalize(side) : vec3(1.0, 0.0, 0.0);
      float len = size.x * lengthScale + sp * velocityScale;
      p = iPos + side * (position.y * size.y) - axis * ((position.x + 0.5) * len);
    }
    gl_Position = projectionMatrix * viewMatrix * vec4(p, 1.0);
  }`;
const FRAG = /* glsl */`
  uniform sampler2D map; uniform vec4 st; uniform vec2 scroll; uniform bool hasMap;
  varying vec2 vUv; varying vec4 vColor;
  void main() {
    vec4 t = hasMap ? texture2D(map, vUv * st.xy + st.zw + scroll) : vec4(1.0);
    vec4 c = t * vColor;
    if (c.a < 0.003) discard;
    gl_FragColor = c;          // gamma-space output, like the game
  }`;
const TRAIL_VERT = /* glsl */`
  attribute vec4 color; varying vec2 vUv; varying vec4 vColor;
  void main() { vUv = uv; vColor = color; gl_Position = projectionMatrix * viewMatrix * vec4(position, 1.0); }`;

// The canvas is transparent over the page background: additive particles must not write alpha.
function particleMaterial(uniforms, vertexShader, additive) {
  return new THREE.ShaderMaterial({
    vertexShader, fragmentShader: FRAG, uniforms, transparent: true, depthWrite: false, side: THREE.DoubleSide,
    blending: THREE.CustomBlending, blendEquation: THREE.AddEquation,
    blendSrc: THREE.SrcAlphaFactor, blendDst: additive ? THREE.OneFactor : THREE.OneMinusSrcAlphaFactor,
    blendSrcAlpha: additive ? THREE.ZeroFactor : THREE.OneFactor,
    blendDstAlpha: additive ? THREE.OneFactor : THREE.OneMinusSrcAlphaFactor,
  });
}

// ------------------------------------------------------------------ emission shapes (Unity ShapeModule, Unity space)
function randInSphere(v) {
  do v.set(Math.random() * 2 - 1, Math.random() * 2 - 1, Math.random() * 2 - 1); while (v.lengthSq() > 1 || v.lengthSq() < 1e-6);
  return v;
}
function shapeSample(S, pos, dir) {
  const rad = S.radius?.value ?? 1, thick = S.radiusThickness ?? 1;
  const arc = (S.arc?.value ?? 360) * DEG;
  const rr = () => Math.cbrt(1 - thick + thick * Math.random());
  switch (S.type) {
    case 0: case 1: randInSphere(dir).normalize(); pos.copy(dir).multiplyScalar(rad * rr()); break;      // sphere
    case 2: case 3: randInSphere(dir).normalize(); if (dir.z < 0) dir.z = -dir.z;                        // hemisphere
      pos.copy(dir).multiplyScalar(rad * rr()); break;
    case 4: case 7: case 8: case 9: {                                                                    // cone (+Z)
      const th = Math.random() * arc, rho = Math.sqrt(1 - thick + thick * Math.random()), ang = (S.angle ?? 25) * DEG;
      pos.set(Math.cos(th) * rho * rad, Math.sin(th) * rho * rad, 0);
      dir.set(Math.cos(th) * Math.sin(ang * rho), Math.sin(th) * Math.sin(ang * rho), Math.cos(ang * rho)).normalize();
      if (S.type === 8 || S.type === 9) pos.addScaledVector(dir, Math.random() * (S.length ?? 1));
      break;
    }
    case 5: case 15: case 16: pos.set(Math.random() - 0.5, Math.random() - 0.5, Math.random() - 0.5); dir.set(0, 0, 1); break;  // box
    case 10: case 11: {                                                                                  // circle (XY)
      const th = Math.random() * arc, rho = S.type === 11 ? 1 : Math.sqrt(1 - thick + thick * Math.random());
      pos.set(Math.cos(th) * rho * rad, Math.sin(th) * rho * rad, 0); dir.set(Math.cos(th), Math.sin(th), 0); break;
    }
    case 12: pos.set((Math.random() * 2 - 1) * rad, 0, 0); dir.set(0, 1, 0); break;                     // edge
    default: pos.set(0, 0, 0); randInSphere(dir).normalize();
  }
  const sc = S.m_Scale || { x: 1, y: 1, z: 1 }, ro = S.m_Rotation || { x: 0, y: 0, z: 0 }, po = S.m_Position || { x: 0, y: 0, z: 0 };
  pos.set(pos.x * sc.x, pos.y * sc.y, pos.z * sc.z);
  e1.set(ro.x * DEG, ro.y * DEG, ro.z * DEG, 'YXZ');            // Unity applies Z, then X, then Y
  pos.applyEuler(e1); dir.applyEuler(e1);
  pos.x += po.x; pos.y += po.y; pos.z += po.z;
  if (S.randomDirectionAmount) dir.lerp(randInSphere(v3).normalize(), S.randomDirectionAmount).normalize();
  if (S.sphericalDirectionAmount && pos.lengthSq() > 1e-8) dir.lerp(v3.copy(pos).normalize(), S.sphericalDirectionAmount).normalize();
  if (S.randomPositionAmount) pos.add(randInSphere(v3).multiplyScalar(S.randomPositionAmount));
}

// Unity bursts, shared by normal and sub-emitter emission
function runBursts(E, st, time, dur, looping, tn, emit) {
  if (!E.m_Bursts || !E.m_Bursts.length) return;
  const loopIdx = looping ? Math.floor(time / dur) : 0;
  const local = looping ? time - loopIdx * dur : Math.min(time, dur);
  if (st.loop !== loopIdx) { st.loop = loopIdx; st.fired = E.m_Bursts.map(() => 0); }
  E.m_Bursts.forEach((b, i) => {
    const cycles = b.cycleCount || 0, iv = Math.max(0.01, b.repeatInterval || 0.01);
    while ((cycles === 0 || st.fired[i] < cycles) && local >= b.time + st.fired[i] * iv && st.fired[i] < 500) {
      if (Math.random() <= (b.probability ?? 1)) emit(Math.round(mmc(b.countCurve, tn, Math.random())));
      st.fired[i]++;
    }
  });
}

const TRAIL_PTS = 24;

class Emitter {
  constructor(world, d) {
    this.W = world; this.d = d;
    this.node = world.nodeByIndex[d.node];
    this.worldSpace = d.moveWithTransform === 1;
    this.simSpeed = d.simulationSpeed ?? 1;
    this.parts = [];
    this.playing = false; this.time = 0; this.delay = 0; this.acc = 0; this.dist = 0; this.bursts = {};
    this.subs = []; this.children = []; this.isSub = false;
    this.nodePos = new THREE.Vector3(); this.nodeVel = new THREE.Vector3(); this.hasNodePos = false;
    const r = d.renderer || {};
    const M = r.material != null ? world.vfx.materials[r.material] : null;
    this.max = Math.max(1, d.InitialModule?.maxNumParticles ?? 50);
    this.renderMode = r.mode ?? 0;
    this.mode = r.mode === 4 ? 4 : r.mode === 1 ? 1 : r.mode === 2 ? 2 : r.mode === 3 ? 3 : r.align === 2 ? 5 : r.align === 4 ? 6 : 0;
    this.align = r.align ?? 0;
    const geo = (this.mode === 4 && r.mesh != null) ? world.meshGeos[r.mesh] : world.quad;
    const g = geo.clone(), n = this.max;
    this.attr = {};
    for (const [k, size] of [['iPos', 3], ['iSize', 3], ['iRot', 1], ['iColor', 4], ['iUV', 4], ['iVel', 3]]) {
      this.attr[k] = new THREE.InstancedBufferAttribute(new Float32Array(n * size), size).setUsage(THREE.DynamicDrawUsage);
      g.setAttribute(k, this.attr[k]);
    }
    const additive = !M || !/Alpha Blended/i.test(M.shader || '');
    this.uniforms = {
      map: { value: M && M.tex != null ? world.textures[M.tex] : null }, hasMap: { value: !!(M && M.tex != null) },
      st: { value: new THREE.Vector4(...(M ? M.st : [1, 1, 0, 0])) }, scroll: { value: new THREE.Vector2() },
      mode: { value: this.mode }, emitterRot: { value: new THREE.Matrix3() },
      lengthScale: { value: r.lengthScale ?? 2 }, velocityScale: { value: r.velocityScale ?? 0 },
      pivot: { value: new THREE.Vector2(r.pivot ? -r.pivot[0] : 0, r.pivot ? r.pivot[1] : 0) },
      hasMeshColor: { value: !!g.getAttribute('meshColor') },
      maxSize: { value: this.renderMode === 0 ? (r.maxSize ?? 0.5) : 1e6 }, tanHalfFov: world.tanHalfFov,
    };
    this.mesh = new THREE.InstancedMesh(g, particleMaterial(this.uniforms, VERT, additive), n);
    this.mesh.count = 0; this.mesh.frustumCulled = false;
    // Unity's transparent sort: order in layer, then the material's render queue (custom queue or the shader's
    // Transparent = 3000), then distance (the sorting fudge pulls a system forward). three.js sorts by renderOrder,
    // then by distance.
    const queue = M && M.queue > 0 ? M.queue : 3000;
    this.mesh.renderOrder = (r.order || 0) * 10000 + queue - (r.fudge || 0) / 1000;
    this.rendererEnabled = r.enabled !== false;
    this.mesh.visible = this.rendererEnabled;
    world.scene.add(this.mesh);
    this.trail = d.trail ? new Trail(this, d.trail) : null;
  }

  get duration() { return this.d.lengthInSec || 1; }
  get alive() { return this.playing || this.parts.length > 0; }
  scale(mode) {
    if (mode === 2) return 1;                                   // ScalingMode.Shape: sizes are not scaled
    if (mode === 1) { const s = this.node.scale; return (Math.abs(s.x) + Math.abs(s.y) + Math.abs(s.z)) / 3; }
    this.node.matrixWorld.decompose(v1, q1, s1);
    return (Math.abs(s1.x) + Math.abs(s1.y) + Math.abs(s1.z)) / 3;
  }

  // ParticleSystem.Play(withChildren): no effect on a system that is still alive; sub-emitters are driven by their parent
  play(withChildren = true) {
    if (!this.isSub && !this.alive) {
      this.playing = true; this.time = 0; this.acc = 0; this.dist = 0; this.bursts = {};
      this.delay = mmc(this.d.startDelay, 0, Math.random());
      if (this.d.looping && this.d.prewarm) for (let i = 0; i < this.duration * 30; i++) this.update(1 / 30, true);
    }
    if (withChildren) this.children.forEach(c => c.play(true));
  }
  clear() {
    this.parts.length = 0; this.playing = false; this.mesh.count = 0;
    if (this.trail) this.trail.mesh.geometry.setDrawRange(0, 0);
  }

  // emit `count` particles, either from this system's own transform or from a parent particle (sub-emitter)
  emit(count, tn, origin = null) {
    const d = this.d, I = d.InitialModule, S = d.ShapeModule, IV = d.InheritVelocityModule;
    const M = this.node.matrixWorld;
    const sizeScale = this.scale(d.scalingMode);
    const speedScale = d.scalingMode === 0 ? 1 : 1 / Math.max(this.scale(0), 1e-4);  // hierarchy mode scales speeds
    for (let i = 0; i < count && this.parts.length < this.max; i++) {
      const R = Math.random;
      const pos = new THREE.Vector3(), dir = new THREE.Vector3(0, 0, 1);
      if (S) shapeSample(S, pos, dir);
      flipX(pos); flipX(dir);
      // to world space
      let wpos;
      if (origin) { wpos = pos.applyMatrix4(M).sub(v1.setFromMatrixPosition(M)).add(origin.pos); }
      else wpos = pos.applyMatrix4(M);
      const speed = mmc(I.startSpeed, tn, R());
      const wvel = dir.applyMatrix3(m3.setFromMatrix4(M)).multiplyScalar(speed * speedScale);
      if (IV && IV.m_Mode === 0) wvel.addScaledVector(origin ? origin.vel : this.nodeVel, mmc(IV.m_Curve, tn, R()));
      const p = {
        age: 0, life: Math.max(0.01, mmc(I.startLifetime, tn, R())),
        r: [R(), R(), R(), R(), R(), R()],
        size: mmc(I.startSize, tn, R()), sizeY: I.size3D ? mmc(I.startSizeY, tn, R()) : null,
        sizeZ: I.size3D ? mmc(I.startSizeZ, tn, R()) : null,
        rot: -mmc(I.startRotation, tn, R()),
        rotX: I.rotation3D ? mmc(I.startRotationX, tn, R()) : 0,
        rotY: I.rotation3D ? -mmc(I.startRotationY, tn, R()) : 0,
        color: mmg(I.startColor, tn, R(), [1, 1, 1, 1]),
        grav: mmc(I.gravityModifier, tn, R()), sf: sizeScale, row: 0, sub: null,
      };
      if (R() < (I.randomizeRotationDirection || 0)) p.rot = -p.rot;
      if (this.worldSpace) { p.pos = wpos; p.vel = wvel; }
      else {
        m4.copy(M).invert();
        p.pos = wpos.applyMatrix4(m4);
        p.vel = wvel.applyMatrix3(m3.setFromMatrix4(m4));
      }
      const U = d.UVModule;
      if (U) p.row = U.animationType === 1 ? ((U.rowMode === 1 || U.randomRow) ? Math.floor(R() * U.tilesY) : U.rowIndex) : 0;
      this.parts.push(p);
    }
  }

  // Birth sub-emitter: continuous emission from a parent particle, timed by the parent's age
  emitFromParent(st, wpos, wvel, dt, probability) {
    const d = this.d, E = d.EmissionModule, dur = this.duration;
    st.t += dt;
    if (!st.last) st.last = wpos.clone();
    if (!d.looping && st.t > dur + dt) { st.last.copy(wpos); return; }
    const tn = d.looping ? (st.t % dur) / dur : Math.min(st.t / dur, 1);
    const origin = { pos: wpos, vel: wvel };
    const emit = n => { if (n > 0 && Math.random() <= probability) this.emit(n, tn, origin); };
    if (E) {
      st.acc += mmc(E.rateOverTime, tn, Math.random()) * dt;
      st.acc += wpos.distanceTo(st.last) * mmc(E.rateOverDistance, tn, Math.random());
      const n = Math.floor(st.acc); st.acc -= n; emit(n);
      runBursts(E, st.b ||= {}, st.t, dur, d.looping, tn, emit);
    }
    st.last.copy(wpos);
  }

  update(dt, prewarm = false) {
    dt *= this.simSpeed;
    const d = this.d;
    // emitter transform velocity (for InheritVelocity and rateOverDistance)
    v1.setFromMatrixPosition(this.node.matrixWorld);
    const moved = this.hasNodePos ? v1.distanceTo(this.nodePos) : 0;
    if (this.hasNodePos && dt > 0) this.nodeVel.subVectors(v1, this.nodePos).divideScalar(dt);
    this.nodePos.copy(v1); this.hasNodePos = true;
    if (this.playing && !this.isSub) {
      if (this.delay > 0) this.delay -= dt;
      else {
        const dur = this.duration;
        this.time += dt;
        const tn = d.looping ? (this.time % dur) / dur : Math.min(this.time / dur, 1);
        const E = d.EmissionModule;
        if (E) {
          this.acc += mmc(E.rateOverTime, tn, Math.random()) * dt + moved * mmc(E.rateOverDistance, tn, Math.random());
          const n = Math.floor(this.acc); this.acc -= n;
          if (n > 0) this.emit(n, tn);
          runBursts(E, this.bursts, this.time, dur, d.looping, tn, k => k > 0 && this.emit(k, tn));
        }
        if (!d.looping && this.time >= dur) this.playing = false;
      }
    }
    this.simulate(dt);
    if (!prewarm) { this.upload(); if (this.trail) this.trail.update(); }
  }

  worldPosOf(p, out) { out.copy(p.pos); if (!this.worldSpace) out.applyMatrix4(this.node.matrixWorld); return out; }
  worldVelOf(p, out) { out.copy(p.vel); if (!this.worldSpace) out.applyMatrix3(m3.setFromMatrix4(this.node.matrixWorld)); return out; }

  simulate(dt) {
    const d = this.d, V = d.VelocityModule, F = d.ForceModule, C = d.ClampVelocityModule;
    const Rm = d.RotationModule, Rs = d.RotationBySpeedModule;
    const M = this.node.matrixWorld;
    const toLocal = this.worldSpace ? null : _toLocal.setFromMatrix4(m4.copy(M).invert());
    const wp = _wp, wv = _wv;
    for (let i = this.parts.length - 1; i >= 0; i--) {
      const p = this.parts[i];
      p.age += dt;
      if (p.age >= p.life) {
        for (const s of this.subs) if (s.type === 2) {      // death sub-emitters
          this.worldPosOf(p, wp); this.worldVelOf(p, wv);
          s.emitter.emitFromParent({ t: 0, acc: 0 }, wp, wv, 1e-3, s.probability);
        }
        this.parts.splice(i, 1);
        continue;
      }
      const t = p.age / p.life;
      if (p.grav) {
        v2.set(0, -GRAVITY * p.grav * dt, 0);
        if (toLocal) v2.applyMatrix3(toLocal);
        p.vel.add(v2);
      }
      if (F) {
        const rr = F.randomizePerFrame ? Math.random() : p.r[3];
        v2.set(-mmc(F.x, t, rr), mmc(F.y, t, rr), mmc(F.z, t, rr)).multiplyScalar(dt);
        if (F.inWorldSpace && toLocal) v2.applyMatrix3(toLocal);
        else if (!F.inWorldSpace && !toLocal) v2.applyMatrix3(m3.setFromMatrix4(M));
        p.vel.add(v2);
      }
      if (C) {
        const lim = mmc(C.magnitude, t, p.r[4]);
        const sp = p.vel.length();
        if (sp > lim && sp > 1e-6) {
          const damp = 1 - Math.pow(1 - (C.dampen ?? 1), dt * 30);
          p.vel.multiplyScalar(1 + (lim / sp - 1) * damp);
        }
        const drag = C.drag ? mmc(C.drag, t, p.r[4]) : 0;
        if (drag) p.vel.multiplyScalar(1 / (1 + drag * dt));
      }
      v2.set(0, 0, 0);
      let sm = 1;
      if (V) {
        v2.set(-mmc(V.x, t, p.r[0]), mmc(V.y, t, p.r[1]), mmc(V.z, t, p.r[2]));
        if (V.inWorldSpace && toLocal) v2.applyMatrix3(toLocal);
        else if (!V.inWorldSpace && !toLocal) v2.applyMatrix3(m3.setFromMatrix4(M));
        const radial = mmc(V.radial, t, p.r[0]);
        if (radial && p.pos.lengthSq() > 1e-8) v2.addScaledVector(v3.copy(p.pos).normalize(), radial);
        if (V.speedModifier) sm = mmc(V.speedModifier, t, p.r[1]);
      }
      p.pos.addScaledVector(p.vel, dt * sm).addScaledVector(v2, dt * sm);
      if (Rm) {
        p.rot -= mmc(Rm.curve, t, p.r[5]) * dt;
        if (Rm.separateAxes) { p.rotX += mmc(Rm.x, t, p.r[5]) * dt; p.rotY -= mmc(Rm.y, t, p.r[5]) * dt; }
      }
      if (Rs) {
        p.rot -= mmc(Rs.curve, 0, p.r[5]) * dt;
        if (Rs.separateAxes) { p.rotX += mmc(Rs.x, 0, p.r[5]) * dt; p.rotY -= mmc(Rs.y, 0, p.r[5]) * dt; }
      }
      // birth sub-emitters follow the particle
      if (this.subs.length) {
        this.worldPosOf(p, wp); this.worldVelOf(p, wv);
        p.sub ||= this.subs.map(() => ({ t: 0, acc: 0 }));
        this.subs.forEach((s, k) => { if (s.type === 0) s.emitter.emitFromParent(p.sub[k], wp, wv, dt, s.probability); });
      }
    }
  }

  alignQuat(worldVel) {   // orientation of mesh particles, per Unity render alignment (a shared scratch quaternion)
    const q = _qa.identity();
    switch (this.align) {
      case 0: return q.copy(this.W.camera.quaternion).multiply(this.W.flipY);   // View (Unity cameras look down +Z)
      case 1: return q;                                                         // World
      case 4: {                                                                 // Velocity: +Z against the motion
        if (worldVel.lengthSq() < 1e-10) return q;
        const z = _az.copy(worldVel).normalize().negate();
        const x = _ax.crossVectors(_up, z); if (x.lengthSq() < 1e-8) x.set(1, 0, 0); x.normalize();
        const y = _ay.crossVectors(z, x);
        return q.setFromRotationMatrix(_basis.makeBasis(x, y, z));
      }
      default: this.node.matrixWorld.decompose(v1, q, s1); return q;          // Local
    }
  }

  upload() {
    const d = this.d, A = this.attr, n = this.parts.length;
    const Sz = d.SizeModule, Col = d.ColorModule, U = d.UVModule;
    if (d.uvScroll) {
      const tt = (this.W.time % d.uvScroll.duration) / d.uvScroll.duration;
      this.uniforms.scroll.value.set(curveEval(d.uvScroll.x, tt), curveEval(d.uvScroll.y, tt));
    }
    this.uniforms.emitterRot.value.setFromMatrix4(m4.extractRotation(this.node.matrixWorld));
    const wp = _wp, wv = _wv;
    for (let i = 0; i < n; i++) {
      const p = this.parts[i], t = p.age / p.life;
      this.worldPosOf(p, wp); this.worldVelOf(p, wv);
      A.iPos.setXYZ(i, wp.x, wp.y, wp.z);
      A.iVel.setXYZ(i, wv.x, wv.y, wv.z);
      let sx = p.size, sy = p.sizeY ?? p.size, sz = p.sizeZ ?? p.size;
      if (Sz) {
        if (Sz.separateAxes) { sx *= mmc(Sz.curve, t, p.r[2]); sy *= mmc(Sz.y, t, p.r[2]); sz *= mmc(Sz.z, t, p.r[2]); }
        else { const k = mmc(Sz.curve, t, p.r[2]); sx *= k; sy *= k; sz *= k; }
      }
      sx *= p.sf; sy *= p.sf; sz *= p.sf;
      p.curSize = sx; p.curT = t;
      A.iSize.setXYZ(i, sx, sy, sz);
      A.iRot.setX(i, p.rot);
      c4[0] = p.color[0]; c4[1] = p.color[1]; c4[2] = p.color[2]; c4[3] = p.color[3];
      if (Col) { mmg(Col.gradient, t, p.r[3], g4); for (let k = 0; k < 4; k++) c4[k] *= g4[k]; }
      A.iColor.setXYZW(i, c4[0], c4[1], c4[2], c4[3]);
      if (U) {
        const tx = U.tilesX, ty = U.tilesY, frames = U.animationType === 1 ? tx : tx * ty;
        const ft = (t * (U.cycles || 1)) % 1;
        let f = Math.floor((mmc(U.frameOverTime, ft, p.r[0]) + mmc(U.startFrame, 0, p.r[1])) * frames);
        f = ((f % frames) + frames) % frames;
        const col = f % tx, row = U.animationType === 1 ? p.row : Math.floor(f / tx);
        A.iUV.setXYZW(i, col / tx, 1 - (row + 1) / ty, 1 / tx, 1 / ty);   // sheet frames are numbered from the top
      } else A.iUV.setXYZW(i, 0, 0, 1, 1);
      if (this.mode === 4) {
        // flat particle meshes are authored in the XZ plane; a 2D rotation spins them around their normal (Y)
        if (d.InitialModule.rotation3D) e1.set(p.rotX, p.rotY, p.rot, 'YXZ'); else e1.set(0, p.rot, 0, 'YXZ');
        q1.setFromEuler(e1).premultiply(this.alignQuat(wv));
        this.mesh.setMatrixAt(i, m4.compose(wp, q1, s1.set(sx, sy, sz)));
      }
    }
    this.mesh.count = n;
    for (const a of Object.values(A)) a.needsUpdate = true;
    if (this.mode === 4) this.mesh.instanceMatrix.needsUpdate = true;
  }
}

// ------------------------------------------------------------------ trails (TrailModule, "Particles" mode)
class Trail {
  constructor(em, T) {
    this.em = em; this.T = T;
    const M = T.material != null ? em.W.vfx.materials[T.material] : null;
    const additive = !M || !/Alpha Blended/i.test(M.shader || '');
    const maxV = em.max * TRAIL_PTS * 6;
    this.pos = new Float32Array(maxV * 3); this.uv = new Float32Array(maxV * 2); this.col = new Float32Array(maxV * 4);
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(this.pos, 3).setUsage(THREE.DynamicDrawUsage));
    g.setAttribute('uv', new THREE.BufferAttribute(this.uv, 2).setUsage(THREE.DynamicDrawUsage));
    g.setAttribute('color', new THREE.BufferAttribute(this.col, 4).setUsage(THREE.DynamicDrawUsage));
    g.setDrawRange(0, 0);
    this.uniforms = {
      map: { value: M && M.tex != null ? em.W.textures[M.tex] : null }, hasMap: { value: !!(M && M.tex != null) },
      st: { value: new THREE.Vector4(...(M ? M.st : [1, 1, 0, 0])) }, scroll: { value: new THREE.Vector2() },
    };
    this.mesh = new THREE.Mesh(g, particleMaterial(this.uniforms, TRAIL_VERT, additive));
    this.mesh.frustumCulled = false; this.mesh.renderOrder = em.mesh.renderOrder - 0.5;
    em.W.scene.add(this.mesh);
    // edges of one trail: at most TRAIL_PTS stored points, then the particle itself
    this.L = Array.from({ length: TRAIL_PTS + 1 }, () => new THREE.Vector3());
    this.R = Array.from({ length: TRAIL_PTS + 1 }, () => new THREE.Vector3());
    this.F = new Float32Array(TRAIL_PTS + 1);
  }
  put(v, p, u, vv, c) {
    const P = this.pos, U = this.uv, C = this.col;
    P[v * 3] = p.x; P[v * 3 + 1] = p.y; P[v * 3 + 2] = p.z; U[v * 2] = u; U[v * 2 + 1] = vv;
    C[v * 4] = c[0]; C[v * 4 + 1] = c[1]; C[v * 4 + 2] = c[2]; C[v * 4 + 3] = c[3];
    return v + 1;
  }
  update() {
    const em = this.em, T = this.T, P = this.pos, L = this.L, R = this.R, F = this.F;
    if (T.uvScroll) {
      const tt = (em.W.time % T.uvScroll.duration) / T.uvScroll.duration;
      this.uniforms.scroll.value.set(curveEval(T.uvScroll.x, tt), curveEval(T.uvScroll.y, tt));
    }
    this.mesh.visible = em.mesh.visible;
    const cam = em.W.camera.position;
    let v = 0;
    for (const p of em.parts) {
      const head = em.worldPosOf(p, _head);
      const pts = (p.trail ||= []);
      if (!pts.length || pts[pts.length - 1].p.distanceTo(head) >= (T.minVertexDistance || 0.1) * 0.5) pts.push({ p: head.clone(), age: p.age });
      const life = mmc(T.lifetime, p.curT ?? 0, p.r[5]) * p.life;
      while (pts.length && (p.age - pts[0].age > life || pts.length > TRAIL_PTS)) pts.shift();
      const n = pts.length + 1;                       // the stored points, then the particle
      if (n < 2) continue;
      mmg(T.colorOverLifetime, p.curT ?? 0, p.r[3], _c2);
      const base = T.inheritParticleColor ? p.color : WHITE;
      for (let i = 0; i < n; i++) {
        const frac = 1 - i / (n - 1);                 // 0 at the particle, 1 at the oldest point
        const a = i - 1 < 0 ? pts[0].p : pts[i - 1].p;
        const b = i + 1 >= pts.length ? head : pts[i + 1].p, c = i < pts.length ? pts[i].p : head;
        _side.crossVectors(v1.subVectors(b, a), v2.subVectors(cam, c));
        if (_side.lengthSq() < 1e-12) _side.set(1, 0, 0);
        const w = mmc(T.widthOverTrail, frac, p.r[2]) * (T.sizeAffectsWidth ? (p.curSize ?? 1) : 1);
        _side.normalize().multiplyScalar(w * 0.5);
        L[i].copy(c).add(_side); R[i].copy(c).sub(_side); F[i] = frac;
      }
      for (let i = 0; i < n - 1 && v + 6 <= P.length / 3; i++) {
        mmg(T.colorOverTrail, F[i], p.r[1], _ca); mmg(T.colorOverTrail, F[i + 1], p.r[1], _cb);
        for (let k = 0; k < 4; k++) { _ca[k] *= _c2[k] * base[k]; _cb[k] *= _c2[k] * base[k]; }
        v = this.put(v, L[i], F[i], 1, _ca); v = this.put(v, R[i], F[i], 0, _ca); v = this.put(v, L[i + 1], F[i + 1], 1, _cb);
        v = this.put(v, R[i], F[i], 0, _ca); v = this.put(v, R[i + 1], F[i + 1], 0, _cb); v = this.put(v, L[i + 1], F[i + 1], 1, _cb);
      }
    }
    const g = this.mesh.geometry;
    g.setDrawRange(0, v);
    for (const k of ['position', 'uv', 'color']) g.attributes[k].needsUpdate = true;
  }
}

// ------------------------------------------------------------------ world: all systems of the character
export class ParticleWorld {
  constructor({ scene, camera, vfx, nodeByIndex, associations, assetBase, textures }) {
    this.scene = scene; this.camera = camera; this.vfx = vfx; this.nodeByIndex = nodeByIndex;
    this.time = 0;
    this.flipY = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI);
    this.tanHalfFov = { value: Math.tan(camera.fov * DEG / 2) };
    this.textures = textures || vfx.textures.map(t => {
      const tex = new THREE.TextureLoader().load(assetBase + t.src);
      tex.colorSpace = THREE.NoColorSpace; tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
      return tex;
    });
    this.quad = new THREE.PlaneGeometry(1, 1);
    this.meshGeos = vfx.meshes.map(m => {
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.Float32BufferAttribute(m.pos, 3));
      g.setAttribute('uv', new THREE.Float32BufferAttribute(m.uv, 2));
      if (m.color) g.setAttribute('meshColor', new THREE.Float32BufferAttribute(m.color, 4));
      g.setIndex(m.index);
      return g;
    });
    this.emitters = vfx.systems.filter(s => nodeByIndex[s.node]).map(s => new Emitter(this, s));
    this.byNode = new Map(this.emitters.map(e => [e.d.node, e]));
    for (const e of this.emitters) {
      e.subs = (e.d.subEmitters || []).map(s => ({ ...s, emitter: this.byNode.get(s.node) })).filter(s => s.emitter);
      e.subs.forEach(s => { s.emitter.isSub = true; });
    }
    // Play(withChildren) reaches every descendant system except sub-emitters
    for (const e of this.emitters) {
      e.node.traverse(o => {
        if (o === e.node) return;
        const a = associations.get(o), c = a && this.byNode.get(a.nodes);
        if (c && !c.isSub && !e.children.includes(c)) e.children.push(c);
      });
    }
    this.ambient = this.emitters.filter(e => e.d.playOnAwake && e.d.active && !e.isSub);
    this.rotators = (vfx.rotators || []).map(r => ({ node: nodeByIndex[r.node], speed: r.speed })).filter(r => r.node);
    this.enabled = true;
  }
  start() { this.ambient.forEach(e => e.play(false)); }
  playNode(nodeIndex) { const e = this.byNode.get(nodeIndex); if (e && this.enabled) e.play(true); }
  clearTriggered() { this.emitters.forEach(e => { if (!this.ambient.includes(e)) e.clear(); }); }
  setEnabled(on) {
    this.enabled = on;
    this.emitters.forEach(e => { e.mesh.visible = on && e.rendererEnabled; if (!on) e.clear(); });
    if (on) this.start();
  }
  update(dt) {
    this.time += dt;
    this.tanHalfFov.value = Math.tan(this.camera.fov * DEG / 2);
    // RotateXYZBehaviour: transform.Rotate(X, Y, Z degrees per second) in local space
    for (const r of this.rotators) {
      e1.set(r.speed[0] * DEG * dt, -r.speed[1] * DEG * dt, -r.speed[2] * DEG * dt, 'YXZ');
      r.node.quaternion.multiply(q1.setFromEuler(e1));
    }
    if (!this.enabled) return;
    for (const e of this.emitters) e.update(dt);
  }
}
