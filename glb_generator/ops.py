import os

import bpy

from . import checks, core, gltf_hook, prefs, serialize, verify


class GLBG_OT_root_ensure(bpy.types.Operator):
    """Crée l'empty GLBI_ROOT dans la scène, ou le répare"""
    bl_idname = "glbg.root_ensure"
    bl_label = "Créer / réparer GLBI_ROOT"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        cands = core.root_candidates()
        if len(cands) > 1:
            self.report({'ERROR'}, "Plusieurs GLBI_ROOT dans le .blend : "
                        + ", ".join(o.name for o in cands) + ". Supprime les doublons.")
            return {'CANCELLED'}
        if not cands:
            root = bpy.data.objects.new(core.ROOT_NAME, None)
            root.empty_display_type = 'PLAIN_AXES'
            root.empty_display_size = 0.5
            scene.collection.objects.link(root)
            self.report({'INFO'}, "GLBI_ROOT créé")
            return {'FINISHED'}
        root = cands[0]
        if root.type != 'EMPTY':
            self.report({'ERROR'}, f"« {root.name} » n'est pas un empty : renomme-le, puis relance")
            return {'CANCELLED'}
        fixed = []
        if root.name != core.ROOT_NAME:
            root.name = core.ROOT_NAME
            fixed.append("renommé")
        if root.name not in scene.objects:
            scene.collection.objects.link(root)
            fixed.append("relié à la scène")
        self.report({'INFO'}, "GLBI_ROOT " + (", ".join(fixed) if fixed else "déjà en place"))
        return {'FINISHED'}


def _unique_armature(scene):
    arms = core.scene_armatures(scene)
    return arms[0] if len(arms) == 1 else None


class GLBG_OT_action_add(bpy.types.Operator):
    """Ajoute une action (un bouton du lecteur)"""
    bl_idname = "glbg.action_add"
    bl_label = "Ajouter une action"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.glbg
        arm = s.idle.obj or _unique_armature(context.scene)
        if s.idle.obj is None and arm is not None:
            s.idle.obj = arm
        item = s.actions.add()
        item.obj = arm
        s.actions_index = len(s.actions) - 1
        return {'FINISHED'}


class GLBG_OT_action_remove(bpy.types.Operator):
    """Supprime l'action sélectionnée"""
    bl_idname = "glbg.action_remove"
    bl_label = "Supprimer l'action"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        s = context.scene.glbg
        return 0 <= s.actions_index < len(s.actions)

    def execute(self, context):
        s = context.scene.glbg
        s.actions.remove(s.actions_index)
        s.actions_index = min(s.actions_index, len(s.actions) - 1)
        return {'FINISHED'}


class GLBG_OT_action_move(bpy.types.Operator):
    """Monte ou descend l'action sélectionnée"""
    bl_idname = "glbg.action_move"
    bl_label = "Déplacer l'action"
    bl_options = {'REGISTER', 'UNDO'}

    direction: bpy.props.EnumProperty(items=(('UP', "Monter", ""), ('DOWN', "Descendre", "")))

    @classmethod
    def poll(cls, context):
        s = context.scene.glbg
        return 0 <= s.actions_index < len(s.actions)

    def execute(self, context):
        s = context.scene.glbg
        i = s.actions_index
        j = i - 1 if self.direction == 'UP' else i + 1
        if not 0 <= j < len(s.actions):
            return {'CANCELLED'}
        s.actions.move(i, j)
        s.actions_index = j
        return {'FINISHED'}


def get_root(scene):
    """GLBI_ROOT utilisable (unique, empty, dans la scène) ou None."""
    cands = core.root_candidates()
    if len(cands) == 1 and cands[0].name == core.ROOT_NAME and cands[0].type == 'EMPTY' \
            and cands[0].name in scene.objects:
        return cands[0]
    return None


class GLBG_OT_apply(bpy.types.Operator):
    """Écrit glbi_version et glbi_config sur GLBI_ROOT"""
    bl_idname = "glbg.apply"
    bl_label = "Appliquer"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        return apply(self, context.scene)


def _verify(op, scene):
    """Lance les contrôles, remplit le rapport ; renvoie le nombre d'erreurs."""
    results = checks.run_checks(scene)
    checks.store_report(scene, results)
    n_err = len(checks.errors(results))
    n_warn = sum(1 for r in results if r[1] == checks.WARNING)
    op.report({'ERROR'} if n_err else {'INFO'},
              f"{n_err} erreur(s), {n_warn} avertissement(s) — détail dans le panneau Rapport")
    return n_err


def apply(op, scene):
    """Contrôles puis écriture sur GLBI_ROOT, seulement si aucune erreur (spec addon §3)."""
    if _verify(op, scene):
        return {'CANCELLED'}
    serialize.write_root(get_root(scene), scene)
    op.report({'INFO'}, "glbi_config écrit sur GLBI_ROOT")
    return {'FINISHED'}


class GLBG_OT_check(bpy.types.Operator):
    """Lance les contrôles et affiche le rapport"""
    bl_idname = "glbg.check"
    bl_label = "Vérifier"

    def execute(self, context):
        _verify(self, context.scene)
        return {'FINISHED'}


class GLBG_OT_fix_names(bpy.types.Operator):
    """Remplace les caractères interdits par _ dans les noms des objets de la scène et des Actions déclarées, sans jamais créer de collision"""
    bl_idname = "glbg.fix_names"
    bl_label = "Corriger les noms"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        roots = set(core.root_candidates())
        objs = [o for o in scene.objects if o not in roots]
        acts = [a for a in dict.fromkeys(a for _, _, a in checks._declared(scene.glbg)) if a is not None]
        renamed, skipped = [], []
        for ids, pool in ((objs, bpy.data.objects), (acts, bpy.data.actions)):
            for id_block in ids:
                old = id_block.name
                if checks.BLENDER_NAME_RE.fullmatch(old):
                    continue
                new = checks.normalize_name(old)
                if pool.get(new) is not None:
                    skipped.append(f"{old} (→ {new} existe déjà)")
                    continue
                id_block.name = new
                renamed.append(f"{old} → {new}")
        for line in renamed:
            print("GLBG renommé :", line)
        for line in skipped:
            print("GLBG non renommé :", line)
        msg = f"{len(renamed)} renommé(s)"
        if skipped:
            msg += f", {len(skipped)} non renommé(s) : " + " ; ".join(skipped)
        self.report({'WARNING'} if skipped else {'INFO'}, msg)
        _verify(self, scene)
        return {'FINISHED'}


# Réglages imposés quand l'addon exporte lui-même (spec addon §6).
EXPORT_SETTINGS = dict(
    export_format='GLB',
    export_extras=True,
    export_animations=True,
    export_animation_mode='ACTIONS',
    export_anim_single_armature=True,
    use_active_scene=True,
    use_selection=False,
    use_visible=False,
    use_renderable=False,
    use_active_collection=False,
    export_lights=True,                              # D-8.10 : l'éclairage de la scène vient du GLB
    export_import_convert_lighting_mode='SPEC',      # « Standard » : unités physiques glTF
)


_POSE_CHANNELS = ("location", "rotation_quaternion", "rotation_euler", "rotation_axis_angle", "scale")


def _snapshot_poses(scene):
    """Pose de chaque armature de la scène (canaux bruts de chaque os) et image courante."""
    poses = []
    for ob in core.scene_armatures(scene):
        bones = {pb.name: (pb.rotation_mode, tuple(tuple(getattr(pb, c)) for c in _POSE_CHANNELS))
                 for pb in ob.pose.bones}
        poses.append((ob, bones))
    return scene.frame_current, poses


def _restore_poses(scene, snapshot):
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


class GLBG_OT_export(bpy.types.Operator):
    """Contrôles, écriture de glbi_config, puis export GLB aux réglages imposés"""
    bl_idname = "glbg.export"
    bl_label = "Exporter le GLB"

    def execute(self, context):
        scene = context.scene
        out = prefs.output_path(scene.glbg)
        if not out:
            self.report({'ERROR'}, "Fichier GLB non défini : dossier du livre ou id de scène manquant")
            return {'CANCELLED'}
        if apply(self, scene) != {'FINISHED'}:
            return {'CANCELLED'}
        config_text = get_root(scene)["glbi_config"]
        clips = serialize.declared_clips(scene)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        snapshot = _snapshot_poses(scene)
        gltf_hook.set_clip_filter(clips)
        try:
            result = bpy.ops.export_scene.gltf(filepath=out, **EXPORT_SETTINGS)
        finally:
            gltf_hook.set_clip_filter(None)
            _restore_poses(scene, snapshot)
        if 'FINISHED' not in result:
            self.report({'ERROR'}, "L'exportateur glTF a échoué (voir la console)")
            return {'CANCELLED'}
        problems = verify.verify_glb(out, clips, config_text, len(core.scene_lights(scene)))
        report = scene.glbg.report
        for message in problems:
            item = report.add()
            item.code, item.level, item.message = 0, 'ERROR', "GLB exporté : " + message
        if problems:
            self.report({'ERROR'}, f"GLB écrit mais non conforme ({len(problems)} écart(s)) : {out}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"GLB exporté et vérifié ({len(clips)} clip(s)) : {out}")
        return {'FINISHED'}
