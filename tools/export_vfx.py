"""Export Jesterhead particle systems, VFX triggers and clock-chain rigs to JSON.

Node indices follow the same DFS order as build_glb.py, so they address the GLB nodes.
usage: export_vfx.py <out_dir> <bundle> [dependency bundles...]   (writes <out_dir>/vfx.json and <out_dir>/vfx/*.png)
"""
import base64, io, json, os, re, sys
import numpy as np
import UnityPy
from UnityPy.helpers.MeshHelper import MeshHandler

out_dir, *bundles = sys.argv[1:]
os.makedirs(os.path.join(out_dir, "vfx"), exist_ok=True)
env = UnityPy.load(*bundles)
root_go = next(o.read() for o in env.objects
               if o.type.name == "GameObject" and o.peek_name() == "Jesterhead_Prefab")

# ------------------------------------------------------------ hierarchy (same order as build_glb)
order = []          # (go, transform)
go_node = {}        # GameObject path_id -> node index


def walk(tr):
    go = tr.m_GameObject.read()
    go_node[tr.m_GameObject.path_id] = len(order)
    order.append((go, tr))
    for ch in tr.m_Children:
        walk(ch.read())


root_tr = [p.component.read() for p in root_go.m_Component if p.component.type.name == "Transform"][0]
walk(root_tr)


def comps(go):
    return [p.component for p in go.m_Component]


def script_name(c):
    try:
        return c.read().m_Script.read().m_ClassName
    except Exception:
        return None


# ------------------------------------------------------------ textures / meshes / materials
textures, tex_ids = [], {}


def texture(pptr, max_size=1024):
    key = (pptr.m_FileID, pptr.m_PathID)
    if key in tex_ids:
        return tex_ids[key]
    try:
        t = pptr.read()
        img = t.image
    except Exception as e:
        print("  ! texture unavailable", key, e)
        tex_ids[key] = None
        return None
    img.thumbnail((max_size, max_size))
    fname = "vfx/" + re.sub(r"[^A-Za-z0-9_.-]+", "_", t.m_Name) + ".png"
    img.save(os.path.join(out_dir, fname), "PNG", optimize=True)
    textures.append({"name": t.m_Name, "src": fname})
    tex_ids[key] = len(textures) - 1
    return tex_ids[key]


meshes, mesh_ids = [], {}


def mesh(pptr):
    key = (pptr.m_FileID, pptr.m_PathID)
    if key in mesh_ids:
        return mesh_ids[key]
    try:
        m = pptr.read()
        h = MeshHandler(m); h.process()
    except Exception as e:
        print("  ! mesh unavailable", key, e)
        mesh_ids[key] = None
        return None
    pos = (np.array(h.m_Vertices, np.float32)[:, :3] * [-1, 1, 1]).round(4)
    uv = np.array(h.m_UV0, np.float32)[:, :2].round(4) if h.m_UV0 else np.zeros((len(pos), 2))
    col = (np.array(h.m_Colors, np.float32)[:, :4].round(3).tolist() if h.m_Colors else None)
    idx = []
    for tri in h.get_triangles():
        idx += np.array(tri, np.uint32).reshape(-1, 3)[:, ::-1].reshape(-1).tolist()
    meshes.append({"name": m.m_Name, "pos": pos.reshape(-1).tolist(), "uv": uv.reshape(-1).tolist(),
                   "color": col and np.array(col).reshape(-1).tolist(), "index": idx})
    mesh_ids[key] = len(meshes) - 1
    return mesh_ids[key]


materials, mat_ids = [], {}


def material(pptr):
    key = (pptr.m_FileID, pptr.m_PathID)
    if key in mat_ids:
        return mat_ids[key]
    try:
        mat = pptr.read()
    except Exception as e:
        print("  ! material unavailable", key, e)
        mat_ids[key] = None
        return None
    try:
        shader = mat.m_Shader.read().m_ParsedForm.m_Name
    except Exception:
        shader = None
    props = mat.m_SavedProperties
    tex = {k: v for k, v in props.m_TexEnvs}
    main = tex.get("_MainTex")
    floats = {k: v for k, v in props.m_Floats}
    colors = {k: [c.r, c.g, c.b, c.a] for k, c in props.m_Colors}
    entry = {
        "name": mat.m_Name, "shader": shader,
        "tex": texture(main.m_Texture) if main and main.m_Texture.path_id else None,
        "st": [main.m_Scale.x, main.m_Scale.y, main.m_Offset.x, main.m_Offset.y] if main else [1, 1, 0, 0],
        "floats": {k: v for k, v in floats.items() if k in (
            "_SrcBlend", "_DstBlend", "_Mode", "_Cutoff", "_Brightness", "_Intensity", "_InvFade",
            "_EmissiveBlend", "_CustomBlend", "_Cull", "_ZWrite")},
        "colors": {k: v for k, v in colors.items() if k in ("_TintColor", "_Color", "_EmissionColor")},
        "texs": sorted(k for k, v in tex.items() if v.m_Texture.path_id),
        "queue": mat.m_CustomRenderQueue,             # -1: the shader's queue (Transparent = 3000 for particles)
    }
    materials.append(entry)
    mat_ids[key] = len(materials) - 1
    return mat_ids[key]


# ------------------------------------------------------------ particle systems
KEEP_PS = ("lengthInSec", "simulationSpeed", "looping", "prewarm", "playOnAwake", "startDelay",
           "moveWithTransform", "scalingMode")
KEEP_MODULES = ("InitialModule", "ShapeModule", "EmissionModule", "SizeModule", "RotationModule",
                "ColorModule", "UVModule", "VelocityModule", "ForceModule", "ClampVelocityModule",
                "SizeBySpeedModule", "ColorBySpeedModule", "NoiseModule", "InheritVelocityModule", "RotationBySpeedModule")


def prune(v):
    """Drop empty curves/defaults to keep the JSON small."""
    if isinstance(v, dict):
        if "m_Curve" in v and isinstance(v["m_Curve"], list) and all(isinstance(x, dict) for x in v["m_Curve"]):
            return {"k": [[k["time"], k["value"], k["inSlope"], k["outSlope"]] for k in v["m_Curve"]]}
        if "key0" in v and "ctime0" in v:
            nc, na = v["m_NumColorKeys"], v["m_NumAlphaKeys"]
            return {"c": [[v[f"ctime{i}"] / 65535, v[f"key{i}"]["r"], v[f"key{i}"]["g"], v[f"key{i}"]["b"]] for i in range(nc)],
                    "a": [[v[f"atime{i}"] / 65535, v[f"key{i}"]["a"]] for i in range(na)],
                    "mode": v.get("m_Mode", 0)}
        return {k: prune(x) for k, x in v.items() if not (isinstance(x, dict) and set(x) == {"m_FileID", "m_PathID"})}
    if isinstance(v, list):
        return [prune(x) for x in v]
    return v


systems = []
uv_scrollers = {}
for go, tr in order:
    node = go_node[tr.m_GameObject.path_id]
    for c in comps(go):
        if c.type.name == "MonoBehaviour" and script_name(c) == "ControlUVOffsetsFromCurve":
            tt = c.deref().read_typetree()
            # materialIndex 1 on a particle renderer targets the trail material, not the particles
            if tt.get("texturePropertyName", "_MainTex") == "_MainTex":
                uv_scrollers.setdefault(node, {})[tt.get("materialIndex", 0)] = {
                    "duration": tt["duration"], "loop": tt["loop"],
                    "x": prune(tt["xOffset"]), "y": prune(tt["yOffset"])}
for go, tr in order:
    node = go_node[tr.m_GameObject.path_id]
    ps = [c for c in comps(go) if c.type.name == "ParticleSystem"]
    if not ps:
        continue
    tt = ps[0].deref().read_typetree()
    sysd = {"node": node, "name": go.m_Name, "active": bool(go.m_IsActive)}
    for k in KEEP_PS:
        sysd[k] = prune(tt[k])
    for k in KEEP_MODULES:
        if k in tt and tt[k].get("enabled"):
            sysd[k] = prune(tt[k])
    shape = tt["ShapeModule"]
    if shape.get("enabled") and shape["m_Mesh"]["m_PathID"]:
        ps0 = ps[0].read()
        sysd["shapeMesh"] = mesh(ps0.ShapeModule.m_Mesh)
    rend = [c for c in comps(go) if c.type.name == "ParticleSystemRenderer"]
    if rend:
        r = rend[0].read()
        rt = rend[0].deref().read_typetree()
        sysd["renderer"] = {
            "enabled": bool(r.m_Enabled),
            "mode": rt["m_RenderMode"], "lengthScale": rt["m_LengthScale"], "velocityScale": rt["m_VelocityScale"],
            "maxSize": rt["m_MaxParticleSize"], "align": rt["m_RenderAlignment"], "fudge": rt["m_SortingFudge"],
            "order": rt["m_SortingOrder"], "pivot": [rt["m_Pivot"]["x"], rt["m_Pivot"]["y"], rt["m_Pivot"]["z"]],
            "material": material(r.m_Materials[0]) if r.m_Materials else None,
            "mesh": mesh(r.m_Mesh) if rt["m_RenderMode"] == 4 and r.m_Mesh.path_id else None,
        }
    sub = tt.get("SubModule", {})
    if sub.get("enabled"):
        sysd["subEmitters"] = [{"emitterPid": e["emitter"]["m_PathID"], "type": e["type"],
                                "properties": e["properties"], "probability": e.get("emitProbability", 1.0)}
                               for e in sub["subEmitters"] if e["emitter"]["m_PathID"]]
    if 0 in uv_scrollers.get(node, {}):
        sysd["uvScroll"] = uv_scrollers[node][0]
    if tt.get("TrailModule", {}).get("enabled") and rend:
        sysd["trail"] = prune(tt["TrailModule"])
        mats_ = r.m_Materials
        sysd["trail"]["material"] = material(mats_[1]) if len(mats_) > 1 else sysd["renderer"]["material"]
        if 1 in uv_scrollers.get(node, {}):
            sysd["trail"]["uvScroll"] = uv_scrollers[node][1]
    systems.append(sysd)

# resolve sub-emitter ParticleSystem pointers to node indices
ps_node = {}
for go, tr in order:
    for c in comps(go):
        if c.type.name == "ParticleSystem":
            ps_node[c.path_id] = go_node[tr.m_GameObject.path_id]
for sd in systems:
    for e in sd.get("subEmitters", []):
        e["node"] = ps_node.get(e.pop("emitterPid"))

# ------------------------------------------------------------ RotateXYZBehaviour (Transform.Rotate * VFX delta time)
rotators = []
for go, tr in order:
    for c in comps(go):
        if c.type.name == "MonoBehaviour" and script_name(c) == "RotateXYZBehaviour":
            tt = c.deref().read_typetree()
            if tt.get("m_Enabled", 1):
                rotators.append({"node": go_node[tr.m_GameObject.path_id], "speed": [tt["Xspeed"], tt["Yspeed"], tt["Zspeed"]]})

# ------------------------------------------------------------ DirectionalLightFX (the "ScreenDimmer" objects)
light_fx = {}
for go, tr in order:
    for c in comps(go):
        if c.type.name == "MonoBehaviour" and script_name(c) == "DirectionalLightFX":
            tt = c.deref().read_typetree()
            dc = tt["darkColor"]
            light_fx[go.m_Name] = {"delay": tt["delayTime"], "duration": tt["updateTime"], "curve": prune(tt["lightControl"]),
                                   "darkColor": [dc["r"], dc["g"], dc["b"], dc["a"]]}

# ------------------------------------------------------------ triggers (PlayParticlesWithAudio)
triggers = []
for go, tr in order:
    node = go_node[tr.m_GameObject.path_id]
    for c in comps(go):
        if c.type.name != "MonoBehaviour" or script_name(c) != "PlayParticlesWithAudio":
            continue
        tt = c.deref().read_typetree()
        toggle = tt["toggleGeometry"]
        triggers.append({
            "node": node, "state": tt["stateName"], "delay": tt["playDelay"],
            "extra": tt["additionalDelays"],
            "toggle": go_node.get(toggle["m_PathID"]) if toggle["m_PathID"] else None,
            "toggleName": (next((g.m_Name for g, t in order if t.m_GameObject.path_id == toggle["m_PathID"]), None)
                           if toggle["m_PathID"] else None),
            "toggleEnable": bool(tt["toggleEnable"]), "startUnToggled": bool(tt["startUnToggled"]),
            "playAudioOnly": bool(tt["playAudioOnly"]),
        })

# ------------------------------------------------------------ clock chains (Rigidbody + joints)
rb_go = {}
chains = []
for go, tr in order:
    for c in comps(go):
        if c.type.name == "Rigidbody":
            rb = c.read()
            rb_go[c.path_id] = (go, tr, rb)
links = {}
for go, tr in order:
    for c in comps(go):
        if c.type.name in ("HingeJoint", "SpringJoint"):
            j = c.read()
            parent = rb_go.get(j.m_ConnectedBody.path_id)
            links[tr.m_GameObject.path_id] = {
                "parent": parent[0].object_reader.path_id if parent else None,
                "conn": [-j.m_ConnectedAnchor.x, j.m_ConnectedAnchor.y, j.m_ConnectedAnchor.z],
                "type": c.type.name}
for pid, (go, tr, rb) in rb_go.items():
    if not rb.m_IsKinematic:
        continue
    seq, cur = [], go.object_reader.path_id
    while True:
        nxt = [k for k, v in links.items() if v["parent"] == cur]
        if not nxt:
            break
        cur = nxt[0]
        g = next(g for g, t in order if t.m_GameObject.path_id == cur)
        r = next(r for (gg, t, r) in rb_go.values() if t.m_GameObject.path_id == cur)
        seq.append({"node": go_node[cur], "name": g.m_Name, "conn": links[cur]["conn"],
                    "mass": r.m_Mass, "drag": r.m_Drag})
    if seq:
        chains.append({"anchor": go_node[go.object_reader.path_id], "name": go.m_Name, "links": seq})

# ------------------------------------------------------------ material tweens (shader emission pulses)
tweens = []
tween_ids = {}
for go, tr in order:
    for c in comps(go):
        if c.type.name != "MonoBehaviour" or script_name(c) != "TweenMaterialProperty":
            continue
        tt = c.deref().read_typetree()
        tween_ids[c.path_id] = len(tweens)
        tweens.append({"node": go_node[tr.m_GameObject.path_id], "name": go.m_Name, "style": tt["style"],
                       "curve": prune(tt["animationCurve"]), "duration": tt["duration"], "delay": tt["delay"],
                       "from": tt["from"], "to": tt["to"], "prop": tt["propertyName"], "matIndex": tt["matIndex"]})
resets = {}
for go, tr in order:
    for c in comps(go):
        if c.type.name == "MonoBehaviour" and script_name(c) == "ResetTweensOnEnable":
            tt = c.deref().read_typetree()
            resets[go.m_Name] = [tween_ids[t["m_PathID"]] for t in tt["tweens"] if t["m_PathID"] in tween_ids]
char_mats = {}
for go, tr in order:
    for c in comps(go):
        if c.type.name in ("SkinnedMeshRenderer", "MeshRenderer"):
            for mp in c.read().m_Materials:
                try:
                    m = mp.read()
                except Exception:
                    continue
                fl = {k: v for k, v in m.m_SavedProperties.m_Floats}
                co = {k: [x.r, x.g, x.b, x.a] for k, x in m.m_SavedProperties.m_Colors}
                tx = {}
                for k, v in m.m_SavedProperties.m_TexEnvs:
                    if v.m_Texture.path_id and k in ("_CharacterLightRampTex", "_RimRampTex"):
                        tx[k] = texture(v.m_Texture, 1024)
                try:
                    shader = m.m_Shader.read().m_ParsedForm.m_Name
                except Exception:
                    shader = None
                char_mats[m.m_Name] = {
                    "shader": shader, "emissiveBlend": fl.get("_EmissiveBlend", 0.0),
                    "rimBlend": fl.get("_RimBlend", 1.0), "rampScale": fl.get("_CharacterLightRampHDRScale", 1.0),
                    "rescaleNormal": fl.get("_RescaleNormal", 1.0),
                    "emissiveColor": co.get("_EmissiveColor", [0, 0, 0, 1]), "rimColor": co.get("_RimColor", [0, 0, 0, 1]),
                    "lightDir": co.get("_LightDir", [1, 10, 2, 0]), "tintColor": co.get("_TintColor", [0.5, 0.5, 0.5, 0.5]),
                    "rampTex": tx.get("_CharacterLightRampTex"), "rimTex": tx.get("_RimRampTex")}

overlay_scroll = {}
for go, tr in order:
    node = go_node[tr.m_GameObject.path_id]
    sc = uv_scrollers.get(node, {}).get(0)
    smr = [c for c in comps(go) if c.type.name == "SkinnedMeshRenderer"]
    if sc and smr:
        m = smr[0].read().m_Materials[0].read()
        st = dict(m.m_SavedProperties.m_TexEnvs)["_MainTex"]
        overlay_scroll[go.m_Name] = {**sc, "st": [st.m_Scale.x, st.m_Scale.y, st.m_Offset.x, st.m_Offset.y]}
print("overlay scroll:", {k: (v["duration"], v["st"]) for k, v in overlay_scroll.items()})

data = {"rotators": rotators, "lightFx": light_fx, "overlayScroll": overlay_scroll, "tweens": tweens, "tweenResets": resets, "charMats": char_mats, "systems": systems, "triggers": triggers, "chains": chains,
        "materials": materials, "textures": textures, "meshes": meshes}
import math
def clean(x):
    if isinstance(x, float) and not math.isfinite(x): return None
    if isinstance(x, dict): return {k: clean(y) for k, y in x.items()}
    if isinstance(x, list): return [clean(y) for y in x]
    return x
json.dump(clean(data), open(os.path.join(out_dir, "vfx.json"), "w"), separators=(",", ":"), allow_nan=False)
print("rotators:", rotators); print("lightFx:", {k: (v["delay"], v["duration"]) for k, v in light_fx.items()})
print("sub-emitters:", [(sd["name"], [(e["node"], e["type"]) for e in sd["subEmitters"]]) for sd in systems if sd.get("subEmitters")])
print("tweens:", [(t["name"], t["style"], t["prop"], t["from"], t["to"]) for t in tweens]); print("resets:", resets); print("charMats:", char_mats)
print(f"{len(systems)} systems, {len(triggers)} triggers, {len(chains)} chains, {len(materials)} materials, "
      f"{len(textures)} textures, {len(meshes)} meshes")
for m in materials:
    print("  mat", m["name"], "|", m["shader"], "| tex", m["tex"], m["floats"], m["colors"])
modes = {}
for s in systems:
    r = s.get("renderer") or {}
    modes.setdefault(r.get("mode"), []).append(s["name"])
print("render modes:", {k: len(v) for k, v in modes.items()})
print("shape types:", sorted({s.get("ShapeModule", {}).get("type") for s in systems}, key=str))
print("modules used:", sorted({k for s in systems for k in s if k.endswith("Module")}))
for c in chains:
    print("  chain", c["name"], [(l["name"], round(l["conn"][1], 3)) for l in c["links"]])
for t in triggers:
    print("  trig", t["state"], t["delay"], order[t["node"]][0].m_Name, t["toggleName"], t["toggleEnable"])
