"""Generate site/index.html from tools/site_template.html, the event data (site/assets/event/data.json,
written by export_event.py) and the files in site/assets. Missing thumbnails are made with ImageMagick.

usage: python3 tools/build_site.py            (run from anywhere)
       SITE_URL=https://example.org/ python3 tools/build_site.py
                                              (address of the published site, for the link previews of Discord and
                                               other apps; defaults to the GitHub Pages address)
"""
import html
import json
import os
import re
import subprocess

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "site")
# link previews need absolute URLs
SITE_URL = os.environ.get("SITE_URL", "https://kiriancaumes.github.io/In-Flames-Iron-Maiden-Legacy-of-the-Beast-Archive/")
SITE_URL = SITE_URL.rstrip("/") + "/"
SOCIAL = "assets/social-preview.jpg"                  # 1200 x 630 viewer render (tools/social_preview.mjs)
THUMBS = "assets/library/thumbs"
E = html.escape

# ------------------------------------------------------------------ helpers
SMALL = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to", "with"}


def title(s):
    words = s.lower().split()
    return " ".join(w if (i and w in SMALL) else w[:1].upper() + w[1:] for i, w in enumerate(words))


def rich(s):
    """Game text markup: [RRGGBB]...[-] colour runs, [i]...[/i], literal \\n line breaks, bullets."""
    s = E(s.replace("\\n", "\n"))
    s = re.sub(r"\[[0-9A-Fa-f]{6}\](.*?)\[-\]", r'<span class="kw">\1</span>', s)
    s = re.sub(r"\[i\](.*?)\[/i\]", r"<em>\1</em>", s)
    s = re.sub(r"\[/?[0-9A-Fa-f]{6}\]|\[-\]", "", s)
    lines = [l.strip() for l in s.split("\n") if l.strip()]
    if all(l.startswith("•") for l in lines):
        return "<ul class=\"bullets\">" + "".join(f"<li>{l.lstrip('• ').strip()}</li>" for l in lines) + "</ul>"
    return "".join(f"<p>{l}</p>" for l in lines)


def img_size(path):
    with Image.open(path) as im:
        return im.size


WEB = "assets/web"


def web(rel, max_w):
    """Display copy of a page image: WebP, at most max_w pixels wide (the original stays downloadable)."""
    out = f"{WEB}/{os.path.splitext(os.path.basename(rel))[0]}.webp"
    dst = os.path.join(SITE, out)
    if not os.path.exists(dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with Image.open(os.path.join(SITE, rel)) as im:
            im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
            if im.width > max_w:
                im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
            im.save(dst, "WEBP", quality=85, method=6)
    return out


def img(src, alt="", cls=None, **attrs):
    """<img> with its intrinsic size, so the page does not shift while lazy images load."""
    w, h = attrs.pop("width", None), attrs.pop("height", None)
    if w is None:
        w, h = img_size(os.path.join(SITE, src))
    extra = "".join(f' {k}="{v}"' for k, v in attrs.items())
    return f'<img{f" class={chr(34)}{cls}{chr(34)}" if cls else ""} src="{src}" alt="{E(alt)}" width="{w}" height="{h}" loading="lazy"{extra}>'


def human(n):
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def thumb(rel, alpha):
    """alpha: 'off' ignores the alpha channel (emission masks), 'flatten' composites on the panel colour."""
    name = os.path.splitext(os.path.basename(rel))[0] + ".jpg"
    out = os.path.join(SITE, THUMBS, name)
    if not os.path.exists(out):
        os.makedirs(os.path.dirname(out), exist_ok=True)
        args = ["-alpha", "off"] if alpha == "off" else ["-background", "#1c1415", "-flatten"]
        subprocess.run(["magick", os.path.join(SITE, rel), *args, "-resize", "320x320>", "-quality", "84", out], check=True)
    return f"{THUMBS}/{name}"


# ------------------------------------------------------------------ data
data = json.load(open(os.path.join(SITE, "assets/event/data.json"), encoding="utf-8"))
sounds = json.load(open(os.path.join(SITE, "assets/audio/sounds.json"), encoding="utf-8"))
lineups = json.load(open(os.path.join(SITE, "assets/enemies/lineups.json"), encoding="utf-8"))
char, abilities, event = data["character"], data["abilities"], data["event"]
ICON = "assets/event/img/{}.png"
PLAY = {"Basic": "attack", "Energy": "energy"}       # viewer entries that play the ability's animation


def quest_name(q):
    """Quest names are the event name and the difficulty in capitals; str.title() keeps the event written as
    "In The Dark", the way the rest of the page writes it (title() would give "In the Dark")."""
    return q["name"].title()


def character_html():
    rows = []
    for r in char["ranks"]:
        cells = "".join(f'<td>{r[k]:,}<small>+{r["growth"][k]}</small></td>' for k in ("hp", "atk", "def", "ap", "mr", "sp"))
        rows.append(f'<tr><th scope="row">{r["rank"]} <span class="stars" aria-label="{r["stars"]} stars">'
                    f'{"★" * r["stars"]}</span></th>{cells}<td>{r["partyCost"]}</td></tr>')
    cards = []
    for a in abilities:
        meta = a["kind"] + (f' &middot; {a["energy"]} energy' if a.get("energy") else "")
        play = (f'<button type="button" class="link" data-play="{PLAY[a["kind"]]}">Play in the viewer</button>'
                if a["kind"] in PLAY else "")
        cards.append(f'''<article class="ability">
      {img(web(ICON.format(a["icon"]), 192), width=96, height=96)}
      <div><p class="meta">{meta}</p><h4>{E(title(a["name"]))}</h4>{rich(a["description"])}{play}</div>
    </article>''')
    cal = event["calendar"]
    return f'''<section id="jesterhead" class="wrap doc">
  <h2>{E(char["name"])}</h2>
  <div class="profile">
    {img(web("assets/library/ERC_FG_Jesterhead.png", 600), "Jesterhead, login calendar artwork", "portrait")}
    <div>
      <p class="lead">The In Flames mascot joined <i>Legacy of the Beast</i> in July 2023 with the In The Dark event: a hooded
        figure whose face is a clock, with a staff and three pocket watches on chains, surrounded by drifting ghost faces.
        His three abilities are named after In Flames songs.</p>
      <dl class="facts">
        <dt>Class</dt><dd>{E(char["class"])}</dd>
        <dt>Role</dt><dd>{E(char["role"])}</dd>
        <dt>Ranks</dt><dd>I to V (one to five stars)</dd>
        <dt>{E(title(cal["title"]))}</dt><dd>&ldquo;{E(cal["description"])}&rdquo;</dd>
      </dl>
    </div>
  </div>
  <h3>Abilities</h3>
  <div class="abilities">
    {"".join(cards)}
  </div>
  <h3>Statistics</h3>
  <div class="table-scroll">
  <table class="stats">
    <caption>Base values at level 1 for each rank, with the gain per level.</caption>
    <thead><tr><th scope="col">Rank</th><th scope="col">HP</th><th scope="col">Attack</th><th scope="col">Defense</th>
      <th scope="col">Magic</th><th scope="col">Magic resist</th><th scope="col">Special</th><th scope="col">Party cost</th></tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table>
  </div>
</section>'''


ARENAS = {"InFlames_road1": ("road1", "The road"), "hellgate_road2": ("road2", "Hell Gate"),
          "InFlames_roadEnd": ("roadEnd", "The gate")}


def event_html():
    q0 = event["quests"][0]
    quest_cards = "".join(f'''<figure class="quest">
      {img(web(ICON.format(q["card"]), 512))}
      <figcaption><b>{E(quest_name(q))}</b><em>{E(q["line"])}</em></figcaption>
    </figure>''' for q in event["quests"])
    battles = []
    for i, b in enumerate(q0["battles"], 1):
        _, label = ARENAS[b["arena"]]
        foes = []
        for e in next(x for x in lineups["battles"] if x["battle"] == i)["enemies"]:
            boss = " &middot; boss" if e.get("boss") else ""
            c = lineups["characters"][e["id"]]
            foes.append(f'<li>{img("assets/enemies/" + c["icon"], width=40, height=40)}'
                        f'<span><b>{E(c["name"])}</b><small>{E(c["class"])} &middot; level {e["level"]}{boss}</small></span></li>')
        battles.append(f'''<li class="battle">
      <div class="bhead"><h4>Battle {i}</h4><span>{label} <code>{b["arena"]}</code></span>
        <button type="button" class="link" data-battle="{i}">View in 3D</button></div>
      <ul class="foes">{"".join(foes)}</ul>
    </li>''')
    scenes = []
    for d in event["dialogues"]:
        when = "before" if d["trigger"] == "BattleStart" else "after"
        lines = "".join(f'<div class="line {"right" if l["side"] == "Right" else "left"}">'
                        + (img(f"assets/event/img/portraits/{l['portrait']}.jpg", width=44, height=44) if l.get("portrait") else "")
                        + f'<p><b>{E(l["speaker"] or "")}</b> {rich(l["text"])[3:-4]}</p></div>'
                        for l in d["lines"])
        scenes.append(f'<div class="scene"><h4>Battle {d["battleNumber"]}, {when} the fight</h4>{lines}</div>')
    cur = event["currency"]
    music = sounds["music"]["battle"]
    return f'''<section id="in-the-dark" class="wrap doc">
  <h2>In The Dark</h2>
  <div class="cols">
    <div>
      <p class="lead">&ldquo;{E(q0["line"])}&rdquo; The event&rsquo;s dungeon had four difficulties, each a run of five
        battles; the story below is the one told in the Normal run.</p>
      <p>The five battles take place in three arenas, each with a past, a present and a future version and its own
        lighting. Every battle played the instrumental of {E(music["artist"])}&rsquo;s &ldquo;Caught Somewhere in Time&rdquo;
        (<code>{E(music["clip"])}</code> in the game data); the music is not included in this archive.</p>
      <p class="currency">{img(web(ICON.format(cur["icon"]), 80), width=40, height=40)}
        <span><b>{E(cur["name"])}</b>, the event currency: {E(cur["description"])}</span></p>
    </div>
    <figure class="video">
      <a href="https://www.youtube.com/watch?v=lcOZMtM2nto" rel="noopener" target="_blank">
        {img(web("assets/event/video-thumbnail.jpg", 1120), "Video: In Flames/Legacy of the Beast, In The Dark, Jesterhead gameplay")}
        <span class="play-badge">Watch on YouTube</span>
      </a>
      <figcaption>In-game footage of Jesterhead in the dungeon, uploaded by
        <a href="https://www.youtube.com/@jesters_collection" rel="noopener" target="_blank">A Jester&rsquo;s Collection</a>.</figcaption>
    </figure>
  </div>
  <h3>Battles</h3>
  <p class="small">The enemy line-ups were sent by the game servers and are not in the cached data. These are the ones
    of the Normal run, read from the gameplay video above and matched to the game&rsquo;s characters by portrait and class.</p>
  <ol class="battles">{"".join(battles)}</ol>
  <h3>Difficulties</h3>
  <div class="quests">{quest_cards}</div>
  <details class="story">
    <summary>Story (dialogue of the Normal difficulty)</summary>
    {"".join(scenes)}
  </details>
  <div class="gallery">
    <figure><a href="assets/event/key-art.jpg">{img(web("assets/event/key-art.jpg", 830), "Event key art: Jesterhead with Eddie")}</a>
      <figcaption>Event key art. Via <a href="https://www.theprp.com/2023/07/12/news/in-flames-jesterhead-added-to-iron-maidens-legacy-of-the-beast-video-game/" rel="noopener" target="_blank">ThePRP</a>.</figcaption></figure>
    <figure>{img(web("assets/event/event-banner-landscape.jpg", 600), "Event banner")}
      <figcaption>In-game event banner (low resolution copy).</figcaption></figure>
  </div>
</section>'''


# ------------------------------------------------------------------ library
LIB = "assets/library"
GROUPS = [
    ("Artwork and icons", "flatten", [
        (f"{LIB}/graphic_IM_legacy_logo.png", "Game logo, from the application package."),
        (f"{LIB}/ERC_FG_Jesterhead.png", "Login calendar artwork."),
        (f"{LIB}/ERC_FG_Jesterhead_psd.png", "Same artwork, tall layout variant."),
        (f"{LIB}/carousel_732_Jesterhead.png", "Store carousel tile."),
        (f"{LIB}/hud_icon_Jesterhead.png", "Battle HUD portrait."),
    ] + [(ICON.format(a["icon"]), f"{a['kind']} ability icon, {title(a['name'])}.") for a in abilities]),
    ("Character textures", "off", [
        (f"{LIB}/jesterhead_diff.png", "Main texture. Alpha = emission mask (watch face, crystal)."),
        (f"{LIB}/jesterhead_gear_diff.png", "Staff, watches and chains. Alpha = emission mask."),
        (f"{LIB}/jesterhead_skirt_faces_5.png", "Ghost faces scrolling on the robe."),
        (f"{LIB}/jesterhead_skirt_faces_6.png", "Ghost faces scrolling on the upper body."),
        (f"{LIB}/jesterhead_diff_moving.png", "Alternate robe texture (disabled in the prefab)."),
        (f"{LIB}/characterramp_1_8_hell.png", "Light ramp of the character shader."),
        (f"{LIB}/rim_ramp.png", "Rim ramp of the character shader."),
    ]),
    ("In The Dark", "flatten", [
        (ICON.format(q["card"]), f"Quest card, {quest_name(q)}.") for q in event["quests"]
    ] + [
        (ICON.format(event["currency"]["icon"]), f"{event['currency']['name']}, the event currency."),
        (f"{LIB}/Inflames_symbol_glow_dif.png", "Glowing In Flames sigil: floats over the road (battles 1 and 2) and burns "
                                                "in the fire portal of the final gate (past and present)."),
        (f"{LIB}/Inflames_Frame_no_lighting_dif.png", "Ring frame of the final gate in the future period, when the gate is "
                                                      "sealed (arena of battle 5)."),
        (f"{LIB}/Inflames_platform_dif.png", "Floor of the final arena in the future period, engraved with the "
                                             "In Flames sigil."),
    ]),
]
FILES = [
    ("assets/model/jesterhead.glb", "Skinned model with its 11 animations (glTF binary, textures embedded)."),
    (f"{LIB}/jesterhead_obj_meshes.zip", "The 9 meshes as static OBJ files, in bind pose."),
    ("assets/vfx.json", "Particle systems, effect triggers, tweens and chains."),
    ("assets/arena/road1.glb", "Arena InFlames_road1 (battles 1 and 2), the three time periods."),
    ("assets/arena/road2.glb", "Arena hellgate_road2 (battles 3 and 4), the three time periods."),
    ("assets/arena/roadEnd.glb", "Arena InFlames_roadEnd (boss battle), the three time periods."),
    ("assets/arena/arena.json", "Arena materials, fog ramps, animations, particles, battle camera and slot."),
    ("assets/event/data.json", "Character, abilities, quests and dialogue, from the game data tables."),
    ("assets/enemies/lineups.json", "Enemy line-ups of the five battles, with names, classes and levels."),
]
AUDIO_DESC = {
    "Jesterhead_Attack_01": "Only for the Weak (attack_single_01).",
    "Jesterhead_Attack_03": "Take This Life (attack_single_03).",
    "despawn_whoosh": "Death.",
    "Revive_March15_2016": "Revive (shared by all characters).",
    "Leve_Up_Star_Slam": "Each tap of a &ldquo;tap rapidly&rdquo; prompt.",
    "bc_amb_mapHub_fe_stage1": "Ambience of the In The Dark map.",
}


def library_html():
    out = []
    for name, alpha, items in GROUPS:
        out.append(f'<div class="lib-group"><h3>{E(name)}</h3><div class="lib">')
        for rel, desc in items:
            path = os.path.join(SITE, rel)
            w, h = img_size(path)
            base = os.path.basename(rel)
            out.append(
                f'<figure class="item"><a class="thumb" href="{rel}">{img(thumb(rel, alpha))}</a>'
                f'<figcaption><b><a href="{rel}" download>{E(base)}</a></b>'
                f'{w}&times;{h} &middot; {human(os.path.getsize(path))}<br>{E(desc)}</figcaption></figure>')
        out.append("</div></div>")
    out.append('<div class="lib-group"><h3>Model and data</h3><table class="files"><tbody>')
    for rel, desc in FILES:
        size = human(os.path.getsize(os.path.join(SITE, rel)))
        out.append(f'<tr><td><a href="{rel}" download>{E(os.path.basename(rel))}</a></td>'
                   f'<td>{E(desc)}</td><td>{size}</td></tr>')
    out.append("</tbody></table></div>")
    out.append('<div class="lib-group"><h3>Enemies</h3><table class="files"><tbody>')
    for cid, c in sorted(lineups["characters"].items(), key=lambda kv: kv[1]["name"] + kv[1]["class"]):
        rel = f"assets/enemies/{c['file']}"
        where = ", ".join(str(b["battle"]) for b in lineups["battles"] if any(e["id"] == cid for e in b["enemies"]))
        out.append(f'<tr><td><a href="{rel}" download>{E(c["file"])}</a></td>'
                   f'<td>{E(c["name"])} ({E(c["class"])}), battle {where}. Idle animation <code>{E(c["idle"])}</code>.</td>'
                   f'<td>{human(os.path.getsize(os.path.join(SITE, rel)))}</td></tr>')
    out.append("</tbody></table></div>")
    out.append('<div class="lib-group"><h3>Sound</h3><table class="files"><tbody>')
    for clip, f in sounds["files"].items():
        rel = f"assets/audio/{f['ogg']}"
        out.append(f'<tr><td><a href="{rel}" download>{E(f["ogg"])}</a></td><td>{AUDIO_DESC.get(clip, "")} '
                   f'{f["length"]:.1f}&nbsp;s</td><td>{human(os.path.getsize(os.path.join(SITE, rel)))}</td></tr>')
    out.append("</tbody></table>")
    out.append('<p class="small">Sounds are re-encoded (Ogg Vorbis) from the game&rsquo;s decoded audio. The battle music,'
               ' the instrumental of Iron Maiden&rsquo;s &ldquo;Caught Somewhere in Time&rdquo;, is not included.</p></div>')
    return "\n  ".join(out)


def battle_options():
    """Options of the viewer's battle list; data-arena is the arena file the viewer loads (QuestBattles battleArena)."""
    out = ['<option value="">None (no arena)</option>']
    for i, b in enumerate(event["quests"][0]["battles"], 1):
        key, label = ARENAS[b["arena"]]
        boss = any(e.get("boss") for x in lineups["battles"] if x["battle"] == i for e in x["enemies"])
        out.append(f'<option value="{i}" data-arena="{key}"{" selected" if i == 1 else ""}>'
                   f'{i} &middot; {label}{" (boss)" if boss else ""}</option>')
    return "".join(out)


def file_sizes():
    """Sizes of the downloads the viewer shows progress for (served compressed, their Content-Length is smaller)."""
    files = ["model/jesterhead.glb"] + [f"arena/{key}.glb" for key, _ in ARENAS.values()]
    return json.dumps({f: os.path.getsize(os.path.join(SITE, "assets", f)) for f in files}, separators=(",", ":"))


if __name__ == "__main__":
    tpl = open(os.path.join(ROOT, "tools", "site_template.html"), encoding="utf-8").read()
    if not os.path.exists(os.path.join(SITE, SOCIAL)):
        print(f"warning: no {SOCIAL} (link preview image): run tools/social_preview.mjs")
    elif img_size(os.path.join(SITE, SOCIAL)) != (1200, 630):
        raise SystemExit(f"{SOCIAL}: link previews expect 1200x630")
    page = (tpl.replace("<!--LIBRARY-->", library_html())
               .replace("<!--CHARACTER-->", character_html())
               .replace("<!--EVENT-->", event_html())
               .replace("<!--BATTLES-->", battle_options())
               .replace("<!--SIZES-->", file_sizes())
               .replace("%SITE_URL%", SITE_URL))
    open(os.path.join(SITE, "index.html"), "w", encoding="utf-8").write(page)
    print("wrote site/index.html")
