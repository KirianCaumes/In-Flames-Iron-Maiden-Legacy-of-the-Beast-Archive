import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { CHAR_VERT, CHAR_FRAG, gammaTexture } from './character.js';
import { curveEval, clamp01 } from './curves.js';

// Other characters (the enemies of the In The Dark battles), exported by tools/build_character.py with their idle
// clip. Same character shaders as Jesterhead; material parameters come from the glTF material extras.
export class Npc {
  constructor(scene) {
    this.scene = scene; this.root = new THREE.Group(); this.root.visible = false;
    scene.add(this.root); this.mixer = null; this.materials = []; this.tweens = []; this.time = 0;
  }

  async load(url) {
    const gltf = await new GLTFLoader().loadAsync(url);
    const parser = gltf.parser, ramps = new Map();
    // Ramps are sampled with Unity's (bottom-up) coordinates, so their image is flipped as it is for Jesterhead's.
    // WebGL ignores flipY for an ImageBitmap, which GLTFLoader decodes to in most browsers: such an image is
    // decoded again, flipped.
    const ramp = i => {
      if (i == null) return null;
      if (!ramps.has(i)) ramps.set(i, parser.getDependency('texture', i).then(async src => {
        const t = gammaTexture(src, true);           // a copy with the glTF sampler settings
        if (typeof ImageBitmap !== 'undefined' && t.image instanceof ImageBitmap) {
          t.source = new THREE.Source(await createImageBitmap(t.image,
            { imageOrientation: 'flipY', premultiplyAlpha: 'none', colorSpaceConversion: 'none' }));
        } else t.flipY = true;
        t.needsUpdate = true;
        return t;
      }));
      return ramps.get(i);
    };
    const jobs = [];
    gltf.scene.traverse(o => {
      if (!o.isMesh) return;
      o.frustumCulled = false; o.castShadow = true;
      const src = o.material, P = src.userData || {};
      jobs.push((async () => {
        const [rampMap, rimMap] = await Promise.all([ramp(P.rampTex), ramp(P.rimTex)]);
        const m = new THREE.ShaderMaterial({
          vertexShader: CHAR_VERT, fragmentShader: CHAR_FRAG,
          defines: /wCustomColor/.test(P.shader || '') ? { CUSTOM: 1 } : {},
          uniforms: {
            map: { value: src.map ? gammaTexture(src.map) : null },
            rampMap: { value: rampMap }, hasRamp: { value: !!rampMap }, rimMap: { value: rimMap }, hasRim: { value: !!rimMap },
            rampScale: { value: P.rampScale ?? 1 }, rimBlend: { value: P.rimBlend ?? 1 },
            rimColor: { value: new THREE.Vector3(...(P.rimColor || [0, 0, 0]).slice(0, 3)) },
            emissiveColor: { value: new THREE.Vector3(...(P.emissiveColor || [0, 0, 0]).slice(0, 3)) },
            emissiveBlend: { value: /wEmissiveColor/.test(P.shader || '') ? (P.emissiveBlend ?? 0) : 0 },
            customColor: { value: new THREE.Vector3(...(P.customColor || [0, 0, 0]).slice(0, 3)) },
            customBlend: { value: P.customBlend ?? 0 },
            uLightDir: { value: new THREE.Vector3(-(P.lightDir || [1])[0], (P.lightDir || [1, 10])[1], (P.lightDir || [1, 10, 2])[2]) },
            uRescale: { value: P.rescaleNormal ?? 1 },
          },
        });
        // looping TweenMaterialProperty pulses exported with the material (glow of the veins, rim)
        const uniform = { _EmissiveBlend: 'emissiveBlend', _RimBlend: 'rimBlend' };
        for (const t of P.tweens || []) if (m.uniforms[uniform[t.prop]]) this.tweens.push({ ...t, u: m.uniforms[uniform[t.prop]] });
        this.materials.push({ m, defaults: { lightDir: m.uniforms.uLightDir.value.clone(), rimColor: m.uniforms.rimColor.value.clone(),
          rampMap, hasRamp: !!rampMap, rampScale: m.uniforms.rampScale.value } });
        o.material = m;
      })());
    });
    await Promise.all(jobs);
    this.model = gltf.scene;
    this.root.add(this.model);
    if (gltf.animations.length) {
      this.mixer = new THREE.AnimationMixer(this.model);
      this.mixer.clipAction(gltf.animations[0]).play();
    }
    return this;
  }

  place(matrix) { matrix.decompose(this.root.position, this.root.quaternion, this.root.scale); }

  // the same BlackShaderManager overrides as for Jesterhead (AddMaterialsForCharacters)
  setEnvironment(p) {
    for (const { m, defaults: d } of this.materials) {
      const u = m.uniforms;
      u.uLightDir.value.copy(p ? p.lightDir : d.lightDir);
      if (p) u.rimColor.value.set(p.rimColor[0], p.rimColor[1], p.rimColor[2]); else u.rimColor.value.copy(d.rimColor);
      u.rampMap.value = p ? p.ramp : d.rampMap; u.hasRamp.value = p ? true : d.hasRamp;
      u.rampScale.value = p ? p.rampScale : d.rampScale;
    }
  }

  update(dt) {
    if (!this.root.visible) return;
    if (this.mixer) this.mixer.update(dt);
    this.time += dt;
    for (const t of this.tweens) {                   // value = Lerp(from, to, curve(time / duration)), looped
      const x = this.time - t.delay;
      if (x >= 0) t.u.value = t.from + (t.to - t.from) * clamp01(curveEval(t.curve, (x % t.duration) / t.duration));
    }
  }
}
