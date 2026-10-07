"""Export the enemies of the five In The Dark (Normal) battles: one skinned GLB per character with its idle clip,
its battle portrait, and site/assets/enemies/lineups.json (who stands in which enemy slot of which battle).

The line-ups themselves were sent by the game servers and are not in the cached data. They were read from in-game
footage of the Normal run (the gameplay video linked on the site):
  * wave V from the team-selection screen, which shows its portraits (0:12);
  * every wave from the battle itself: the colour of each level badge gives the class, the model and the order on
    screen give the character and its slot (battle 1 at 0:57, 2 at 1:14, 3 at 2:15, 4 at 2:33, 5 at 2:53).
The characters are then matched to the game data: Characters.csv (classType, spriteName = battle portrait,
characterScriptableObject) and the scriptable object's modelPrefab.

usage: export_enemies.py <out_dir> <ABMv6 dir> <AssetRipper export dir> [<AssetRipper export dir>...]
       (the AssetRipper exports of the bundles that hold the idle clips; see README)
"""
import csv, glob, io, json, os, subprocess, sys
import UnityPy

out_dir, abm, *ripped = sys.argv[1:]
os.makedirs(os.path.join(out_dir, "icons"), exist_ok=True)
B = lambda h: os.path.join(abm, h, "b")
TOOLS = os.path.dirname(os.path.abspath(__file__))
CSV, LOC, PORTRAITS = "e9413e152ef8e76cd6c451e6749777ac", "9738e6a4da3de776a1a902ceb7a7a8c0", "fee724bd897a1f7edf5670a2a14be275"

# enemy slots of DungeonSlotsContainer/Team2, listed left to right as seen from the battle camera
LINEUPS = {
    1: ["cso_hellraiser_sentinel", "cso_roller_demon_magus", "cso_hellraiser_warrior", "cso_roller_demon_assassin",
        "cso_hellraiser_gunner"],
    2: ["cso_wrathchild_assassin", None, "cso_wrathchild_warrior", None, "cso_wrathchild_mage"],
    3: ["CSO_ChildOfTheDamned_A_Assassin", "CSO_Winged_Demon_B_Tank", "cso_demon_prince_astaroth",
        "CSO_Winged_Demon_A_Fighter", "CSO_ChildOfTheDamned_B_Gunner"],
    4: ["cso_roller_demon_warrior", None, "cso_belshazzar_empowered", None, "cso_roller_demon_sentinel"],
    5: ["CSO_DemonSkeleton_C", "CSO_DemonSkeleton_A_01", "cso_the_ferryman", "CSO_DemonSkeleton_B", "CSO_DemonSkeleton_C"],
}
SLOTS = ["Slot01", "Slot02", "Slot03", "Slot04", "Slot05"]
LEVELS = {3: 60}                                  # the middle enemy is level 60, the others 55 (Normal difficulty)
BOSS = {5: "cso_the_ferryman"}

# bundle of each scriptable object in the cache used here, and the bundles it depends on
SHARED = ["67e008b053c12dceaf08614584cb2083", "6e64738d2d44797c4bbe88a956d38cb7", "ff271c3b56407274c0be2448e472d60b"]
BUNDLES = {
    "cso_hellraiser_sentinel": "a1994610357bc95fc303f2f391d2ba34", "cso_hellraiser_warrior": "a1994610357bc95fc303f2f391d2ba34",
    "cso_hellraiser_gunner": "a1994610357bc95fc303f2f391d2ba34", "cso_wrathchild_assassin": "a1994610357bc95fc303f2f391d2ba34",
    "cso_wrathchild_warrior": "a1994610357bc95fc303f2f391d2ba34", "cso_wrathchild_mage": "e64d58ec7d21ac1d85e348617a068e8f",
    "cso_roller_demon_magus": "317b072b9dd5af012e374795d46d11b0", "cso_roller_demon_assassin": "317b072b9dd5af012e374795d46d11b0",
    "cso_roller_demon_warrior": "317b072b9dd5af012e374795d46d11b0", "cso_roller_demon_sentinel": "317b072b9dd5af012e374795d46d11b0",
    "cso_childofthedamned_a_assassin": "48bac9bd5e27fbdf06178c9267f08288",
    "cso_childofthedamned_b_gunner": "48bac9bd5e27fbdf06178c9267f08288", "cso_winged_demon_b_tank": "ca77f60834d56cae35c7b894dfe07304",
    "cso_winged_demon_a_fighter": "ca77f60834d56cae35c7b894dfe07304", "cso_demon_prince_astaroth": "07db5788df58f1f295c31289ba4e6394",
    "cso_belshazzar_empowered": "07db5788df58f1f295c31289ba4e6394",
    "cso_demonskeleton_a_01": "9b6ead6e6b8d44696046aa3c84f4eb5b", "cso_demonskeleton_b": "9b6ead6e6b8d44696046aa3c84f4eb5b",
    "cso_demonskeleton_c": "9b6ead6e6b8d44696046aa3c84f4eb5b", "cso_the_ferryman": "c9da9103afe95c295a8c99aba9a78e20",
}
EXTRA_DEPS = {"07db5788df58f1f295c31289ba4e6394": ["bde1011281fe33b3ca7c6a7bae9ff62d"]}


def text_assets(bundle):
    out = {}
    for o in UnityPy.load(B(bundle)).objects:
        if o.type.name == "TextAsset":
            t = o.read()
            data = t.m_Script if isinstance(t.m_Script, (bytes, bytearray)) else t.m_Script.encode("utf-8", "surrogateescape")
            out[t.m_Name] = bytes(data)
    return out


def read7(b, i):
    n = s = 0
    while True:
        c = b[i]; i += 1; n |= (c & 0x7F) << s; s += 7
        if c < 0x80:
            return n, i


def loc_table(files, lang="en"):                  # same format as in export_event.py
    kb, vb = files["localization_index"], files[f"localization_{lang}"]
    keys, i = [], 8
    while i < len(kb):
        n, i = read7(kb, i); keys.append(kb[i:i + n].decode("utf-8", "replace")); i += n
    vals, i = [], 4
    while i + 2 <= len(vb):
        n = vb[i] | vb[i + 1] << 8; i += 2; vals.append(vb[i:i + n].decode("utf-8", "replace")); i += n
    return dict(zip(keys, vals))


L = loc_table(text_assets(LOC))
chars = list(csv.DictReader(io.StringIO(text_assets(CSV)["Characters"].decode("utf-8-sig"))))


def char_row(cso):
    """The plain three-star row (the Normal run's enemies have three stars), not an NPC or boss copy."""
    rs = [r for r in chars if r["characterScriptableObject"].lower() == cso.lower() and r["starLevel"] == "3"]
    return next((r for r in rs if "(" not in r["name"].split(" III")[-1] and "NPC" not in r["name"]), rs[0])


def nice(s):
    return " ".join(w if w in ("of", "the") and i else w.capitalize() for i, w in enumerate(s.lower().split()))


def name_of(o):
    try:
        return o.read().m_Name
    except Exception:
        return None


def idle_clip(go):
    """Clip played by the 'idle' state of the prefab's animator, after the override controller's substitutions."""
    def find(tr):
        g = tr.m_GameObject.read()
        for c in g.m_Component:
            if c.component.type.name == "Animator":
                return c.component.read()
        for ch in tr.m_Children:
            a = find(ch.read())
            if a:
                return a
    anim = find(next(c.component.read() for c in go.m_Component if c.component.type.name == "Transform"))
    ctrl = anim.m_Controller.read()
    base, override = ctrl, {}
    if type(ctrl).__name__ == "AnimatorOverrideController":
        base = ctrl.m_Controller.read()
        override = {name_of(p.m_OriginalClip): name_of(p.m_OverrideClip) for p in ctrl.m_Clips}
    clips = [name_of(c) for c in base.m_AnimationClips]
    tos = dict(base.m_TOS)
    for sm in base.m_Controller.m_StateMachineArray:
        for st in sm.data.m_StateConstantArray:
            st = st.data
            if tos.get(st.m_NameID, "").lower() == "idle":           # first leaf of the state's blend tree
                cid = next(n.data.m_ClipID for bt in st.m_BlendTreeConstantArray for n in bt.data.m_NodeArray
                           if n.data.m_ClipID < len(clips))
                return override.get(clips[cid]) or clips[cid]


portraits = {}
for o in UnityPy.load(B(PORTRAITS)).objects:
    if o.type.name == "Texture2D":
        portraits[o.peek_name()] = o

characters, done = {}, set()
for cso in sorted({c.lower() for line in LINEUPS.values() for c in line if c}):
    cid = cso.replace("cso_", "")
    bundle = BUNDLES[cso]
    env = UnityPy.load(B(bundle))
    so = next(o for o in env.objects if o.type.name == "MonoBehaviour" and (o.peek_name() or "").lower() == cso)
    pptr = so.read_typetree()["modelPrefab"]
    prefab = so.assets_file.objects[pptr["m_PathID"]].read()
    deps = [B(d) for d in SHARED + EXTRA_DEPS.get(bundle, [])]
    full = UnityPy.load(B(bundle), *deps)             # the animator's base controller lives in a shared bundle
    clip = idle_clip(next(o.read() for o in full.objects
                          if o.type.name == "GameObject" and o.peek_name() == prefab.m_Name))
    anim = next((f for d in ripped for f in glob.glob(f"{d}/**/{clip}.anim", recursive=True)), None)
    if not anim:
        sys.exit(f"{clip}.anim (idle of {cso}) not found in the AssetRipper exports")
    print(f"== {cid}: {prefab.m_Name}, idle {clip}")
    subprocess.run([sys.executable, os.path.join(TOOLS, "build_character.py"), os.path.join(out_dir, cid + ".glb"),
                    prefab.m_Name, os.path.dirname(anim), clip, B(bundle), *deps], check=True)
    row = char_row(cso)
    icon = portraits[row["spriteName"]].read().image.convert("RGB")
    icon.thumbnail((128, 128)); icon.save(os.path.join(out_dir, "icons", cid + ".jpg"), quality=88)
    characters[cid] = {"name": nice(L[row["locName"]]), "class": L[row["classType"] + "_Lowercase"],
                       "prefab": prefab.m_Name, "idle": clip, "file": cid + ".glb", "icon": f"icons/{cid}.jpg"}

lineups = {"source": "observed in gameplay footage of the Normal run (the line-ups were served by the game servers)",
           "battles": [{"battle": n, "enemies": [
               {"slot": SLOTS[i], "id": c.lower().replace("cso_", ""), "level": LEVELS.get(i + 1, 55),
                **({"boss": True} if BOSS.get(n) == c else {})}
               for i, c in enumerate(line) if c]} for n, line in LINEUPS.items()],
           "characters": characters}
json.dump(lineups, open(os.path.join(out_dir, "lineups.json"), "w"), indent=1)
print(f"wrote {out_dir}/lineups.json: {len(characters)} characters")
