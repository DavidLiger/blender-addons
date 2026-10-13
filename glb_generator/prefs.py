import os

import bpy


class GLBG_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    book_root: bpy.props.StringProperty(
        name="Dossier du livre",
        description="Dossier source du livre (celui qui contient book.json) ; "
                    "le GLB est écrit dans scenes/<id>.glb sous ce dossier",
        subtype='DIR_PATH',
    )

    def draw(self, context):
        self.layout.prop(self, "book_root")


def get_prefs():
    return bpy.context.preferences.addons[__package__].preferences


def book_root():
    """Dossier du livre en chemin absolu, ou chaîne vide s'il n'est pas défini."""
    path = get_prefs().book_root.strip()
    return bpy.path.abspath(path) if path else ""


def default_output_path(scene_id):
    root = book_root()
    sid = scene_id.strip()
    if not root or not sid:
        return ""
    return os.path.join(root, "scenes", sid + ".glb")


def output_path(settings):
    """Fichier GLB de sortie : le champ s'il est rempli, sinon <dossier du livre>/scenes/<id>.glb."""
    path = settings.output_path.strip()
    return bpy.path.abspath(path) if path else default_output_path(settings.scene_id)
