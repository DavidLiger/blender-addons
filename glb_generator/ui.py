import bpy

from . import core, prefs, serialize


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
        root, origin = prefs.book_location()
        box = layout.box()
        if root:
            box.label(text="Livre : " + root, icon='FILE_FOLDER')
            box.label(text="Origine : " + prefs.ORIGIN_LABELS[origin])
        else:
            box.label(text="Dossier du livre non trouvé", icon='ERROR')
            box.label(text=prefs.NOT_FOUND_HINT[0].upper() + prefs.NOT_FOUND_HINT[1:])
        col = box.column()
        col.active = origin != prefs.ORIGIN_BLEND  # préférence sans effet quand le dossier est déduit du .blend
        col.prop(prefs.get_prefs(), "book_root", text="Préférence (repli)")
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
        layout.prop(context.scene.glbg, "scene_id")
        layout.label(text="Nom, image, textes, sons : atelier (scenes/<id>.edit.json)", icon='INFO')


class GLBG_PT_camera(bpy.types.Panel):
    bl_label = "Caméra"
    bl_idname = "GLBG_PT_camera"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = core.TAB
    bl_parent_id = "GLBG_PT_main"

    def draw(self, context):
        layout = self.layout
        layout.prop(context.scene.glbg, "camera_target")
        layout.label(text="Sans cible : pas de module caméra. Cadrage : atelier", icon='INFO')


def _clip_prop(scene):
    """Champ du clip : pointeur d'Action, ou nom de piste NLA (v0.3)."""
    return "track" if scene.glbg.clip_source == core.SOURCE_NLA else "action"


class GLBG_UL_actions(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row()
        row.label(text=item.ident or "(sans id)", icon='PLAY')
        if _clip_prop(context.scene) == "track":
            row.label(text=item.track or "(pas de piste)", icon='NLA')
        else:
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
        clip = _clip_prop(context.scene)

        layout.prop(s, "clip_source", expand=True)
        if clip == "track":
            layout.label(text="Un clip = un nom de piste, commun à toutes les armatures", icon='INFO')

        box = layout.box()
        box.label(text="Repos (idle, en boucle)")
        box.prop(s.idle, "obj")
        box.prop(s.idle, clip)

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
        box.prop(item, "obj", text="Acteur" if clip == "track" else "Objet")
        box.prop(item, clip)
        box.prop(item, "loop")
        box.prop(item, "cut")
        events, _ignored = serialize.marker_events(context.scene)
        evs = events.get(item.ident.strip(), [])
        col = box.column(align=True)
        col.label(text=f"Événements : marqueurs « {item.ident.strip() or '<id>'}:<événement> »", icon='MARKER')
        for event_id, time, frame, _name in evs:
            col.label(text=f"{event_id} — image {frame} — {time:.3f} s".replace(".", ","))
        if not evs:
            col.label(text="(aucun)")


class GLBG_PT_report(bpy.types.Panel):
    bl_label = "Rapport"
    bl_idname = "GLBG_PT_report"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = core.TAB
    bl_parent_id = "GLBG_PT_main"

    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        row.operator("glbg.check", icon='VIEWZOOM')
        row.operator("glbg.apply", icon='FILE_TICK')
        layout.operator("glbg.export", icon='EXPORT')
        layout.operator("glbg.fix_names", icon='SORTALPHA')
        report = context.scene.glbg.report
        if not len(report):
            layout.label(text="Pas encore vérifié")
            return
        col = layout.column(align=True)
        for item in list(report)[:_MAX_LINES]:
            col.label(text=f"{item.code} — {item.message}", icon=_LEVEL_ICONS[item.level])
        if len(report) > _MAX_LINES:
            col.label(text=f"… {len(report) - _MAX_LINES} ligne(s) de plus")


_MAX_LINES = 40
_LEVEL_ICONS = {'ERROR': 'ERROR', 'WARNING': 'INFO', 'INFO': 'BLANK1'}
