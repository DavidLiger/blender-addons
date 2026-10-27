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


MARKER_SEP = ":"


def marker_events(scene):
    """Marqueurs de la timeline de la scène nommés <id d'action>:<id d'événement> (DP-6).
    Renvoie ({id d'action déclarée: [(id d'événement, instant en s, image, nom du marqueur)] triés par instant},
             [(nom du marqueur, raison)] des marqueurs ignorés).
    Instant = image / (fps × fps_base), la base de temps des clés écrites par l'exportateur (test de tête du 21/10),
    arrondi à la milliseconde."""
    fps = core.scene_fps(scene)
    declared = {item.ident.strip() for item in scene.glbg.actions if item.ident.strip()}
    events, ignored = {}, []
    for m in sorted(scene.timeline_markers, key=lambda m: (m.frame, m.name)):
        action_id, sep, event_id = m.name.partition(MARKER_SEP)
        action_id, event_id = action_id.strip(), event_id.strip()
        if not sep:
            ignored.append((m.name, f"sans « {MARKER_SEP} » (nom attendu <action>{MARKER_SEP}<événement>)"))
        elif action_id not in declared:
            reason = ("pas d'événements sur idle" if action_id == "idle"
                      else f"action « {action_id} » non déclarée")
            ignored.append((m.name, reason))
        else:
            events.setdefault(action_id, []).append((event_id, round(m.frame / fps, 3), m.frame, m.name))
    for evs in events.values():
        evs.sort(key=lambda e: (e[1], e[0]))
    return events, ignored


def _action_entry(scene, item, events):
    entry = {
        "id": item.ident.strip(),
        "object": _name(item.obj),
        "type": "armature",
        "clip": clip_of(scene, item),
        "trigger": "button",
        "loop": bool(item.loop),
    }
    if item.cut:
        entry["transition"] = "cut"  # absent = fondu (DP-8)
    if item.camera_position is not None:
        entry["cameraPosition"] = item.camera_position.name  # absent = caméra libre (DP-19)
    evs = events.get(entry["id"])
    if evs:
        entry["events"] = [{"id": e[0], "time": e[1]} for e in evs]
    return entry


def build_config(scene):
    """Couche 3D, clés dans l'ordre du contrat ; sans cible caméra, pas de module caméra."""
    s = scene.glbg
    events, _ignored = marker_events(scene)
    # Un id d'action en double (contrôle 8) ne reçoit ses événements qu'une fois.
    seen = set()
    actions = []
    for item in s.actions:
        ident = item.ident.strip()
        actions.append(_action_entry(scene, item, {} if ident in seen else events))
        seen.add(ident)

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


def camera_position_keyed(scene, item):
    """L'empty de position de l'action est-il animé par ses propres clés de position dans le clip, tel que
    l'exportateur l'échantillonne ? Pistes NLA : bandes de sa piste du nom du clip. Actions : l'exportateur n'exporte
    l'animation d'un empty que pour son Action active (essai du 26/10), donc seulement si c'est l'Action du clip."""
    empty = item.camera_position
    if empty is None or empty.animation_data is None:
        return False
    if is_nla(scene):
        clip = clip_of(scene, item)
        return any(core.slot_location_keyed(st.action, st.action_slot)
                   for t in core.tracks_named(empty, clip) for st in t.strips
                   if st.action is not None and not st.mute)  # bande coupée : ignorée par l'exportateur
    ad = empty.animation_data
    return item.action is not None and ad.action == item.action and core.slot_location_keyed(ad.action, ad.action_slot)


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
