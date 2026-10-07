"""Collect Jesterhead's game data and the "In The Dark" event text from the game's CSV and localisation bundles.

Sources: CSV_AssetBundle (Characters, CharacterAbilityLevels, Effects, Quests, QuestBattles, BattleDialogue,
DynamicCurrencies, Worlds) and Loc_AssetBundle (binary string tables, English). Images: quest cards
(Quests_AssetBundle), ability icons (Abilities_AssetBundle), the event currency (UIGraphics_AssetBundle) and the
portraits of the dialogue speakers (Characters_AssetBundle, img/portraits/).

usage: export_event.py <out_dir> <ABMv6 dir>      writes <out_dir>/data.json and <out_dir>/img/*.png
"""
import csv, io, json, os, re, sys
import UnityPy

out_dir, abm = sys.argv[1:]
os.makedirs(os.path.join(out_dir, "img"), exist_ok=True)
B = lambda h: os.path.join(abm, h, "b")
CSV, LOC = "e9413e152ef8e76cd6c451e6749777ac", "9738e6a4da3de776a1a902ceb7a7a8c0"
IMAGES = {"b29c7c7ebfde2326d80ac171cf354739", "f27474a2e807cd0d5cdcf5601bfd8ab0", "b8bb00808ef7ad857a679d0a50978cab"}
PORTRAITS = "fee724bd897a1f7edf5670a2a14be275"       # Characters_AssetBundle


def text_assets(bundle):
    out = {}
    for o in UnityPy.load(B(bundle)).objects:
        if o.type.name == "TextAsset":
            t = o.read()
            data = t.m_Script if isinstance(t.m_Script, (bytes, bytearray)) else t.m_Script.encode("utf-8", "surrogateescape")
            out[t.m_Name] = bytes(data)
    return out


tables = text_assets(CSV)
def rows(name):
    return list(csv.DictReader(io.StringIO(tables[name].decode("utf-8-sig"))))


def raw_rows(name):
    """Tables without a header row: id, type, JSON definition."""
    return {r[0]: r for r in csv.reader(io.StringIO(tables[name].decode("utf-8-sig"))) if r}


# Localisation: localization_index = [int32 count][7-bit-length strings]; localization_<lang> = 4-byte header,
# then uint16-length strings in the same order.
loc_files = text_assets(LOC)
def read7(b, i):
    n = s = 0
    while True:
        c = b[i]; i += 1; n |= (c & 0x7F) << s; s += 7
        if c < 0x80:
            return n, i
def loc_table(lang="en"):
    kb, vb = loc_files["localization_index"], loc_files[f"localization_{lang}"]
    keys, i = [], 8
    while i < len(kb):
        n, i = read7(kb, i); keys.append(kb[i:i + n].decode("utf-8", "replace")); i += n
    vals, i = [], 4
    while i + 2 <= len(vb):
        n = vb[i] | vb[i + 1] << 8; i += 2; vals.append(vb[i:i + n].decode("utf-8", "replace")); i += n
    return dict(zip(keys, vals))
L = loc_table()


def jfield(s):
    """The CSVs embed JSON with doubled quotes and trailing commas."""
    s = s.replace('""', '"').replace('\\"', '"')
    s = re.sub(r",\s*([}\]])", r"\1", s)
    return json.loads(s)


# ------------------------------------------------------------------ character
chars = {r["id"]: r for r in rows("Characters")}
jh = [r for r in chars.values() if r["characterScriptableObject"] == "cso_jesterhead"]
jh.sort(key=lambda r: int(r["starLevel"]))
CLASS = {"Tank": L.get("Tank_Lowercase", "Sentinel")}
character = {
    "name": L[jh[0]["locName"]].title(), "role": L[jh[0]["locDescription"]],
    "class": CLASS.get(jh[0]["classType"], jh[0]["classType"]), "ids": [int(r["id"]) for r in jh],
    "ranks": [{"rank": r["name"].split()[-1], "stars": int(r["starLevel"]), "partyCost": int(r["partyCost"]),
               **{k: int(r[k]) for k in ("hp", "atk", "def", "ap", "mr", "sp")},
               "growth": {k: int(r[k + "Growth"]) for k in ("hp", "atk", "def", "ap", "mr", "sp")}} for r in jh],
}
first = jh[0]

levels = {k: jfield(r[2]) for k, r in raw_rows("CharacterAbilityLevels").items()}
effects = raw_rows("Effects")
actions = raw_rows("Actions")


def ability(aid, kind):
    lv = levels.get(aid, {})
    if kind == "Passive":
        e = jfield(effects[aid][2])
        key = e.get("locName", "").replace("_Name", "")
        icon = "ABicon_732P_" + key.split("_")[-1]
    else:
        key = "AB_" + lv["icon"].split("_", 2)[-1]
        icon = lv["icon"]
        e = jfield(actions[aid][2])
    return {"kind": kind, "id": int(aid), "name": L.get(key + "_Name", lv.get("name", "")).title(),
            "description": L.get(key + "_Desc", ""), "icon": icon,
            "energy": e.get("energyCost"), "maxLevel": lv.get("maxLevel")}


abilities = [ability(first["basicAbilityPresent"], "Basic"), ability(first["energyAbilityPresent"], "Energy"),
             ability(first["passiveEffect"], "Passive")]

# ------------------------------------------------------------------ event
quests = [r for r in rows("Quests") if r["name"].startswith("IN_THE_DARK_DUNGEON")]
qb = rows("QuestBattles")
dialogue_rows = rows("BattleDialogue")
battle_names = {r["id"]: r["name"] for r in rows("Battles")}
world = next(r for r in rows("Worlds") if r["name"] == "IN_THE_DARK_DUNGEON_MAP_01")
currency = next(r for r in rows("DynamicCurrencies") if "ENCHANTED_SIGILS" in r[list(r)[4]])
cur_vals = list(currency.values())


def speaker(cid):
    r = chars.get(str(cid))
    return L.get(r["locName"], r["name"]).title() if r else None


def portrait(cid):                                    # battle HUD portrait shown in the dialogue box
    r = chars.get(str(cid))
    return r["spriteName"] if r else None


event = {
    "name": L["EVENT_NAME_IN_THE_DARK"].title(), "dungeon": L["IN_THE_DARK_DUNGEON_NAME"].title(),
    "mapModel": world["modelAsset"], "mapMusic": world["worldMusic"],
    "currency": {"name": L[cur_vals[4]], "description": L[cur_vals[5]], "icon": cur_vals[3], "internal": cur_vals[2]},
    "calendar": {"title": re.sub(r"\[/?i\]", "", L["ER_JESTERHEAD_TITLE"]), "description": L["ER_JESTERHEAD_DESC"]},
    "quests": [], "dialogues": [],
}
for q in quests:
    battles = [b for b in qb if b["questId"] == q["id"]]
    event["quests"].append({
        "id": int(q["id"]), "name": L[q["name"]], "line": L.get(q["locDescription"], ""),
        "card": q["questPanelSprite"], "staminaCost": int(q["playBattleCost"]), "camera": q["cameraClassOverride"],
        "battles": [{"id": int(b["battleId"]), "name": battle_names.get(b["battleId"]), "arena": b["battleArena"],
                     "music": b["battleMusic"], "boss": b["hasBoss"] == "TRUE"} for b in battles],
    })
order = {b["id"]: i for q in event["quests"] for i, b in enumerate(q["battles"])}
for r in dialogue_rows:
    if int(r["battleId"]) not in order:
        continue
    lines = jfield(r["encodedBattleDialogue"])
    event["dialogues"].append({
        "battle": int(r["battleId"]), "battleNumber": order[int(r["battleId"])] + 1, "trigger": r["triggerType"],
        "lines": [{"speaker": speaker(l.get("characterId")), "portrait": portrait(l.get("characterId")),
                   "side": l.get("windowPosition"), "text": L.get(l["locText"], l["locText"])} for l in lines],
    })

data = {"character": character, "abilities": abilities, "event": event,
        "sources": ["Characters.csv", "CharacterAbilityLevels.csv", "Actions.csv", "Effects.csv", "Quests.csv",
                    "QuestBattles.csv", "BattleDialogue.csv", "Worlds.csv", "DynamicCurrencies.csv", "localization_en"]}

# ------------------------------------------------------------------ images
wanted = {q["card"] for q in event["quests"]} | {a["icon"] for a in abilities} | {event["currency"]["icon"]}
for h in IMAGES:
    for o in UnityPy.load(B(h)).objects:
        if o.type.name == "Texture2D" and o.peek_name() in wanted:
            t = o.read()
            t.image.save(os.path.join(out_dir, "img", t.m_Name + ".png"), optimize=True)
            wanted.discard(t.m_Name)
portraits = {l["portrait"] for d in event["dialogues"] for l in d["lines"] if l["portrait"]}
os.makedirs(os.path.join(out_dir, "img", "portraits"), exist_ok=True)
for o in UnityPy.load(B(PORTRAITS)).objects:
    if o.type.name == "Texture2D" and o.peek_name() in portraits:
        im = o.read().image.convert("RGB")
        im.thumbnail((96, 96)); im.save(os.path.join(out_dir, "img", "portraits", o.peek_name() + ".jpg"), quality=88)
        portraits.discard(o.peek_name())
wanted |= portraits
if wanted:
    print("missing images:", wanted)
json.dump(data, open(os.path.join(out_dir, "data.json"), "w"), indent=1, ensure_ascii=False)
print(json.dumps(data, indent=1, ensure_ascii=False)[:6000])
