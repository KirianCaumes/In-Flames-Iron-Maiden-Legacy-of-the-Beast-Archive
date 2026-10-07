"""Build a skinned GLB of another Legacy of the Beast character (here: the enemies of the In The Dark battles)
with a chosen set of animation clips and the parameters of its RHI/BlackcombCharacter* materials.

Same conventions as build_glb.py (X mirrored, quaternions (x,-y,-z,w), reversed winding); textures are read
from the bundle and embedded; material parameters and ramp textures are stored in material extras.

usage: build_character.py <out.glb> <prefab name> <AssetRipper AnimationClip dir> <clip>[,<clip>...] <bundle> [deps...]
"""
import json, os, re, struct, sys
import numpy as np
import yaml
import UnityPy
from UnityPy.helpers.MeshHelper import MeshHandler

out_path, prefab, anim_dir, clip_list, *bundles = sys.argv[1:]
CLIPS = clip_list.split(",")
FPS = 30
env = UnityPy.load(*bundles)
root_go = next(o.read() for o in env.objects if o.type.name == "GameObject" and o.peek_name() == prefab)


def comps(go):
    return [p.component for p in go.m_Component]


# ---------------------------------------------------------------- hierarchy
nodes, tr_index, node_path, node_active, go_of = [], {}, {}, {}, {}


def add_node(tr, path, active):
    go = tr.m_GameObject.read()
    idx = len(nodes)
    tr_index[tr.object_reader.path_id] = idx
    p, r, s = tr.m_LocalPosition, tr.m_LocalRotation, tr.m_LocalScale
    nodes.append({"name": go.m_Name, "translation": [-p.x, p.y, p.z], "rotation": [r.x, -r.y, -r.z, r.w],
                  "scale": [s.x, s.y, s.z]})
    node_path[idx] = path
    node_active[idx] = active and bool(go.m_IsActive)
    go_of[idx] = go
    kids = [add_node(ch.read(), (path + "/" if path else "") + ch.read().m_GameObject.read().m_Name, node_active[idx])
            for ch in tr.m_Children]
    if kids:
        nodes[idx]["children"] = kids
    return idx


root_tr = next(c.read() for c in comps(root_go) if c.type.name == "Transform")
add_node(root_tr, "", True)

# "Optimize Game Objects" prefabs keep their skeleton only in the Avatar: rebuild it under the Animator node.
# The Avatar's TOS table maps path hashes (used by the mesh bones and by the clips) to paths.
hash_node, tos = {}, {}
anim_idx = next(i for i, g in go_of.items() if any(c.type.name == "Animator" for c in comps(g)))
animator = next(c.read() for c in comps(go_of[anim_idx]) if c.type.name == "Animator")
if animator.m_Avatar.path_id:
    av = animator.m_Avatar.read()
    tos = {h: p for h, p in av.m_TOS}
    have = {p for p in node_path.values()}
    base_path = node_path[anim_idx]
    sk, pose = av.m_Avatar.m_AvatarSkeleton.data, av.m_Avatar.m_AvatarSkeletonPose.data
    if any(c.type.name == "SkinnedMeshRenderer" and not c.read().m_Bones for g in go_of.values() for c in comps(g)):
        for i, (node, h) in enumerate(zip(sk.m_Node, sk.m_ID)):
            path = tos.get(h, "")
            if i == 0:
                hash_node[h] = anim_idx
                continue
            x = pose.m_X[i]
            idx = len(nodes)
            nodes.append({"name": path.split("/")[-1] + "_avatar", "translation": [-x.t.x, x.t.y, x.t.z],
                          "rotation": [x.q.x, -x.q.y, -x.q.z, x.q.w], "scale": [x.s.x, x.s.y, x.s.z]})
            parent = hash_node[sk.m_ID[node.m_ParentId]]
            nodes[parent].setdefault("children", []).append(idx)
            node_path[idx] = (base_path + "/" if base_path else "") + path
            node_active[idx] = True
            hash_node[h] = idx
        print(f"  rebuilt {len(hash_node) - 1} bones from {av.m_Name}")
        # "Extra transforms to expose" (weapon joints holding rigid props) stay in the hierarchy, flattened, and
        # the Animator copies the bone's pose onto them: attach each one to its rebuilt bone with an identity pose.
        bone_by_name = {}
        for idx in hash_node.values():
            if idx != anim_idx:
                bone_by_name.setdefault(nodes[idx]["name"][:-len("_avatar")], []).append(idx)
        parent_of = {c: p for p, n in enumerate(nodes) for c in n.get("children", [])}
        for idx in list(go_of):
            bones = bone_by_name.get(nodes[idx]["name"], [])
            if (idx == anim_idx or len(bones) != 1 or idx not in parent_of
                    or any(c.type.name == "SkinnedMeshRenderer" for c in comps(go_of[idx]))):
                continue
            siblings = nodes[parent_of[idx]]["children"]
            siblings.remove(idx)
            if not siblings:
                del nodes[parent_of[idx]]["children"]
            nodes[idx].update(translation=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1])
            nodes[bones[0]].setdefault("children", []).append(idx)
            print(f"  exposed transform {nodes[idx]['name']} follows its bone")

# ---------------------------------------------------------------- buffers
chunks, views, accessors, offset = [], [], [], 0


def view(data, target=None):
    global offset
    pad = (-offset) % 4
    if pad:
        chunks.append(b"\0" * pad); offset += pad
    v = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
    if target:
        v["target"] = target
    views.append(v); chunks.append(data); offset += len(data)
    return len(views) - 1


TYPES = {1: "SCALAR", 2: "VEC2", 3: "VEC3", 4: "VEC4", 16: "MAT4"}
CT = {np.float32: 5126, np.uint16: 5123, np.uint32: 5125, np.uint8: 5121}


def acc(arr, target=None, minmax=False):
    arr = np.ascontiguousarray(arr)
    a = {"bufferView": view(arr.tobytes(), target), "componentType": CT[arr.dtype.type], "count": int(arr.shape[0]),
         "type": TYPES[1 if arr.ndim == 1 else arr.shape[1]]}
    if minmax:
        a["min"] = np.atleast_1d(arr.min(axis=0)).tolist(); a["max"] = np.atleast_1d(arr.max(axis=0)).tolist()
    accessors.append(a)
    return len(accessors) - 1


# ---------------------------------------------------------------- textures / materials
images, textures, materials, tex_ids, mat_ids = [], [], [], {}, {}


def texture(pptr):
    if not pptr.path_id:
        return None
    t = pptr.read()
    key = (t.assets_file.name, t.object_reader.path_id)
    if key not in tex_ids:
        import io
        from PIL import Image
        img = t.image
        if max(img.size) > 1024:
            size = tuple(max(1, round(v * 1024 / max(img.size))) for v in img.size)
            if img.mode == "RGBA":
                # the alpha channel is a mask (emission, custom colour), not transparency: Pillow would premultiply
                # and lose the colour where it is 0, so both are resized on their own
                rgb = img.convert("RGB").resize(size, Image.LANCZOS)
                img = Image.merge("RGBA", (*rgb.split(), img.getchannel("A").resize(size, Image.LANCZOS)))
            else:
                img = img.resize(size, Image.LANCZOS)
        b = io.BytesIO(); img.save(b, "PNG", optimize=True)
        images.append({"bufferView": view(b.getvalue()), "mimeType": "image/png", "name": t.m_Name})
        textures.append({"source": len(images) - 1, "sampler": 0})
        tex_ids[key] = len(textures) - 1
    return tex_ids[key]


def material(pptr):
    mat = pptr.read()
    key = (mat.assets_file.name, mat.object_reader.path_id)
    if key in mat_ids:
        return mat_ids[key]
    shader = mat.m_Shader.read().m_ParsedForm.m_Name
    P = mat.m_SavedProperties
    tx = dict(P.m_TexEnvs); fl = dict(P.m_Floats); co = {k: [c.r, c.g, c.b, c.a] for k, c in P.m_Colors}
    main = tx.get("_MainTex")
    extras = {"shader": shader,
              "rampTex": texture(tx["_CharacterLightRampTex"].m_Texture) if "_CharacterLightRampTex" in tx else None,
              "rimTex": texture(tx["_RimRampTex"].m_Texture) if "_RimRampTex" in tx else None,
              "rampScale": fl.get("_CharacterLightRampHDRScale", 1.0), "rimBlend": fl.get("_RimBlend", 1.0),
              "rimColor": co.get("_RimColor", [0, 0, 0, 1]), "lightDir": co.get("_LightDir", [1, 10, 2, 0]),
              "rescaleNormal": fl.get("_RescaleNormal", 1.0),
              "emissiveColor": co.get("_EmissiveColor", [0, 0, 0, 1]), "emissiveBlend": fl.get("_EmissiveBlend", 0.0),
              "customColor": co.get("_CustomColor", [0, 0, 0, 1]), "customBlend": fl.get("_CustomBlend", 0.0)}
    m = {"name": mat.m_Name, "extras": extras, "pbrMetallicRoughness": {"metallicFactor": 0, "roughnessFactor": 1}}
    if main and main.m_Texture.path_id:
        m["pbrMetallicRoughness"]["baseColorTexture"] = {"index": texture(main.m_Texture)}
        extras["st"] = [main.m_Scale.x, main.m_Scale.y, main.m_Offset.x, main.m_Offset.y]
    materials.append(m)
    mat_ids[key] = len(materials) - 1
    return mat_ids[key]


# ---------------------------------------------------------------- meshes
meshes, skins, FLIP = [], [], np.diag([-1.0, 1, 1, 1])


def loop_tweens(go):
    """TweenMaterialProperty components of a renderer's GameObject that loop on their own (style 1): the idle glow
    pulses. Same evaluation as the viewer's port for Jesterhead: value = Lerp(from, to, curve(time / duration))."""
    finite = lambda x: x if isinstance(x, (int, float)) and np.isfinite(x) else None
    out = []
    for c in comps(go):
        if c.type.name != "MonoBehaviour":
            continue
        try:
            if c.read().m_Script.read().m_Name != "TweenMaterialProperty":
                continue
            tt = c.deref().read_typetree()
        except Exception:
            continue
        if tt.get("m_Enabled") and tt["style"] == 1:
            out.append({"prop": tt["propertyName"], "matIndex": tt["matIndex"], "from": tt["from"], "to": tt["to"],
                        "duration": tt["duration"], "delay": tt["delay"],
                        "curve": {"k": [[k["time"], k["value"], finite(k["inSlope"]), finite(k["outSlope"])]
                                        for k in tt["animationCurve"]["m_Curve"]]}})
    return out


def attach_tweens(go, mats):
    for tw in loop_tweens(go):
        mi = tw.pop("matIndex")
        if mi < len(mats):
            materials[material(mats[mi])]["extras"].setdefault("tweens", []).append(tw)
            print(f"  material tween {tw['prop']} {tw['from']} -> {tw['to']} every {tw['duration']} s on {go.m_Name}")


def prims(m, mats, skinned):
    h = MeshHandler(m); h.process()
    attrs = {"POSITION": acc((np.array(h.m_Vertices, np.float32)[:, :3] * [-1, 1, 1]).astype(np.float32), 34962, True),
             "NORMAL": acc((np.array(h.m_Normals, np.float32)[:, :3] * [-1, 1, 1]).astype(np.float32), 34962)}
    uv = np.array(h.m_UV0, np.float32)[:, :2].copy()
    uv[:, 1] = 1.0 - uv[:, 1]                       # glTF texture origin is top-left
    attrs["TEXCOORD_0"] = acc(uv, 34962)
    if skinned:
        j = np.array(h.m_BoneIndices, np.uint16)
        w = np.array(h.m_BoneWeights, np.float32)
        if j.shape[1] < 4:                             # meshes skinned with 1 or 2 bones per vertex
            j = np.pad(j, ((0, 0), (0, 4 - j.shape[1])))
            w = np.pad(w, ((0, 0), (0, 4 - w.shape[1])))
        bad = (w < 0).any(axis=1) | (np.abs(w.sum(axis=1) - 1) > 0.02)   # UnityPy compressed-weight decoding
        w[bad, 3] = np.clip(1 - w[bad, :3].sum(axis=1), 0, 1)
        attrs["JOINTS_0"] = acc(j, 34962)
        attrs["WEIGHTS_0"] = acc((w / np.maximum(w.sum(axis=1, keepdims=True), 1e-8)).astype(np.float32), 34962)
    out = []
    for i, tri in enumerate(h.get_triangles()):
        if i < len(mats):
            out.append({"attributes": attrs, "material": material(mats[i]),
                        "indices": acc(np.array(tri, np.uint32).reshape(-1, 3)[:, ::-1].reshape(-1), 34963)})
    return out


roots = [0]
for idx in list(node_path):
    if not node_active[idx] or idx not in go_of:
        continue
    go = go_of[idx]
    cs = comps(go)
    for c in cs:
        if c.type.name == "SkinnedMeshRenderer":
            r = c.read()
            if not r.m_Enabled:
                continue
            m = r.m_Mesh.read()
            ibm = np.array([[[getattr(bp, f"e{a}{b}") for b in range(4)] for a in range(4)] for bp in m.m_BindPose])
            fallback = tr_index.get(r.m_RootBone.path_id, 0)      # missing (null) bone references
            joints = ([tr_index.get(b.path_id, fallback) for b in r.m_Bones] if r.m_Bones
                      else [hash_node[h] for h in m.m_BoneNameHashes])
            skins.append({"joints": joints,
                          "inverseBindMatrices": acc(np.array([(FLIP @ x @ FLIP).T.reshape(-1) for x in ibm], np.float32))})
            meshes.append({"name": m.m_Name, "primitives": prims(m, r.m_Materials, True)})
            attach_tweens(go, r.m_Materials)
            nodes.append({"name": go.m_Name + "_skinned", "mesh": len(meshes) - 1, "skin": len(skins) - 1})
            roots.append(len(nodes) - 1)
        elif c.type.name == "MeshFilter":
            mr = next((x.read() for x in cs if x.type.name == "MeshRenderer"), None)
            if mr is None or not mr.m_Enabled:
                continue
            m = c.read().m_Mesh.read()
            meshes.append({"name": m.m_Name, "primitives": prims(m, mr.m_Materials, False)})
            attach_tweens(go, mr.m_Materials)
            nodes[idx]["mesh"] = len(meshes) - 1

# ---------------------------------------------------------------- animations (paths relative to the Animator)
base = node_path[anim_idx]
by_path = {}
for i, p in node_path.items():
    if p == base or p.startswith(base + "/") or not base:
        rel = p[len(base) + 1:] if base else p
        by_path[rel] = i
        if "/" in rel:                                  # clips are relative to the avatar root (the *_mdl child)
            by_path.setdefault(rel.split("/", 1)[1], i)


def load_yaml(path):
    txt = re.sub(r"^--- !u!\d+ &\d+.*$", "---", open(path).read(), flags=re.M)
    return next(yaml.load_all(re.sub(r"^%TAG.*$", "", txt, flags=re.M), Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader)))


def hermite(keys, t, cs):
    times = np.array([k["time"] for k in keys])
    vals = np.array([[k["value"][c] for c in cs] for k in keys], np.float64)
    sl = lambda k, w: [float(k[w][c]) if not isinstance(k[w][c], str) else float("inf") for c in cs]
    ins = np.array([sl(k, "inSlope") for k in keys]); outs = np.array([sl(k, "outSlope") for k in keys])
    res = np.empty((len(t), len(cs)))
    for i, tt in enumerate(t):
        if tt <= times[0] or len(keys) == 1:
            res[i] = vals[0]; continue
        if tt >= times[-1]:
            res[i] = vals[-1]; continue
        j = np.searchsorted(times, tt) - 1
        dt = times[j + 1] - times[j]; s = (tt - times[j]) / dt
        m0, m1 = outs[j] * dt, ins[j + 1] * dt
        r = ((2 * s**3 - 3 * s**2 + 1) * vals[j] + (s**3 - 2 * s**2 + s) * m0
             + (-2 * s**3 + 3 * s**2) * vals[j + 1] + (s**3 - s**2) * m1)
        st = ~np.isfinite(m0) | ~np.isfinite(m1)
        r[st] = vals[j][st]
        res[i] = r
    return res


animations = []
for name in CLIPS:
    clip = load_yaml(os.path.join(anim_dir, name + ".anim"))["AnimationClip"]
    stop = float(clip["m_AnimationClipSettings"]["m_StopTime"])
    t = np.arange(0, stop + 1e-6, 1.0 / FPS)
    if t[-1] < stop - 1e-4:
        t = np.append(t, stop)
    ta = acc(t.astype(np.float32), minmax=True)
    ch, sa = [], []
    for key, prop, cs in (("m_RotationCurves", "rotation", "xyzw"), ("m_PositionCurves", "translation", "xyz"),
                          ("m_ScaleCurves", "scale", "xyz")):
        for cv in clip[key] or []:
            path = cv["path"]
            if isinstance(path, int) or (isinstance(path, str) and path.isdigit()):
                h = int(path)
                node = hash_node.get(h)
                if node is None:
                    node = by_path.get(tos.get(h, ""))
            else:
                node = by_path.get(path)
            if node is None:
                continue
            v = hermite(cv["curve"]["m_Curve"], t, cs)
            if prop == "rotation":
                v = v * [1, -1, -1, 1]; v /= np.linalg.norm(v, axis=1, keepdims=True)
            elif prop == "translation":
                v = v * [-1, 1, 1]
            sa.append({"input": ta, "output": acc(v.astype(np.float32)), "interpolation": "LINEAR"})
            ch.append({"sampler": len(sa) - 1, "target": {"node": node, "path": prop}})
    animations.append({"name": name, "channels": ch, "samplers": sa})
    print(f"  clip {name}: {stop:.2f} s, {len(ch)} channels")

bin_ = b"".join(chunks); bin_ += b"\0" * ((-len(bin_)) % 4)
gltf = {"asset": {"version": "2.0", "generator": "build_character.py"}, "scene": 0, "scenes": [{"nodes": roots}],
        "nodes": nodes, "meshes": meshes, "skins": skins, "materials": materials, "textures": textures, "images": images,
        "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}],
        "animations": animations, "accessors": accessors, "bufferViews": views, "buffers": [{"byteLength": len(bin_)}]}
js = json.dumps(gltf, separators=(",", ":")).encode(); js += b" " * ((-len(js)) % 4)
with open(out_path, "wb") as f:
    f.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(bin_)))
    f.write(struct.pack("<II", len(js), 0x4E4F534A)); f.write(js)
    f.write(struct.pack("<II", len(bin_), 0x004E4942)); f.write(bin_)
print(f"wrote {out_path}: {len(nodes)} nodes, {len(meshes)} meshes, {os.path.getsize(out_path) / 1e6:.1f} MB")
