import * as THREE from 'three';
import { ParticleWorld } from './particles.js';
import { CharacterLook } from './character.js';
import { Chains } from './chains.js';

const _hip = new THREE.Vector3();

// One Jesterhead in the scene: model, animation, effect triggers (PlayParticlesWithAudio), particles, shading, chains.
// Several can stand on the battle slots; they all play the same animation.
// asset: { scene, animations, associations } (a copy of the parsed glTF, see main.js); textures: particle textures
// already loaded by another Jesterhead, shared instead of loaded again.
export class Jester {
  constructor({ asset, vfx, scene, camera, assetBase, textures, onToggle }) {
    this.vfx = vfx; this.onToggle = onToggle || (() => {});
    this.root = new THREE.Group(); scene.add(this.root);
    this.model = asset.scene; this.root.add(this.model);
    const assoc = asset.associations;
    this.nodeByIndex = [];
    this.model.traverse(o => {
      const a = assoc.get(o);
      if (a && a.nodes !== undefined && this.nodeByIndex[a.nodes] === undefined) this.nodeByIndex[a.nodes] = o;
    });
    this.hip = this.model.getObjectByName('CenterHip_joint');
    this.particles = new ParticleWorld({ scene, camera, vfx, nodeByIndex: this.nodeByIndex, associations: assoc, assetBase, textures });
    this.look = new CharacterLook({ model: this.model, vfx, vfxTextures: this.particles.textures });
    this.chains = new Chains(this.model, vfx, this.nodeByIndex);
    this.mixer = new THREE.AnimationMixer(this.model);
    this.clips = Object.fromEntries(asset.animations.map(c => [c.name, c]));
    this.triggersByState = {};
    for (const t of vfx.triggers) (this.triggersByState[t.state] ||= []).push(t);
    this.initialActive = { VFX_bodyglow: true };             // active in the prefab, then SetInitialGeoState()
    for (const t of vfx.triggers) if (t.toggleName && t.startUnToggled) this.initialActive[t.toggleName] = !t.toggleEnable;
    this.active = { ...this.initialActive };
    this.fired = []; this.timeInState = 0; this.prevTime = 0; this.entry = null; this.action = null;
    this.particles.start();
  }

  setActive(name, on) {                                      // GameObject.SetActive with OnEnable / OnDisable
    const was = this.active[name] ?? false;
    this.active[name] = on;
    if (on && !was && /bodyglow/i.test(name)) this.look.restartTweens(name);
    if (on !== was) this.onToggle(name, on);
  }

  play(entry, loop) {
    this.entry = entry;
    // a clip cut short must not leave its toggled objects (screen dimmer, body glow) in their mid-ability state
    for (const [name, on] of Object.entries(this.initialActive)) this.setActive(name, on);
    this.mixer.stopAllAction();
    this.action = this.mixer.clipAction(this.clips[entry.clip]);
    this.setLoop(loop); this.action.reset().play();
    this.particles.clearTriggered();
    this.prevTime = 0;
  }

  setLoop(loop) {
    if (!this.action) return;
    this.action.setLoop(loop ? THREE.LoopRepeat : THREE.LoopOnce, Infinity);
    this.action.clampWhenFinished = true;
  }

  enterState() {
    this.timeInState = 0;
    this.fired = this.entry.states.flatMap(s => (this.triggersByState[s] || []).map(t => ({ t, done: false })));
  }

  // join a state another Jesterhead entered `time` seconds ago: the triggers already past do not fire, but their
  // toggles are applied so the objects are in the same state
  enterStateAt(time) {
    this.enterState();
    this.timeInState = time;
    for (const f of [...this.fired].sort((a, b) => a.t.delay - b.t.delay)) {
      if (f.t.delay >= time) continue;
      f.done = true;
      if (f.t.toggleName) this.setActive(f.t.toggleName, f.t.toggleEnable);
    }
  }

  // PlayParticlesWithAudio.UpdateSpecifics
  fire(t) {
    if (!t.playAudioOnly) this.particles.playNode(t.node);
    if (t.toggleName) this.setActive(t.toggleName, t.toggleEnable);
  }

  // returns true when the clip looped (the animator state is entered again)
  advance(dt) {
    this.mixer.update(dt);
    const t = this.action.time, looped = t < this.prevTime;
    if (looped) this.enterState();
    this.timeInState += dt;
    for (const f of this.fired) if (!f.done && this.timeInState > f.t.delay) { f.done = true; this.fire(f.t); }
    this.prevTime = t;
    return looped;
  }

  scrub(time) {
    const a = this.action;
    a.paused = false; a.enabled = true;
    if (!a.isRunning()) a.play();
    a.time = time; this.mixer.update(0); this.prevTime = a.time;
  }

  restart() { this.action.reset().play(); this.prevTime = 0; this.enterState(); }

  // travelling clips move the root several metres; optionally keep the character on his slot
  keepCentred(on) {
    this.model.position.set(0, 0, 0);
    if (!(this.entry.travels && on)) return;
    this.model.updateMatrixWorld(true);
    const p = this.model.worldToLocal(this.hip.getWorldPosition(_hip));
    this.model.position.set(-p.x, 0, -p.z);
  }

  update(dt, { physics, environment }) {
    this.chains.update(dt, physics && dt > 0);
    this.model.updateMatrixWorld(true);
    this.particles.update(dt);
    if (environment !== undefined) this.look.setEnvironment(environment);
    this.look.update(dt);
  }

  setVisible(on) {
    this.root.visible = on;
    this.particles.setEnabled(on && this.fxEnabled !== false);
  }

  setFx(on) { this.fxEnabled = on; this.particles.setEnabled(on && this.root.visible); }
}
