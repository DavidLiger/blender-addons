bl_info = {
    "name": "Asset Library",
    "author": "David",
    "version": (1, 0, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar (N) > Assets",
    "description": ("Bibliotheque d'assets .blend : categories et sous-categories "
                    "creees depuis l'interface, vignettes, credits et filtres"),
    "category": "3D View",
}

import bpy
import bpy.utils.previews
import json
import os
import re

from mathutils import Matrix, Vector


LIBRARY = "library"
TREE_FILE = "categories.json"

# Metadonnees portees par chaque asset
K_ERA = "asset_era"
K_STYLE = "asset_style"
K_SRC_ORIGINAL = "src_original"
K_SRC_NAME = "src_name"
K_SRC_AUTHOR = "src_author"
K_SRC_LICENSE = "src_license"
K_SRC_URL = "src_url"

ERAS = [
    ('ANY', "Intemporel", "Utilisable a toutes les epoques"),
    ('MEDIEVAL', "Medieval", ""),
    ('CLASSIQUE', "XVIIe - XVIIIe", ""),
    ('XIX', "XIXe", ""),
    ('1900', "1900 - 1930", ""),
    ('1930', "1930 - 1960", ""),
    ('1960', "1960 - 1990", ""),
    ('MODERNE', "Contemporain", ""),
    ('FUTUR', "Futuriste", ""),
]

SKETCHFAB_LICENSES = [
    ('CC-BY-4.0', "CC BY 4.0", "Attribution"),
    ('CC-BY-SA-4.0', "CC BY-SA 4.0", "Attribution, partage identique"),
    ('CC-BY-ND-4.0', "CC BY-ND 4.0", "Attribution, sans modification"),
    ('CC-BY-NC-4.0', "CC BY-NC 4.0", "Attribution, non commercial"),
    ('CC-BY-NC-SA-4.0', "CC BY-NC-SA 4.0", "Non commercial, partage identique"),
    ('CC-BY-NC-ND-4.0', "CC BY-NC-ND 4.0", "Non commercial, sans modification"),
    ('CC0', "CC0 (domaine public)", "Aucune attribution requise"),
    ('STANDARD', "Sketchfab Standard", "Licence payante du store"),
    ('EDITORIAL', "Editorial", "Usage editorial uniquement"),
]

# Libelles connus, pour que la migration garde les noms francais existants
KNOWN_LABELS = {
    "robot": "Robot", "human": "Humanoide", "archi": "Architecture",
    "urban": "Urbain", "shared": "Commun",
    "body": "Corps", "head": "Tete", "hands": "Mains", "feet": "Chaussures",
    "joints": "Charnieres", "hair": "Cheveux", "hats": "Chapeaux",
    "accessories": "Accessoires", "other": "Autre",
    "torso": "Torse", "arms": "Bras", "legs": "Jambes",
    "door": "Portes", "window": "Fenetres", "roof": "Toitures",
    "gutter": "Gouttieres", "wall": "Murs", "balcony": "Balcons",
    "shop": "Devantures", "chimney": "Cheminees",
    "lamp": "Lampadaires", "pole": "Poteaux", "sidewalk": "Trottoirs",
    "ground": "Sols", "furniture": "Mobilier", "vegetal": "Vegetation",
}


def slugify(text, fallback="categorie"):
    out = re.sub(r"[^A-Za-z0-9_-]+", "-", (text or "").strip().lower())
    return out.strip("-") or fallback


def nice_label(key):
    return KNOWN_LABELS.get(key, key.replace("-", " ").replace("_", " ").capitalize())


# ---------------------------------------------------------------------------
# Racine
# Chaque addon de la suite a sa preference ; celle qui est renseignee sert
# aux autres, pour ne la saisir qu'une fois.
# ---------------------------------------------------------------------------
def get_root():
    for module in (__name__, "robot_maker", "face_expressions"):
        try:
            addon = bpy.context.preferences.addons.get(module)
            if addon is not None and addon.preferences.root:
                return bpy.path.abspath(addon.preferences.root)
        except Exception:
            continue
    return ""


def library_root():
    root = get_root()
    return os.path.join(root, LIBRARY) if root else ""


def resolve_key(key):
    """Suit les raccourcis : une sous-categorie peut pointer le dossier d'une
    autre categorie, ce qui evite de dupliquer les fichiers."""
    parts = key.split("/")
    if len(parts) != 2:
        return key

    cat = find_cat(parts[0])
    if cat is None:
        return key

    sub = next((c for c in cat.get("children", []) if c["key"] == parts[1]), None)
    return sub["from"] if sub and sub.get("from") else key


def is_shortcut(key):
    return resolve_key(key) != key


def folder_of(key, create=False):
    """key = 'categorie/sous-categorie'."""
    base = library_root()
    if not base or not key:
        return ""

    path = os.path.join(base, *resolve_key(key).split("/"))
    if create:
        os.makedirs(path, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Arbre des categories
# ---------------------------------------------------------------------------
_tree = []          # [{key, label, children: [{key, label}]}]


def tree_path():
    base = library_root()
    return os.path.join(base, TREE_FILE) if base else ""


def scan_tree_from_disk():
    """Construit l'arbre a partir des dossiers presents : sert de migration
    au premier lancement, sans rien deplacer."""
    base = library_root()
    if not base or not os.path.isdir(base):
        return []

    out = []
    for cat in sorted(os.listdir(base)):
        cat_dir = os.path.join(base, cat)
        if not os.path.isdir(cat_dir) or cat.startswith("."):
            continue

        children = [{"key": sub, "label": nice_label(sub)}
                    for sub in sorted(os.listdir(cat_dir))
                    if os.path.isdir(os.path.join(cat_dir, sub))
                    and not sub.startswith(".")]

        out.append({"key": cat, "label": nice_label(cat), "children": children})

    return out


def load_tree():
    global _tree

    path = tree_path()
    if path and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                _tree = json.load(f).get("tree", [])
            return _tree
        except Exception:
            pass

    _tree = scan_tree_from_disk()
    if _tree:
        save_tree()
    return _tree


def save_tree():
    path = tree_path()
    if not path:
        return False

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "tree": _tree}, f,
                      ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def find_cat(key):
    return next((c for c in _tree if c["key"] == key), None)


def categories():
    return _tree


# --- enumerations : les chaines doivent rester referencees cote Python ---
_cat_cache = []
_sub_cache = {}


def cat_items(self, context):
    global _cat_cache
    _cat_cache = [(c["key"], c["label"], "") for c in _tree]
    return _cat_cache or [('NONE', "(aucune categorie)", "")]


def sub_items(self, context):
    global _sub_cache

    scene = context.scene if context else None
    cat = find_cat(scene.al_cat) if scene else None
    children = cat.get("children", []) if cat else []

    key = (scene.al_cat if scene else "", tuple(c["key"] for c in children))
    if key not in _sub_cache:
        _sub_cache.clear()
        _sub_cache[key] = ([(c["key"], c["label"], "") for c in children]
                           or [('NONE', "(aucune sous-categorie)", "")])

    return _sub_cache[key]

_scoped_cache = {}


def scoped_cat_items(scope=None):
    """Fabrique une liste de categories limitee a certaines cles."""
    def items(self, context):
        allowed = [c for c in _tree if not scope or c["key"] in scope]
        sig = (tuple(scope or ()), tuple(c["key"] for c in allowed))

        if sig not in _scoped_cache:
            _scoped_cache[sig] = ([(c["key"], c["label"], "") for c in allowed]
                                  or [('NONE', "(aucune categorie)", "")])
        return _scoped_cache[sig]

    return items


def scoped_sub_items(cat_prop):
    """Sous-categories de la categorie designee par cat_prop."""
    def items(self, context):
        scene = context.scene if context else None
        cat = find_cat(getattr(scene, cat_prop, "")) if scene else None
        children = cat.get("children", []) if cat else []
        sig = (cat_prop, getattr(scene, cat_prop, "") if scene else "",
               tuple(c["key"] for c in children))

        if sig not in _scoped_cache:
            _scoped_cache[sig] = ([(c["key"], c["label"], "") for c in children]
                                  or [('NONE', "(aucune sous-categorie)", "")])
        return _scoped_cache[sig]

    return items

def key_of(scene, cat_prop="al_cat", sub_prop="al_sub"):
    cat = getattr(scene, cat_prop, "")
    sub = getattr(scene, sub_prop, "")

    if not cat or cat == 'NONE':
        return ""
    if not sub or sub == 'NONE':
        return cat
    return cat + "/" + sub


# ---------------------------------------------------------------------------
# Index des assets
# ---------------------------------------------------------------------------
_previews = None
_assets = {}        # "famille/categorie" -> [(nom, chemin)]
_meta = {}          # "famille/categorie/nom" -> fiche


def scan(context=None):
    """Relit l'arbre, les assets, leurs fiches et leurs vignettes."""
    global _assets

    load_tree()
    _assets = {}
    _meta.clear()
    if _previews is not None:
        _previews.clear()

    total = 0
    for cat in _tree:
        for sub in cat.get("children", []) or [{"key": ""}]:
            key = cat["key"] + ("/" + sub["key"] if sub["key"] else "")
            folder = folder_of(key)
            items = []

            if os.path.isdir(folder):
                for fname in sorted(os.listdir(folder)):
                    if not fname.lower().endswith(".blend"):
                        continue

                    name = fname[:-6]
                    items.append((name, os.path.join(folder, fname)))

                    try:
                        with open(os.path.join(folder, name + ".json"),
                                  "r", encoding="utf-8") as fh:
                            _meta[key + "/" + name] = json.load(fh)
                    except Exception:
                        pass

                    thumb = os.path.join(folder, name + ".png")
                    if _previews is not None and os.path.isfile(thumb):
                        tag = key + "/" + name
                        if tag not in _previews:
                            _previews.load(tag, thumb, 'IMAGE')

            _assets[key] = items
            total += len(items)

    return total


def assets(key):
    return _assets.get(key, [])


def asset_meta(key, name):
    return _meta.get(key + "/" + name, {})


def icon_of(key, name):
    if _previews is None:
        return 0
    prev = _previews.get(key + "/" + name)
    return prev.icon_id if prev else 0


def path_of(key, name):
    return next((p for n, p in _assets.get(key, []) if n == name), None)


def filtered(scene, key=None):
    """Assets de la categorie donnee, apres recherche, epoque et style."""
    key = key or key_of(scene)
    items = _assets.get(key, [])

    query = scene.al_search.strip().lower()
    era = scene.al_filter_era
    style = scene.al_filter_style.strip().lower()

    out = []
    for name, path in items:
        if query and query not in name.lower():
            continue

        meta = _meta.get(key + "/" + name, {})
        if era != 'ALL' and meta.get("era", 'ANY') not in (era, 'ANY'):
            continue
        if style and style not in str(meta.get("style", "")).lower():
            continue

        out.append((name, path))

    return out


def style_search(self, context, edit_text):
    """Styles deja presents : evite qu'art-deco et artdeco coexistent."""
    seen = {str(m.get("style", "")).strip().lower() for m in _meta.values()}
    seen.discard("")
    return sorted(s for s in seen if edit_text.lower() in s)


def page_count(scene, total):
    per = max(1, scene.al_per_page)
    return max(1, (total + per - 1) // per)


def _reset_page(self, context):
    try:
        context.scene.al_page = 0
    except Exception:
        pass


def _cat_changed(self, context):
    try:
        context.scene.al_page = 0
        cat = find_cat(context.scene.al_cat)
        children = cat.get("children", []) if cat else []
        if children:
            context.scene.al_sub = children[0]["key"]
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Import et vignettes
# ---------------------------------------------------------------------------
def import_blend(path):
    """Importe les objets d'un .blend d'asset. Ils ne sont relies a aucune
    collection : c'est a l'appelant de les placer."""
    if not path or not os.path.isfile(path):
        return []

    try:
        with bpy.data.libraries.load(path, link=False) as (src, dst):
            dst.objects = list(src.objects)
    except Exception:
        return []

    kept = []
    for obj in dst.objects:
        if obj is None:
            continue
        if obj.type in {'CAMERA', 'LIGHT'}:
            bpy.data.objects.remove(obj)
            continue
        kept.append(obj)

    return kept


def _mesh_bounds_world(obj):
    mw = obj.matrix_world
    verts = getattr(obj.data, "vertices", None)

    if verts and len(verts):
        pts = [mw @ v.co for v in verts]
    else:
        pts = [mw @ Vector(c) for c in obj.bound_box]

    lo = Vector((min(p[i] for p in pts) for i in range(3)))
    hi = Vector((max(p[i] for p in pts) for i in range(3)))
    return lo, hi


def _build_thumb_scene(obj, size):
    scn = bpy.data.scenes.new("_al_thumb")
    scn.render.engine = 'BLENDER_WORKBENCH'
    scn.render.resolution_x = size
    scn.render.resolution_y = size
    scn.render.resolution_percentage = 100
    scn.render.film_transparent = True
    scn.render.image_settings.file_format = 'PNG'
    scn.render.image_settings.color_mode = 'RGBA'

    scn.collection.objects.link(obj)
    obj.hide_viewport = False
    obj.hide_render = False
    obj.hide_set(False, view_layer=scn.view_layers[0])

    lo, hi = _mesh_bounds_world(obj)
    center = (lo + hi) / 2.0
    extent = max(hi[i] - lo[i] for i in range(3)) or 1.0

    cam_data = bpy.data.cameras.new("_al_cam")
    cam_data.type = 'ORTHO'
    cam_data.ortho_scale = extent * 1.6
    cam_data.clip_start = 0.001
    cam_data.clip_end = extent * 20.0

    cam = bpy.data.objects.new("_al_cam", cam_data)
    scn.collection.objects.link(cam)
    scn.camera = cam

    direction = Vector((1.0, -1.2, 0.7)).normalized()
    cam.matrix_world = (Matrix.Translation(center + direction * extent * 5.0)
                        @ direction.to_track_quat('Z', 'Y').to_matrix().to_4x4())

    return scn, cam


def _render_thumb(context, scn, path):
    scn.render.filepath = path
    scn.render.use_file_extension = False

    window = context.window
    previous = window.scene if window else None

    try:
        if window is not None:
            window.scene = scn
            bpy.ops.render.render(write_still=True)
        else:
            with context.temp_override(scene=scn):
                bpy.ops.render.render(write_still=True)
    except Exception as e:
        return False, str(e)
    finally:
        if window is not None and previous is not None:
            window.scene = previous

    return os.path.isfile(path), "" if os.path.isfile(path) else "fichier non ecrit"


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------
class AL_Preferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    def _root_changed(self, context):
        try:
            scan(context)
        except Exception:
            pass

    root: bpy.props.StringProperty(
        name="Dossier racine", subtype='DIR_PATH', default="",
        update=_root_changed,
        description="Contient library/. Laisser vide pour reprendre celui "
                    "de Character Maker")

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "root")

        base = library_root()
        row = layout.row()
        row.scale_y = 0.7
        row.label(text=base or "racine non definie",
                  icon='CHECKMARK' if os.path.isdir(base or "") else 'ERROR')


# ---------------------------------------------------------------------------
# Gestion de l'arbre
# ---------------------------------------------------------------------------
class AL_OT_scan(bpy.types.Operator):
    bl_idname = "al.scan"
    bl_label = "Relire la bibliotheque"
    bl_description = "Relit les categories, les assets et les vignettes"

    def execute(self, context):
        if not library_root():
            self.report({'ERROR'}, "Racine non definie (preferences de l'addon)")
            return {'CANCELLED'}

        total = scan(context)
        self.report({'INFO'}, "{} categorie(s) - {} asset(s)".format(
            len(_tree), total))
        return {'FINISHED'}


class AL_OT_cat_add(bpy.types.Operator):
    bl_idname = "al.cat_add"
    bl_label = "Nouvelle categorie"
    bl_description = "Cree une categorie ou une sous-categorie, dossier compris"

    level: bpy.props.EnumProperty(
        items=[('CAT', "Categorie", ""), ('SUB', "Sous-categorie", "")],
        default='CAT', options={'SKIP_SAVE'})
    label: bpy.props.StringProperty(name="Nom", default="")

    def invoke(self, context, event):
        self.label = ""
        return context.window_manager.invoke_props_dialog(self, width=320)

    def draw(self, context):
        self.layout.prop(self, "label")
        if self.level == 'SUB':
            cat = find_cat(context.scene.al_cat)
            row = self.layout.row()
            row.scale_y = 0.7
            row.label(text="Dans : " + (cat["label"] if cat else "?"))

    def execute(self, context):
        scene = context.scene
        key = slugify(self.label)

        if not library_root():
            self.report({'ERROR'}, "Racine non definie")
            return {'CANCELLED'}

        if self.level == 'CAT':
            if find_cat(key):
                self.report({'ERROR'}, "Cette categorie existe deja")
                return {'CANCELLED'}

            _tree.append({"key": key, "label": self.label.strip() or key,
                          "children": []})
            os.makedirs(folder_of(key), exist_ok=True)
        else:
            cat = find_cat(scene.al_cat)
            if cat is None:
                self.report({'ERROR'}, "Choisir d'abord une categorie")
                return {'CANCELLED'}

            children = cat.setdefault("children", [])
            if any(c["key"] == key for c in children):
                self.report({'ERROR'}, "Cette sous-categorie existe deja")
                return {'CANCELLED'}

            children.append({"key": key, "label": self.label.strip() or key})
            os.makedirs(folder_of(cat["key"] + "/" + key), exist_ok=True)

        save_tree()
        scan(context)

        if self.level == 'CAT':
            scene.al_cat = key
        else:
            scene.al_sub = key

        self.report({'INFO'}, "'{}' cree".format(self.label or key))
        return {'FINISHED'}

def _keys_search(self, context, edit_text):
    """Categories et sous-categories reelles, hors raccourcis."""
    out = []
    for cat in _tree:
        out.append(cat["key"])
        for sub in cat.get("children", []):
            if not sub.get("from"):
                out.append(cat["key"] + "/" + sub["key"])

    return sorted(k for k in out if edit_text.lower() in k.lower())

class AL_OT_cat_link(bpy.types.Operator):
    bl_idname = "al.cat_link"
    bl_label = "Ajouter un raccourci"
    bl_description = ("Fait apparaitre ici une sous-categorie rangee ailleurs, "
                      "sans dupliquer les fichiers. Une categorie entiere "
                      "ajoute tous ses enfants d'un coup")

    source: bpy.props.StringProperty(name="Dossier source", default="",
                                     search=_keys_search)
    label: bpy.props.StringProperty(
        name="Nom affiche", default="",
        description="Laisser vide pour reprendre celui de la source")

    def invoke(self, context, event):
        self.source = ""
        self.label = ""
        return context.window_manager.invoke_props_dialog(self, width=340)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "source")
        layout.prop(self, "label")

        cat = find_cat(context.scene.al_cat)
        row = layout.row()
        row.scale_y = 0.7
        row.label(text="Ajoute dans : " + (cat["label"] if cat else "?"))

    def execute(self, context):
        cat = find_cat(context.scene.al_cat)
        if cat is None or not self.source.strip():
            self.report({'ERROR'}, "Choisir une categorie cible et une source")
            return {'CANCELLED'}

        src = self.source.strip().strip("/")
        parts = src.split("/")
        children = cat.setdefault("children", [])

        # Une categorie entiere : on cree un raccourci par enfant
        if len(parts) == 1:
            source_cat = find_cat(parts[0])
            if source_cat is None:
                self.report({'ERROR'}, "Source introuvable")
                return {'CANCELLED'}

            targets = [(parts[0] + "/" + c["key"], c["label"])
                       for c in source_cat.get("children", [])
                       if not c.get("from")]
        else:
            source_cat = find_cat(parts[0])
            sub = next((c for c in (source_cat.get("children", []) if source_cat else [])
                        if c["key"] == parts[1]), None)
            if sub is None:
                self.report({'ERROR'}, "Source introuvable")
                return {'CANCELLED'}
            targets = [(src, self.label.strip() or sub["label"])]

        added = 0
        for full, label in targets:
            key = slugify(full.split("/")[-1])
            if any(c["key"] == key for c in children):
                continue
            children.append({"key": key, "label": label, "from": full})
            added += 1

        if not added:
            self.report({'WARNING'}, "Deja present")
            return {'CANCELLED'}

        save_tree()
        _sub_cache.clear()
        scan(context)

        self.report({'INFO'}, "{} raccourci(s) ajoute(s)".format(added))
        return {'FINISHED'}

class AL_OT_cat_rename(bpy.types.Operator):
    bl_idname = "al.cat_rename"
    bl_label = "Renommer"
    bl_description = "Change le libelle affiche, sans toucher au dossier"

    level: bpy.props.EnumProperty(
        items=[('CAT', "Categorie", ""), ('SUB', "Sous-categorie", "")],
        default='CAT', options={'SKIP_SAVE'})
    label: bpy.props.StringProperty(name="Nouveau nom", default="")

    def invoke(self, context, event):
        scene = context.scene
        cat = find_cat(scene.al_cat)

        if self.level == 'CAT':
            self.label = cat["label"] if cat else ""
        else:
            sub = next((c for c in (cat.get("children", []) if cat else [])
                        if c["key"] == scene.al_sub), None)
            self.label = sub["label"] if sub else ""

        return context.window_manager.invoke_props_dialog(self, width=320)

    def execute(self, context):
        scene = context.scene
        cat = find_cat(scene.al_cat)
        if cat is None or not self.label.strip():
            return {'CANCELLED'}

        if self.level == 'CAT':
            cat["label"] = self.label.strip()
        else:
            sub = next((c for c in cat.get("children", [])
                        if c["key"] == scene.al_sub), None)
            if sub is None:
                return {'CANCELLED'}
            sub["label"] = self.label.strip()

        save_tree()
        _sub_cache.clear()
        return {'FINISHED'}


class AL_OT_cat_remove(bpy.types.Operator):
    bl_idname = "al.cat_remove"
    bl_label = "Retirer de la liste"
    bl_description = ("Retire la categorie du menu. Le dossier et les assets "
                      "restent sur le disque")

    level: bpy.props.EnumProperty(
        items=[('CAT', "Categorie", ""), ('SUB', "Sous-categorie", "")],
        default='CAT', options={'SKIP_SAVE'})

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        scene = context.scene
        cat = find_cat(scene.al_cat)
        if cat is None:
            return {'CANCELLED'}

        if self.level == 'CAT':
            _tree.remove(cat)
        else:
            children = cat.get("children", [])
            sub = next((c for c in children if c["key"] == scene.al_sub), None)
            if sub is None:
                return {'CANCELLED'}
            children.remove(sub)

        save_tree()
        scan(context)
        self.report({'INFO'}, "Retire du menu - dossier conserve")
        return {'FINISHED'}


class AL_OT_open_folder(bpy.types.Operator):
    bl_idname = "al.open_folder"
    bl_label = "Ouvrir le dossier"
    bl_description = "Ouvre le dossier de la categorie dans l'explorateur"

    def execute(self, context):
        path = folder_of(key_of(context.scene), create=True) or library_root()
        if not path or not os.path.isdir(path):
            self.report({'ERROR'}, "Dossier introuvable")
            return {'CANCELLED'}

        bpy.ops.wm.path_open(filepath=path)
        return {'FINISHED'}


class AL_OT_page(bpy.types.Operator):
    bl_idname = "al.page"
    bl_label = "Page"
    bl_description = "Page suivante ou precedente"

    delta: bpy.props.IntProperty(default=1)

    def execute(self, context):
        scene = context.scene
        last = page_count(scene, len(filtered(scene))) - 1
        scene.al_page = max(0, min(last, scene.al_page + self.delta))
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------
class AL_OT_add(bpy.types.Operator):
    bl_idname = "al.add"
    bl_label = "Ajouter a la bibliotheque"
    bl_description = ("Enregistre l'objet selectionne comme asset reutilisable, "
                      "avec sa vignette. L'objet de la scene n'est pas modifie")

    def execute(self, context):
        scene = context.scene

        key = key_of(scene)
        if not key:
            self.report({'ERROR'}, "Choisir une categorie")
            return {'CANCELLED'}

        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            self.report({'ERROR'}, "Selectionner la piece (mesh) a enregistrer")
            return {'CANCELLED'}

        src_name = scene.al_src_name.strip()
        src_author = scene.al_src_author.strip()
        if not scene.al_src_original and not (src_name and src_author):
            self.report({'ERROR'},
                        "Nom original et auteur requis (ou cocher Creation originale)")
            return {'CANCELLED'}

        name = slugify(scene.al_asset_name, "") or slugify(obj.name, "asset")

        folder = folder_of(key, create=True)
        path = os.path.join(folder, name + ".blend")

        if os.path.isfile(path) and not scene.al_overwrite:
            self.report({'ERROR'},
                        "'{}' existe deja (cocher Ecraser)".format(name))
            return {'CANCELLED'}

        # Copie de travail : l'objet de la scene reste intact
        context.view_layer.update()
        world = obj.matrix_world.copy()

        tmp = obj.copy()
        tmp.data = obj.data.copy()
        tmp.name = name
        tmp.parent = None
        tmp.animation_data_clear()

        for k in ("robot", "robot_socket", "robot_tube",
                  "robot_part", "robot_slot", "mirror_of", "mirror_sig"):
            if k in tmp:
                del tmp[k]

        credit = {
            "asset": name,
            "category": key,
            "era": scene.al_era,
            "style": scene.al_style.strip().lower(),
            "original": bool(scene.al_src_original),
            "src_name": "" if scene.al_src_original else src_name,
            "author": "David" if scene.al_src_original else src_author,
            "license": "" if scene.al_src_original else scene.al_src_license,
            "url": "" if scene.al_src_original else scene.al_src_url.strip(),
        }

        tmp[K_ERA] = credit["era"]
        tmp[K_STYLE] = credit["style"]
        tmp[K_SRC_ORIGINAL] = credit["original"]
        tmp[K_SRC_NAME] = credit["src_name"]
        tmp[K_SRC_AUTHOR] = credit["author"]
        tmp[K_SRC_LICENSE] = credit["license"]
        tmp[K_SRC_URL] = credit["url"]

        if scene.al_freeze:
            basis = world.copy()
            basis.translation = Vector((0.0, 0.0, 0.0))
            tmp.data.transform(basis)
            tmp.matrix_world = Matrix.Identity(4)
        else:
            tmp.matrix_world = world

        scn, cam = _build_thumb_scene(tmp, scene.al_thumb_size)
        thumb_ok, thumb_err = _render_thumb(context, scn,
                                            os.path.join(folder, name + ".png"))
        scn.collection.objects.unlink(cam)

        error = ""
        try:
            bpy.data.libraries.write(path, {scn, tmp}, fake_user=True)
        except Exception as e:
            error = str(e)

        mesh = tmp.data
        bpy.data.scenes.remove(scn)
        cam_data = cam.data
        bpy.data.objects.remove(cam)
        bpy.data.cameras.remove(cam_data)
        bpy.data.objects.remove(tmp)
        bpy.data.meshes.remove(mesh)

        if error:
            self.report({'ERROR'}, "Enregistrement impossible : {}".format(error))
            return {'CANCELLED'}

        try:
            with open(os.path.join(folder, name + ".json"), "w",
                      encoding="utf-8") as f:
                json.dump(credit, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.report({'WARNING'}, "Fiche non ecrite : {}".format(e))

        scan(context)
        msg = "'{}' ajoute a {}".format(name, key)
        if not thumb_ok:
            msg += " (vignette : {})".format(thumb_err or "echec")
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class AL_OT_delete(bpy.types.Operator):
    bl_idname = "al.delete"
    bl_label = "Supprimer l'asset"
    bl_description = ("Supprime definitivement cet asset : .blend, vignette et "
                      "fiche. Les pieces deja posees ne sont pas touchees")

    asset: bpy.props.StringProperty()

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        key = key_of(context.scene)
        path = path_of(key, self.asset)

        if path is None:
            self.report({'ERROR'}, "Asset introuvable : relire la bibliotheque")
            return {'CANCELLED'}

        stem = os.path.splitext(path)[0]
        removed = []
        for target in (path, stem + ".png", stem + ".json"):
            if os.path.isfile(target):
                try:
                    os.remove(target)
                    removed.append(os.path.basename(target))
                except Exception as e:
                    self.report({'ERROR'}, "Suppression impossible : {}".format(e))
                    return {'CANCELLED'}

        scan(context)
        context.scene.al_page = 0
        self.report({'INFO'}, "'{}' supprime ({})".format(
            self.asset, ", ".join(removed)))
        return {'FINISHED'}


class AL_OT_place(bpy.types.Operator):
    bl_idname = "al.place"
    bl_label = "Placer au curseur"
    bl_description = "Importe l'asset et le pose au curseur 3D"
    bl_options = {'REGISTER', 'UNDO'}

    asset: bpy.props.StringProperty()
    category: bpy.props.StringProperty(default="")

    def execute(self, context):
        scene = context.scene
        key = self.category or key_of(scene)

        path = path_of(key, self.asset)
        if path is None:
            self.report({'ERROR'}, "Fichier introuvable : relire la bibliotheque")
            return {'CANCELLED'}

        objects = import_blend(path)
        if not objects:
            self.report({'ERROR'}, "Le fichier ne contient aucun objet")
            return {'CANCELLED'}

        for obj in objects:
            context.collection.objects.link(obj)
            obj.matrix_world = (Matrix.Translation(scene.cursor.location)
                                @ obj.matrix_world)

        for o in context.selected_objects:
            o.select_set(False)
        for obj in objects:
            obj.select_set(True)
        context.view_layer.objects.active = objects[0]

        self.report({'INFO'}, "'{}' place".format(self.asset))
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Interface reutilisable
# Character Maker appelle draw_browser en passant son propre operateur, pour
# poser sur un repere au lieu du curseur 3D.
# ---------------------------------------------------------------------------
def draw_categories(layout, context, manage=True,
                    cat_prop="al_cat", sub_prop="al_sub"):
    scene = context.scene

    if not library_root():
        layout.label(text="Racine non definie (preferences)", icon='ERROR')
        return False

    if not _tree:
        col = layout.column(align=True)
        col.label(text="Aucune categorie", icon='INFO')
        col.operator("al.scan", icon='FILE_REFRESH')
        col.operator("al.cat_add", text="Creer une categorie",
                     icon='ADD').level = 'CAT'
        return False

    row = layout.row(align=True)
    row.prop(scene, cat_prop, text="")
    if manage:
        row.operator("al.cat_add", text="", icon='ADD').level = 'CAT'
        row.operator("al.cat_rename", text="", icon='GREASEPENCIL').level = 'CAT'
        row.operator("al.cat_remove", text="", icon='X').level = 'CAT'

    row = layout.row(align=True)
    row.prop(scene, sub_prop, text="")
    if manage:
        row.operator("al.cat_add", text="", icon='ADD').level = 'SUB'
        row.operator("al.cat_link", text="", icon='LINKED')
        row.operator("al.cat_rename", text="", icon='GREASEPENCIL').level = 'SUB'
        row.operator("al.cat_remove", text="", icon='X').level = 'SUB'

    key = key_of(scene, cat_prop, sub_prop)
    if is_shortcut(key):
        sub = layout.row()
        sub.scale_y = 0.7
        sub.label(text="Raccourci vers " + resolve_key(key), icon='LINKED')

    return True


def draw_browser(layout, context, op_idname="al.place", enabled=True, key=None):
    """Grille de vignettes. L'operateur recoit asset et category."""
    scene = context.scene
    key = key or key_of(scene)
    items = filtered(scene, key)
    total = len(_assets.get(key, []))

    if total > scene.al_per_page or scene.al_search:
        layout.prop(scene, "al_search", text="", icon='VIEWZOOM')

    r = layout.row(align=True)
    r.prop(scene, "al_filter_era", text="")
    r.prop(scene, "al_filter_style", text="", icon='SHADERFX')

    if not items:
        layout.label(text="Aucun asset" if not total
                     else "Aucun resultat pour ce filtre", icon='INFO')
        return

    per = max(1, scene.al_per_page)
    pages = page_count(scene, len(items))
    page = min(scene.al_page, pages - 1)
    shown = items[page * per:(page + 1) * per]

    grid = layout.grid_flow(row_major=True, columns=scene.al_columns,
                            even_columns=True, align=False)
    grid.enabled = enabled

    for name, _path in shown:
        cell = grid.box()
        cell.scale_y = 0.9

        icon = icon_of(key, name)
        if icon:
            cell.template_icon(icon_value=icon, scale=scene.al_scale)

        line = cell.row(align=True)
        op = line.operator(op_idname, text=name,
                           icon='IMPORT' if not icon else 'NONE')
        op.asset = name
        if hasattr(op, "category"):
            op.category = key

        if scene.al_edit:
            line.operator("al.delete", text="", icon='TRASH').asset = name

    if pages > 1:
        nav = layout.row(align=True)
        nav.operator("al.page", text="", icon='TRIA_LEFT').delta = -1
        nav.label(text="{} / {}  ({} assets)".format(page + 1, pages, len(items)))
        nav.operator("al.page", text="", icon='TRIA_RIGHT').delta = 1

    r = layout.row(align=True)
    r.prop(scene, "al_columns", text="Colonnes")
    r.prop(scene, "al_scale", text="Taille")
    if total > 12:
        layout.prop(scene, "al_per_page")


def draw_add_panel(layout, context):
    scene = context.scene

    col = layout.column(align=True)
    col.label(text="Ajouter la piece selectionnee :")

    r = col.row(align=True)
    r.prop(scene, "al_asset_name", text="")
    r.prop(scene, "al_overwrite")
    col.prop(scene, "al_freeze")
    col.prop(scene, "al_thumb_size")

    meta = layout.column(align=True)
    meta.prop(scene, "al_era")
    meta.prop(scene, "al_style")

    cred = layout.column(align=True)
    cred.prop(scene, "al_src_original")
    if not scene.al_src_original:
        cred.prop(scene, "al_src_name")
        cred.prop(scene, "al_src_author")
        cred.prop(scene, "al_src_license")
        cred.prop(scene, "al_src_url")

    layout.operator("al.add", icon='EXPORT')


# ---------------------------------------------------------------------------
# Panneau
# ---------------------------------------------------------------------------
class AL_PT_panel(bpy.types.Panel):
    bl_label = "Asset Library"
    bl_idname = "AL_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Assets"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        row = layout.row(align=True)
        row.label(text="Bibliotheque", icon='ASSET_MANAGER')
        row.prop(scene, "al_edit", text="", icon='TRASH', toggle=True)
        row.operator("al.open_folder", text="", icon='FILEBROWSER')
        row.operator("al.scan", text="", icon='FILE_REFRESH')

        box = layout.box()
        if not draw_categories(box, context):
            return

        draw_browser(box, context)

        box = layout.box()
        draw_add_panel(box, context)


# ---------------------------------------------------------------------------
# Enregistrement
# ---------------------------------------------------------------------------
classes = (
    AL_Preferences,
    AL_OT_scan,
    AL_OT_cat_add,
    AL_OT_cat_rename,
    AL_OT_cat_remove,
    AL_OT_open_folder,
    AL_OT_page,
    AL_OT_add,
    AL_OT_delete,
    AL_OT_place,
    AL_PT_panel,
    AL_OT_cat_link,
)


@bpy.app.handlers.persistent
def _on_load(dummy=None):
    try:
        scan(bpy.context)
    except Exception:
        pass


def _deferred_scan():
    try:
        scan(bpy.context)
    except Exception:
        pass
    return None


def register():
    global _previews

    for cls in classes:
        bpy.utils.register_class(cls)

    _previews = bpy.utils.previews.new()

    S = bpy.types.Scene
    S.al_cat = bpy.props.EnumProperty(name="Categorie", items=cat_items,
                                      update=_cat_changed)
    S.al_sub = bpy.props.EnumProperty(name="Sous-categorie", items=sub_items,
                                      update=_reset_page)
    S.al_search = bpy.props.StringProperty(
        name="Rechercher", default="", options={'TEXTEDIT_UPDATE'},
        update=_reset_page)
    S.al_page = bpy.props.IntProperty(name="Page", default=0, min=0)
    S.al_per_page = bpy.props.IntProperty(name="Par page", default=12,
                                          min=3, max=60, update=_reset_page)
    S.al_columns = bpy.props.IntProperty(name="Colonnes", default=3, min=1, max=6)
    S.al_scale = bpy.props.FloatProperty(name="Taille", default=4.0,
                                         min=1.0, max=10.0)
    S.al_edit = bpy.props.BoolProperty(
        name="Mode gestion", default=False,
        description="Affiche un bouton de suppression sur chaque asset")

    S.al_filter_era = bpy.props.EnumProperty(
        name="Epoque", items=[('ALL', "Toutes epoques", "")] + ERAS,
        default='ALL', update=_reset_page)
    S.al_filter_style = bpy.props.StringProperty(
        name="Style", default="", search=style_search, update=_reset_page)

    S.al_asset_name = bpy.props.StringProperty(
        name="Nom", default="",
        description="Vide = nom de l'objet")
    S.al_overwrite = bpy.props.BoolProperty(name="Ecraser", default=False)
    S.al_freeze = bpy.props.BoolProperty(
        name="Figer la transformation", default=True,
        description="Passe rotation et echelle dans la geometrie")
    S.al_thumb_size = bpy.props.IntProperty(
        name="Resolution vignette", default=256, min=64, max=512)

    S.al_era = bpy.props.EnumProperty(name="Epoque", items=ERAS, default='ANY')
    S.al_style = bpy.props.StringProperty(name="Style", default="",
                                          search=style_search)
    S.al_src_original = bpy.props.BoolProperty(
        name="Creation originale", default=False)
    S.al_src_name = bpy.props.StringProperty(name="Nom d'origine", default="")
    S.al_src_author = bpy.props.StringProperty(name="Auteur", default="")
    S.al_src_license = bpy.props.EnumProperty(
        name="Licence", items=SKETCHFAB_LICENSES, default='CC-BY-4.0')
    S.al_src_url = bpy.props.StringProperty(name="Lien", default="")

    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)
    if not bpy.app.timers.is_registered(_deferred_scan):
        bpy.app.timers.register(_deferred_scan, first_interval=0.5)


def unregister():
    global _previews

    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    if bpy.app.timers.is_registered(_deferred_scan):
        bpy.app.timers.unregister(_deferred_scan)

    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None

    S = bpy.types.Scene
    for prop in ("al_src_url", "al_src_license", "al_src_author", "al_src_name",
                 "al_src_original", "al_style", "al_era",
                 "al_thumb_size", "al_freeze", "al_overwrite", "al_asset_name",
                 "al_filter_style", "al_filter_era",
                 "al_edit", "al_scale", "al_columns", "al_per_page",
                 "al_page", "al_search", "al_sub", "al_cat"):
        if hasattr(S, prop):
            delattr(S, prop)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
