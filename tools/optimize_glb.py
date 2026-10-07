"""Make a GLB lighter for the web without changing what it shows.

* Animation curves (LINEAR samplers): a keyframe is dropped when every removed key between its neighbours stays within
  --tol of the straight line (quaternions are compared after normalisation). The first and last keys are always kept,
  so clip durations do not change. The exporters resample every curve at 30 fps, so most keys are redundant.
* Embedded images: re-encoded as WebP (EXT_texture_webp, lossy colour at --quality, lossless alpha, which these models
  use as an emission or colour mask). The light and rim ramps (material extras rampTex / rimTex), which the shaders
  read as lookup tables, are lossless. The original PNGs stay in the asset library.

Nodes, materials, extras and accessor order are kept: the viewer relies on node indices and material extras.

usage: optimize_glb.py <file.glb> [<file.glb>...] --out-dir <dir> [--tol 1e-4] [--quality 90] [--no-webp]
       reads the full-quality exports (kept in sources/assets/...) and writes the web copies to <dir>, e.g.
       optimize_glb.py sources/assets/enemies/*.glb --out-dir site/assets/enemies
"""
import os
import argparse, io, json, struct
import numpy as np
from PIL import Image

ap = argparse.ArgumentParser()
ap.add_argument("files", nargs="+")
ap.add_argument("--out-dir", required=True)
ap.add_argument("--tol", type=float, default=1e-4)
ap.add_argument("--quality", type=int, default=90)
ap.add_argument("--no-webp", action="store_true")
args = ap.parse_args()

DT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}


def read_glb(path):
    b = open(path, "rb").read()
    n = struct.unpack("<I", b[12:16])[0]
    g = json.loads(b[20:20 + n])
    m = struct.unpack("<I", b[20 + n:24 + n])[0]
    return g, b[28 + n:28 + n + m]


def accessor_data(g, bin_, i):
    a = g["accessors"][i]
    v = g["bufferViews"][a["bufferView"]]
    dt, nc = np.dtype(DT[a["componentType"]]), NC[a["type"]]
    start = v.get("byteOffset", 0) + a.get("byteOffset", 0)
    stride = v.get("byteStride", dt.itemsize * nc)
    raw = np.frombuffer(bin_, np.uint8, count=stride * (a["count"] - 1) + dt.itemsize * nc, offset=start)
    rows = np.lib.stride_tricks.as_strided(raw, (a["count"], dt.itemsize * nc), (stride, 1))
    return rows.copy().view(dt).reshape(a["count"], nc)


def reduce_keys(t, v, quat, tol):
    """Indices of the keys to keep (greedy, every dropped key checked against the final segment)."""
    n = len(t)
    if n <= 2:
        return list(range(n))
    keep, last = [0], 0
    for i in range(1, n - 1):
        j = i + 1                                      # can last..j be one segment?
        s = (t[last + 1:j] - t[last]) / (t[j] - t[last])
        lerp = v[last] + (v[j] - v[last]) * s[:, None]
        if quat:
            lerp /= np.linalg.norm(lerp, axis=1, keepdims=True)
        if np.abs(lerp - v[last + 1:j]).max() > tol:
            keep.append(i); last = i
    keep.append(n - 1)
    return keep


def optimize(path, out_path):
    g, bin_ = read_glb(path)
    before = len(bin_)
    data = {i: accessor_data(g, bin_, i) for i, a in enumerate(g["accessors"]) if "bufferView" in a}
    meta = {i: {k: a[k] for k in ("componentType", "type", "normalized") if k in a} for i, a in enumerate(g["accessors"])}
    targets = {}
    for m in g.get("meshes", []):
        for p in m["primitives"]:
            for i in p["attributes"].values():
                targets[i] = 34962
            if "indices" in p:
                targets[p["indices"]] = 34963

    # ---- animations: reduce the keys; drop the channels that hold the node's rest value for the whole clip
    # (three.js restores a property's original value once no running action animates it)
    keys_before = keys_after = dropped = 0
    rest = {"rotation": lambda n: n.get("rotation", [0, 0, 0, 1]), "translation": lambda n: n.get("translation", [0, 0, 0]),
            "scale": lambda n: n.get("scale", [1, 1, 1])}
    inputs, anim_acc = {}, set()                      # shared time arrays

    def new_accessor(arr, type_):
        idx = len(g["accessors"])
        g["accessors"].append({"componentType": 5126, "type": type_, "count": len(arr)})
        data[idx] = arr; meta[idx] = {"componentType": 5126, "type": type_}
        anim_acc.add(idx)
        return idx

    for anim in g.get("animations", []):
        channels, samplers = [], []
        for c in anim["channels"]:
            smp = anim["samplers"][c["sampler"]]
            prop, node = c["target"]["path"], g["nodes"][c["target"]["node"]]
            t, v = data[smp["input"]][:, 0].astype(np.float64), data[smp["output"]].astype(np.float64)
            keys_before += len(t)
            if prop in rest and smp.get("interpolation", "LINEAR") == "LINEAR":
                r = np.array(rest[prop](node), np.float64)
                off = np.abs(v - r).max(axis=1)
                if prop == "rotation":
                    off = np.minimum(off, np.abs(v + r).max(axis=1))
                if off.max() <= args.tol:
                    dropped += 1
                    continue
                k = reduce_keys(t, v, prop == "rotation", args.tol)
                t, v = t[k], v[k]
            keys_after += len(t)
            tt = t.astype(np.float32)[:, None]
            h = tt.tobytes()
            if h not in inputs:
                inputs[h] = new_accessor(tt, "SCALAR")
            out = new_accessor(v.astype(np.float32), g["accessors"][smp["output"]]["type"])
            samplers.append({"input": inputs[h], "output": out, "interpolation": smp.get("interpolation", "LINEAR")})
            channels.append({"sampler": len(samplers) - 1, "target": c["target"]})
        anim["channels"], anim["samplers"] = channels, samplers

    # ---- drop unreferenced accessors, keeping the order of the others
    used = set(targets)
    for m in g.get("meshes", []):
        for p in m["primitives"]:
            for tg in p.get("targets", []):
                used |= set(tg.values())
    for s in g.get("skins", []):
        if "inverseBindMatrices" in s:
            used.add(s["inverseBindMatrices"])
    for anim in g.get("animations", []):
        for smp in anim["samplers"]:
            used |= {smp["input"], smp["output"]}
    order = sorted(used)
    remap = {old: new for new, old in enumerate(order)}
    for m in g.get("meshes", []):
        for p in m["primitives"]:
            p["attributes"] = {k: remap[i] for k, i in p["attributes"].items()}
            if "indices" in p:
                p["indices"] = remap[p["indices"]]
            for tg in p.get("targets", []):
                for k in tg:
                    tg[k] = remap[tg[k]]
    for s in g.get("skins", []):
        if "inverseBindMatrices" in s:
            s["inverseBindMatrices"] = remap[s["inverseBindMatrices"]]
    for anim in g.get("animations", []):
        for smp in anim["samplers"]:
            smp["input"], smp["output"] = remap[smp["input"]], remap[smp["output"]]

    # ---- images
    exact = {g["textures"][t]["source"] for m in g.get("materials", []) for k in ("rampTex", "rimTex")
             if (t := m.get("extras", {}).get(k)) is not None}
    images = []
    for i, im in enumerate(g.get("images", [])):
        v = g["bufferViews"][im["bufferView"]]
        raw = bin_[v.get("byteOffset", 0):v.get("byteOffset", 0) + v["byteLength"]]
        if not args.no_webp and im.get("mimeType") == "image/png":
            pic = Image.open(io.BytesIO(raw))
            out = io.BytesIO()
            if i in exact:
                pic.save(out, "WEBP", lossless=True, exact=True, method=6)
            elif pic.mode in ("RGBA", "LA", "P"):
                pic.convert("RGBA").save(out, "WEBP", quality=args.quality, alpha_quality=100, method=6, exact=True)
            else:
                pic.convert("RGB").save(out, "WEBP", quality=args.quality, method=6)
            if len(out.getvalue()) < len(raw):
                raw = out.getvalue(); im["mimeType"] = "image/webp"
        images.append(raw)
    if any(im.get("mimeType") == "image/webp" for im in g.get("images", [])):
        for tex in g.get("textures", []):
            if "source" in tex and g["images"][tex["source"]]["mimeType"] == "image/webp":
                tex.setdefault("extensions", {})["EXT_texture_webp"] = {"source": tex.pop("source")}
        for key in ("extensionsUsed", "extensionsRequired"):
            g[key] = sorted(set(g.get(key, [])) | {"EXT_texture_webp"})

    # ---- rewrite the buffer
    chunks, views, offset = [], [], 0

    def add_view(blob, target=None, align=4):
        nonlocal offset
        pad = (-offset) % align
        if pad:
            chunks.append(b"\0" * pad); offset += pad
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(blob)}
        if target:
            view["target"] = target
        views.append(view); chunks.append(blob); offset += len(blob)
        return len(views) - 1

    accessors, anim_blob = [], []
    anim_view = None
    for old in order:
        arr = np.ascontiguousarray(data[old].astype(DT[meta[old]["componentType"]]))
        if old in anim_acc:                            # all animation data in one buffer view
            if anim_view is None:
                anim_view = len(views); views.append(None); anim_size = 0
            a = dict(meta[old], count=int(arr.shape[0]), bufferView=anim_view, byteOffset=anim_size)
            anim_blob.append(arr.tobytes()); anim_size += len(anim_blob[-1])
            accessors.append(a)
            continue
        a = dict(meta[old], count=int(arr.shape[0]), bufferView=add_view(arr.tobytes(), targets.get(old)))
        if old in targets and targets[old] == 34962 and g["accessors"][old].get("min") is not None:
            a["min"], a["max"] = g["accessors"][old]["min"], g["accessors"][old]["max"]
        accessors.append(a)
    for anim in g.get("animations", []):                 # glTF requires min/max on sampler inputs
        for smp in anim["samplers"]:
            t = data[order[smp["input"]]][:, 0]
            accessors[smp["input"]]["min"], accessors[smp["input"]]["max"] = [float(t.min())], [float(t.max())]
    if anim_view is not None:                          # placed after the others, then the placeholder is filled
        n = len(views)
        add_view(b"".join(anim_blob))
        views[anim_view] = views.pop(n)
    for im, raw in zip(g.get("images", []), images):
        im["bufferView"] = add_view(raw)
    g["accessors"], g["bufferViews"] = accessors, views
    blob = b"".join(chunks); blob += b"\0" * ((-len(blob)) % 4)
    g["buffers"] = [{"byteLength": len(blob)}]
    js = json.dumps(g, separators=(",", ":")).encode(); js += b" " * ((-len(js)) % 4)
    with open(out_path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(blob)))
        f.write(struct.pack("<II", len(js), 0x4E4F534A)); f.write(js)
        f.write(struct.pack("<II", len(blob), 0x004E4942)); f.write(blob)
    print(f"{out_path}: binary {before / 1e6:.2f} -> {len(blob) / 1e6:.2f} MB, file {(28 + len(js) + len(blob)) / 1e6:.2f} MB"
          + (f", keys {keys_before} -> {keys_after}, {dropped} channels at rest dropped" if keys_before else ""))


os.makedirs(args.out_dir, exist_ok=True)
for f in args.files:
    dst = os.path.join(args.out_dir, os.path.basename(f))
    if os.path.abspath(dst) == os.path.abspath(f):
        raise SystemExit(f"{f}: refusing to overwrite the source")
    optimize(f, dst)
