# Crochets de l'exportateur glTF de Blender 4.5 (io_scene_gltf2).
# L'exportateur cherche, au niveau du module de chaque addon activé, la classe
# glTF2ExportUserExtension et la fonction glTF2_pre_export_callback : __init__.py
# les expose sous ces noms.
import bpy

from . import core

_state = {"clips": None, "frame": None}


def set_clip_filter(names):
    """Clips à exporter (export lancé par l'addon) ; None = pas de filtre (export manuel)."""
    _state["clips"] = set(names) if names is not None else None


class GLBG_GltfExtension:
    def pre_export_hook(self, export_settings):
        # Mémorise l'image courante : l'exportateur la passe à 0 avant le callback de contrôle.
        scene = bpy.context.scene
        _state["frame"] = (scene, scene.frame_current)

    def gather_scene_hook(self, gltf2_scene, blender_scene, export_settings):
        # Spec addon §6 : extras de scène réduits aux clés glbi_ ; le .blend n'est pas modifié.
        if not core.scene_has_root(blender_scene):
            return
        extras = gltf2_scene.extras
        if isinstance(extras, dict):
            kept = {k: v for k, v in extras.items() if k.startswith("glbi_")}
            gltf2_scene.extras = kept or None

    def gather_actions_hook(self, blender_object, actions, export_settings):
        # Filtre d'Actions : exactement idle + actions[].clip quand l'addon exporte.
        allowed = _state["clips"]
        if allowed is None:
            return
        for key in list(actions.actions.keys()):
            kept = [a for a in actions.actions[key] if a.action.name in allowed]
            if kept:
                actions.actions[key] = kept
            else:
                del actions.actions[key]


def pre_export_callback(export_settings):
    """Contrôle 9 : interrompt un export d'une scène avec GLBI_ROOT si les réglages ne sont pas conformes.
    Une exception levée ici arrête l'export avant l'écriture du fichier (les crochets de
    glTF2ExportUserExtension, eux, sont protégés par l'exportateur et ne peuvent pas l'arrêter)."""
    if export_settings.get('gltf_active_scene'):
        scenes = [bpy.context.scene]
    else:
        scenes = list(bpy.data.scenes)
    if not any(core.scene_has_root(s) for s in scenes):
        return
    problems = []
    if not export_settings.get('gltf_extras'):
        problems.append("Include > Custom Properties doit être coché")
    if export_settings.get('gltf_animation_mode') != 'ACTIONS':
        problems.append("Animation > Mode doit être Actions (animations activées)")
    if not export_settings.get('gltf_lights'):
        problems.append("Include > Data > Punctual Lights doit être coché")
    if export_settings.get('gltf_lighting_mode') != 'COMPAT':
        problems.append("Data > Lighting > Lighting Mode doit être Unitless")
    if not problems:
        return
    frame = _state.get("frame")
    if frame is not None:
        try:
            frame[0].frame_set(frame[1])
        except ReferenceError:
            pass
    window = bpy.context.window
    if window is not None:
        window.cursor_set('DEFAULT')
    raise RuntimeError("GLBI, contrôle 9 : export interrompu. " + " ; ".join(problems)
                       + ". Utilise le bouton Exporter du panneau GLBI, qui impose les réglages.")
