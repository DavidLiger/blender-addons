bl_info = {
    "name": "GLB Generator",
    "author": "David",
    "version": (0, 1, 0),
    "blender": (4, 5, 0),
    "location": "View3D > Sidebar (N) > BD > GLBI — GLB Generator",
    "description": "Configuration GLBInterpreter d'une scène de livre (GLBI_ROOT) et export GLB",
    "category": "Import-Export",
}

if "bpy" in locals():
    import importlib
    for _m in (core, prefs, ops, ui):
        importlib.reload(_m)
else:
    from . import core, prefs, ops, ui

import bpy

classes = (
    prefs.GLBG_Preferences,
    ops.GLBG_OT_root_ensure,
    ui.GLBG_PT_main,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
