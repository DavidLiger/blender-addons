import bpy

from . import core


def _poll_armature(self, obj):
    scene = bpy.context.scene
    return obj.type == 'ARMATURE' and scene is not None and obj.name in scene.objects


def _poll_action(self, action):
    return core.is_armature_action(action, self.obj)


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


class GLBG_ActionItem(bpy.types.PropertyGroup):
    ident: bpy.props.StringProperty(name="id", description="Identifiant de l'action (lettres, chiffres, _ et -)")
    obj: bpy.props.PointerProperty(name="Objet", type=bpy.types.Object, poll=_poll_armature)
    action: bpy.props.PointerProperty(name="Clip", type=bpy.types.Action, poll=_poll_action)
    label_fr: bpy.props.StringProperty(name="Libellé FR")
    label_en: bpy.props.StringProperty(name="Libellé EN")
    label_es: bpy.props.StringProperty(name="Libellé ES")
    icon: bpy.props.StringProperty(name="Icône", description="Chemin relatif au dossier du livre (facultatif)")
    sound: bpy.props.StringProperty(name="Son", description="Chemin relatif au dossier du livre (facultatif)")
    loop: bpy.props.BoolProperty(name="Boucle", default=False)


class GLBG_SceneSettings(bpy.types.PropertyGroup):
    scene_id: bpy.props.StringProperty(name="id", description="Nom technique de la scène, égal au nom du fichier GLB")
    name_fr: bpy.props.StringProperty(name="Nom FR")
    name_en: bpy.props.StringProperty(name="Nom EN")
    name_es: bpy.props.StringProperty(name="Nom ES")
    loader_image: bpy.props.StringProperty(name="Image de chargement",
                                           description="Chemin relatif au dossier du livre (facultatif)")
    output_path: bpy.props.StringProperty(name="Fichier GLB", subtype='FILE_PATH',
                                          description="Vide : <dossier du livre>/scenes/<id>.glb")
    idle: bpy.props.PointerProperty(type=GLBG_Idle)
    actions: bpy.props.CollectionProperty(type=GLBG_ActionItem)
    actions_index: bpy.props.IntProperty()
    report: bpy.props.CollectionProperty(type=GLBG_ReportItem)


classes = (GLBG_ReportItem, GLBG_Idle, GLBG_ActionItem, GLBG_SceneSettings)
