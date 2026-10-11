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
        s = context.scene.glbg
        layout.prop(s, "output_path")
        out = prefs.output_path(s)
        layout.label(text="→ " + (out or "(dossier du livre ou id manquant)"))


class GLBG_PT_scene(bpy.types.Panel):
    bl_label = "Scène"
    bl_idname = "GLBG_PT_scene"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = core.TAB
    bl_parent_id = "GLBG_PT_main"

    def draw(self, context):
        layout = self.layout
        s = context.scene.glbg
        layout.prop(s, "scene_id")
        col = layout.column(align=True)
        col.prop(s, "name_fr")
        col.prop(s, "name_en")
        col.prop(s, "name_es")
        row = layout.row(align=True)
        row.prop(s, "loader_image")
        op = row.operator("glbg.pick_asset", text="", icon='FILEBROWSER')
        op.target = 'LOADER'


class GLBG_UL_actions(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row()
        row.label(text=item.ident or "(sans id)", icon='PLAY')
        row.label(text=item.action.name if item.action else "(pas de clip)")


class GLBG_PT_animations(bpy.types.Panel):
    bl_label = "Animations"
    bl_idname = "GLBG_PT_animations"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = core.TAB
    bl_parent_id = "GLBG_PT_main"

    def draw(self, context):
        layout = self.layout
        s = context.scene.glbg

        box = layout.box()
        box.label(text="Repos (idle, en boucle)")
        box.prop(s.idle, "obj")
        box.prop(s.idle, "action")

        layout.label(text="Actions (boutons)")
        row = layout.row()
        row.template_list("GLBG_UL_actions", "", s, "actions", s, "actions_index", rows=3)
        col = row.column(align=True)
        col.operator("glbg.action_add", text="", icon='ADD')
        col.operator("glbg.action_remove", text="", icon='REMOVE')
        col.separator()
        col.operator("glbg.action_move", text="", icon='TRIA_UP').direction = 'UP'
        col.operator("glbg.action_move", text="", icon='TRIA_DOWN').direction = 'DOWN'

        if not 0 <= s.actions_index < len(s.actions):
            return
        item = s.actions[s.actions_index]
        box = layout.box()
        box.prop(item, "ident")
        box.prop(item, "obj")
        box.prop(item, "action")
        col = box.column(align=True)
        col.prop(item, "label_fr")
        col.prop(item, "label_en")
        col.prop(item, "label_es")
        for field, target in (("icon", 'ICON'), ("sound", 'SOUND')):
            row = box.row(align=True)
            row.prop(item, field)
            op = row.operator("glbg.pick_asset", text="", icon='FILEBROWSER')
            op.target = target
            op.index = s.actions_index
        box.prop(item, "loop")
