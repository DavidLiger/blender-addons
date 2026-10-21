import bpy

from . import core


def _poll_armature(self, obj):
    scene = bpy.context.scene
    return obj.type == 'ARMATURE' and scene is not None and obj.name in scene.objects


def _poll_action(self, action):
    return core.is_armature_action(action, self.obj)


def _poll_target(self, obj):
    scene = bpy.context.scene
    return obj.type == 'EMPTY' and obj.name != core.ROOT_NAME and scene is not None and obj.name in scene.objects


def _search_track(self, context, edit_text):
    """Menu des noms de piste NLA de la scène (les noms par défaut de Blender sont écartés)."""
    scene = context.scene
    if scene is None:
        return []
    return [n for n in core.scene_track_names(scene)
            if not core.is_default_track_name(n) and edit_text.lower() in n.lower()]


_TRACK_DESC = "Nom de la piste NLA (pistes de même nom regroupées en un clip commun à toutes les armatures)"


class GLBG_ReportItem(bpy.types.PropertyGroup):
    code: bpy.props.IntProperty()
    level: bpy.props.EnumProperty(items=(
        ('ERROR', "Erreur", ""),
        ('WARNING', "Avertissement", ""),
        ('INFO', "Information", ""),
    ))
    message: bpy.props.StringProperty()


class GLBG_Idle(bpy.types.PropertyGroup):
    obj: bpy.props.PointerProperty(name="Objet", type=bpy.types.Object, poll=_poll_armature)
    action: bpy.props.PointerProperty(name="Clip", type=bpy.types.Action, poll=_poll_action)
    track: bpy.props.StringProperty(name="Piste", description=_TRACK_DESC, search=_search_track)


class GLBG_ActionItem(bpy.types.PropertyGroup):
    ident: bpy.props.StringProperty(name="id", description="Identifiant de l'action (lettres, chiffres, _ et -)")
    obj: bpy.props.PointerProperty(name="Objet", type=bpy.types.Object, poll=_poll_armature,
                                   description="Armature de l'acteur principal")
    action: bpy.props.PointerProperty(name="Clip", type=bpy.types.Action, poll=_poll_action)
    track: bpy.props.StringProperty(name="Piste", description=_TRACK_DESC, search=_search_track)
    loop: bpy.props.BoolProperty(name="Boucle", default=False)
    cut: bpy.props.BoolProperty(
        name="Coupe au noir",
        description="Passage par le noir à l'entrée et à la sortie de l'action (clip qui commence ou finit loin de "
                    "la pose de repos) ; décoché : fondu de poses",
        default=False)


class GLBG_SceneSettings(bpy.types.PropertyGroup):
    scene_id: bpy.props.StringProperty(name="id", description="Nom technique de la scène, égal au nom du fichier GLB")
    camera_target: bpy.props.PointerProperty(
        name="Cible", type=bpy.types.Object, poll=_poll_target,
        description="Empty visé par la caméra du lecteur (facultatif : sans cible, pas de module caméra)")
    output_path: bpy.props.StringProperty(name="Fichier GLB", subtype='FILE_PATH',
                                          description="Vide : <dossier du livre>/scenes/<id>.glb")
    clip_source: bpy.props.EnumProperty(
        name="Clips",
        items=(
            (core.SOURCE_ACTIONS, "Actions", "Un clip = une Action de l'armature (une seule armature dans la scène)"),
            (core.SOURCE_NLA, "Pistes NLA", "Un clip = un nom de piste NLA, commun à toutes les armatures "
                                            "(obligatoire à partir de deux armatures)"),
        ),
        default=core.SOURCE_ACTIONS,
    )
    idle: bpy.props.PointerProperty(type=GLBG_Idle)
    actions: bpy.props.CollectionProperty(type=GLBG_ActionItem)
    actions_index: bpy.props.IntProperty()
    report: bpy.props.CollectionProperty(type=GLBG_ReportItem)


classes = (GLBG_ReportItem, GLBG_Idle, GLBG_ActionItem, GLBG_SceneSettings)
