import bpy

from . import core


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
