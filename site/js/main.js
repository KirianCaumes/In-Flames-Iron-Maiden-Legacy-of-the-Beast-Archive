import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import * as SkeletonUtils from 'three/addons/utils/SkeletonUtils.js';
import { LightFx } from './character.js';
import { Jester } from './jester.js';
import { Npc } from './npc.js';
import { Arena } from './arena.js';
import { Sound } from './audio.js';

const ASSETS = './assets/';
const $ = id => document.getElementById(id);

// Clip -> animator states of MASTER_character_AnimController whose effect triggers run with it
// (mapping read from Jesterhead_AnimOverride). Ability names: AB_OnlyForTheWeak (animationType 10001 =
// attack_single_01) and AB_TakeThisLife (10003 = attack_single_03).
const ENTRIES = [
  { id: 'idle', clip: 'idle', label: 'Idle', states: [] },
  { id: 'idle_wounded', clip: 'idle_wounded', label: 'Idle (wounded)', states: [] },
  { id: 'attack', clip: 'attack', label: 'Only for the Weak', kind: 'basic', states: ['attack_single_01'] },
  { id: 'energy', clip: 'buff', label: 'Take This Life', kind: 'energy', states: ['attack_single_03'] },
  { id: 'buff', clip: 'buff', label: 'Buff', states: ['Buff_01'] },
  { id: 'rapid_antic', clip: 'rapid_antic', label: 'Rapid tap (wind-up)', states: [] },
  { id: 'rapid_tap', clip: 'rapid_tap', label: 'Rapid tap', states: [], tap: true },
  { id: 'hit_left', clip: 'hit_left', label: 'Hit (left)', states: ['Hit_left'] },
  { id: 'hit_right', clip: 'hit_right', label: 'Hit (right)', states: [] },
  { id: 'death', clip: 'death', label: 'Death', states: ['Death01'] },
  { id: 'revive', clip: 'run_in', label: 'Revive', states: ['Revive'], travels: true },
  { id: 'run_in', clip: 'run_in', label: 'Enter battle', states: ['Run_In'], travels: true },
  { id: 'run_out', clip: 'run_out', label: 'Leave battle', states: ['Run_Out'], travels: true },
];
const CAMERA_BG = [0.192, 0.302, 0.475];          // cam_MAIN background colour (no skybox in these arenas)
// arena of each battle of the dungeon (QuestBattles.csv battleArena), written into the options by build_site.py
const BATTLE_ARENA = Object.fromEntries([...$('battle').options].filter(o => o.value).map(o => [o.value, o.dataset.arena]));
// decoded sizes of the large downloads, written by build_site.py: when the server compresses a file, the
// Content-Length the progress events use is the compressed size
const SIZES = JSON.parse($('file-sizes')?.textContent || '{}');
const percent = (e, file) => {
  const total = SIZES[file] || e.total;
  return total ? ` ${Math.min(100, Math.round(100 * e.loaded / total))} %` : '';
};

// ---------------------------------------------------------------- scene
const canvas = $('c');
const viewer = $('viewer');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.shadowMap.enabled = true;
const scene = new THREE.Scene();
const sun = new THREE.DirectionalLight(0xffffff, 1);          // only used for the ground shadows
sun.position.set(3, 8, 6); sun.castShadow = true; sun.shadow.mapSize.set(2048, 2048);
Object.assign(sun.shadow.camera, { left: -10, right: 10, top: 10, bottom: -10, near: 0.5, far: 40 });
scene.add(sun);
const shadowMat = new THREE.ShadowMaterial({ opacity: 0.45 });
const ground = new THREE.Mesh(new THREE.CircleGeometry(11, 64), shadowMat);
ground.rotation.x = -Math.PI / 2; ground.receiveShadow = true; scene.add(ground);

const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 1000);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true; controls.autoRotateSpeed = 1.5;
const focus = new THREE.Vector3();
const resetCam = () => {
  camera.fov = 32; camera.updateProjectionMatrix();
  camera.position.set(3.0, 2.4, 10.2).add(focus); controls.target.set(0, 1.85, 0).add(focus); controls.update();
};
resetCam();
new ResizeObserver(() => {
  const r = canvas.parentElement.getBoundingClientRect();
  renderer.setSize(r.width, r.height, false);
  camera.aspect = r.width / r.height; camera.updateProjectionMatrix();
}).observe(canvas.parentElement);

// ---------------------------------------------------------------- load
const status = $('status');
const fetchBuffer = (url, onProgress) => new Promise((resolve, reject) => {
  new THREE.FileLoader().setResponseType('arraybuffer').load(url, resolve, onProgress, reject);
});
const startArena = data => {                        // the default arena downloads alongside the model
  const a = new Arena({ scene, camera, data, assetBase: ASSETS + 'arena/' });
  a.place(data.slot);
  const key = BATTLE_ARENA[$('battle').value];
  if (key) a.load(key).catch(() => {});
  return a;
};
const [glb, vfx, arena, sounds, lineups, abilityCams] = await Promise.all([
  fetchBuffer(ASSETS + 'model/jesterhead.glb', e => { status.textContent = 'Loading model…' + percent(e, 'model/jesterhead.glb'); }),
  fetch(ASSETS + 'vfx.json').then(r => r.json()),
  fetch(ASSETS + 'arena/arena.json').then(r => r.json()).then(startArena),
  fetch(ASSETS + 'audio/sounds.json').then(r => r.json()),
  fetch(ASSETS + 'enemies/lineups.json').then(r => r.json()),
  fetch(ASSETS + 'cameras.json').then(r => r.json()),
]).catch(err => { status.textContent = 'Could not load the model: ' + err.message; throw err; });
const model = await new GLTFLoader().parseAsync(glb, ASSETS + 'model/');

const lightFx = new LightFx(vfx, $('lightfx'));
const sound = new Sound(ASSETS + 'audio/', sounds);
const arenaData = arena.data;

// slot transforms in viewer space: the Forward slot is the origin, facing +Z
const slotMatrix = s => new THREE.Matrix4().compose(new THREE.Vector3(-s.pos[0], s.pos[1], s.pos[2]),
  new THREE.Quaternion(s.rot[0], -s.rot[1], -s.rot[2], s.rot[3]), new THREE.Vector3(1, 1, 1));
const toViewer = slot => slotMatrix(slot).premultiply(arena.group.matrix);

// ---------------------------------------------------------------- state
const jesters = {};                                  // slot name -> Jester
const npcs = {};                                     // "battle:slot" -> Npc
let current = null, playing = true;
let battleCam = false;                               // battle camera in use
let abilityCam = null;                               // ability camera of the current frame
let solo = null;                                     // HideAllExceptCaster: the only agent left on screen
let vfxTextures = null;                              // particle textures, shared by every Jesterhead

// One copy of the parsed model per Jesterhead (SkeletonUtils.clone rebinds the skins). The parser's node
// associations are carried over by walking both hierarchies in the same order.
function instance(gltf) {
  const scene = SkeletonUtils.clone(gltf.scene), associations = new Map(), src = [];
  gltf.scene.traverse(o => src.push(o));
  let i = 0;
  scene.traverse(o => { const a = gltf.parser.associations.get(src[i++]); if (a) associations.set(o, a); });
  return { scene, animations: gltf.animations, associations };
}
function makeJester(slot) {
  if (jesters[slot]) return jesters[slot];
  const j = new Jester({ asset: instance(model), vfx, scene, camera, assetBase: ASSETS, textures: vfxTextures });
  vfxTextures ||= j.particles.textures;
  j.onToggle = (name, on) => {                         // DirectionalLightFX is driven by the first Jesterhead only
    if (j !== primary() || !vfx.lightFx[name]) return;
    if (on) lightFx.enable(name); else lightFx.disable();
  };
  toViewer(arenaData.slots.team1[slot]).decompose(j.root.position, j.root.quaternion, j.root.scale);
  j.setVisible(false);                                 // shown (and synced with the others) by applyVisibility
  j.setFx($('fx').checked); j.look.setGhosts($('faces').checked); j.look.setWireframe($('wire').checked);
  return (jesters[slot] = j);
}
function syncJester(j) {                             // join the animation the others are playing
  if (!current) return;
  const p = Object.values(jesters).find(o => o !== j && o.root.visible && o.action);
  j.play(current, $('loop').checked);
  if (p) { j.scrub(p.action.time); j.enterStateAt(p.timeInState); } else j.enterState();
}
const SLOT_ORDER = ['ForwardSlot', 'LeftSlot', 'RightSlot'];
const selectedSlots = () => [...document.querySelectorAll('input[name=slot]:checked')].map(i => i.value);
const shown = () => SLOT_ORDER.filter(s => jesters[s] && jesters[s].root.visible);
const primary = () => jesters[shown()[0]] || jesters.ForwardSlot || Object.values(jesters)[0];
const slotOf = j => Object.keys(jesters).find(k => jesters[k] === j);
const action = () => primary()?.action;

// Who is on screen follows the controls (slots, battle, enemies) and the ability camera, so changing a control
// while a camera hides the others cannot bring back what the control removed.
function applyVisibility() {
  const sel = selectedSlots();
  if (solo && !sel.includes(slotOf(solo))) solo = null;
  for (const [s, j] of Object.entries(jesters)) {
    const show = sel.includes(s) && (!solo || j === solo), was = j.root.visible;
    j.setVisible(show);
    if (show && !was) syncJester(j);
  }
  const battle = $('battle').value, on = !solo && $('enemies').checked && !!battle;
  for (const [k, n] of Object.entries(npcs)) n.root.visible = on && n.ready && k.startsWith(battle + ':');
}
function setSolo(j) { if (solo !== j) { solo = j; applyVisibility(); } }

// ---------------------------------------------------------------- Jesterhead positions (dungeon battle slots)
function applySlots() {
  let sel = selectedSlots();
  if (!sel.length) { document.querySelector('input[name=slot][value=ForwardSlot]').checked = true; sel = ['ForwardSlot']; }
  for (const s of sel) makeJester(s);
  applyVisibility();
  const p = primary();
  if (p) {
    p.root.getWorldPosition(focus);
    if (!battleCam) { controls.target.set(0, 1.85, 0).add(focus); controls.update(); }
  }
}
document.querySelectorAll('input[name=slot]').forEach(i => i.onchange = applySlots);
applySlots();
status.remove();

// ---------------------------------------------------------------- playback + UI
const list = $('anims');
const clips = primary().clips;
for (const e of ENTRIES.filter(e => clips[e.clip])) {
  const b = document.createElement('button');
  b.type = 'button'; b.dataset.id = e.id;
  const hasFx = e.states.some(s => primary().triggersByState[s]), hasSound = e.tap || e.states.some(s => sounds.states[s]);
  b.innerHTML = `<span class="name">${e.label}${e.kind ? ` <small>${e.kind}</small>` : ''}</span>`
    + `<span class="tags">${hasFx ? '<span title="Particle effects">fx</span>' : ''}${hasSound ? '<span title="Sound">♪</span>' : ''}</span>`
    + `<span class="dur">${clips[e.clip].duration.toFixed(1)} s</span>`;
  b.onclick = () => play(e.id);
  list.appendChild(b);
}
function enterState() {
  for (const j of Object.values(jesters)) j.enterState();
  sound.stop();
  for (const s of current.states) sound.state(s);
  if (current.tap) sound.tap(0);
}
function play(id, silent = false) {
  current = ENTRIES.find(e => e.id === id);
  for (const j of Object.values(jesters)) j.play(current, $('loop').checked);
  playing = true; $('play').textContent = 'Pause'; sound.pause(false);
  for (const b of list.children) b.setAttribute('aria-pressed', b.dataset.id === id);
  lightFx.disable();
  if (silent) { sound.stop(); for (const j of Object.values(jesters)) j.fired = []; } else enterState();
  $('travel').hidden = !current.travels;
}
play('idle', true);

$('play').onclick = () => {
  playing = !playing; $('play').textContent = playing ? 'Pause' : 'Play';
  sound.pause(!playing);
  if (playing && action() && !action().isRunning()) { for (const j of Object.values(jesters)) j.restart(); enterState(); }
};
$('loop').onchange = e => { for (const j of Object.values(jesters)) j.setLoop(e.target.checked); };
$('speed').oninput = e => { $('speedv').textContent = (+e.target.value).toFixed(2) + '×'; sound.setRate(+e.target.value); };
$('spin').onchange = e => controls.autoRotate = e.target.checked;
$('faces').onchange = e => { for (const j of Object.values(jesters)) j.look.setGhosts(e.target.checked); };
$('wire').onchange = e => { for (const j of Object.values(jesters)) j.look.setWireframe(e.target.checked); };
$('fx').onchange = e => {
  for (const j of Object.values(jesters)) j.setFx(e.target.checked);
  arena.setFx(e.target.checked);
};
$('sfx').onchange = e => sound.setEnabled(e.target.checked);
$('volume').oninput = e => { sound.setVolume(+e.target.value / 100); $('volumev').textContent = e.target.value + ' %'; };
$('legend').onchange = e => viewer.classList.toggle('no-hint', !e.target.checked);
$('reset').onclick = () => { $('battlecam').checked = false; useBattleCamera(false); resetCam(); };
let scrubbing = false;
$('scrub').addEventListener('pointerdown', () => { scrubbing = true; });
addEventListener('pointerup', () => scrubbing = false);
$('scrub').oninput = e => {                          // mouse, touch or keyboard: pause, with the sounds
  if (!action()) return;
  playing = false; $('play').textContent = 'Play';
  sound.stop();
  const t = primary().clips[current.clip].duration * e.target.value / 1000;
  for (const j of Object.values(jesters)) j.scrub(t);
};
// Firefox restores form values on reload: apply what the controls show
for (const id of ['speed', 'volume']) $(id).dispatchEvent(new Event('input'));
for (const id of ['spin', 'sfx', 'legend', 'fx']) $(id).dispatchEvent(new Event('change'));

// full screen (the whole viewer, so the options stay available)
const toggleFullscreen = () => {
  if (document.fullscreenElement) document.exitFullscreen();
  else viewer.requestFullscreen?.().catch(() => {});
};
$('fullscreen').onclick = toggleFullscreen;
addEventListener('keydown', e => {
  if (e.key.toLowerCase() === 'f' && !e.ctrlKey && !e.metaKey && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement?.tagName)) toggleFullscreen();
});
document.addEventListener('fullscreenchange', () => {
  const on = document.fullscreenElement === viewer;
  $('fullscreen').textContent = on ? 'Exit full screen' : 'Full screen';
  $('fullscreen').setAttribute('aria-pressed', on);
});

// ---------------------------------------------------------------- loading note (arena and enemies)
const note = $('arena-status');
let busy = 0, loadError = null;
async function track(text, job) {
  if (busy++ === 0) loadError = null;                // a new round of downloads clears the last error
  note.hidden = false; if (!loadError) note.textContent = text;
  try { return await job; } catch (err) {
    loadError = err; note.textContent = `Could not load: ${err.message}. Select the battle again to retry.`; throw err;
  } finally { if (--busy === 0 && !loadError) note.hidden = true; }
}

// ---------------------------------------------------------------- enemies (line-ups seen in gameplay footage)
const lineup = n => lineups.battles.find(b => b.battle === +n)?.enemies || [];
async function applyEnemies() {
  const battle = $('battle').value;
  applyVisibility();
  if (!$('enemies').checked || !battle) return;
  const missing = lineup(battle).filter(e => !npcs[`${battle}:${e.slot}`]);
  if (!missing.length) return;
  await track('Loading the enemies…', Promise.all(missing.map(async e => {
    const key = `${battle}:${e.slot}`, n = new Npc(scene);
    npcs[key] = n;
    try { await n.load(ASSETS + 'enemies/' + lineups.characters[e.id].file); } catch (err) {
      delete npcs[key]; scene.remove(n.root); throw err;        // tried again the next time
    }
    n.place(toViewer(arenaData.slots.team2[e.slot]));
    n.ready = true;
  }))).catch(() => {});
  applyVisibility();
}
$('enemies').onchange = applyEnemies;

// ---------------------------------------------------------------- battle: arena + enemies
const stage = canvas.parentElement;
function setBattle(n) {
  $('battle').value = n;
  $('period').disabled = $('battlecam').disabled = $('enemies').disabled = !n;
  setArena(BATTLE_ARENA[n] || '');
  applyEnemies();
}
let arenaRequest = 0;
async function setArena(key) {
  const request = ++arenaRequest;
  if (!key) {
    arena.hide(); lightFx.setTarget(null);
    renderer.setClearColor(0x000000, 0); stage.classList.remove('in-arena');
    shadowMat.color.set(0x000000); shadowMat.opacity = 0.45;
    $('battlecam').checked = false; useBattleCamera(false);
    return;
  }
  try {
    await track('Loading the arena…', arena.load(key, e => {
      if (request === arenaRequest && !loadError) note.textContent = 'Loading the arena…' + percent(e, `arena/${key}.glb`);
    }));
  } catch { return; }
  if (request !== arenaRequest) return;              // another battle was selected while this one loaded
  arena.show(key, $('period').value);
  lightFx.setTarget(c => arena.setDim(c));
  renderer.setClearColor(new THREE.Color().setRGB(...CAMERA_BG, THREE.SRGBColorSpace), 1);
  stage.classList.add('in-arena');
  applyPeriod();
}
function applyPeriod() {
  if (!arena.current) return;
  arena.setPeriod($('period').value);
  const p = arena.characterParams();
  shadowMat.color.setRGB(p.shadowColor[0], p.shadowColor[1], p.shadowColor[2], THREE.SRGBColorSpace);
  shadowMat.opacity = Math.max(0.3, p.shadowColor[3]);
}
$('battle').onchange = e => setBattle(e.target.value);
$('period').onchange = applyPeriod;

// Buttons further down the page bring the viewer back into view. The target is recomputed when the scroll ends:
// images loading on the way (or a scroll-anchoring adjustment) can stop a smooth scroll short of it.
function showViewer() {
  const target = () => {
    const top = viewer.getBoundingClientRect().top + scrollY;          // whole viewer on screen, masthead too if it fits
    return Math.min(top, Math.max(0, top + viewer.offsetHeight - innerHeight));
  };
  scrollTo({ top: target(), behavior: 'smooth' });
  const check = () => { if (Math.abs(scrollY - target()) > 2) scrollTo({ top: target(), behavior: 'smooth' }); };
  if ('onscrollend' in window) { addEventListener('scrollend', check, { once: true }); return; }
  let timer;                                         // no scrollend event (Safari): wait for the scroll to go quiet
  const quiet = () => {
    clearTimeout(timer);
    timer = setTimeout(() => { removeEventListener('scroll', quiet); check(); }, 600);
  };
  addEventListener('scroll', quiet);
  quiet();
}
document.querySelectorAll('[data-battle]').forEach(b => b.addEventListener('click', () => {
  $('enemies').checked = true;
  setBattle(b.dataset.battle);
  showViewer();
}));
document.querySelectorAll('[data-play]').forEach(b => b.addEventListener('click', () => {
  play(b.dataset.play); showViewer();
}));

// cam_MAIN with the CharacterDungeonB offset that every In The Dark quest selects (Quests.csv cameraClassOverride)
const saved = { pos: new THREE.Vector3(), target: new THREE.Vector3() };
const MIRROR = new THREE.Matrix4().makeScale(-1, 1, 1);
const LOOK_Z = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI);   // Unity cameras look down +Z
// a camera's Unity world matrix -> viewer camera (X mirrored, arena placement)
function placeCamera(unityMatrix, fov) {
  const m = MIRROR.clone().multiply(unityMatrix).multiply(MIRROR);
  const p = new THREE.Vector3(), q = new THREE.Quaternion();
  m.decompose(p, q, new THREE.Vector3());
  m.compose(p, q.multiply(LOOK_Z), new THREE.Vector3(1, 1, 1)).premultiply(arena.group.matrix);
  m.decompose(camera.position, camera.quaternion, new THREE.Vector3());
  if (camera.fov !== fov) { camera.fov = fov; camera.updateProjectionMatrix(); }
}
const placeMainCamera = () => {
  const c = arenaData.camera;
  placeCamera(new THREE.Matrix4().compose(new THREE.Vector3(...c.pos), new THREE.Quaternion(...c.rot), new THREE.Vector3(1, 1, 1)), c.fov);
};
function useBattleCamera(on) {
  if (on === battleCam) return;
  battleCam = on;
  controls.enabled = !on;
  abilityCam = null;
  if (on) {
    saved.pos.copy(camera.position); saved.target.copy(controls.target);
    placeMainCamera();
  } else {
    setSolo(null);
    camera.fov = 32; camera.updateProjectionMatrix();
    camera.position.copy(saved.pos); controls.target.copy(saved.target); controls.update();
  }
}
$('battlecam').onchange = e => useBattleCamera(e.target.checked);

// ---------------------------------------------------------------- ability cameras (battle camera mode only)
// AB_TakeThisLife: cameraType 400 (cam_BUFF_01) while the "tap rapidly" interaction runs, then secondCamera 1000
// (its custom rig) from the end of the interaction until the clip's OnMainCameraReturn event (tools/export_cameras.py).
const ABILITY_CAMERA = { rapid_antic: 'interaction', rapid_tap: 'interaction', energy: 'ability' };
function rigMatrix(cam, slot, t) {
  const root = cam.chain[0];
  const rot = cam.position === 'CasterStaticWithRotation' ? slot.rot : root.rot;   // SetStaticTarget
  const m = new THREE.Matrix4().compose(new THREE.Vector3(...slot.pos), new THREE.Quaternion(...rot), new THREE.Vector3(...root.scale));
  const last = cam.chain.length - 1;
  cam.chain.forEach((n, i) => {
    if (!i) return;
    const p = new THREE.Vector3(...n.pos), q = new THREE.Quaternion(...n.rot), s = new THREE.Vector3(...n.scale);
    if (cam.anim && i === last) {                      // the rig's Animator, sampled at 30 fps
      const a = cam.anim, x = Math.min(Math.max(t, 0) * a.fps, a.pos.length / 3 - 1), k = Math.floor(x), f = x - k;
      const k1 = Math.min(k + 1, a.pos.length / 3 - 1);
      p.fromArray(a.pos, k * 3).lerp(new THREE.Vector3().fromArray(a.pos, k1 * 3), f);
      q.fromArray(a.rot, k * 4).slerp(new THREE.Quaternion().fromArray(a.rot, k1 * 4), f);
      if (a.scale) s.fromArray(a.scale, k * 3).lerp(new THREE.Vector3().fromArray(a.scale, k1 * 3), f);
    }
    m.multiply(new THREE.Matrix4().compose(p, q, s));
  });
  return m;
}
function updateAbilityCamera() {
  if (!battleCam || !arena.current) return;
  const spec = abilityCams.abilities.TakeThisLife, which = current && ABILITY_CAMERA[current.id], a = action();
  let cam = null;
  if (which && a && (which !== 'ability' || a.time < spec.returnAt)) cam = abilityCams.cameras[spec[which]];
  if (cam) {
    const caster = primary();
    placeCamera(rigMatrix(cam, arenaData.slots.team1[slotOf(caster)], a.time), cam.fov);
    if (cam.show === 40) setSolo(caster);            // CameraDefinition.showVisualAgents 40: HideAllExceptCaster
  } else if (abilityCam) {                            // OnMainCameraReturn
    placeMainCamera(); setSolo(null);
  }
  abilityCam = cam;
}

setBattle($('battle').value);
useBattleCamera($('battlecam').checked);             // restored by the browser (setBattle unticks it without an arena)

// ---------------------------------------------------------------- main loop
const clock = new THREE.Clock();
renderer.setAnimationLoop(() => {
  const rdt = Math.min(clock.getDelta(), 0.1);
  const dt = playing ? rdt * +$('speed').value : 0;
  const all = Object.values(jesters).filter(j => j.root.visible);
  if (playing) {
    let looped = false;
    for (const j of all) looped = j.advance(dt) || looped;
    if (looped) {                                               // the clip looped: the state is entered again
      sound.stop(); for (const s of current.states) sound.state(s); if (current.tap) sound.tap(0);
    }
  }
  const env = arena.current ? arena.characterParams() : null;
  for (const j of all) {
    j.keepCentred($('inplace').checked);
    j.update(dt, { physics: $('physics').checked, environment: env });
  }
  for (const n of Object.values(npcs)) { n.setEnvironment(env); n.update(dt); }
  arena.update(dt);
  lightFx.update(dt);
  const a = action();
  if (a && !scrubbing) {
    const d = primary().clips[current.clip].duration, t = Math.min(a.time, d);
    $('scrub').value = Math.round(t / d * 1000);
    $('time').textContent = `${t.toFixed(2)} / ${d.toFixed(2)} s`;
  }
  updateAbilityCamera();
  if (controls.enabled) controls.update();
  renderer.render(scene, camera);
});
window.__viewer = { jesters, play, camera, controls, arena, sound, setBattle, useBattleCamera, applySlots, applyEnemies, npcs, lineups,
  get particles() { return primary().particles; }, get model() { return primary().model; } };
