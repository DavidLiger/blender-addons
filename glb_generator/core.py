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
