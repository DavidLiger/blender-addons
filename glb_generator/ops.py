import os

import bpy

from . import checks, core, prefs, serialize


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


_PICK_FILTERS = {
    'LOADER': "*.webp;*.png;*.jpg;*.jpeg;*.avif",
    'ICON': "*.webp;*.png;*.jpg;*.jpeg;*.avif",
    'SOUND': "*.mp3;*.ogg;*.opus;*.m4a;*.aac;*.wav",
}


class GLBG_OT_pick_asset(bpy.types.Operator):
    """Choisit un fichier sous le dossier du livre et écrit son chemin relatif"""
    bl_idname = "glbg.pick_asset"
    bl_label = "Choisir un fichier"
    bl_options = {'REGISTER', 'UNDO'}

    target: bpy.props.EnumProperty(items=(
        ('LOADER', "Image de chargement", ""), ('ICON', "Icône", ""), ('SOUND', "Son", "")),
        options={'SKIP_SAVE'})
    index: bpy.props.IntProperty(default=-1, options={'SKIP_SAVE'})
    filepath: bpy.props.StringProperty(subtype='FILE_PATH', options={'SKIP_SAVE'})
    filter_glob: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        root = prefs.book_root()
        if not root:
            self.report({'ERROR'}, "Dossier du livre non défini (préférences de l'addon)")
            return {'CANCELLED'}
        self.filter_glob = _PICK_FILTERS[self.target]
        self.filepath = os.path.join(root, "")
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        root = prefs.book_root()
        if not root:
            self.report({'ERROR'}, "Dossier du livre non défini (préférences de l'addon)")
            return {'CANCELLED'}
        try:
            rel = os.path.relpath(bpy.path.abspath(self.filepath), root)
        except ValueError:  # autre lecteur sous Windows
            rel = ".."
        if rel.startswith("..") or os.path.isabs(rel):
            self.report({'ERROR'}, "Le fichier doit être sous le dossier du livre")
            return {'CANCELLED'}
        rel = rel.replace(os.sep, "/")
        s = context.scene.glbg
        if self.target == 'LOADER':
            s.loader_image = rel
        else:
            if not 0 <= self.index < len(s.actions):
                return {'CANCELLED'}
            setattr(s.actions[self.index], "icon" if self.target == 'ICON' else "sound", rel)
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
