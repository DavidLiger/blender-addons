bl_info = {
    "name": "GLB Generator",
    "author": "David",
    "version": (0, 1, 0),
    "blender": (4, 5, 0),
    "location": "View3D > Sidebar (N) > BD > GLBI — GLB Generator",
    "description": "Configuration GLBInterpreter d'une scène de livre (GLBI_ROOT) et export GLB",
    "category": "Import-Export",
}

# Rechargement (F3 > Reload Scripts) : recharge aussi les sous-modules.
_reloading = "bpy" in locals()
from . import core, prefs, props, serialize, checks, ops, ui
if _reloading:
    import importlib
    for _m in (core, prefs, props, serialize, checks, ops, ui):
        importlib.reload(_m)

import bpy

classes = (
    *props.classes,
    prefs.GLBG_Preferences,
    ops.GLBG_OT_root_ensure,
    ops.GLBG_OT_action_add,
    ops.GLBG_OT_action_remove,
    ops.GLBG_OT_action_move,
    ops.GLBG_OT_pick_asset,
    ops.GLBG_OT_apply,
    ops.GLBG_OT_check,
    ops.GLBG_OT_fix_names,
    ui.GLBG_PT_main,
    ui.GLBG_PT_scene,
    ui.GLBG_UL_actions,
    ui.GLBG_PT_animations,
    ui.GLBG_PT_report,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.glbg = bpy.props.PointerProperty(type=props.GLBG_SceneSettings)


def unregister():
    del bpy.types.Scene.glbg
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
