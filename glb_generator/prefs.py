import os

import bpy


class GLBG_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    book_root: bpy.props.StringProperty(
        name="Dossier du livre",
        description="Repli : dossier source du livre (celui qui contient book.json), utilisé seulement si "
                    "le .blend n'est pas enregistré ou n'est rangé dans aucun livre (<livre>/blender/<id>.blend) ; "
                    "le GLB est écrit dans scenes/<id>.glb sous ce dossier",
        subtype='DIR_PATH',
    )

    def draw(self, context):
        self.layout.prop(self, "book_root")


def get_prefs():
    return bpy.context.preferences.addons[__package__].preferences


BOOK_FILE = "book.json"
BLENDER_DIR = "blender"  # dossier réservé du livre (spec-app-bureau.md §5) : blender/<id>.blend
ORIGIN_BLEND = "BLEND"  # dossier déduit de l'emplacement du .blend
ORIGIN_PREF = "PREF"    # préférence book_root, en repli
ORIGIN_LABELS = {ORIGIN_BLEND: "déduit du .blend", ORIGIN_PREF: "préférence"}
NOT_FOUND_HINT = "enregistrer le .blend dans <livre>/blender/ ou définir la préférence de l'addon"


def find_book_from_blend(blend_path):
    """Premier dossier parent du .blend qui contient book.json, en remontant ; chaîne vide sinon."""
    if not blend_path:
        return ""
    folder = os.path.dirname(os.path.abspath(blend_path))
    while True:
        if os.path.isfile(os.path.join(folder, BOOK_FILE)):
            return folder
        parent = os.path.dirname(folder)
        if parent == folder:
            return ""
        folder = parent


def book_location():
    """(dossier du livre en chemin absolu, origine) ; ("", "") si rien n'est trouvé ni défini.
    Seule source du dossier du livre (DP-2) : déduit du .blend enregistré, sinon la préférence."""
    found = find_book_from_blend(bpy.data.filepath)
    if found:
        return found, ORIGIN_BLEND
    path = get_prefs().book_root.strip()
    if path:
        return bpy.path.abspath(path), ORIGIN_PREF
    return "", ""


def book_root():
    """Dossier du livre en chemin absolu, ou chaîne vide s'il n'est ni déduit du .blend ni défini."""
    return book_location()[0]


def blend_in_book_blender_dir():
    """Vrai si le dossier est déduit d'un .blend rangé directement dans <livre>/blender/."""
    root, origin = book_location()
    if origin != ORIGIN_BLEND:
        return False
    here = os.path.dirname(os.path.abspath(bpy.data.filepath))
    return os.path.normcase(here) == os.path.normcase(os.path.join(root, BLENDER_DIR))


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
