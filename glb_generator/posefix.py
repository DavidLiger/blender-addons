# Pose non enregistrée dans les Actions (incident du 14/10, pointes sur les doigts du raptor).
#
# L'exportateur glTF remet chaque os à la matrice identité avant d'échantillonner une Action
# (export_reset_pose_bones, anim_utils.reset_bone_matrix) : un canal de pose hors repos qu'aucune F-curve de
# l'Action n'anime (ex. échelle de compensation héritée d'un FBX) est perdu dans le GLB, alors que Blender
# l'affiche. Pendant l'export par le bouton, l'addon enregistre ces valeurs par des F-curves temporaires à une
# clé, puis les retire : le GLB reproduit Blender et le .blend n'est pas modifié.
#
# Seuls les canaux « statiques » sont enregistrés : hors repos dans la pose et animés par AUCUNE Action de
# l'armature. Un canal hors repos animé par une autre Action mais pas par l'Action exportée n'a pas de valeur
# bien définie (dans Blender il garde la valeur laissée par la dernière Action jouée) : il est signalé, pas
# enregistré.
import bpy

from . import core

SEUIL = 1e-4
_REPOS = {
    "location": (0.0, 0.0, 0.0),
    "rotation_quaternion": (1.0, 0.0, 0.0, 0.0),
    "rotation_euler": (0.0, 0.0, 0.0),
    "rotation_axis_angle": (0.0, 0.0, 1.0, 0.0),
    "scale": (1.0, 1.0, 1.0),
}


def _rotation_prop(pb):
    if pb.rotation_mode == 'QUATERNION':
        return "rotation_quaternion"
    if pb.rotation_mode == 'AXIS_ANGLE':
        return "rotation_axis_angle"
    return "rotation_euler"


def _data_path(bone_name, prop):
    return f'pose.bones["{bpy.utils.escape_identifier(bone_name)}"].{prop}'


def _bone_channelbags(action):
    """(slot, channelbag) des slots OBJECT de l'Action qui animent au moins un os."""
    out = []
    for slot in action.slots:
        if slot.target_id_type != 'OBJECT':
            continue
        for layer in action.layers:
            for strip in layer.strips:
                if strip.type != 'KEYFRAME':
                    continue
                cb = strip.channelbag(slot)
                if cb is not None and any(fc.data_path.startswith('pose.bones["') for fc in cb.fcurves):
                    out.append((slot, cb))
    return out


def _keyed(action):
    """Ensemble (data_path, index) animés par l'Action, tous slots OBJECT confondus."""
    return {(fc.data_path, fc.array_index)
            for _slot, cb in _bone_channelbags(action) for fc in cb.fcurves}


def _off_rest(arm):
    """{(data_path, index): valeur} des canaux de pose hors repos de l'armature (pose actuelle)."""
    out = {}
    for pb in arm.pose.bones:
        for prop in ("location", _rotation_prop(pb), "scale"):
            values = tuple(getattr(pb, prop))
            for i, (v, r) in enumerate(zip(values, _REPOS[prop])):
                if abs(v - r) > SEUIL:
                    out[(_data_path(pb.name, prop), i)] = v
    return out


def analyse(arm, actions):
    """Par Action : (canaux statiques à enregistrer {clé: valeur}, canaux ambigus [clé]).
    La pose doit être celle du viewport (appelé avant que l'exportateur ne la touche)."""
    off = _off_rest(arm)
    keyed_any = set()
    for act in core.armature_actions(arm):
        keyed_any |= _keyed(act)
    result = {}
    for act in actions:
        k = _keyed(act)
        static = {key: v for key, v in off.items() if key not in k and key not in keyed_any}
        ambiguous = sorted(key for key in off if key not in k and key in keyed_any)
        result[act] = (static, ambiguous)
    return result


def add_temporary_keys(arm, actions):
    """Ajoute une F-curve à une clé pour chaque canal statique. Renvoie (créées, résumé par Action)."""
    created, summary = [], {}
    for act, (static, ambiguous) in analyse(arm, actions).items():
        bags = _bone_channelbags(act)
        summary[act.name] = (static, ambiguous)
        if not static or not bags:
            continue
        frame = act.frame_range[0]
        for slot, cb in bags:
            for (path, index), value in static.items():
                fc = cb.fcurves.new(path, index=index)
                fc.keyframe_points.insert(frame, value, options={'FAST'})
                fc.update()
                created.append((cb, fc))
    return created, summary


def remove_temporary_keys(created):
    for cb, fc in reversed(created):
        try:
            cb.fcurves.remove(fc)
        except (ReferenceError, RuntimeError):
            pass


def describe(static):
    """« 24 échelles, 18 positions »."""
    names = {"location": "position", "scale": "échelle"}
    count = {}
    for path, _i in static:
        prop = path.rsplit(".", 1)[-1]
        label = names.get(prop, "rotation")
        count[label] = count.get(label, 0) + 1
    return ", ".join(f"{n} {label}{'s' if n > 1 else ''}" for label, n in sorted(count.items()))
