# Données de l'addon → couche 3D de glbi_config (spec-atelier-glbi.md §3, D-8.9), écrite sur GLBI_ROOT.
import json

import bpy

from . import core


def _name(id_block):
    return id_block.name if id_block is not None else ""


def is_nla(scene):
    return scene.glbg.clip_source == core.SOURCE_NLA


def clip_of(scene, item):
    """Nom du clip glTF d'idle ou d'une action : nom de l'Action, ou nom de la piste NLA (v0.3)."""
    return item.track.strip() if is_nla(scene) else _name(item.action)


def build_config(scene):
    """Couche 3D, clés dans l'ordre du contrat ; sans cible caméra, pas de module caméra."""
    s = scene.glbg
    actions = [{
        "id": item.ident.strip(),
        "object": _name(item.obj),
        "type": "armature",
        "clip": clip_of(scene, item),
        "trigger": "button",
        "loop": bool(item.loop),
    } for item in s.actions]

    modules = {}
    if s.camera_target is not None:
        modules["camera"] = {"target": s.camera_target.name}
    modules["animations"] = {
        "mode": "buttons",
        "idle": {"object": _name(s.idle.obj), "clip": clip_of(scene, s.idle), "loop": True},
        "actions": actions,
    }
    return {"version": core.GLBI_VERSION, "scene": {"id": s.scene_id.strip()}, "modules": modules}


def to_json(config):
    return json.dumps(config, ensure_ascii=False, separators=(",", ":"))


def declared_clips(scene):
    """Noms des clips déclarés (idle + actions), sans doublon ni nom vide."""
    s = scene.glbg
    names = [clip_of(scene, i) for i in [s.idle, *s.actions]]
    return list(dict.fromkeys(n for n in names if n))


def clip_objects(scene, clip):
    """Pistes NLA : objets de la scène qui portent une piste de ce nom (armatures, empty cible…)."""
    return [o for o in core.scene_track_objects(scene) if core.tracks_named(o, clip)]


def declared_armature_actions(scene):
    """[(armature, [Actions déclarées qui l'animent])].
    Actions : armatures déclarées (idle et actions) et leurs clips.
    Pistes NLA : toute armature de la scène qui porte une piste d'un clip déclaré, et les Actions des bandes de ces
    pistes (posefix : la pose non clée est perdue comme en mode Actions, test de tête du 21/10)."""
    s = scene.glbg
    if is_nla(scene):
        clips = set(declared_clips(scene))
        out = []
        for arm in core.scene_armatures(scene):
            acts = [a for t in core.nla_tracks(arm) if t.name in clips for a in core.track_actions(t)]
            acts = [a for a in dict.fromkeys(acts) if core.is_armature_action(a, arm)]
            if acts:
                out.append((arm, acts))
        return out
    actions = [a for a in dict.fromkeys([s.idle.action, *(i.action for i in s.actions)]) if a is not None]
    arms = [o for o in dict.fromkeys([s.idle.obj, *(i.obj for i in s.actions)])
            if o is not None and o.type == 'ARMATURE']
    return [(arm, [a for a in actions if core.is_armature_action(a, arm)]) for arm in arms]


def clip_frame_range(scene, clip):
    """(première, dernière image) du clip telles que l'exportateur les échantillonne ; None si inconnu.
    Actions : intervalle de l'Action (entiers). Pistes NLA : union des pistes de ce nom sur les objets de la scène."""
    if not clip:
        return None
    if not is_nla(scene):
        act = bpy.data.actions.get(clip)
        if act is None:
            return None
        return int(act.frame_range[0]), int(act.frame_range[1])
    ranges = [r for o in clip_objects(scene, clip) for t in core.tracks_named(o, clip)
              for r in [core.track_range(t)] if r is not None]
    if not ranges:
        return None
    return min(r[0] for r in ranges), max(r[1] for r in ranges)


def write_root(root, scene):
    """Écrit glbi_version (entier 2) et glbi_config (chaîne JSON) ; renvoie la chaîne écrite."""
    text = to_json(build_config(scene))
    root["glbi_version"] = core.GLBI_VERSION
    root["glbi_config"] = text
    # Piège de JSON.parse("1.0") : la relecture doit donner un objet.
    if not isinstance(json.loads(root["glbi_config"]), dict) or root["glbi_version"] != core.GLBI_VERSION:
        raise RuntimeError("relecture de glbi_config incorrecte")
    return text
