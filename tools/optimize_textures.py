"""Shrink the loose textures the viewer loads (particles and arenas) and update the JSON files that reference them.

* PNG files larger than --min-kb become WebP (lossy colour at --quality, lossless alpha, colour kept under transparent
  pixels for additive blending) when that saves at least 20 %. Small files, such as the light and fog ramps whose
  exact values matter, stay PNG.
* Byte-identical files referenced from several JSON files are kept once.

Nothing is lost: the replaced or merged files and the JSON files as exported are moved to <sources>/assets/...
(same relative paths) before anything is rewritten.

usage: optimize_textures.py <site dir> [--sources <dir>] [--quality 90] [--min-kb 16]
       (rewrites site/assets/vfx.json and site/assets/arena/arena.json and the files they list;
        <sources> defaults to the sources/ folder next to the site)
"""
import argparse, hashlib, json, os, shutil
from PIL import Image

ap = argparse.ArgumentParser()
ap.add_argument("site")
ap.add_argument("--sources")
ap.add_argument("--quality", type=int, default=90)
ap.add_argument("--min-kb", type=int, default=16)
args = ap.parse_args()
sources = args.sources or os.path.join(os.path.dirname(os.path.abspath(args.site)), "sources")


def keep(path, move=True):
    """Store the original under sources/ (first time only)."""
    dst = os.path.join(sources, os.path.relpath(path, args.site))
    if not os.path.exists(dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        (shutil.move if move else shutil.copy2)(path, dst)
    elif move:
        os.remove(path)

# JSON file -> directory its "src" paths are relative to
SOURCES = {"assets/vfx.json": "assets", "assets/arena/arena.json": "assets/arena"}
seen, saved, removed = {}, 0, []
for rel, base in SOURCES.items():
    path = os.path.join(args.site, rel)
    keep(path, move=False)
    doc = json.load(open(path))
    for t in doc["textures"]:
        src = os.path.normpath(os.path.join(args.site, base, t["src"]))
        if not os.path.exists(src):                   # already handled (earlier entry or earlier run)
            kept = os.path.join(sources, os.path.relpath(src, args.site))
            if os.path.exists(src[:-4] + ".webp"):
                src = src[:-4] + ".webp"
            elif os.path.exists(kept) and hashlib.sha1(open(kept, "rb").read()).hexdigest() in seen:
                src = seen[hashlib.sha1(open(kept, "rb").read()).hexdigest()]
            else:
                print("missing:", t["src"])
                continue
        if src.endswith(".png") and os.path.getsize(src) > args.min_kb * 1024:
            out = src[:-4] + ".webp"
            img = Image.open(src)
            if img.mode in ("RGBA", "LA", "P"):
                img.convert("RGBA").save(out, "WEBP", quality=args.quality, alpha_quality=100, method=6, exact=True)
            else:
                img.convert("RGB").save(out, "WEBP", quality=args.quality, method=6)
            if os.path.getsize(out) < 0.8 * os.path.getsize(src):
                saved += os.path.getsize(src) - os.path.getsize(out)
                keep(src); removed.append(src); src = out
            else:
                os.remove(out)
        digest = hashlib.sha1(open(src, "rb").read()).hexdigest()
        if digest in seen and seen[digest] != src:            # same file already shipped elsewhere
            saved += os.path.getsize(src)
            keep(src); removed.append(src); src = seen[digest]
        seen.setdefault(digest, src)
        t["src"] = os.path.relpath(src, os.path.join(args.site, base))
    json.dump(doc, open(path, "w"), separators=(",", ":"))
print(f"{len(removed)} files replaced or merged, {saved / 1e6:.2f} MB saved")
