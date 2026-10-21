# Constantes et fonctions partagées de GLB Generator (sans interface).
import re

import bpy

ROOT_NAME = "GLBI_ROOT"
TAB = "BD"
GLBI_VERSION = 2  # version de la couche 3D (D-8.9)

_ROOT_RE = re.compile(re.escape(ROOT_NAME) + r"(\.\d+)?")


def root_candidates():
    """Objets du .blend nommés GLBI_ROOT ou GLBI_ROOT.nnn (contrôle 1 : un seul par .blend)."""
    return [o for o in bpy.data.objects if _ROOT_RE.fullmatch(o.name)]


def scene_has_root(scene):
    return any(o.name == ROOT_NAME for o in scene.objects)


# Types de lampes exportés en KHR_lights_punctual (Area et Hemi ne sont pas pris en charge par glTF).
EXPORTED_LIGHT_TYPES = ('POINT', 'SPOT', 'SUN')


def scene_lights(scene):
    """Lampes de la scène exportées par l'exportateur glTF."""
    return [o for o in scene.objects if o.type == 'LIGHT' and o.data is not None
            and o.data.type in EXPORTED_LIGHT_TYPES]


def scene_armatures(scene):
    return [o for o in scene.objects if o.type == 'ARMATURE']


# ---------------------------------------------------------------------------
# Actions « de l'armature » (spec addon §5) : Action dont un slot OBJECT anime
# au moins un os existant dans l'armature. API des Actions à slots (4.4+).
# ---------------------------------------------------------------------------
_BONE_PATH = re.compile(r'^pose\.bones\["((?:[^"\\]|\\.)*)"\]')


def _slot_fcurves(action, slot):
    for layer in action.layers:
        for strip in layer.strips:
            if strip.type != 'KEYFRAME':
                continue
            channelbag = strip.channelbag(slot)
            if channelbag is not None:
                yield from channelbag.fcurves


def animated_bones(action):
    """Noms des os animés par les slots OBJECT de l'Action."""
    names = set()
    for slot in action.slots:
        if slot.target_id_type != 'OBJECT':
            continue
        for fcurve in _slot_fcurves(action, slot):
            match = _BONE_PATH.match(fcurve.data_path)
            if match:
                names.add(bpy.utils.unescape_identifier(match.group(1)))
    return names


def is_armature_action(action, arm):
    if action is None or arm is None or arm.type != 'ARMATURE':
        return False
    return not animated_bones(action).isdisjoint(arm.data.bones.keys())


def armature_actions(arm):
    """Actions de l'armature, triées par nom."""
    return sorted((a for a in bpy.data.actions if is_armature_action(a, arm)), key=lambda a: a.name)


# ---------------------------------------------------------------------------
# Source des clips (v0.3) : Actions (une armature, mode Actions de l'exportateur) ou pistes NLA
# (plusieurs armatures : un clip glTF commun par nom de piste, mode NLA Tracks de l'exportateur, test de tête
# du 21/10 : en mode Actions, une piste de plusieurs bandes est ignorée et la position des bandes est perdue).
# ---------------------------------------------------------------------------
SOURCE_ACTIONS = 'ACTIONS'
SOURCE_NLA = 'NLA'
# Noms de piste que l'exportateur ne regroupe jamais en clip commun (io_scene_gltf2, tracks.py).
DEFAULT_TRACK_PREFIXES = ("NlaTrack", "[Action Stash]")


def is_default_track_name(name):
    return name.startswith(DEFAULT_TRACK_PREFIXES)


def nla_tracks(obj):
    ad = obj.animation_data if obj is not None else None
    return list(ad.nla_tracks) if ad is not None else []


def tracks_named(obj, name):
    return [t for t in nla_tracks(obj) if t.name == name]


def scene_track_objects(scene):
    """Objets de la scène qui portent au moins une piste NLA (armatures, empties…)."""
    return [o for o in scene.objects if nla_tracks(o)]


def scene_track_names(scene):
    """Noms des pistes NLA des objets de la scène, triés (menu des clips)."""
    return sorted({t.name for o in scene_track_objects(scene) for t in nla_tracks(o)})


def track_range(track):
    """(première image, dernière image) d'une piste, comme l'exportateur (première et dernière bande)."""
    strips = list(track.strips)
    if not strips:
        return None
    return int(strips[0].frame_start), int(strips[-1].frame_end)


def track_actions(track):
    return [s.action for s in track.strips if s.action is not None]


def tweaking_objects(scene):
    """Objets de la scène dont une bande NLA est en édition (Tab dans l'éditeur NLA) : leur Action active est alors en
    lecture seule."""
    return [o for o in scene.objects if o.animation_data is not None and o.animation_data.use_tweak_mode]


def scene_fps(scene):
    """Diviseur des instants de l'exportateur glTF 4.5 : t = image / (fps × fps_base) (test de tête du 21/10)."""
    return scene.render.fps * scene.render.fps_base


# ---------------------------------------------------------------------------
# Pose et image courante des armatures, enregistrées puis rétablies autour d'un export ou d'une évaluation des clips.
# ---------------------------------------------------------------------------
_POSE_CHANNELS = ("location", "rotation_quaternion", "rotation_euler", "rotation_axis_angle", "scale")


def snapshot_poses(scene):
    """Pose de chaque armature de la scène (canaux bruts de chaque os) et image courante."""
    poses = []
    for ob in scene_armatures(scene):
        bones = {pb.name: (pb.rotation_mode, tuple(tuple(getattr(pb, c)) for c in _POSE_CHANNELS))
                 for pb in ob.pose.bones}
        poses.append((ob, bones))
    return scene.frame_current, poses


def restore_poses(scene, snapshot):
    """L'exportateur glTF remet à zéro les os entre deux Actions (reset_bone_matrix) et ne
    restaure que l'Action active : les os qu'elle n'anime pas restent à la pose de repos.
    On réévalue l'image d'origine, puis on remet chaque os tel qu'il était avant l'export."""
    frame, poses = snapshot
    scene.frame_set(frame)
    for ob, bones in poses:
        try:
            pose_bones = ob.pose.bones
        except ReferenceError:
            continue
        for pb in pose_bones:
            saved = bones.get(pb.name)
            if saved is None:
                continue
            pb.rotation_mode = saved[0]
            for channel, value in zip(_POSE_CHANNELS, saved[1]):
                setattr(pb, channel, value)
    bpy.context.view_layer.update()
