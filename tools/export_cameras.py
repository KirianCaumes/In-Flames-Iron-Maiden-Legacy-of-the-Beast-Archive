"""Export the cameras of Jesterhead's energy ability, Take This Life, for the viewer's battle camera mode.

AB_TakeThisLife: cameraType 400 (Buff01) and secondCamera 1000 (Custom01 = its customCameras[0],
baphomet_special_cam1). CameraManager (IL2CPP):
  * SetActiveCamera switches to cameraType when the ability starts (the "tap rapidly" interaction);
  * OnInteractionFinished (0x1389CF0) calls SwitchToCamera(secondCameraType) when the interaction ends;
  * the OnMainCameraReturn animation event of the ability clip (Jesterhead_buff_anm) returns to the main camera.
Placement (SetStaticTarget, 0x13889D8): the rig root (the "_Parent" transform) is moved to the caster; for
CasterStaticWithRotation it also takes the caster's rotation. CameraDefinition.offsetRegular is the offset used for
the player's team. Each rig's own Animator plays its default state from the moment the camera is switched on.

usage: export_cameras.py <out cameras.json> <ABMv6 dir> <APK assets/bin/Data dir> <AssetRipper export of the energy
       abilities bundle> <AssetRipper export of Jesterhead's bundle>
"""
import glob, json, os, re, struct, sys
import numpy as np
import yaml
import UnityPy

out_path, abm, apk_data, ripped_abilities, ripped_jester = sys.argv[1:]
ABILITIES = os.path.join(abm, "73d61df9d996d9ac4bcc33d9d7385b4c", "b")      # Energy_Abilities_AssetBundle
CAMERAS = glob.glob(os.path.join(apk_data, "7cf4a003*"))[0]                  # built-in CamerasPrefab
FPS = 30
POSITION = ["CasterDynamic", "CasterStatic", "TargetDynamic", "TargetStatic", "WorldStatic", "CasterStaticHead",
            "TargetStaticHead", "CasterDynamicHead", "TargetDynamicHead", "CasterRespectTarget", "TargetRespectCaster",
            "CasterStaticWithRotation"]


def trs(tr):
    p, r, s = tr.m_LocalPosition, tr.m_LocalRotation, tr.m_LocalScale
    return {"pos": [p.x, p.y, p.z], "rot": [r.x, r.y, r.z, r.w], "scale": [s.x, s.y, s.z]}


def chain(cam_tr):
    """Local transforms (Unity space) from the rig root down to the camera."""
    nodes, tr = [], cam_tr
    while True:
        nodes.append((tr.m_GameObject.read().m_Name, trs(tr)))
        if tr.m_GameObject.read().m_Name.endswith("_Parent") or not tr.m_Father.path_id:
            break
        tr = tr.m_Father.read()
    return [dict(name=n, **t) for n, t in reversed(nodes)]


def camera_definition_raw(raw):
    """CameraDefinition without a type tree (APK built-in data): field layout from Il2CppDumper's dump.cs."""
    i = 12 + 4 + 12
    n = struct.unpack_from("<i", raw, i)[0]; i += 4 + n; i += (-i) % 4
    ct, cpt = struct.unpack_from("<ii", raw, i); i += 8 + 12 + 4
    countdown, wait, show = struct.unpack_from("<ffi", raw, i)
    return {"type": ct, "position": POSITION[cpt], "countdown": countdown, "show": show}


def load_yaml(path):
    txt = re.sub(r"^--- !u!\d+ &\d+.*$", "---", open(path).read(), flags=re.M)
    return next(yaml.load_all(re.sub(r"^%TAG.*$", "", txt, flags=re.M), Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader)))


def hermite(keys, t, cs):
    times = np.array([k["time"] for k in keys]); vals = np.array([[k["value"][c] for c in cs] for k in keys], float)
    ins = np.array([[float(k["inSlope"][c]) for c in cs] for k in keys]); outs = np.array([[float(k["outSlope"][c]) for c in cs] for k in keys])
    res = np.empty((len(t), len(cs)))
    for i, tt in enumerate(t):
        if tt <= times[0] or len(keys) == 1:
            res[i] = vals[0]; continue
        if tt >= times[-1]:
            res[i] = vals[-1]; continue
        j = np.searchsorted(times, tt) - 1; dt = times[j + 1] - times[j]; s = (tt - times[j]) / dt
        m0, m1 = outs[j] * dt, ins[j + 1] * dt
        r = (2*s**3 - 3*s**2 + 1) * vals[j] + (s**3 - 2*s**2 + s) * m0 + (-2*s**3 + 3*s**2) * vals[j + 1] + (s**3 - s**2) * m1
        bad = ~np.isfinite(m0) | ~np.isfinite(m1); r[bad] = vals[j][bad]
        res[i] = r
    return res


def sample_clip(path):
    clip = load_yaml(path)["AnimationClip"]
    stop = float(clip["m_AnimationClipSettings"]["m_StopTime"])
    t = np.arange(0, stop + 1e-6, 1 / FPS)
    out = {"duration": stop, "fps": FPS}
    for key, name, cs in (("m_PositionCurves", "pos", "xyz"), ("m_RotationCurves", "rot", "xyzw"), ("m_ScaleCurves", "scale", "xyz")):
        for cv in clip[key] or []:
            if cv["path"] in ("", None):                 # the camera node itself (its Animator's own transform)
                v = hermite(cv["curve"]["m_Curve"], t, cs)
                if name == "rot":
                    v /= np.linalg.norm(v, axis=1, keepdims=True)
                out[name] = np.round(v, 5).reshape(-1).tolist()
    return out


# ---------------------------------------------------------------- the ability
env = UnityPy.load(ABILITIES)
ab = next(o for o in env.objects if o.type.name == "MonoBehaviour" and o.peek_name() == "AB_TakeThisLife")
abt = ab.read_typetree()
custom_root = ab.assets_file.objects[abt["customCameras"][0]["m_PathID"]].read()

# ---------------------------------------------------------------- Custom01: the ability's own camera rig
def find(go_root, comp):
    def walk(tr):
        g = tr.m_GameObject.read()
        for c in g.m_Component:
            if c.component.type.name == comp:
                return tr, g
        for ch in tr.m_Children:
            r = walk(ch.read())
            if r:
                return r
    return walk(next(c.component.read() for c in go_root.m_Component if c.component.type.name == "Transform"))

cam_tr, cam_go = find(custom_root, "Camera")
cd = next(c.component.deref().read_typetree() for c in cam_go.m_Component
          if c.component.type.name == "MonoBehaviour" and c.component.read().m_Script.read().m_Name == "CameraDefinition")
fov = next(c.component.deref().read_typetree()["field of view"] for c in cam_go.m_Component if c.component.type.name == "Camera")
offset = ab.assets_file.objects[cd["offsetRegular"]["m_PathID"]].read()
root_tr = next(c.component.read() for c in custom_root.m_Component if c.component.type.name == "Transform")
custom = {"name": cam_go.m_Name, "type": cd["cameraType"], "position": POSITION[cd["cameraPositionType"]],
          "show": cd["showVisualAgents"], "fov": fov,
          "chain": [dict(name=root_tr.m_GameObject.read().m_Name, **trs(root_tr)),
                    dict(name=offset.m_GameObject.read().m_Name, **trs(offset)),
                    dict(name=cam_go.m_Name, **trs(cam_tr))],
          "anim": sample_clip(glob.glob(f"{ripped_abilities}/**/{cam_go.m_Name}.anim", recursive=True)[0])}

# ---------------------------------------------------------------- Buff01 from the built-in CamerasPrefab
cenv = UnityPy.load(CAMERAS)
buff_go = next(o.read() for o in cenv.objects if o.type.name == "GameObject" and o.peek_name() == "cam_BUFF_01")
buff_tr = next(c.component.read() for c in buff_go.m_Component if c.component.type.name == "Transform")
bdef = next(camera_definition_raw(c.component.deref().get_raw_data()) for c in buff_go.m_Component
            if c.component.type.name == "MonoBehaviour" and len(c.component.deref().get_raw_data()) == 112)
bfov = next(c.component.deref().read_typetree()["field of view"] for c in buff_go.m_Component if c.component.type.name == "Camera")
buff = {"name": "cam_BUFF_01", "type": bdef["type"], "position": bdef["position"], "show": bdef["show"], "fov": bfov,
        "chain": chain(buff_tr), "anim": None}

# ---------------------------------------------------------------- return event in the ability clip
clip = load_yaml(glob.glob(f"{ripped_jester}/**/Jesterhead_buff_anm.anim", recursive=True)[0])["AnimationClip"]
ret = next(float(e["time"]) for e in clip["m_Events"] if e["functionName"] == "OnMainCameraReturn")

data = {"source": "AB_TakeThisLife cameraType/secondCamera/customCameras; CameraManager; CameraDefinition",
        "cameras": {str(buff["type"]): buff, str(custom["type"]): custom},
        "abilities": {"TakeThisLife": {"interaction": str(abt["cameraType"]), "ability": str(abt["secondCamera"]),
                                       "returnAt": round(ret, 4)}}}
json.dump(data, open(out_path, "w"), separators=(",", ":"))
print(json.dumps({k: (v if k != "cameras" else {c: {x: y for x, y in d.items() if x not in ("anim",)} for c, d in v.items()})
                  for k, v in data.items()}, indent=1)[:2500])
