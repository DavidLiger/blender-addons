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


def _poll_camera_position(self, obj):
    """Empties de la scène, sauf GLBI_ROOT et la cible caméra (DP-19)."""
    scene = bpy.context.scene
    return _poll_target(self, obj) and obj != scene.glbg.camera_target


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
    camera_position: bpy.props.PointerProperty(
        name="Position de la caméra", type=bpy.types.Object, poll=_poll_camera_position,
        description="Empty animé où se place la caméra pendant l'action, qui regarde la cible (facultatif : vide, "
                    "caméra libre) ; clés de position dans la piste du clip")


def _clip_role(role):
    return bpy.props.PointerProperty(name=role, type=bpy.types.Action, poll=_poll_action,
                                     description=f"Action jouée pour le rôle de clip « {role} »")


class GLBG_GameEntity(bpy.types.PropertyGroup):
    """Entité du mini-jeu (v0.4, spec-mini-jeu.md §5, §16) : rôle, armature, rôles de clip, capsule."""
    role: bpy.props.EnumProperty(
        name="Rôle",
        items=(("player", "Joueur", "Personnage dirigé par le joueur (idle, walk, run, attack obligatoires)"),
               ("prey", "Proie", "Personnage chassé (idle, walk, caught, et run ou flee obligatoires)")),
        default="player",
    )
    obj: bpy.props.PointerProperty(name="Armature", type=bpy.types.Object, poll=_poll_armature,
                                   description="Armature de l'entité (une seule par entité)")
    clip_idle: _clip_role("idle")
    clip_walk: _clip_role("walk")
    clip_run: _clip_role("run")
    clip_attack: _clip_role("attack")
    clip_caught: _clip_role("caught")
    clip_flee: _clip_role("flee")
    capsule_manual: bpy.props.BoolProperty(
        name="Saisie manuelle",
        description="Rayon et hauteur saisis à la main ; décoché : mesurés sur les maillages à la première image d'idle",
        default=False)
    radius: bpy.props.FloatProperty(name="Rayon", default=0.5, min=0.01, soft_max=2.0, precision=3, unit='LENGTH')
    height: bpy.props.FloatProperty(name="Hauteur", default=1.8, min=0.02, soft_max=5.0, precision=3, unit='LENGTH',
                                    description="Hauteur totale de la capsule (pieds à l'origine sans COLLIDER)")


class GLBG_SceneSettings(bpy.types.PropertyGroup):
    kind: bpy.props.EnumProperty(
        name="Type",
        items=(
            (core.KIND_SCENE, "Scène", "Scène de livre : scenes/<id>.glb"),
            (core.KIND_ENTITY, "Entité de jeu", "Joueur ou proie du mini-jeu : game/<id>.glb"),
            (core.KIND_ARENA, "Arène", "Arène du mini-jeu : game/arena.glb"),
        ),
        default=core.KIND_SCENE,
    )
    game: bpy.props.PointerProperty(type=GLBG_GameEntity)
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


classes = (GLBG_ReportItem, GLBG_Idle, GLBG_ActionItem, GLBG_GameEntity, GLBG_SceneSettings)
