"""Build an animated, skinned GLB of Jesterhead from the Unity bundle (UnityPy)
and AssetRipper's decompressed .anim YAML clips.

usage: build_glb.py <bundle> <anim_dir> <texture_dir> <out.glb>
"""
import io, json, os, re, struct, sys
import numpy as np
import yaml
import UnityPy
from UnityPy.helpers.MeshHelper import MeshHandler

bundle, anim_dir, tex_dir, out_path = sys.argv[1:5]
FPS = 30

env = UnityPy.load(bundle)
root_go = next(o.read() for o in env.objects
               if o.type.name == "GameObject" and o.peek_name() == "Jesterhead_Prefab")


def comps(go):
    return [p.component.read() for p in go.m_Component]


def cname(c):
    return type(c).__name__


# ---------------------------------------------------------------- hierarchy
nodes = []          # gltf nodes
tr_index = {}       # transform path_id -> node index
node_path = {}      # node index -> path relative to prefab root
node_active = {}


def add_node(tr, parent_path, parent_active):
    go = tr.m_GameObject.read()
    idx = len(nodes)
    tr_index[tr.object_reader.path_id] = idx
    p, r, s = tr.m_LocalPosition, tr.m_LocalRotation, tr.m_LocalScale
    nodes.append({
        "name": go.m_Name,
        "translation": [-p.x, p.y, p.z],
        "rotation": [r.x, -r.y, -r.z, r.w],
        "scale": [s.x, s.y, s.z],
    })
    path = go.m_Name if parent_path is None else (parent_path + "/" + go.m_Name if parent_path else go.m_Name)
    node_path[idx] = path
    active = parent_active and bool(go.m_IsActive)
    node_active[idx] = active
    kids = []
    for ch in tr.m_Children:
        kids.append(add_node(ch.read(), "" if parent_path is None else path, active))
    if kids:
        nodes[idx]["children"] = kids
    return idx


root_tr = [c for c in comps(root_go) if cname(c) == "Transform"][0]
add_node(root_tr, None, True)

# ---------------------------------------------------------------- binary buffer
bin_chunks = []
buffer_views, accessors = [], []
offset = 0


def add_view(data: bytes, target=None):
    global offset
    pad = (-offset) % 4
    if pad:
        bin_chunks.append(b"\0" * pad)
        offset += pad
    bv = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
    if target:
        bv["target"] = target
    buffer_views.append(bv)
    bin_chunks.append(data)
    offset += len(data)
    return len(buffer_views) - 1


TYPES = {1: "SCALAR", 2: "VEC2", 3: "VEC3", 4: "VEC4", 16: "MAT4"}
CT = {np.float32: 5126, np.uint16: 5123, np.uint32: 5125, np.uint8: 5121}


def add_acc(arr, target=None, minmax=False):
    arr = np.ascontiguousarray(arr)
    comp = 1 if arr.ndim == 1 else arr.shape[1]
    bv = add_view(arr.tobytes(), target)
    a = {"bufferView": bv, "componentType": CT[arr.dtype.type], "count": int(arr.shape[0]),
         "type": TYPES[comp]}
    if minmax:
        a["min"] = np.atleast_1d(arr.min(axis=0)).tolist()
        a["max"] = np.atleast_1d(arr.max(axis=0)).tolist()
    accessors.append(a)
    return len(accessors) - 1


# ---------------------------------------------------------------- textures/materials
images, textures, materials = [], [], []
img_index = {}


def texture(name):
    if name not in img_index:
        path = os.path.join(tex_dir, name + ".png")
        bv = add_view(open(path, "rb").read())
        images.append({"bufferView": bv, "mimeType": "image/png", "name": name})
        textures.append({"source": len(images) - 1, "sampler": 0})
        img_index[name] = len(textures) - 1
    return img_index[name]


mat_index = {}


def material(mat):
    key = mat.m_Name
    if key in mat_index:
        return mat_index[key]
    main = None
    for k, v in mat.m_SavedProperties.m_TexEnvs:
        if k == "_MainTex" and v.m_Texture.path_id:
            main = v.m_Texture.read().m_Name
    overlay = "faces" in mat.m_Name
    m = {"name": key, "pbrMetallicRoughness": {
        "baseColorTexture": {"index": texture(main)}, "metallicFactor": 0.0, "roughnessFactor": 0.85}}
    if overlay:
        m["alphaMode"] = "BLEND"
        m["extras"] = {"overlay": True}
    else:
        m["alphaMode"] = "OPAQUE"  # alpha channel holds a mask, not transparency
    materials.append(m)
    mat_index[key] = len(materials) - 1
    return mat_index[key]


def tex_st(mat):
    for k, v in mat.m_SavedProperties.m_TexEnvs:
        if k == "_MainTex":
            return v.m_Scale.x, v.m_Scale.y, v.m_Offset.x, v.m_Offset.y
    return 1, 1, 0, 0


# ---------------------------------------------------------------- meshes
meshes, skins = [], []
FLIP = np.diag([-1.0, 1, 1, 1])


def mesh_prims(m, mat, skinned):
    h = MeshHandler(m)
    h.process()
    pos = np.array(h.m_Vertices, np.float32)[:, :3] * [-1, 1, 1]
    nrm = np.array(h.m_Normals, np.float32)[:, :3] * [-1, 1, 1]
    uv = np.array(h.m_UV0, np.float32)[:, :2].copy()
    sx, sy, ox, oy = tex_st(mat)
    uv[:, 0] = uv[:, 0] * sx + ox
    uv[:, 1] = 1.0 - (uv[:, 1] * sy + oy)
    attrs = {"POSITION": add_acc(pos.astype(np.float32), 34962, True),
             "NORMAL": add_acc(nrm.astype(np.float32), 34962),
             "TEXCOORD_0": add_acc(uv.astype(np.float32), 34962)}
    if skinned:
        j = np.array(h.m_BoneIndices, np.uint16)
        w = np.array(h.m_BoneWeights, np.float32)
        # UnityPy mis-decodes the implicit last weight of compressed meshes (-28 instead of 1-sum)
        bad = (w < 0).any(axis=1) | (np.abs(w.sum(axis=1) - 1) > 0.02)
        w[bad, 3] = np.clip(1 - w[bad, :3].sum(axis=1), 0, 1)
        w = w / np.maximum(w.sum(axis=1, keepdims=True), 1e-8)
        attrs["JOINTS_0"] = add_acc(j, 34962)
        attrs["WEIGHTS_0"] = add_acc(w.astype(np.float32), 34962)
    prims = []
    for tri in h.get_triangles():
        idx = np.array(tri, np.uint32).reshape(-1, 3)[:, ::-1].reshape(-1)
        prims.append({"attributes": attrs, "indices": add_acc(idx, 34963),
                      "material": material(mat)})
    return prims


scene_roots = [0]
for idx, path in list(node_path.items()):
    if not node_active[idx]:
        continue
    tr_pid = [k for k, v in tr_index.items() if v == idx][0]
    go = next(o for o in env.objects if o.path_id == tr_pid).read().m_GameObject.read()
    for c in comps(go):
        n = cname(c)
        if n == "SkinnedMeshRenderer" and c.m_Enabled:
            m = c.m_Mesh.read()
            mat = c.m_Materials[0].read()
            joints = [tr_index[b.path_id] for b in c.m_Bones]
            ibm = np.array([[[getattr(bp, f"e{r}{col}") for col in range(4)] for r in range(4)]
                            for bp in m.m_BindPose], np.float64)
            ibm = np.array([(FLIP @ x @ FLIP).T.reshape(-1) for x in ibm], np.float32)  # column-major
            skins.append({"joints": joints, "inverseBindMatrices": add_acc(ibm), "name": go.m_Name})
            meshes.append({"name": m.m_Name, "primitives": mesh_prims(m, mat, True)})
            # glTF: skinned mesh node transform is ignored, so put it at scene root
            nodes.append({"name": go.m_Name + "_skinned", "mesh": len(meshes) - 1,
                          "skin": len(skins) - 1})
            scene_roots.append(len(nodes) - 1)
        elif n == "MeshFilter":
            mr = [x for x in comps(go) if cname(x) == "MeshRenderer"]
            if not mr or not mr[0].m_Enabled:
                continue
            m = c.m_Mesh.read()
            meshes.append({"name": m.m_Name,
                           "primitives": mesh_prims(m, mr[0].m_Materials[0].read(), False)})
            nodes[idx]["mesh"] = len(meshes) - 1

# ---------------------------------------------------------------- animations
by_path = {}
for idx, p in node_path.items():
    by_path[p] = idx
    if p.startswith("jesterhead_mdl/"):
        by_path.setdefault(p[len("jesterhead_mdl/"):], idx)


def load_yaml(path):
    txt = open(path).read()
    txt = re.sub(r"^--- !u!\d+ &\d+.*$", "---", txt, flags=re.M)
    txt = re.sub(r"^%TAG.*$", "", txt, flags=re.M)
    return next(yaml.load_all(txt, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader)))


def hermite_eval(keys, t, comps_):
    """Evaluate a Unity AnimationCurve (non-weighted Hermite) at times t."""
    times = np.array([k["time"] for k in keys])
    vals = np.array([[k["value"][c] for c in comps_] for k in keys], np.float64)

    def slope(k, which):
        out = []
        for c in comps_:
            v = k[which][c]
            out.append(float(v) if not isinstance(v, str) else float("inf"))
        return out
    ins = np.array([slope(k, "inSlope") for k in keys])
    outs = np.array([slope(k, "outSlope") for k in keys])
    res = np.empty((len(t), len(comps_)))
    for i, tt in enumerate(t):
        if tt <= times[0] or len(keys) == 1:
            res[i] = vals[0]; continue
        if tt >= times[-1]:
            res[i] = vals[-1]; continue
        j = np.searchsorted(times, tt) - 1
        dt = times[j + 1] - times[j]
        s = (tt - times[j]) / dt
        m0, m1 = outs[j] * dt, ins[j + 1] * dt
        h00 = 2 * s**3 - 3 * s**2 + 1; h10 = s**3 - 2 * s**2 + s
        h01 = -2 * s**3 + 3 * s**2; h11 = s**3 - s**2
        r = h00 * vals[j] + h10 * m0 + h01 * vals[j + 1] + h11 * m1
        stepped = ~np.isfinite(m0) | ~np.isfinite(m1)
        r[stepped] = vals[j][stepped]
        res[i] = r
    return res


animations = []
missing = set()
for fn in sorted(os.listdir(anim_dir)):
    if not fn.lower().startswith("jesterhead") or not fn.endswith(".anim"):
        continue
    clip = load_yaml(os.path.join(anim_dir, fn))["AnimationClip"]
    stop = float(clip["m_AnimationClipSettings"]["m_StopTime"])
    t = np.arange(0, stop + 1e-6, 1.0 / FPS)
    if t[-1] < stop - 1e-4:
        t = np.append(t, stop)
    t_acc = add_acc(t.astype(np.float32), minmax=True)
    channels, samplers = [], []
    for key, prop, comps_ in (("m_RotationCurves", "rotation", "xyzw"),
                              ("m_PositionCurves", "translation", "xyz"),
                              ("m_ScaleCurves", "scale", "xyz")):
        for cv in clip[key] or []:
            node = by_path.get(cv["path"])
            if node is None:
                missing.add(cv["path"]); continue
            v = hermite_eval(cv["curve"]["m_Curve"], t, comps_)
            if prop == "rotation":
                v = v * [1, -1, -1, 1]
                v /= np.linalg.norm(v, axis=1, keepdims=True)
            elif prop == "translation":
                v = v * [-1, 1, 1]
            samplers.append({"input": t_acc, "output": add_acc(v.astype(np.float32)),
                             "interpolation": "LINEAR"})
            channels.append({"sampler": len(samplers) - 1, "target": {"node": node, "path": prop}})
    name = clip["m_Name"].replace("_anm", "").replace("jesterhead_", "").replace("Jesterhead_", "")
    animations.append({"name": name, "channels": channels, "samplers": samplers,
                       "extras": {"loop": bool(clip["m_AnimationClipSettings"]["m_LoopTime"])}})
    print(f"anim {name}: {stop:.2f}s, {len(channels)} channels")
if missing:
    print("unresolved paths:", len(missing), sorted(missing)[:5])

# ---------------------------------------------------------------- write GLB
gltf = {
    "asset": {"version": "2.0", "generator": "jesterhead build_glb.py"},
    "scene": 0, "scenes": [{"nodes": scene_roots}],
    "nodes": nodes, "meshes": meshes, "skins": skins, "materials": materials,
    "textures": textures, "images": images,
    "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}],
    "animations": animations, "accessors": accessors, "bufferViews": buffer_views,
    "buffers": [{"byteLength": offset}],
}
js = json.dumps(gltf, separators=(",", ":")).encode()
js += b" " * ((-len(js)) % 4)
bin_ = b"".join(bin_chunks)
bin_ += b"\0" * ((-len(bin_)) % 4)
gltf["buffers"][0]["byteLength"] = len(bin_)
js = json.dumps(gltf, separators=(",", ":")).encode()
js += b" " * ((-len(js)) % 4)
with open(out_path, "wb") as f:
    f.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(bin_)))
    f.write(struct.pack("<II", len(js), 0x4E4F534A)); f.write(js)
    f.write(struct.pack("<II", len(bin_), 0x004E4942)); f.write(bin_)
print(f"wrote {out_path}: {len(nodes)} nodes, {len(meshes)} meshes, {len(skins)} skins, "
      f"{len(animations)} animations, {os.path.getsize(out_path)/1e6:.1f} MB")
