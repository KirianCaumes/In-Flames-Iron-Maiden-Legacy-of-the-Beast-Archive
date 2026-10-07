// Character rendering, ported from the game's shaders and scripts:
//  - RHI/BlackcombCharacter_wEmissiveColor (pass LOD 230): unlit texture x light ramp, + emissive (texture alpha mask), + rim ramp
//  - Legacy Shaders/Particles/Alpha Blended for the scrolling ghost faces (2 x _TintColor x texture)
//  - TweenMaterialProperty / ResetTweensOnEnable (NGUI UITweener) driving _EmissiveBlend
//  - ControlUVOffsetsFromCurve on the ghost faces
import * as THREE from 'three';
import { curveEval, clamp01 } from './curves.js';

const SKIN_VERT_HEAD = /* glsl */`
  #include <common>
  #include <skinning_pars_vertex>
`;
const SKIN_VERT_BODY = /* glsl */`
  #include <beginnormal_vertex>
  #include <skinbase_vertex>
  #include <skinnormal_vertex>
  #include <begin_vertex>
  #include <skinning_vertex>
`;

export const CHAR_VERT = SKIN_VERT_HEAD + /* glsl */`
  uniform vec3 uLightDir; uniform float uRescale;
  varying vec2 vUv; varying float vNdl; varying float vRim;
  void main() {
    ${SKIN_VERT_BODY}
    vec4 world = modelMatrix * vec4(transformed, 1.0);
    vec3 n = normalize(mat3(modelMatrix) * objectNormal);
    vNdl = dot(n * uRescale, uLightDir) * 0.5 + 0.5; // half-lambert against the material's fixed light direction
    vec4 clip = projectionMatrix * viewMatrix * world;
    gl_Position = clip;
    vRim = dot(cameraPosition - world.xyz, n) / clip.w;   // the game's rim term does not use _RescaleNormal
    vUv = uv;
  }`;
// RHI/BlackcombCharacter (emissiveBlend 0), _wEmissiveColor, and _wCustomColor (CUSTOM: lerp towards a fixed colour, masked by alpha)
export const CHAR_FRAG = /* glsl */`
  uniform sampler2D map; uniform sampler2D rampMap; uniform sampler2D rimMap;
  uniform bool hasRamp; uniform bool hasRim;
  uniform float rampScale; uniform float emissiveBlend; uniform float rimBlend;
  uniform vec3 emissiveColor; uniform vec3 rimColor;
#ifdef CUSTOM
  uniform vec3 customColor; uniform float customBlend;
#endif
  varying vec2 vUv; varying float vNdl; varying float vRim;
  void main() {
    vec3 ramp = hasRamp ? texture2D(rampMap, vec2(vNdl, 1.0)).rgb : vec3(1.0);
    vec4 tex = texture2D(map, vUv);
    vec3 c = tex.rgb * ramp * rampScale;
#ifdef CUSTOM
    c += tex.a * customBlend * (customColor - c);
#endif
    c += tex.a * emissiveColor * emissiveBlend;
    vec3 rim = (hasRim ? texture2D(rimMap, vec2(1.0, vRim)).rgb : vec3(1.0)) * rimColor;
    gl_FragColor = vec4(c + rim * rimBlend, 1.0);
  }`;
const GHOST_VERT = SKIN_VERT_HEAD + /* glsl */`
  varying vec2 vUv;
  void main() {
    ${SKIN_VERT_BODY}
    gl_Position = projectionMatrix * viewMatrix * modelMatrix * vec4(transformed, 1.0);
    vUv = uv;
  }`;
const GHOST_FRAG = /* glsl */`
  uniform sampler2D map; uniform vec4 tint; uniform vec2 offset;
  varying vec2 vUv;
  void main() {
    vec4 c = 2.0 * tint * texture2D(map, vUv + offset);
    gl_FragColor = vec4(c.rgb, clamp(c.a, 0.0, 1.0));
  }`;

export const gammaTexture = (tex, clamp = false) => {
  const t = tex.clone();
  t.colorSpace = THREE.NoColorSpace;                   // the game renders in gamma space
  if (clamp) t.wrapS = t.wrapT = THREE.ClampToEdgeWrapping;
  t.needsUpdate = true;
  return t;
};

export class CharacterLook {
  constructor({ model, vfx, vfxTextures }) {
    this.vfx = vfx;
    this.meshes = {};
    this.ghosts = [];
    this.materials = new Set();
    const mirror = v => new THREE.Vector3(-v[0], v[1], v[2]);   // Unity space -> GLB space
    model.traverse(o => {
      if (!o.isMesh) return;
      o.frustumCulled = false;
      const src = o.material, P = vfx.charMats[src.name];
      if (!P) return;
      if (/Legacy Shaders\/Particles/.test(P.shader || '')) {
        o.material = new THREE.ShaderMaterial({
          vertexShader: GHOST_VERT, fragmentShader: GHOST_FRAG,
          uniforms: { map: { value: gammaTexture(src.map) }, tint: { value: new THREE.Vector4(...P.tintColor) }, offset: { value: new THREE.Vector2() } },
          transparent: true, depthWrite: false, side: THREE.DoubleSide,
          blending: THREE.CustomBlending, blendSrc: THREE.SrcAlphaFactor, blendDst: THREE.OneMinusSrcAlphaFactor,
          blendSrcAlpha: THREE.OneFactor, blendDstAlpha: THREE.OneMinusSrcAlphaFactor,
          // the face layers lie exactly on the body surface but are triangulated differently: pull them towards
          // the camera so the depth test never flickers between the two (z-fighting)
          polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -4,
        });
        o.renderOrder = 2 + this.ghosts.length; o.castShadow = false;   // fixed order: no sorting swaps between layers
        const goName = Object.keys(vfx.overlayScroll || {}).find(n => THREE.PropertyBinding.sanitizeNodeName(n) + '_skinned' === o.name);
        this.ghosts.push({ mesh: o, scroll: goName ? vfx.overlayScroll[goName] : null });
      } else {
        o.material = new THREE.ShaderMaterial({
          vertexShader: CHAR_VERT, fragmentShader: CHAR_FRAG,
          uniforms: {
            map: { value: gammaTexture(src.map) },
            rampMap: { value: P.rampTex != null ? vfxTextures[P.rampTex] : null }, hasRamp: { value: P.rampTex != null },
            rimMap: { value: P.rimTex != null ? vfxTextures[P.rimTex] : null }, hasRim: { value: P.rimTex != null },
            rampScale: { value: P.rampScale }, emissiveBlend: { value: P.emissiveBlend }, rimBlend: { value: P.rimBlend },
            emissiveColor: { value: new THREE.Vector3(...P.emissiveColor.slice(0, 3)) },
            rimColor: { value: new THREE.Vector3(...P.rimColor.slice(0, 3)) },
            uLightDir: { value: mirror(P.lightDir) }, uRescale: { value: P.rescaleNormal },
          },
        });
        o.castShadow = true;
      }
      this.materials.add(o.material);
      this.meshes[o.name] = o;
    });
    // material values, kept so that an arena's BlackShaderManager overrides can be undone
    this.defaults = [...this.materials].filter(m => m.uniforms.uLightDir).map(m => ({
      m, lightDir: m.uniforms.uLightDir.value.clone(), rimColor: m.uniforms.rimColor.value.clone(),
      rampMap: m.uniforms.rampMap.value, hasRamp: m.uniforms.hasRamp.value, rampScale: m.uniforms.rampScale.value,
    }));
    for (const i of [P => P.rampTex, P => P.rimTex].flatMap(f => Object.values(vfx.charMats).map(f)).filter(x => x != null)) {
      vfxTextures[i].wrapS = vfxTextures[i].wrapT = THREE.ClampToEdgeWrapping;
    }

    // TweenMaterialProperty: value = Mathf.Lerp(from, to, curve(factor)) (Lerp clamps), style 0 = once, 1 = loop
    this.tweens = vfx.tweens.map((t, index) => {
      const key = THREE.PropertyBinding.sanitizeNodeName(t.name);
      const target = this.meshes[key] || this.meshes[key + '_skinned'];
      return target && target.material.uniforms?.emissiveBlend ? { ...t, index, target, time: -t.delay } : null;
    }).filter(Boolean);
    this.ghostTime = 0;
    this.tweenOf = new Map(); this.factorOf = new Map();   // per frame: target -> winning tween and its factor
    this.setGhosts(true);
  }

  // ResetTweensOnEnable: each tween that was enabled at start is reset to the beginning and played forward
  restartTweens(goName) {
    for (const i of this.vfx.tweenResets[goName] || []) {
      const t = this.tweens.find(x => x.index === i);
      if (t) t.time = -t.delay;
    }
  }

  // BlackShaderManager.UpdateAllMaterials: _LightDir = -forward of the arena's directional light, plus the custom
  // parameters (_CharacterLightRampTex, _CharacterLightRampHDRScale) and the time period's _RimColor.
  setEnvironment(p) {
    for (const d of this.defaults) {
      const u = d.m.uniforms;
      u.uLightDir.value.copy(p ? p.lightDir : d.lightDir);
      if (p) u.rimColor.value.set(p.rimColor[0], p.rimColor[1], p.rimColor[2]); else u.rimColor.value.copy(d.rimColor);
      u.rampMap.value = p ? p.ramp : d.rampMap;
      u.hasRamp.value = p ? true : d.hasRamp;
      u.rampScale.value = p ? p.rampScale : d.rampScale;
    }
  }

  setGhosts(on) { this.ghosts.forEach(g => g.mesh.visible = on); }
  setWireframe(on) { this.materials.forEach(m => m.wireframe = on); }

  update(dt) {
    const tweenOf = this.tweenOf, factorOf = this.factorOf;
    tweenOf.clear(); factorOf.clear();
    for (const t of this.tweens) {
      t.time += dt;
      if (t.time < 0) continue;
      if (t.style === 1 && !tweenOf.has(t.target)) { tweenOf.set(t.target, t); factorOf.set(t.target, (t.time % t.duration) / t.duration); }
    }
    for (const t of this.tweens) {             // a running one-shot tween wins over the idle loop
      if (t.style !== 1 && t.time >= 0 && t.time <= t.duration) { tweenOf.set(t.target, t); factorOf.set(t.target, t.time / t.duration); }
    }
    for (const [target, t] of tweenOf) {
      target.material.uniforms.emissiveBlend.value = t.from + (t.to - t.from) * clamp01(curveEval(t.curve, factorOf.get(target)));
    }
    // ControlUVOffsetsFromCurve on the ghost faces (offset replaces the material offset baked into the UVs)
    this.ghostTime += dt;
    for (const g of this.ghosts) {
      if (!g.scroll) continue;
      const s = g.scroll, u = (this.ghostTime % s.duration) / s.duration;
      g.mesh.material.uniforms.offset.value.set(curveEval(s.x, u) - s.st[2], -(curveEval(s.y, u) - s.st[3]));
    }
  }
}

// DirectionalLightFX ("ScreenDimmer"): environment light coefficient = Lerp(darkColor, white, curve(t)) for updateTime seconds.
// BlackShaderManager feeds it to the arena as _VertexColorMultiplier; the character shader ignores it.
// Without an arena, the page background is multiplied by that colour instead.
export class LightFx {
  constructor(vfx, overlay) {
    this.defs = vfx.lightFx || {}; this.overlay = overlay; this.active = null; this.target = null;
  }
  setTarget(fn) { this.target = fn; this.apply([1, 1, 1]); }   // fn(rgb) or null for the page background
  apply(c) {
    if (this.target) { this.target(c); this.overlay.style.backgroundColor = '#fff'; return; }
    this.overlay.style.backgroundColor = `rgb(${c.map(x => Math.round(255 * Math.min(1, Math.max(0, x)))).join(',')})`;
  }
  enable(name) {
    const d = this.defs[name];
    if (d && !this.active) this.active = { d, timer: d.delay > 0 ? d.delay : d.duration, phase: d.delay > 0 ? 1 : 2 };
  }
  disable() { this.active = null; this.apply([1, 1, 1]); }
  update(dt) {
    const a = this.active;
    if (!a) return;
    a.timer -= dt;
    if (a.phase === 1) { if (a.timer <= 0) { a.phase = 2; a.timer = a.d.duration; } return; }
    if (a.timer <= 0) { this.disable(); return; }
    const v = clamp01(curveEval(a.d.curve, 1 - a.timer / a.d.duration));
    this.apply(a.d.darkColor.slice(0, 3).map(x => x + (1 - x) * v));
  }
}
