"""Export the "In The Dark" battle arenas (InFlames_road1, InFlames_roadEnd) to GLB + JSON.

Static meshes keep their baked lighting (RHICommon.ImplicitMeshVertexColorApply), materials and the per-period
(Past / Present / Future) visibility, fog ramps and light directions. Animated transforms (RotateXYZBehaviour,
ControlEulerAnglesFromCurve), UV scrollers and particle systems are exported for the viewer to replay.
The battle camera and the character slot come from the APK's built-in data (CamerasPrefab, SlotsContainer).

usage: export_arena.py <out_dir> <ABMv6 dir> <APK assets/bin/Data dir>
writes <out_dir>/<scene>.glb, <out_dir>/arena.json and <out_dir>/tex/*
"""
import json, math, os, re, struct, sys
import numpy as np
import UnityPy
from UnityPy.helpers.MeshHelper import MeshHandler

out_dir, abm, apk_data = sys.argv[1:]
os.makedirs(os.path.join(out_dir, "tex"), exist_ok=True)
BUNDLES = ("289f30b0673a9def1722fa09341de50b",   # InFlames_Dungeon_AssetBundle (InFlames_road1, InFlames_roadEnd)
           "6a3f8c7e893a9a91f7dd65f1e6f106d0",   # HellGate_World_AssetBundle (hellgate_road2, battles 3-4 of the event)
           "5e447a1d255a57e8fe149954cd451dae",   # AilingKingdomEnvShared_AssetBundle (shared rock and river meshes)
           "67e008b053c12dceaf08614584cb2083")   # Base_BaseBundle (shaders, particle textures)
env = UnityPy.load(*[os.path.join(abm, h, "b") for h in BUNDLES], os.path.join(apk_data, "unity default resources"))

# QuestBattles.csv: battles 1-2 of every In The Dark quest use InFlames_road1, 3-4 hellgate_road2, the boss InFlames_roadEnd
WANTED = {"road1": "InFlames_road1_ArenaScene", "road2": "hellgate_road2_ArenaScene", "roadEnd": "InFlames_roadEnd_ArenaScene"}
scene_files = {}
for o in env.objects:
    if o.type.name == "AssetBundle":
        for path, cab in o.read_typetree().get("m_SceneHashes", []):
            scene_files[os.path.splitext(os.path.basename(path))[0]] = cab
SCENES = {k: scene_files[v] for k, v in WANTED.items()}
print("scenes:", SCENES)


def script_name(c):
    try:
        return c.read().m_Script.read().m_ClassName
    except Exception:
        return None


def prune(v):
    if isinstance(v, dict):
        if "m_Curve" in v and isinstance(v["m_Curve"], list) and all(isinstance(x, dict) for x in v["m_Curve"]):
            return {"k": [[k["time"], k["value"], k["inSlope"], k["outSlope"]] for k in v["m_Curve"]]}
        if "key0" in v and "ctime0" in v:
            nc, na = v["m_NumColorKeys"], v["m_NumAlphaKeys"]
            return {"c": [[v[f"ctime{i}"] / 65535, v[f"key{i}"]["r"], v[f"key{i}"]["g"], v[f"key{i}"]["b"]] for i in range(nc)],
                    "a": [[v[f"atime{i}"] / 65535, v[f"key{i}"]["a"]] for i in range(na)], "mode": v.get("m_Mode", 0)}
        return {k: prune(x) for k, x in v.items() if not (isinstance(x, dict) and set(x) == {"m_FileID", "m_PathID"})}
    if isinstance(v, list):
        return [prune(x) for x in v]
    return v


def clean(x):
    if isinstance(x, float) and not math.isfinite(x):
        return None
    if isinstance(x, dict):
        return {k: clean(y) for k, y in x.items()}
    if isinstance(x, list):
        return [clean(y) for y in x]
    return x


def col(c):
    return [round(c.r, 4), round(c.g, 4), round(c.b, 4), round(c.a, 4)]


# ------------------------------------------------------------------ textures
textures, tex_ids = [], {}


def texture(pptr, alpha=False, max_size=1024):
    """Export a texture once. Opaque-shader textures go to JPEG (their alpha is never read)."""
    if not pptr or not pptr.path_id:
        return None
    try:
        t = pptr.read()
    except Exception as e:
        print("  ! texture unavailable", e)
        return None
    if type(t).__name__ != "Texture2D":
        return None
    key = (t.assets_file.name, t.object_reader.path_id, alpha)
    if key in tex_ids:
        return tex_ids[key]
    img = t.image
    img.thumbnail((max_size, max_size))
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", t.m_Name)
    if alpha:
        fname = f"tex/{base}.png"
        img.save(os.path.join(out_dir, fname), "PNG", optimize=True)
    else:
        fname = f"tex/{base}.jpg"
        img.convert("RGB").save(os.path.join(out_dir, fname), "JPEG", quality=88)
    textures.append({"name": t.m_Name, "src": fname, "size": list(img.size)})
    tex_ids[key] = len(textures) - 1
    return tex_ids[key]


# ------------------------------------------------------------------ materials
materials, mat_ids = [], {}
OPAQUE = ("RHI/BlackcombEnvDistanceRampBakeLights", "RHI/BlackcombEnvDistanceRampBakeLightsUVScroll", "RHI/BlackcombSkyBox")


def material(pptr, render_queue=None):
    try:
        mat = pptr.read()
    except Exception as e:
        print("  ! material unavailable", e)
        return None
    key = (mat.assets_file.name, mat.object_reader.path_id)
    if key in mat_ids:
        return mat_ids[key]
    try:
        shader = mat.m_Shader.read().m_ParsedForm.m_Name
    except Exception:
        shader = None
    p = mat.m_SavedProperties
    tex = dict(p.m_TexEnvs)
    fl = {k: v for k, v in p.m_Floats}
    co = {k: col(v) for k, v in p.m_Colors}
    main = tex.get("_MainTex")
    alpha = shader not in OPAQUE
    kw = getattr(mat, "m_ShaderKeywords", None)
    if kw is None:
        kw = " ".join(getattr(mat, "m_ValidKeywords", []) or [])
    entry = {
        "name": mat.m_Name, "shader": shader,
        "tex": texture(main.m_Texture, alpha) if main else None,
        "st": [main.m_Scale.x, main.m_Scale.y, main.m_Offset.x, main.m_Offset.y] if main else [1, 1, 0, 0],
        "fog": "_FOG_ON" in (kw or ""),
        "fogRamp": texture(tex["_FogRampTex"].m_Texture, True, 256) if "_FogRampTex" in tex else None,
        "rampOffset": co.get("_DistanceRampOffset", [0, 0, 0, 1])[:3],
        "rampRange": co.get("_DistanceRampRange", [0.01, 0.01, 0.01, 1])[:3],
        "alphaMode": fl.get("_AlphaMode", 10.0),
        "queue": mat.m_CustomRenderQueue,
        "particle": {"tintColor": co.get("_TintColor"), "invFade": fl.get("_InvFade")} if shader and "Particles" in shader else None,
    }
    materials.append(entry)
    mat_ids[key] = len(materials) - 1
    return mat_ids[key]


# ------------------------------------------------------------------ GLB builder
class GLB:
    def __init__(self):
        self.bin = bytearray()
        self.views, self.accessors, self.meshes, self.nodes, self.mats = [], [], [], [], {}

    def _view(self, data, target=None):
        while len(self.bin) % 4:
            self.bin.append(0)
        off = len(self.bin)
        self.bin += data
        v = {"buffer": 0, "byteOffset": off, "byteLength": len(data)}
        if target:
            v["target"] = target
        self.views.append(v)
        return len(self.views) - 1

    def accessor(self, arr, ctype, typ, normalized=False, target=34962, minmax=False):
        view = self._view(arr.tobytes(), target)
        a = {"bufferView": view, "componentType": ctype, "count": int(arr.shape[0]), "type": typ}
        if normalized:
            a["normalized"] = True
        if minmax:
            a["min"] = arr.min(axis=0).tolist()
            a["max"] = arr.max(axis=0).tolist()
        self.accessors.append(a)
        return len(self.accessors) - 1

    def material(self, mid):
        if mid not in self.mats:
            self.mats[mid] = len(self.mats)
        return self.mats[mid]

    def save(self, path, scene_roots):
        mats = sorted(self.mats.items(), key=lambda kv: kv[1])
        gltf = {
            "asset": {"version": "2.0", "generator": "export_arena.py"},
            "scene": 0, "scenes": [{"nodes": scene_roots}],
            "nodes": self.nodes, "meshes": self.meshes,
            "materials": [{"name": materials[mid]["name"], "extras": {"id": mid}} for mid, _ in mats],
            "accessors": self.accessors, "bufferViews": self.views,
            "buffers": [{"byteLength": len(self.bin)}],
        }
        js = json.dumps(gltf, separators=(",", ":")).encode()
        js += b" " * (-len(js) % 4)
        while len(self.bin) % 4:
            self.bin.append(0)
        with open(path, "wb") as f:
            f.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(self.bin)))
            f.write(struct.pack("<II", len(js), 0x4E4F534A)); f.write(js)
            f.write(struct.pack("<II", len(self.bin), 0x004E4942)); f.write(self.bin)


def vertex_colors(go, n, base):
    """Replay RHICommon.ImplicitMeshVertexColorApply.ImplicitMeshApply (read from the ARM64 code):
    data[0] = channel count (1, 3 or 4); then per vertex 1/3/4 bytes; m_applyTo picks the target channels
    (0 R, 1 G, 2 B, 3 A, 4 RGB); components apply in order on top of the mesh's own colours."""
    c = base.copy()
    for comp in go.m_Component:
        p = comp.component if hasattr(comp, "component") else comp[1]
        if p.type.name != "MonoBehaviour" or script_name(p) != "ImplicitMeshVertexColorApply":
            continue
        tt = p.deref().read_typetree()
        data = bytes(bytearray(tt["m_applydata"]))
        if not data:
            continue
        fmt, to = data[0], tt["m_applyTo"]
        step = 1 if fmt < 2 else (3 if fmt < 4 else 4)
        vals = np.frombuffer(data[1:1 + n * step], np.uint8).reshape(n, step)
        if step == 1:
            rgba = np.repeat(vals, 4, axis=1)
        elif step == 3:
            rgba = np.concatenate([vals, np.full((n, 1), 255, np.uint8)], axis=1)
        else:
            rgba = vals
        if to == 4:
            c[:, :3] = rgba[:, :3]
        elif to in (0, 1, 2, 3):
            c[:, to] = rgba[:, to]
    return c


def mesh_data(mesh_obj, go):
    h = MeshHandler(mesh_obj); h.process()
    pos = np.array(h.m_Vertices, np.float32)[:, :3] * np.array([-1, 1, 1], np.float32)
    n = len(pos)
    uv = np.array(h.m_UV0, np.float32)[:, :2] if h.m_UV0 else np.zeros((n, 2), np.float32)
    baked = any(script_name(c.component if hasattr(c, "component") else c[1]) == "ImplicitMeshVertexColorApply"
                for c in go.m_Component)
    if h.m_Colors:
        base = (np.clip(np.array(h.m_Colors, np.float32)[:, :4], 0, 1) * 255 + 0.5).astype(np.uint8)
    elif baked:
        base = np.zeros((n, 4), np.uint8)          # ImplicitMeshApply starts from Color32(0,0,0,0)
    else:
        base = np.full((n, 4), 255, np.uint8)      # no colour stream: the shaders see white
    colors = vertex_colors(go, n, base)
    subs = [np.array(t, np.uint32).reshape(-1, 3)[:, ::-1].reshape(-1) for t in h.get_triangles()]
    return pos, uv, colors, subs


# ------------------------------------------------------------------ scene walk
def components(go):
    return [comp.component if hasattr(comp, "component") else comp[1] for comp in go.m_Component]


def export_scene(key, fname):
    objs = [o for o in env.objects if o.assets_file.name == fname]
    trs = {o.path_id: o.read() for o in objs if o.type.name == "Transform"}
    roots = [t for t in trs.values() if not t.m_Father.path_id]
    info = {"periods": {}, "nodes": {}}

    # what to keep: renderers, particles, animated transforms (+ ancestors)
    keep = set()
    parent = {}
    for t in trs.values():
        for ch in t.m_Children:
            parent[ch.path_id] = t.object_reader.path_id

    def mark(pid):
        while pid and pid not in keep:
            keep.add(pid)
            pid = parent.get(pid)

    skip_names = {"TS_QuadCamera", "TS_RenderTargetCamera", "fog marker", "BlackShaderManager", "CustomRenderQueues"}
    def skipped(t):
        while t is not None:
            if t.m_GameObject.read().m_Name in skip_names:
                return True
            t = trs.get(t.m_Father.path_id) if t.m_Father.path_id else None
        return False

    for pid, t in trs.items():
        go = t.m_GameObject.read()
        types = [c.type.name for c in components(go)]
        scripts = [script_name(c) for c in components(go) if c.type.name == "MonoBehaviour"]
        if skipped(t):
            continue
        if ("MeshRenderer" in types or "ParticleSystem" in types or "Light" in types and go.m_Name == "Directional light"
                or {"RotateXYZBehaviour", "ControlEulerAnglesFromCurve"} & set(scripts)):
            mark(pid)

    glb = GLB()
    node_index = {}
    order = []

    def add(t):
        pid = t.object_reader.path_id
        go = t.m_GameObject.read()
        p, q, s = t.m_LocalPosition, t.m_LocalRotation, t.m_LocalScale
        nd = {"name": go.m_Name}
        if (p.x, p.y, p.z) != (0, 0, 0):
            nd["translation"] = [-p.x, p.y, p.z]
        if (q.x, q.y, q.z, q.w) != (0, 0, 0, 1):
            nd["rotation"] = [q.x, -q.y, -q.z, q.w]
        if (s.x, s.y, s.z) != (1, 1, 1):
            nd["scale"] = [s.x, s.y, s.z]
        idx = len(glb.nodes)
        glb.nodes.append(nd)
        node_index[pid] = idx
        order.append((go, t, idx))
        extras = {}
        if not go.m_IsActive:
            extras["inactive"] = True
        kids = [trs[c.path_id] for c in t.m_Children if c.path_id in keep and c.path_id in trs]
        if kids:
            nd["children"] = [add(k) for k in kids]
        # mesh
        comps = components(go)
        mf = next((c for c in comps if c.type.name == "MeshFilter"), None)
        mr = next((c for c in comps if c.type.name == "MeshRenderer"), None)
        if mf and mr:
            r = mr.read()
            m = mf.read().m_Mesh
            mesh_obj = None
            if m.path_id and r.m_Enabled:
                try:
                    mesh_obj = m.read()
                except Exception as e:
                    print("  ! mesh unavailable on", go.m_Name, e)
            if mesh_obj is not None:
                pos, uv, colors, subs = mesh_data(mesh_obj, go)
                prims = []
                a_pos = glb.accessor(pos.astype(np.float32), 5126, "VEC3", minmax=True)
                a_uv = glb.accessor(uv.astype(np.float32), 5126, "VEC2")
                a_col = glb.accessor(colors.astype(np.uint8), 5121, "VEC4", normalized=True)
                for i, tri in enumerate(subs):
                    if i >= len(r.m_Materials) or not len(tri):
                        continue
                    mid = material(r.m_Materials[i])
                    if mid is None:
                        continue
                    if pos.shape[0] < 65536:
                        a_idx = glb.accessor(tri.astype(np.uint16), 5123, "SCALAR", target=34963)
                    else:
                        a_idx = glb.accessor(tri.astype(np.uint32), 5125, "SCALAR", target=34963)
                    prims.append({"attributes": {"POSITION": a_pos, "TEXCOORD_0": a_uv, "COLOR_0": a_col},
                                  "indices": a_idx, "material": glb.material(mid)})
                if prims:
                    glb.meshes.append({"name": mesh_obj.m_Name, "primitives": prims})
                    nd["mesh"] = len(glb.meshes) - 1
        if extras:
            nd["extras"] = extras
        return idx

    root_nodes = [add(r) for r in roots if r.object_reader.path_id in keep]

    # per-node behaviours
    rotators, eulers, scrollers, systems, lights = [], [], [], [], {}
    for go, t, idx in order:
        for c in components(go):
            if c.type.name == "MonoBehaviour":
                sn = script_name(c)
                if sn not in ("RotateXYZBehaviour", "ControlEulerAnglesFromCurve", "ControlUVOffsetsFromCurve"):
                    continue
                tt = c.deref().read_typetree()
                if not tt.get("m_Enabled", 1):
                    continue
                if sn == "RotateXYZBehaviour":
                    rotators.append({"node": idx, "speed": [tt["Xspeed"], tt["Yspeed"], tt["Zspeed"]]})
                elif sn == "ControlEulerAnglesFromCurve":
                    eulers.append({"node": idx, "duration": tt["duration"], "delay": tt["startDelay"], "loop": tt["loop"],
                                   "x": prune(tt["xRotation"]), "y": prune(tt["yRotation"]), "z": prune(tt["zRotation"]),
                                   "space": tt["space"], "addStart": tt["addStartRotation"]})
                else:
                    scrollers.append({"node": idx, "duration": tt["duration"], "delay": tt["startDelay"], "loop": tt["loop"],
                                      "x": prune(tt["xOffset"]), "y": prune(tt["yOffset"]), "matIndex": tt["materialIndex"],
                                      "prop": tt["texturePropertyName"]})
            elif c.type.name == "Light" and go.m_Name == "Directional light":
                L = c.read()
                lights[idx] = {"node": idx, "color": col(L.m_Color), "intensity": L.m_Intensity}
            elif c.type.name == "ParticleSystem":
                tt = c.deref().read_typetree()
                sysd = {"node": idx, "name": go.m_Name, "active": bool(go.m_IsActive)}
                for k in ("lengthInSec", "simulationSpeed", "looping", "prewarm", "playOnAwake", "startDelay",
                          "moveWithTransform", "scalingMode"):
                    sysd[k] = prune(tt[k])
                for k in ("InitialModule", "ShapeModule", "EmissionModule", "SizeModule", "RotationModule", "ColorModule",
                          "UVModule", "VelocityModule", "ForceModule", "ClampVelocityModule", "InheritVelocityModule",
                          "RotationBySpeedModule"):
                    if k in tt and tt[k].get("enabled"):
                        sysd[k] = prune(tt[k])
                rend = next((x for x in components(go) if x.type.name == "ParticleSystemRenderer"), None)
                if rend:
                    r = rend.read(); rt = rend.deref().read_typetree()
                    sysd["renderer"] = {
                        "enabled": bool(r.m_Enabled), "mode": rt["m_RenderMode"], "lengthScale": rt["m_LengthScale"],
                        "velocityScale": rt["m_VelocityScale"], "maxSize": rt["m_MaxParticleSize"],
                        "align": rt["m_RenderAlignment"], "fudge": rt["m_SortingFudge"], "order": rt["m_SortingOrder"],
                        "pivot": [rt["m_Pivot"]["x"], rt["m_Pivot"]["y"], rt["m_Pivot"]["z"]],
                        "material": material(r.m_Materials[0]) if r.m_Materials else None, "mesh": None}
                    if rt["m_RenderMode"] == 4:
                        print("  ! mesh particles not exported:", go.m_Name)
                systems.append(sysd)

    # periods (SetVisibleByTimePeriod), shader manager, directional lights
    managers = {}
    for o in objs:
        if o.type.name != "MonoBehaviour":
            continue
        sn = script_name(o)
        if sn in ("SetVisibleByTimePeriod", "BlackShaderManager"):
            managers[sn] = (o, o.read_typetree())
    sv = managers["SetVisibleByTimePeriod"]
    for per in ("past", "present", "future"):
        prm = sv[1][per + "Parameters"]
        vis = [node_index.get(trs_by_go(objs, v["m_PathID"]), None) for v in prm["visibleList"]]
        ramp = resolve_tex(sv[0], prm["FogRampTex"])
        # the period's directional light: Directional light below its root
        light = None
        for go, t, idx in order:
            if idx in lights and is_below(t, [v for v in prm["visibleList"]], trs):
                light = idx
        info["periods"][per] = {"roots": [v for v in vis if v is not None], "fogRamp": ramp,
                                "shadowColor": [prm["shadowColor"][c] for c in "rgba"],
                                "rimColor": [prm["rimColor"][c] for c in "rgba"], "light": light}
    bsm = managers["BlackShaderManager"][1]
    custom = {}
    for prm in bsm["customParameters"]:
        if prm["type"] == 3:
            custom[prm["name"]] = {"tex": resolve_tex(managers["BlackShaderManager"][0], prm["textureValue"])}
        elif prm["type"] == 1:
            custom[prm["name"]] = prm["floatValue"]
        else:
            custom[prm["name"]] = [prm["colorValue"][c] for c in "rgba"]
    info["shaderManager"] = {"custom": custom, "dirLightColor": [bsm["m_dirLightColor"][c] for c in "rgba"]}
    info.update({"rotators": rotators, "eulers": eulers, "scrollers": scrollers, "systems": systems,
                 "lights": list(lights.values())})
    glb.save(os.path.join(out_dir, f"{key}.glb"), root_nodes)
    print(f"{key}: {len(glb.nodes)} nodes, {len(glb.meshes)} meshes, {len(systems)} particle systems, "
          f"{len(rotators)} rotators, {len(eulers)} euler curves, {len(scrollers)} uv scrollers, "
          f"{len(glb.bin) / 1e6:.1f} MB geometry")
    return info


def trs_by_go(objs, go_pid):
    for o in objs:
        if o.type.name == "GameObject" and o.path_id == go_pid:
            for c in o.read().m_Component:
                p = c.component if hasattr(c, "component") else c[1]
                if p.type.name == "Transform":
                    return p.path_id
    return None


def is_below(t, gos, trs):
    want = {g["m_PathID"] for g in gos}
    cur = t
    while cur is not None:
        if cur.m_GameObject.path_id in want:
            return True
        cur = trs.get(cur.m_Father.path_id) if cur.m_Father.path_id else None
    return False


def resolve_tex(owner, ref):
    if not ref["m_PathID"]:
        return None
    af = owner.assets_file
    if ref["m_FileID"] == 0:
        target = af.name
    else:
        target = af.externals[ref["m_FileID"] - 1].name.split("/")[-1]
    for o in env.objects:
        if o.path_id == ref["m_PathID"] and o.assets_file.name.lower() == target.lower():
            return texture(o, True, 256)
    print("  ! unresolved texture", target, ref)
    return None


def apk_transform(fname, path):
    """Local position/rotation of a named transform (path of names from the root) in an APK serialized file."""
    e = UnityPy.load(os.path.join(apk_data, fname))
    trs = [o.read() for o in e.objects if o.type.name == "Transform"]

    def chain(t):
        out = []
        while t is not None:
            out.append(t.m_GameObject.read().m_Name)
            t = t.m_Father.read() if t.m_Father.path_id else None
        return out[::-1]
    t = next(t for t in trs if chain(t)[-len(path):] == path)
    p, q = t.m_LocalPosition, t.m_LocalRotation
    return {"pos": [p.x, p.y, p.z], "rot": [q.x, q.y, q.z, q.w], "file": fname, "path": "/".join(chain(t))}


def apk_camera_fov(fname, name):
    e = UnityPy.load(os.path.join(apk_data, fname))
    for o in e.objects:
        if o.type.name == "Camera" and o.read().m_GameObject.read().m_Name == name:
            return o.read().field_of_view


# Quests.csv: cameraClassOverride = CharacterDungeonB for every In The Dark quest; dungeon battles use DungeonSlotsContainer.
# The camera parents are at the origin, so these local transforms are world transforms.
camera = apk_transform("7cf4a003b06872d48a3a387ffe68f7ac", ["cam_MAIN_Parent", "cam_MAIN_offset_CharacterDungeonB"])
camera["fov"] = apk_camera_fov("7cf4a003b06872d48a3a387ffe68f7ac", "cam_MAIN")
SLOTS = {"team1": ["ForwardSlot", "LeftSlot", "RightSlot", "FarRightSlot"],
         "team2": ["Slot01", "Slot02", "Slot03", "Slot04", "Slot05", "Slot06"]}
slots = {team: {n: apk_transform("27f21ca44a55bcf4286da33d31e2f12d", ["DungeonSlotsContainer", team.title(), n])
                for n in names} for team, names in SLOTS.items()}
slot = slots["team1"]["ForwardSlot"]
print("camera", camera, "\nslot", slot)

data = {"scenes": {}, "camera": camera, "slot": slot, "slots": slots}
for key, fname in SCENES.items():
    data["scenes"][key] = export_scene(key, fname)
data["materials"] = materials
data["textures"] = textures
json.dump(clean(data), open(os.path.join(out_dir, "arena.json"), "w"), separators=(",", ":"), allow_nan=False)
print(f"{len(materials)} materials, {len(textures)} textures")
for m in materials:
    print("  mat", m["name"], "|", m["shader"], "| tex", m["tex"], "fog", m["fog"], m["fogRamp"], "q", m["queue"])
