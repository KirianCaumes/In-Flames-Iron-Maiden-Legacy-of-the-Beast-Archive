import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { curveEval } from './curves.js';
import { ParticleWorld } from './particles.js';

// The "In The Dark" battle arenas (InFlames_road1, hellgate_road2, InFlames_roadEnd), exported by tools/export_arena.py.
// Shaders are ports of the game's RHI/BlackcombEnv* programs (LOD 220, shadows off), evaluated in gamma space.

const DEG = Math.PI / 180;
const _euler = new THREE.Euler(), _u = [0, 0, 0];

const ENV_VERT = /* glsl */`
attribute vec4 color;
uniform mat4 toUnity;            // viewer world -> Unity world (arena placement undone, X mirrored back)
uniform vec3 rampOffset, rampRange;
uniform vec4 vcMult, st;
varying vec4 vColor; varying vec2 vUv; varying float vFog;
void main() {
  vec4 wp = modelMatrix * vec4(position, 1.0);
  vec3 wu = (toUnity * wp).xyz;
  vFog = min(length((wu - rampOffset) * rampRange), 1.0);
  vColor = color * vcMult;
  vUv = uv * st.xy + st.zw;
  gl_Position = projectionMatrix * viewMatrix * wp;
}`;
// RHI/BlackcombEnvDistanceRampBakeLights(UVScroll): baked colour + self-illumination blended with the fog ramp
const ENV_FRAG = /* glsl */`
uniform sampler2D map, fogRamp;
varying vec4 vColor; varying vec2 vUv; varying float vFog;
void main() {
  vec3 t = texture2D(map, vUv).rgb;
  vec3 a = t * vColor.a;
#ifdef FOG
  vec4 f = texture2D(fogRamp, vec2(vFog, 0.0));
  a += f.a * (f.rgb - a);
#endif
  gl_FragColor = vec4(t * vColor.rgb + a, 1.0);
}`;
// RHI/BlackcombEnvTransparentUVScroll: texture * vertex colour, Blend SrcAlpha [_AlphaMode], ZWrite Off
const TRANS_VERT = /* glsl */`
attribute vec4 color;
uniform vec4 st;
varying vec4 vColor; varying vec2 vUv;
void main() {
  vColor = color; vUv = uv * st.xy + st.zw;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}`;
const TRANS_FRAG = /* glsl */`
uniform sampler2D map;
varying vec4 vColor; varying vec2 vUv;
void main() { gl_FragColor = texture2D(map, vUv) * vColor; }`;
const UNLIT_FRAG = /* glsl */`
uniform sampler2D map;
varying vec4 vColor; varying vec2 vUv;
void main() { gl_FragColor = vec4(texture2D(map, vUv).rgb, 1.0); }`;

const curveFactor = (a, t) => {
  const d = Math.max(a.duration, 1e-4), x = t - (a.delay || 0);
  return a.loop ? (((x % d) + d) % d) / d : Math.min(Math.max(x / d, 0), 1);
};

export class Arena {
  constructor({ scene, camera, data, assetBase }) {
    this.scene = scene; this.camera = camera; this.data = data; this.base = assetBase;
    this.group = new THREE.Group(); this.group.name = 'arena'; this.group.visible = false;
    scene.add(this.group);
    this.loaded = {};                     // scene key -> { root, materials, world, ... }
    this.pending = {};                    // scene key -> download in progress
    this.cache = {};                      // "index" or "index clamp" -> THREE.Texture
    this.vcMult = { value: new THREE.Vector4(1, 1, 1, 1) };
    this.toUnity = { value: new THREE.Matrix4() };
    this.time = 0; this.key = null; this.period = 'present'; this.fx = true;
    this.params = { lightDir: new THREE.Vector3() };
  }

  tex(i, clamp = false) {
    if (i == null) return null;
    const key = clamp ? `${i} clamp` : `${i}`;    // a texture used both ways gets one object per wrap mode
    if (!this.cache[key]) {
      const t = new THREE.TextureLoader().load(this.base + this.data.textures[i].src);
      t.colorSpace = THREE.NoColorSpace;
      t.wrapS = t.wrapT = clamp ? THREE.ClampToEdgeWrapping : THREE.RepeatWrapping;
      this.cache[key] = t;
    }
    return this.cache[key];
  }

  // Unity world -> viewer world: put the character's battle slot at the origin, facing +Z.
  place(slot) {
    const m = new THREE.Matrix4().compose(
      new THREE.Vector3(-slot.pos[0], slot.pos[1], slot.pos[2]),
      new THREE.Quaternion(slot.rot[0], -slot.rot[1], -slot.rot[2], slot.rot[3]), new THREE.Vector3(1, 1, 1));
    m.invert();
    this.group.matrixAutoUpdate = false;
    this.group.matrix.copy(m);
    this.group.updateMatrixWorld(true);
    const mirror = new THREE.Matrix4().makeScale(-1, 1, 1);
    this.toUnity.value.copy(mirror).multiply(m.clone().invert());
  }

  material(sceneData, id) {
    const M = this.data.materials[id];
    const uni = {
      map: { value: this.tex(M.tex) }, st: { value: new THREE.Vector4(...M.st) },
    };
    if (/Particles/.test(M.shader)) return null;
    if (M.shader === 'RHI/BlackcombEnvTransparentUVScroll') {
      return new THREE.ShaderMaterial({
        uniforms: uni, vertexShader: TRANS_VERT, fragmentShader: TRANS_FRAG, transparent: true, depthWrite: false,
        blending: THREE.CustomBlending, blendSrc: THREE.SrcAlphaFactor,
        blendDst: M.alphaMode === 1 ? THREE.OneFactor : THREE.OneMinusSrcAlphaFactor,
        blendSrcAlpha: THREE.ZeroFactor, blendDstAlpha: THREE.OneFactor,
      });
    }
    if (M.shader === 'RHI/BlackcombSkyBox') {
      return new THREE.ShaderMaterial({ uniforms: uni, vertexShader: TRANS_VERT, fragmentShader: UNLIT_FRAG });
    }
    Object.assign(uni, {
      fogRamp: sceneData.fogRampUniform, rampOffset: { value: new THREE.Vector3(...M.rampOffset) },
      rampRange: { value: new THREE.Vector3(...M.rampRange) }, vcMult: this.vcMult, toUnity: this.toUnity,
    });
    if (!/UVScroll/.test(M.shader)) uni.st.value.set(1, 1, 0, 0);      // the plain variant ignores _MainTex_ST
    return new THREE.ShaderMaterial({
      uniforms: uni, vertexShader: ENV_VERT, fragmentShader: ENV_FRAG, defines: M.fog ? { FOG: 1 } : {},
    });
  }

  // One download per scene, shared by every caller (the viewer starts the default arena early); progress events go
  // to all the callers' listeners.
  load(key, onProgress) {
    if (this.loaded[key]) return Promise.resolve(this.loaded[key]);
    const p = this.pending[key] ||= { listeners: new Set() };
    if (onProgress) p.listeners.add(onProgress);
    p.promise ||= this.build(key, e => p.listeners.forEach(f => f(e))).finally(() => delete this.pending[key]);
    return p.promise;
  }

  async build(key, onProgress) {
    const sd = this.data.scenes[key];
    const gltf = await new GLTFLoader().loadAsync(`${this.base}${key}.glb`, onProgress);
    const root = gltf.scene;
    const assoc = gltf.parser.associations;
    const nodes = [];
    root.traverse(o => {
      const a = assoc.get(o);
      if (a && a.nodes !== undefined && nodes[a.nodes] === undefined) nodes[a.nodes] = o;
      if (o.userData && o.userData.inactive) o.visible = false;
    });
    const L = { root, nodes, sd, fogRampUniform: { value: null }, mats: new Map(), scroll: [], eulers: [] };
    root.traverse(o => {
      if (!o.isMesh) return;
      const id = o.material.userData.id;
      if (!L.mats.has(id)) {
        const m = this.material(L, id);
        if (m) m.userData.shader = this.data.materials[id].shader;
        L.mats.set(id, m);
      }
      const m = L.mats.get(id);
      if (!m) { o.visible = false; return; }
      o.material = m;
      if (m.transparent) o.renderOrder = 1;
    });
    // ControlUVOffsetsFromCurve works on the renderer's own material instance
    for (const s of sd.scrollers) {
      const n = nodes[s.node]; if (!n || s.prop !== '_MainTex') continue;
      const prims = n.isMesh ? [n] : n.children.filter(c => c.isMesh);
      const o = prims[s.matIndex] || prims[0];
      if (!o || !o.material.uniforms || !/UVScroll/.test(o.material.userData.shader)) continue;   // others ignore _MainTex_ST
      const src = o.material.uniforms;
      o.material = o.material.clone();
      for (const k of Object.keys(src)) if (k !== 'st') o.material.uniforms[k] = src[k];   // share all but the offset
      L.scroll.push({ s, st: o.material.uniforms.st.value });
    }
    // ControlEulerAnglesFromCurve: localEulerAngles = curves(t) + start angles (space Self)
    for (const e of sd.eulers) {
      const n = nodes[e.node]; if (!n) continue;
      const g = new THREE.Euler().setFromQuaternion(n.quaternion, 'YXZ');
      L.eulers.push({ e, n, start: [g.x / DEG, -g.y / DEG, -g.z / DEG] });   // back to Unity's degrees
    }
    // particles and RotateXYZBehaviour reuse the character's particle engine
    const vfx = { systems: sd.systems, materials: this.data.materials, textures: this.data.textures, meshes: [],
      rotators: sd.rotators };
    const textures = this.data.textures.map((t, i) => (sd.systems.some(s => {
      const r = s.renderer; return r && r.material != null && this.data.materials[r.material].tex === i;
    }) ? this.tex(i) : null));
    L.world = new ParticleWorld({ scene: this.scene, camera: this.camera, vfx, nodeByIndex: nodes,
      associations: assoc, assetBase: this.base, textures });
    L.allAmbient = L.world.ambient.slice();
    root.visible = false;
    this.group.add(root);
    this.loaded[key] = L;
    return L;
  }

  // display a scene that load() has finished
  show(key, period) {
    const L = this.loaded[key];
    for (const o of Object.values(this.loaded)) {
      if (o !== L) { o.root.visible = false; o.world.setEnabled(false); }
    }
    this.key = key; this.current = L;
    L.root.visible = true; this.group.visible = true;
    this.setPeriod(period);
    return L;
  }

  hide() {
    this.group.visible = false;
    for (const o of Object.values(this.loaded)) { o.root.visible = false; o.world.setEnabled(false); }
    this.current = null;
  }

  // the viewer's "Particle effects" option
  setFx(on) {
    this.fx = on;
    if (this.current) this.current.world.setEnabled(on);
  }

  // SetVisibleByTimePeriod: one root per period; the period also picks the fog ramp and the character rim colour.
  setPeriod(period) {
    const L = this.current; if (!L) return;
    this.period = period;
    const P = L.sd.periods;
    for (const [name, p] of Object.entries(P)) {
      for (const r of p.roots) if (L.nodes[r]) L.nodes[r].visible = name === period;
    }
    L.fogRampUniform.value = this.tex(P[period].fogRamp, true);
    const visible = o => { for (let n = o; n; n = n.parent) if (!n.visible) return false; return true; };
    L.world.setEnabled(false);
    L.world.ambient = L.allAmbient.filter(e => visible(e.node));
    L.world.setEnabled(this.fx);
  }

  // values BlackShaderManager pushes into the character materials (one object, updated in place every frame)
  characterParams() {
    const L = this.current; if (!L) return null;
    const P = L.sd.periods[this.period], custom = L.sd.shaderManager.custom;
    const light = L.nodes[P.light], p = this.params;
    p.lightDir.set(0, 0, 0);
    if (light) { light.updateWorldMatrix(true, false); light.getWorldDirection(p.lightDir).negate(); }
    p.rimColor = P.rimColor; p.ramp = this.tex(custom._CharacterLightRampTex.tex, true);
    p.rampScale = custom._CharacterLightRampHDRScale; p.shadowColor = P.shadowColor;
    return p;
  }

  setDim(c) { this.vcMult.value.set(c[0], c[1], c[2], c[3] ?? 1); }

  update(dt) {
    const L = this.current; if (!L) return;
    this.time += dt;
    for (const { s, st } of L.scroll) {
      const f = curveFactor(s, this.time);
      st.z = curveEval(s.x, f); st.w = curveEval(s.y, f);
    }
    for (const { e, n, start } of L.eulers) {
      const f = curveFactor(e, this.time);
      _u[0] = curveEval(e.x, f); _u[1] = curveEval(e.y, f); _u[2] = curveEval(e.z, f);
      if (e.addStart) for (let i = 0; i < 3; i++) _u[i] += start[i];
      n.quaternion.setFromEuler(_euler.set(_u[0] * DEG, -_u[1] * DEG, -_u[2] * DEG, 'YXZ'));
    }
    L.world.update(dt);
  }
}
