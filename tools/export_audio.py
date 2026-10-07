"""Export Jesterhead's sounds, with the mapping the game uses, and the ambience of the "In The Dark" map.

The mapping comes from cso_jesterhead (CharacterFX): animationSoundEffects (played by
VisualPlayerAgent.PlayAnimationSFX when an ability animation starts), deathFX (VisualPlayerAgent.Die),
reviveFX and successiveTapEffects (one per successful tap of a "tap rapidly" interaction).
Worlds.csv gives the map ambience. QuestBattles.csv gives the battle music of every In The Dark battle: the instrumental
of Iron Maiden's "Caught Somewhere in Time"; it is recorded in sounds.json by title only, not exported.

usage: export_audio.py <out_dir> <ABMv6 dir>
writes <out_dir>/<clip>.ogg and .mp3 (re-encoded from the decoded game audio) and <out_dir>/sounds.json
"""
import json, os, subprocess, sys
import UnityPy

out_dir, abm = sys.argv[1:]
os.makedirs(out_dir, exist_ok=True)
B = lambda h: os.path.join(abm, h, "b")
CHAR, BASE = "08892f8df03ce3add90d09f13a7e7ebc", "67e008b053c12dceaf08614584cb2083"
AMBIENCE = {"map": ("fbeffab64e8d74aa285143c59ac7b642", "bc_amb_mapHub_fe_stage1")}
MUSIC = {"battle": {"clip": "Caught_SomeWhere_In_Time_Backing_Track_V4", "title": "Caught Somewhere in Time (instrumental)",
                    "artist": "Iron Maiden", "included": False}}
ANIMATION_TYPES = {10001: "attack_single_01", 10002: "attack_single_02", 10003: "attack_single_03", 10004: "attack_single_04"}

env = UnityPy.load(B(CHAR), B(BASE))
by_file = {(o.assets_file.name.lower(), o.path_id): o for o in env.objects}
so = next(o for o in env.objects if o.type.name == "MonoBehaviour" and o.peek_name() == "cso_jesterhead")
fx = so.read_typetree()["characterFX"]


def resolve(ref, owner=so):
    if not ref["m_PathID"]:
        return None
    af = owner.assets_file
    name = af.name if not ref["m_FileID"] else af.externals[ref["m_FileID"] - 1].name.split("/")[-1]
    return by_file.get((name.lower(), ref["m_PathID"]))


def audio_sources(obj):
    """AudioSource clips under a GameObject/Transform, following MultipleAudioSource lists."""
    o = obj.read()
    go = o.m_GameObject.read() if hasattr(o, "m_GameObject") else o
    found = []
    for c in go.m_Component:
        p = c.component if hasattr(c, "component") else c[1]
        if p.type.name == "AudioSource":
            a = p.read()
            if a.m_audioClip.path_id:
                found.append((a.m_audioClip.read(), a.m_Volume))
        elif p.type.name == "MonoBehaviour":
            tt = p.deref().read_typetree()
            for ref in tt.get("AudioSourceList", []):
                r = resolve(ref, p.deref())
                if r is not None:
                    found += audio_sources(r)
    return found


exported = {}


def export_clip(clip, mp3=True):
    """UnityPy decodes the FSB5 stream to PCM; keep a Vorbis copy (download) and an MP3 (playback in browsers)."""
    if clip.m_Name in exported:
        return clip.m_Name
    for name, data in clip.samples.items():
        pcm = os.path.join(out_dir, clip.m_Name + "_pcm" + (os.path.splitext(name)[1] or ".wav"))
        open(pcm, "wb").write(data)
        for ext, codec in ((".ogg", ["libvorbis", "-q:a", "6"]), (".mp3", ["libmp3lame", "-q:a", "4"]))[:2 if mp3 else 1]:
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", pcm, "-codec:a", *codec,
                            os.path.join(out_dir, clip.m_Name + ext)], check=True)
        os.remove(pcm)
        exported[clip.m_Name] = {"ogg": clip.m_Name + ".ogg", **({"mp3": clip.m_Name + ".mp3"} if mp3 else {}),
                                 "length": round(clip.m_Length, 3)}
        break
    return clip.m_Name


def entry(sfx):
    obj = resolve(sfx["audioSourceObject"])
    if obj is None:
        return None
    clips = audio_sources(obj)
    if not clips:
        return None
    clip, volume = clips[0]
    return {"clip": export_clip(clip), "volume": round(volume, 3), "delay": sfx.get("audioStartDelay", 0.0)}


sounds = {"states": {}, "taps": [], "ambience": {}, "music": MUSIC}
for a in fx["animationSoundEffects"]:
    e = entry(a["animationSFX"])
    if e:
        sounds["states"][ANIMATION_TYPES.get(a["animationType"], str(a["animationType"]))] = e
for key, state in (("deathFX", "Death01"), ("reviveFX", "Revive")):
    e = entry(fx[key])
    if e:
        sounds["states"][state] = e
sounds["taps"] = [entry(t) for t in fx["successiveTapEffects"]]
for key, (bundle, name) in AMBIENCE.items():               # download only, not played by the viewer
    menv = UnityPy.load(B(bundle))
    clip = next(o.read() for o in menv.objects if o.type.name == "AudioClip" and o.peek_name() == name)
    sounds["ambience"][key] = {"clip": export_clip(clip, mp3=False)}
sounds["files"] = exported
json.dump(sounds, open(os.path.join(out_dir, "sounds.json"), "w"), indent=1)
print(json.dumps(sounds, indent=1))
