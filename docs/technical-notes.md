# Technical notes

How each part of the archive maps to the game's data and code, and what remains approximate.
Game build: Android 7.16.399736 (`com.roadhousegames.lotb`), Unity with IL2CPP (metadata v27.1).
RVAs below refer to `libil2cpp.so` of that build.

## Data

| Item | Where it lives |
|---|---|
| Prefab `Jesterhead_Prefab`, `cso_jesterhead` (character FX and sounds), clips, sounds | `cso_TheAlchemist_AssetBundle` (`08892f8d…`) |
| Animator override `Jesterhead_AnimOverride` → base `MASTER_character_AnimController` | same bundle |
| Character, particle and environment shaders, ramp textures | `Base_BaseBundle` (`67e008b0…`, `CAB-6347568f…`) and `CAB-547377ce…` |
| Arenas `InFlames_road1`, `InFlames_roadEnd` | `InFlames_Dungeon_AssetBundle` (`289f30b0…`) |
| Arena `hellgate_road2` (reused from the Hell Gate dungeon) | `HellGate_World_AssetBundle` (`6a3f8c7e…`) |
| Shared rocks, river, cliffs of the arenas | `AilingKingdomEnvShared_AssetBundle` (`5e447a1d…`) |
| Abilities `AB_OnlyForTheWeak`, `AB_TakeThisLife` | `Basic_Abilities_AssetBundle`, `Energy_Abilities_AssetBundle` |
| Game tables (characters, abilities, quests, battles, dialogue) | `CSV_AssetBundle` (`e9413e15…`) |
| English text (and 7 other languages) | `Loc_AssetBundle` (`9738e6a4…`), binary string tables |
| Map ambience (the battle music is not included) | `OnboardingAudio_AssetBundle` |
| Battle cameras, battle slots | APK built-in data: `CamerasPrefab` (`assets/bin/Data/7cf4a003…`), `SlotsContainer` (`27f21ca4…`) |
| Enemies of the five battles (20 characters) | `cso_KillerPack`, `cso_Roller_Demon`, `cso_Harpies`, `cso_ChildOfTheDamned`, `cso_Wrath`, `cso_Skeletons`, `Eddies_Halloween`, `OnboardingCharacters` bundles; idle clips also in `SharedAnim`/`SharedAnim2` |
| Battle portraits (enemies, dialogue speakers) | `Characters_AssetBundle` (`fee724bd…`) |

UnityPy decodes the compressed mesh of the robe front with a wrong implicit fourth bone weight (`-28` instead of
`1 - w0 - w1 - w2`); `build_glb.py` and `build_character.py` repair it.

Unity is left-handed, glTF right-handed: positions and directions are mirrored on X, quaternions become
`(x, -y, -z, w)`, triangle winding is reversed, Euler angles `(x, y, z)` become `(x, -y, -z)` in the same `YXZ` order.

## Character data

From `Characters.csv` (ids 30005631–30005635, ranks I–V), `CharacterAbilityLevels.csv`, `Actions.csv`, `Effects.csv`
and the English string table (`tools/export_event.py` writes them to `site/assets/event/data.json`):

* Class `Tank`, shown as **Sentinel**; role **Negative Effect Caster**.
* Basic ability **Only for the Weak** (10002143, icon `ABicon_732B_OnlyForTheWeak`), energy ability **Take This Life**
  (10002149, 6 energy, `ABicon_732E_TakeThisLife`), passive **Crawl Through Knives** (20008859,
  `ABicon_732P_CrawlThroughKnives`): three In Flames song titles.
* Obtained through a 7-day login calendar (`ER_JESTERHEAD_*`, art `ERC_FG_Jesterhead`) and a store pack
  (`SKUArtDefinitions` 757, `carousel_732_Jesterhead`).

The icons `ABicon_68B_ClawInTheDark` and `ABicon_67P_AloneInTheDark`, shown in an earlier version of this archive, belong
to other characters and were removed.

## Animator states → clips → abilities → effects

Read from the override controller, the ability assets and `PlayParticlesWithAudio` components:

| Viewer entry | Clip | Animator state(s) with triggers | Ability |
|---|---|---|---|
| Only for the Weak | `jesterhead_attack_anm` | `attack_single_01` | `AB_OnlyForTheWeak` (animationType 10001, camera 100) |
| Take This Life | `Jesterhead_buff_anm` | `attack_single_03` | `AB_TakeThisLife` (animationType 10003, interaction 3 = "tap rapidly") |
| Buff | `Jesterhead_buff_anm` | `Buff_01` (the clip is also used by `Debuff_01`, `Cast_01/02`) | |
| Hit (left) | `jesterhead_hit_left_anm` | `Hit_left`, `Hit_up` (same triggers; also used by `Hit_back`) | |
| Hit (right) | `jesterhead_hit_right_anm` | `Hit_right`, `Hit_down`: no triggers | |
| Death | `jesterhead_death_anm` | `Death01` | |
| Revive / Enter / Leave | `jesterhead_run_in_anm`, `jesterhead_run_out_anm` | `Revive`, `Run_In`, `Run_Out` | |

`attack_single_02/04` use another character's clips and are not part of Jesterhead's kit. All states play at speed 1.

## Sound

`cso_jesterhead.characterFX` (`tools/export_audio.py`):

* `animationSoundEffects`: 10001 → `Jesterhead_Attack_01` (volume 0.909), 10003 → `Jesterhead_Attack_03` (0.909), played by
  `VisualPlayerAgent.PlayAnimationSFX` when the ability animation starts.
* `deathFX` → `MultipleAudioSource` → `despawn_whoosh` (0.96), played by `VisualPlayerAgent.Die` (`0x10932DC`) right after
  `CharacterAnimator.Die`.
* `reviveFX` → `Revive_March15_2016` (shared). `successiveTapEffects` → `Leve_Up_Star_Slam`, one per successful tap
  (`PlaySuccessiveTapSFX`).
* Battle music of every In The Dark battle (`QuestBattles.csv`): `Caught_SomeWhere_In_Time_Backing_Track_V4`, the
  instrumental of Iron Maiden's "Caught Somewhere in Time". Recorded by title only; the track is not included.
* Map ambience (`Worlds.csv`): `bc_amb_mapHub_fe_stage1`, kept as a download (not played by the viewer).

UnityPy decodes the FSB5 streams to PCM; the archive keeps Ogg Vorbis (download) and MP3 (playback) re-encodings. The
game's mixer snapshots are not reproduced; the viewer's volume slider scales everything.

## Game scripts (IL2CPP, read from the ARM64 code)

* **`PlayParticlesFromAnimationState.Update`** (`0xFA6314`), **`PlayParticlesWithAudio.Update`** (`0xFA735C`):
  while the animator's current state (or the state being transitioned to) has the configured name, `timeInState += deltaTime`;
  when `timeInState > playDelay` the component fires once per state entry, then again at each `additionalDelays` value.
  Firing (`UpdateSpecifics`, `0xFA7424`) calls `ParticleSystem.Play(true)` on its own object unless `playAudioOnly`,
  plays the optional audio, then `toggleGeometry.SetActive(toggleEnable)`. `Start` sets the object to `!toggleEnable`
  when `startUnToggled`.
* **`TweenMaterialProperty.OnUpdate`** (`0xD7F304`): `material.SetFloat(propertyName, Mathf.Lerp(from, to, factor))`.
  `Mathf.Lerp` clamps. Driven by NGUI's `UITweener` (style 0 = once, 1 = loop).
* **`ResetTweensOnEnable`** (`0xEDE6BC`, `0xEDE83C`): on enable, every tween that was enabled at start is reset and played
  forward (`VFX_bodyglow`: the staff and watches flare when he attacks or is hit).
* **`CustomAnimationScript.Update`** (`0x11627D4`): `factor = Repeat(t - startDelay, duration) / duration`.
  `ControlUVOffsetsFromCurve` writes the texture offset of `renderer.materials[materialIndex]` (on a particle renderer,
  index 1 is the trail material); `ControlEulerAnglesFromCurve.Evaluate` (`0x133A344`) sets
  `localEulerAngles` (space Self) or `eulerAngles` (World) to the three curves, plus the start angles read in `Awake` when
  `addStartRotation`.
* **`RotateXYZBehaviour.Update`** (`0x119D2B0`): `transform.Rotate(X·dt, Y·dt, Z·dt)`.
* **`Blackcomb.DirectionalLightFX`** (`0xE25CA4`…): after `delayTime`, sets a light coefficient to
  `Lerp(darkColor, white, lightControl(1 - timer / updateTime))` for `updateTime` seconds. `ScreenDimmer` darkens (3 s),
  `ScreenDimmer (1)` tints orange (1 s, buff). The coefficient reaches the arenas through `BlackShaderManager` (below).

## Shaders

All are evaluated in gamma space; the viewer does the same (no sRGB decode or encode for these materials).

`RHI/BlackcombCharacter*`, pass LOD 230 (GLES), fragment:

```glsl
ramp = texture(_CharacterLightRampTex, vec2(dot(worldNormal * _RescaleNormal, _LightDir.xyz) * 0.5 + 0.5, 1.0)).rgb;
col  = tex.rgb * ramp * _CharacterLightRampHDRScale;
col += tex.a * _EmissiveColor.rgb * _EmissiveBlend;                  // _wEmissiveColor
col  = mix(col, _CustomColor.rgb, tex.a * _CustomBlend);              // _wCustomColor
col += texture(_RimRampTex, vec2(1.0, dot(camPos - pos, normal) / clipW)).rgb * _RimColor.rgb * _RimBlend;
```

`RHI/BlackcombEnvDistanceRampBakeLights(UVScroll)`, LOD 220 (the LOD 210 pass is texture only), shadows off:

```glsl
// vertex
color = in_COLOR0 * _VertexColorMultiplier;
fog   = min(length((worldPos - _DistanceRampOffset) * _DistanceRampRange), 1.0);
// fragment
a   = tex.rgb * color.a;                                     // self-illumination stored in vertex alpha
a  += fogRamp(fog, 0).a * (fogRamp(fog, 0).rgb - a);         // _FOG_ON only
out = tex.rgb * color.rgb + a;
```

`RHI/BlackcombEnvTransparentUVScroll`: `texture × vertex colour`, `Blend SrcAlpha [_AlphaMode]` (10 = alpha blend,
1 = additive), ZWrite off. `RHI/BlackcombSkyBox` (portal flames): unlit texture. Particles: `Mobile/Particles/Additive`
and `Alpha Blended`. Ghost faces: `Legacy Shaders/Particles/Alpha Blended`, `2 × _TintColor × texture`.

The ghost-face layers share their vertices with the body but not its triangulation, so their interpolated depth differs
slightly; the viewer pulls them forward with a polygon offset to avoid flickering.

## Arenas

`tools/export_arena.py`. Each arena scene has a `TS_wicker` root with `SetVisibleByTimePeriod` and three children,
Past / Present / Future (Present active by default), plus `BlackShaderManager`.

* **Baked lighting.** Meshes carry `RHICommon.ImplicitMeshVertexColorApply` components
  (`ImplicitMeshApply`, `0x1558384`): `m_applydata[0]` is the channel count (1, 3 or 4), followed by 1/3/4 bytes per vertex;
  `m_applyTo` picks the target channels (jump table: 0 R, 1 G, 2 B, 3 A, 4 RGB). They start from the mesh's colours, or
  `Color32(0,0,0,0)` without any. The exporter replays this and stores the result as `COLOR_0`.
* **`BlackShaderManager.UpdateAllMaterials`** (`0xF1E248`), for environment materials and the characters registered with
  `AddMaterialsForCharacters`: `_LightDir = -forward` of the scene's directional light (animated by
  `ControlEulerAnglesFromCurve` on `lightcontrol`), `_LightColor = light.color`,
  `_VertexColorMultiplier` = product of the `DirectionalLightFX` coefficients, then the custom parameters
  (`_FogRampTex`, `_ShadowColor`, `_RimColor`, `_CharacterLightRampTex`, `_CharacterLightRampHDRScale` = 2) on every material
  that has them. Names resolved from the string-literal table through the ELF relocations.
* **`SetVisibleByTimePeriod.UpdateShaderManagerParams`** (`0x12C9EE8`) overrides `_FogRampTex`, `_ShadowColor` and
  `_RimColor` with the period's values, so every environment material uses the period's fog ramp and the characters take
  its rim colour. In the arenas Jesterhead's body therefore gets the arena's light ramp (×2) and an orange rim.
* **Camera and slots.** `Quests.csv` sets `cameraClassOverride = CharacterDungeonB` for all four quests:
  `cam_MAIN` (vertical FOV 35°) at `cam_MAIN_offset_CharacterDungeonB`. Characters stand on
  `DungeonSlotsContainer` slots (Team1 `ForwardSlot`, `LeftSlot`, `RightSlot`; Team2 `Slot01`–`Slot06`). The camera clears
  to its background colour `(0.192, 0.302, 0.475)`; the scenes have no skybox.
* `QuestBattles.csv`: battles 1–2 of each difficulty use `InFlames_road1`, 3–4 `hellgate_road2`, 5 (boss) `InFlames_roadEnd`.

## Particle systems

Implemented from the serialized modules: Initial (start values, 3D size/rotation, gravity), Emission (rate over time and
distance, bursts with cycles and probability), Shape (sphere, hemisphere, cone, box, circle, edge, with transform and
randomisation), Velocity / Force / Limit velocity (dampen, drag), Inherit velocity (initial), Size, Rotation, Rotation by
speed (constants), Colour over lifetime, Texture sheet (rows, cycles, start frame), Trails, birth and death sub-emitters,
simulation speed, local / world simulation space, hierarchy / local / shape scaling, render modes (billboard with view,
local and velocity alignment, stretched, horizontal, vertical, mesh), max particle size, sorting fudge, prewarm.

* The attack's projectiles are mesh particles (`vfx_poker`, playing cards) from `VFX_Attack1 (1)`. Their **birth
  sub-emitters** attach the claw, ribbons and, through further sub-emitters, smoke and ash.
* `ParticleSystem.Play()` does nothing on a system that is still alive, and `Play(withChildren)` skips sub-emitters.
* **Velocity-aligned meshes** point their `+Z` axis against the motion. Three independent checks: the ribbon mesh
  (`vfx_ribbon_trail`) extends along `+Z` and must trail behind its projectile; the claw (`vfx_claw`) extends along `-Z`,
  ahead of it; and the In Flames sigil of Take This Life (`FX_Jesterhead_symbol`, whose mesh has its long point towards
  `+Z`) appears in gameplay footage with the triangle up and the long point down. An earlier version had it upside down.
* Mesh particles authored flat in the XZ plane (clock hands, sigil) spin around their normal for 2D rotation.
* **Draw order** follows Unity's transparent sort: the material's render queue first (custom queues from 2700 to 3007
  in these effects), then distance. Take This Life puts a black, alpha-blended glow (`VFX_GlowHB`, queue 2750,
  start colour black) behind each clock; it is drawn before the additive clocks (queue 2990) and their hands. An
  earlier version sorted by distance only, so the black glow could cover the clocks and hide the hands. The darkening
  itself is in the game: in the footage the caster turns into a dark silhouette inside the clocks.

Approximations: Unity's internal formulas are not public, so a few behaviours follow the documentation rather than code:
cone direction blending, stretched-billboard anchoring, limit-velocity dampening per frame, the rotation-by-speed range.

## Watch chains

In the game: `Anchor` (kinematic) → `SpringJoint` (spring 40 000 to 100 000) → three `HingeJoint` links, masses 10–25,
drag 0.2. The viewer uses a Verlet rope with the same link lengths (≈0.25), gravity and damping.

## Enemies

The battle line-ups were sent by the game servers and are not in the cached data. They were read from a recording of
the Normal run (`tools/export_enemies.py` holds them, with the timestamps):

* Wave V is shown on the team-selection screen (portraits, levels and the boss tag).
* In every wave the level badge gives the class (red Warrior, green Sentinel, blue Magus, purple Assassin, yellow
  Gunner) and the middle enemy is level 60, the others 55.
* The models were matched to `Characters.csv` rows by class and battle portrait (`spriteName`), then rendered in the
  arena with the battle camera and compared with the footage. The order on screen gives the Team2 slot: left to right,
  `Slot01` to `Slot05` (the waves of three use `Slot01`, `Slot03`, `Slot05`).

| Battle | Slot01 | Slot02 | Slot03 (level 60) | Slot04 | Slot05 |
|---|---|---|---|---|---|
| 1 | Hellraiser (Sentinel) | Derby Demon (Magus) | Hellraiser (Warrior) | Derby Demon (Assassin) | Hellraiser (Gunner) |
| 2 | Trickster (Assassin) | | Trickster (Warrior) | | Trickster (Magus) |
| 3 | Child of the Damned (Assassin) | Angel of Strife | Demon Prince Astaroth | Angel of Fear | Child of the Damned (Gunner) |
| 4 | Derby Demon (Warrior) | | Belshazzar | | Derby Demon (Sentinel) |
| 5 | Soulless Demon (Sentinel) | Soulless Demon (Warrior) | The Ferryman (boss) | Soulless Demon (Assassin) | Soulless Demon (Sentinel) |

Names are the game's English names (`locName`); several differ from the internal ones (`cso_roller_demon_*` are
"Derby Demon", `cso_wrathchild_*` are "Trickster", `CSO_DemonSkeleton_*` are "Soulless Demon").

Two characters needed a second look. Battle 3's Demon Prince Astaroth first came out black: its texture keeps an
emission mask in the alpha channel, and downscaling it with Pillow premultiplied the colour by that mask (the export now
resizes colour and alpha separately). Battle 4's Magus is Belshazzar in its empowered form (`cso_belshazzar_empowered`,
in the same bundle as Astaroth): levitating, long white hair, and the green machine is its chair
(`belshazzar_chair_mdl`), which stays when the character dies.

`tools/build_character.py` exports each prefab with its idle clip (the clip the `idle` state plays after the override
controller's substitutions) and its material parameters. Several prefabs use Unity's "optimize game objects": their
skeleton exists only in the Avatar (`m_AvatarSkeleton`, `m_AvatarSkeletonPose`), and the mesh bones and clip tracks are
path hashes resolved through the Avatar's `m_TOS` table; the "extra transforms to expose" (weapon joints holding a prop,
such as the Tricksters' wands) are attached back to their bones. Looping `TweenMaterialProperty` pulses on the
renderers (Belshazzar's glowing veins, the Ferryman's lantern) are stored in the material extras and played by the
viewer. The enemies' particle effects (the flames of the Child of the
Damned, the glow of the Soulless Demons) are not exported.

The character shader reads its light and rim ramps with Unity's bottom-up texture coordinates, so the viewer flips
those images, as it does for Jesterhead's. GLTFLoader decodes images to `ImageBitmap` in current browsers, and WebGL
ignores `UNPACK_FLIP_Y` for that source (checked in Chrome), so `npc.js` decodes the enemies' ramps a second time,
flipped (`createImageBitmap(..., { imageOrientation: 'flipY' })`). The rim term uses the plain normal: `_RescaleNormal`
only scales the light ramp's normal. Several enemies set it to 0 (Child of the Damned, Angels, Soulless Demons), which
flattens their lighting but keeps their rim. With the ramp inverted, the Ferryman and Astaroth came out washed out
instead of dark as in the footage.

## Web delivery

First load of the page (viewer with battle 1), measured in Chrome against a server that gzips HTML, JS, JSON and GLB
files: 18.1 MB before, 4.9 MB after.

* `tools/optimize_glb.py`: the exporters resample every curve at 30 fps; keys that lie within 1e-4 of the straight line
  between their neighbours are dropped (every dropped key is checked against the final segment), and channels that
  hold the rest pose for a whole clip are removed (three.js restores a property's original value when no running
  action animates it). Embedded textures become WebP (EXT_texture_webp), lossless alpha; the light and rim ramps,
  read by the shaders as lookup tables, are lossless. `jesterhead.glb`: 9.5 MB to
  2.1 MB (335 k keys to 36 k); the enemies: 20 MB to 9 MB. Renders of six poses with the original and the optimised
  model differ on at most 73 pixels out of 960 000.
* `tools/optimize_textures.py`: particle and arena PNGs above 16 KB become WebP; the particle atlas shipped twice
  (character and arena) is kept once.
* The page shows WebP copies of its images (`site/assets/web/`, made by `build_site.py`), every image has its size, and
  images below the viewer load lazily.
* The default arena downloads alongside the model; the model and the JavaScript modules are preloaded.
* The model is parsed once; each extra Jesterhead is a copy of it (`SkeletonUtils.clone`) and shares its textures and
  the particle textures.
* With compression, the `Content-Length` of a file is its compressed size while the browser counts the decoded bytes,
  so the loading percentages use the file sizes that `build_site.py` writes into the page.
* GitHub Pages gzips the HTML, JS, JSON and GLB files (glTF binaries shrink by about a third, JSON and JS by 70 to
  90 %); it caches every file for 10 minutes, after which the browser revalidates it.

The full-quality files are kept in `sources/` with the same paths.

## Ability camera (Take This Life)

`AB_TakeThisLife` sets `cameraType` 400 (Buff01, `cam_BUFF_01` in the built-in `CamerasPrefab`), `secondCamera` 1000
(Custom01, i.e. its `customCameras[0]`, a rig named `baphomet_special_cam1`, shared with Baphomet). In `CameraManager`:
`SetActiveCamera` switches to the first camera when the ability starts, `OnInteractionFinished` (`0x1389CF0`) calls
`SwitchToCamera(secondCameraType)` when the "tap rapidly" input ends, and the `OnMainCameraReturn` event of
`Jesterhead_buff_anm` (3.30 s) brings back the main camera. `SetStaticTarget` (`0x13889D8`) moves the rig root to the
caster, and also gives it the caster's rotation for `CasterStaticWithRotation` (the custom rig); Buff01 is
`CasterStatic` and keeps the rig's own orientation. Both cameras hide every character but the caster
(`showVisualAgents` 40). The built-in data has no type trees, so `CameraDefinition` is read from its raw bytes with the
field layout of `dump.cs`. `tools/export_cameras.py` writes `site/assets/cameras.json`: the rigs' local transforms, field
of view, and the custom rig's clip sampled at 30 fps. The viewer plays them when the battle camera is on: Buff01 for the
"rapid tap" entries, the custom camera for Take This Life until 3.30 s. The white flash particle attached to the custom
camera (`VFX_CameraWhite_Solid`) is not reproduced.

## Not reproduced

* The time-shift transition (`RHI/TimeWipe` render-texture wipe) and the main camera's push-ins on the other attacks.
* The enemies' attacks, particle effects and other animations.
* The LOD 220 / 210 character passes and the LOD 210 environment pass used on low-end devices.
