import bpy

from . import core, prefs


class GLBG_PT_main(bpy.types.Panel):
    bl_label = "GLBI — GLB Generator"
    bl_idname = "GLBG_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = core.TAB

    def draw(self, context):
        layout = self.layout
        cands = core.root_candidates()
        row = layout.row()
        if len(cands) == 1 and cands[0].name == core.ROOT_NAME and cands[0].type == 'EMPTY' \
                and cands[0].name in context.scene.objects:
            row.label(text="GLBI_ROOT en place", icon='CHECKMARK')
        elif not cands:
            row.label(text="Pas de GLBI_ROOT", icon='ERROR')
        else:
            row.label(text=f"GLBI_ROOT à réparer ({len(cands)})", icon='ERROR')
        row.operator("glbg.root_ensure", text="", icon='EMPTY_AXIS')
        layout.prop(prefs.get_prefs(), "book_root")
