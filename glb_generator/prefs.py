import bpy


class GLBG_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    book_root: bpy.props.StringProperty(
        name="Dossier du livre",
        description="Dossier source du livre (celui qui contient book.json) ; "
                    "tous les chemins d'assets sont relatifs à ce dossier",
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
