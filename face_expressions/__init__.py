bl_info = {
    "name": "Face Expressions",
    "author": "David",
    "version": (1, 0, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar (N) > Expressions",
    "description": ("Pilote un plan texture par sprite sheet (yeux / bouches) : keyframes "
                    "Constant sur le noeud Mapping, reglages memorises par plan"),
    "category": "3D View",
}

import bpy
import bpy.utils.previews
import os
import json
import re


# ===========================================================================
# Vignettes
# Blender ne sait construire une icone qu'a partir d'un fichier : le sprite
# sheet est donc decoupe une fois, et les cellules mises en cache sur disque.
# ===========================================================================
_previews = None            # bpy.utils.previews collection
_thumb_key = ""             # sheet courant charge dans la collection

THUMB_MAX = 128             # cote maximal d'une vignette, en pixels


def _safe_name(text):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text or "sheet").strip("_") or "sheet"


def _thumb_dir(sheet_name, create=False):
    base = bpy.utils.user_resource('DATAFILES', path="face_expressions", create=create)
    path = os.path.join(base, _safe_name(sheet_name))
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def _preview_key(sheet_name, ident):
    return _safe_name(sheet_name) + "/" + ident


def _icon_for(sheet_name, ident):
    """icon_value de la vignette, ou 0 si elle n'existe pas."""
    if _previews is None:
        return 0
    prev = _previews.get(_preview_key(sheet_name, ident))
    return prev.icon_id if prev else 0


def _load_cached_previews(scene):
    """Charge dans la collection les vignettes deja presentes sur disque."""
    global _thumb_key

    if _previews is None:
        return 0

    sheet = scene.expr_name
    folder = _thumb_dir(sheet)
    if not os.path.isdir(folder):
        _thumb_key = ""
        return 0

    count = 0
    for item in scene.expr_items:
        key = _preview_key(sheet, item.ident)
        if key in _previews:
            count += 1
            continue
        path = os.path.join(folder, _safe_name(item.ident) + ".png")
        if os.path.isfile(path):
            _previews.load(key, path, 'IMAGE')
            count += 1

    _thumb_key = sheet if count else ""
    return count


def _srgb_encode(a):
    """Les pixels de Blender sont lineaires ; les vignettes sont ecrites en
    Non-Color, donc l'encodage sRGB doit etre fait ici."""
    import numpy as np
    return np.where(a <= 0.0031308, a * 12.92, 1.055 * np.power(np.clip(a, 0, None), 1/2.4) - 0.055)


def _slice_sheet(scene, report=None):
    """Decoupe le sprite sheet en une image par expression."""
    import numpy as np

    img = scene.expr_image
    if img is None:
        return 0, "Aucune image chargee"
    if img.size[0] == 0 or img.size[1] == 0:
        return 0, "Image vide ou introuvable sur le disque"

    cols = max(1, scene.expr_cols)
    rows = max(1, scene.expr_rows)
    w, h = img.size[0], img.size[1]
    cw, ch = w // cols, h // rows
    if cw < 1 or ch < 1:
        return 0, "Grille incompatible avec la taille de l'image"

    buf = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(buf)
    buf = buf.reshape(h, w, 4)          # ligne 0 = bas de l'image

    step = max(1, int(max(cw, ch) / THUMB_MAX))
    folder = _thumb_dir(scene.expr_name, create=True)
    done = 0

    for item in scene.expr_items:
        x0 = item.col * cw
        y0 = h - (item.row + 1) * ch    # passage du repere haut-gauche au bas-gauche
        cell = buf[y0:y0 + ch, x0:x0 + cw, :][::step, ::step, :]
        if cell.size == 0:
            continue

        cell = cell.copy()
        cell[..., :3] = _srgb_encode(cell[..., :3])

        tmp = bpy.data.images.new("_expr_thumb", width=cell.shape[1], height=cell.shape[0],
                                  alpha=True, float_buffer=False)
        try:
            tmp.colorspace_settings.name = 'Non-Color'
        except Exception:
            pass
        tmp.pixels.foreach_set(cell.ravel())

        path = os.path.join(folder, _safe_name(item.ident) + ".png")
        tmp.filepath_raw = path
        tmp.file_format = 'PNG'
        try:
            tmp.save()
            done += 1
        except Exception as e:
            bpy.data.images.remove(tmp)
            return done, "Ecriture impossible : {}".format(e)
        bpy.data.images.remove(tmp)

    if _previews is not None:
        _previews.clear()
    _load_cached_previews(scene)
    return done, ""


# ===========================================================================
# Donnees
# ===========================================================================
class EXPR_Item(bpy.types.PropertyGroup):
    ident: bpy.props.StringProperty()
    label: bpy.props.StringProperty()
    col: bpy.props.IntProperty()
    row: bpy.props.IntProperty()


# Cache des items d'enumeration : Blender exige que les chaines restent
# referencees en Python, sinon l'interface affiche des caracteres parasites.
_enum_cache = []


def _enum_items(self, context):
    global _enum_cache
    scene = context.scene if context else None
    _enum_cache = []

    if scene:
        sheet = scene.expr_name
        for i, item in enumerate(scene.expr_items):
            icon = _icon_for(sheet, item.ident)
            _enum_cache.append((item.ident, item.label, item.label, icon, i))

    if not _enum_cache:
        _enum_cache = [('NONE', "(aucun sprite sheet charge)", "", 0, 0)]

    return _enum_cache


# ===========================================================================
# Memorisation par plan
# Les reglages vivent sur l'objet lui-meme : en re-selectionnant un plan deja
# configure, le panneau se recharge tout seul, sans repasser par le JSON.
# ===========================================================================
_restoring = False          # evite que la restauration se declenche elle-meme
_last_active = None


def _store_on_object(scene, obj):
    if obj is None:
        return

    data = {
        "source": scene.expr_source,
        "json": scene.expr_json,
        "name": scene.expr_name,
        "cols": scene.expr_cols,
        "rows": scene.expr_rows,
        "image": scene.expr_image.name if scene.expr_image else "",
        "emit": scene.expr_emit,
        "pixel": scene.expr_pixel,
        "frames": [{"id": i.ident, "name": i.label, "col": i.col, "row": i.row}
                   for i in scene.expr_items],
    }
    obj[data_key(scene)] = json.dumps(data)


def _stored_sheet_name(obj):
    """Nom du sprite sheet memorise sur l'objet, lu directement (sans passer
    par l'etat de la scene) : sert de verite de reference dans le panneau."""
    key = data_key()
    if obj is None or key not in obj:
        return ""
    try:
        return json.loads(obj[key]).get("name", "")
    except Exception:
        return ""


def _restore_from_object(scene, obj):
    """Recharge le panneau depuis les reglages memorises sur l'objet."""
    global _restoring
    key = data_key(scene)
    if key not in obj:
        return False

    try:
        data = json.loads(obj[key])
    except Exception:
        return False

    _restoring = True
    try:
        scene.expr_source = data.get("source", 'JSON')
        scene.expr_json = data.get("json", "")
        scene.expr_name = data.get("name", "")
        scene.expr_cols = max(1, int(data.get("cols", 1)))
        scene.expr_rows = max(1, int(data.get("rows", 1)))
        scene.expr_emit = bool(data.get("emit", True))
        scene.expr_pixel = bool(data.get("pixel", False))

        img_name = data.get("image", "")
        scene.expr_image = bpy.data.images.get(img_name) if img_name else None

        scene.expr_items.clear()
        for fr in data.get("frames", []):
            item = scene.expr_items.add()
            item.ident = str(fr.get("id", ""))
            item.label = str(fr.get("name", ""))
            item.col = int(fr.get("col", 0))
            item.row = int(fr.get("row", 0))

        scene.expr_target = obj
    finally:
        _restoring = False

    _load_cached_previews(scene)
    return True


def _sync_active_object():
    """Aligne le panneau sur l'objet actif : cible + reglages memorises."""
    global _last_active, _restoring

    if _restoring:
        return

    try:
        scene = bpy.context.scene
        if scene is None or not scene.expr_follow_selection:
            return
        obj = bpy.context.view_layer.objects.active
    except Exception:
        return

    name = obj.name if obj else None
    if name == _last_active:
        return
    _last_active = name

    if obj is None or obj.type != 'MESH':
        return

    _restoring = True
    try:
        scene.expr_target = obj
    finally:
        _restoring = False

    if data_key(scene) in obj:
        _restore_from_object(scene, obj)


# msgbus : seul mecanisme fiable pour reagir au changement d'objet actif
# (depsgraph_update_post ne se declenche pas sur une simple selection).
_msgbus_owner = object()


def _subscribe_msgbus():
    bpy.msgbus.clear_by_owner(_msgbus_owner)
    bpy.msgbus.subscribe_rna(
        key=(bpy.types.LayerObjects, "active"),
        owner=_msgbus_owner,
        args=(),
        notify=_sync_active_object,
        options={'PERSISTENT'},
    )


# Verification periodique : mecanisme principal, insensible aux evenements
# que msgbus ou le depsgraph peuvent rater selon les versions de Blender.
_POLL_INTERVAL = 0.25


_last_frame = None


def _poll_active():
    global _last_frame
    try:
        _sync_active_object()

        scene = bpy.context.scene
        if scene is not None and scene.frame_current != _last_frame:
            _last_frame = scene.frame_current
            _sync_current_from_material(scene)
    except Exception:
        pass
    return _POLL_INTERVAL


@bpy.app.handlers.persistent
def _on_load(dummy=None):
    # Les abonnements msgbus sont perdus a chaque ouverture de fichier
    global _last_active
    _last_active = None
    global _previews
    _previews = bpy.utils.previews.new()

    _subscribe_msgbus()

    if not bpy.app.timers.is_registered(_poll_active):
        bpy.app.timers.register(_poll_active, first_interval=1.0, persistent=True)


@bpy.app.handlers.persistent
def _on_depsgraph(scene, depsgraph=None):
    # Filet de securite si msgbus rate un evenement
    _sync_active_object()

FACE_ZONES = [('eyes', "Yeux", "Zone des yeux"),
              ('mouth', "Bouche", "Zone de la bouche")]


def current_zone(scene=None):
    scene = scene or bpy.context.scene
    return getattr(scene, "expr_zone", 'eyes')


def zone_material(obj, zone):
    """Materiau FACE_<zone>_* du maillage, sinon le materiau actif."""
    if obj is None or obj.data is None:
        return getattr(obj, "active_material", None)

    prefix = "FACE_" + zone
    for mat in getattr(obj.data, "materials", []):
        if mat is not None and mat.name.startswith(prefix):
            return mat

    return getattr(obj, "active_material", None)


def data_key(scene=None):
    return "expr_data_" + current_zone(scene)

# ===========================================================================
# Materiau
# ===========================================================================
def _find_nodes(obj):
    """Retourne (node_tree, mapping, image_texture) du materiau actif."""
    if obj is None:
        return None, None, None

    mat = zone_material(obj, current_zone())
    if mat is None:
        return None, None, None
    if not mat.use_nodes or mat.node_tree is None:
        return None, None, None

    tree = mat.node_tree
    mapping = tree.nodes.get("EXPR_Mapping")
    if mapping is None or mapping.type != 'MAPPING':
        mapping = next((n for n in tree.nodes if n.type == 'MAPPING'), None)
    tex = next((n for n in tree.nodes if n.type == 'TEX_IMAGE'), None)
    return tree, mapping, tex


def _set_constant(tree, mapping, frame):
    """Force l'interpolation Constant sur les keyframes de Location.
    Sans cela, Blender fait glisser la texture d'une expression a l'autre."""
    ad = tree.animation_data
    if not ad or not ad.action:
        return

    path = 'nodes["{}"].inputs[1].default_value'.format(mapping.name)
    for fcurve in ad.action.fcurves:
        if fcurve.data_path != path:
            continue
        for kp in fcurve.keyframe_points:
            if abs(kp.co.x - frame) < 0.5:
                kp.interpolation = 'CONSTANT'
        fcurve.update()


def _current_item(scene, obj):
    """Expression reellement en place sur le materiau a la frame courante.
    Deduite de la valeur du noeud Mapping, animee ou non."""
    tree, mapping, tex = _find_nodes(obj)
    if mapping is None or not scene.expr_items:
        return None

    cols = max(1, scene.expr_cols)
    rows = max(1, scene.expr_rows)

    try:
        loc = mapping.inputs[1].default_value
        col = int(round(loc[0] * cols))
        row = rows - 1 - int(round(loc[1] * rows))
    except Exception:
        return None

    return next((i for i in scene.expr_items if i.col == col and i.row == row), None)


def _sync_current_from_material(scene):
    """Aligne la liste sur ce qui est affiche, au changement de frame."""
    global _restoring

    if _restoring or not scene.expr_follow_frame:
        return

    obj = scene.expr_target
    item = _current_item(scene, obj)
    if item is None or item.ident == scene.expr_current:
        return

    # Le drapeau evite que l'apercu auto ne reecrive la valeur animee
    _restoring = True
    try:
        scene.expr_current = item.ident
    except Exception:
        pass
    finally:
        _restoring = False


def _apply_expression(scene, obj, ident, keyframe=False):
    """Place la texture sur l'expression demandee.
    Retourne (True, message) ou (False, message d'erreur)."""
    tree, mapping, tex = _find_nodes(obj)
    if tree is None or mapping is None:
        return False, "Materiau non prepare : utiliser 'Preparer le materiau'"

    item = next((i for i in scene.expr_items if i.ident == ident), None)
    if item is None:
        return False, "Aucune expression selectionnee"

    cols = max(1, scene.expr_cols)
    rows = max(1, scene.expr_rows)

    # L'origine UV est en bas a gauche, la grille du sprite sheet en haut a gauche
    mapping.inputs[3].default_value = (1.0 / cols, 1.0 / rows, 1.0)
    mapping.inputs[1].default_value = (item.col / cols, (rows - 1 - item.row) / rows, 0.0)

    if keyframe:
        frame = scene.frame_current
        mapping.inputs[1].keyframe_insert('default_value', frame=frame)
        _set_constant(tree, mapping, frame)
        return True, "{} - keyframe a la frame {}".format(item.label, frame)

    return True, item.label


def _on_current_change(self, context):
    """Apercu immediat au changement d'expression dans la liste."""
    if _restoring:
        return

    scene = context.scene if context else None
    if scene is None or not scene.expr_auto_preview:
        return

    obj = scene.expr_target or (context.active_object if context else None)
    if obj is None:
        return

    _apply_expression(scene, obj, scene.expr_current, keyframe=False)


# ===========================================================================
# Operateurs
# ===========================================================================
class EXPR_OT_load_json(bpy.types.Operator):
    bl_idname = "expr.load_json"
    bl_label = "Charger le JSON"
    bl_description = ("Charge la grille, les noms d'expressions et le sprite sheet "
                      "depuis le fichier JSON du generateur d'expressions")

    def execute(self, context):
        scene = context.scene
        path = bpy.path.abspath(scene.expr_json)

        if not path or not os.path.isfile(path):
            self.report({'ERROR'}, "Fichier JSON introuvable : {}".format(path))
            return {'CANCELLED'}

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self.report({'ERROR'}, "Lecture impossible : {}".format(e))
            return {'CANCELLED'}

        frames = data.get("frames", [])
        if not frames:
            self.report({'ERROR'}, "Aucune expression dans ce fichier")
            return {'CANCELLED'}

        sheet = data.get("sheet", {})
        scene.expr_cols = int(sheet.get("cols", 1)) or 1
        scene.expr_rows = int(sheet.get("rows", 1)) or 1
        scene.expr_name = os.path.basename(path)

        scene.expr_items.clear()
        for fr in frames:
            item = scene.expr_items.add()
            item.ident = str(fr.get("id", ""))
            item.label = str(fr.get("name", fr.get("id", "")))
            item.col = int(fr.get("col", 0))
            item.row = int(fr.get("row", 0))

        # Le JSON reference son sprite sheet : on le charge s'il est a cote
        img_msg = ""
        img_name = data.get("image", "")
        if img_name:
            img_path = os.path.join(os.path.dirname(path), img_name)
            if os.path.isfile(img_path):
                try:
                    scene.expr_image = bpy.data.images.load(img_path, check_existing=True)
                    img_msg = " - image chargee"
                except Exception as e:
                    img_msg = " - image illisible ({})".format(e)
            else:
                img_msg = " - image absente du dossier ({})".format(img_name)

        _load_cached_previews(scene)

        self.report({'INFO'}, "{} expression(s) - grille {}x{}{}".format(
            len(frames), scene.expr_cols, scene.expr_rows, img_msg))
        return {'FINISHED'}


class EXPR_OT_from_image(bpy.types.Operator):
    bl_idname = "expr.from_image"
    bl_label = "Construire depuis l'image"
    bl_description = ("Construit la liste a partir de la grille saisie. Si un JSON du meme "
                      "nom se trouve a cote de l'image, les noms en sont repris")

    def execute(self, context):
        scene = context.scene
        img = scene.expr_image

        if img is None:
            self.report({'ERROR'}, "Aucune image selectionnee")
            return {'CANCELLED'}

        cols = max(1, scene.expr_cols)
        rows = max(1, scene.expr_rows)

        names = {}
        try:
            side = os.path.splitext(bpy.path.abspath(img.filepath))[0] + ".json"
            if os.path.isfile(side):
                with open(side, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for fr in data.get("frames", []):
                    key = (int(fr.get("col", 0)), int(fr.get("row", 0)))
                    names[key] = (str(fr.get("id", "")), str(fr.get("name", "")))
        except Exception:
            names = {}

        scene.expr_items.clear()
        for row in range(rows):
            for col in range(cols):
                ident, label = names.get((col, row), ("", ""))
                item = scene.expr_items.add()
                index = row * cols + col + 1
                item.ident = ident or "cell_{:02d}".format(index)
                item.label = label or "{:02d}  (c{} l{})".format(index, col + 1, row + 1)
                item.col = col
                item.row = row

        scene.expr_name = img.name
        _load_cached_previews(scene)
        msg = "{} cellule(s) - grille {}x{}".format(cols * rows, cols, rows)
        if names:
            msg += " - noms repris du JSON"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class EXPR_OT_setup(bpy.types.Operator):
    bl_idname = "expr.setup"
    bl_label = "Preparer le materiau"
    bl_description = ("Construit le materiau du plan : Image Texture + Mapping cale sur la "
                      "grille, transparence de l'alpha, emission optionnelle")

    def execute(self, context):
        scene = context.scene
        obj = scene.expr_target or context.active_object

        if obj is None or obj.type != 'MESH':
            self.report({'ERROR'}, "Selectionner un plan (mesh) comme cible")
            return {'CANCELLED'}

        # --- Materiau ---
        zone = current_zone(scene)
        mat = zone_material(obj, zone)
        if mat is None:
            mat = bpy.data.materials.new("FACE_{}_{}".format(zone, obj.name))
            obj.data.materials.append(mat)
        mat.use_nodes = True
        tree = mat.node_tree

        # --- Image Texture ---
        tex = next((n for n in tree.nodes if n.type == 'TEX_IMAGE'), None)
        created_chain = False
        if tex is None:
            tex = tree.nodes.new('ShaderNodeTexImage')
            tex.location = (-320, 300)
            created_chain = True

        if scene.expr_image and tex.image is None:
            tex.image = scene.expr_image

        # --- Mapping + Texture Coordinate ---
        mapping = next((n for n in tree.nodes if n.type == 'MAPPING'), None)
        if mapping is None:
            mapping = tree.nodes.new('ShaderNodeMapping')
            mapping.location = (tex.location.x - 220, tex.location.y)

        coord = next((n for n in tree.nodes if n.type == 'TEX_COORD'), None)
        if coord is None:
            coord = tree.nodes.new('ShaderNodeTexCoord')
            coord.location = (mapping.location.x - 220, mapping.location.y)

        if not mapping.inputs['Vector'].is_linked:
            tree.links.new(coord.outputs['UV'], mapping.inputs['Vector'])
        if not tex.inputs['Vector'].is_linked:
            tree.links.new(mapping.outputs['Vector'], tex.inputs['Vector'])

        # --- Chaine de shading, reconstruite seulement si la texture
        # n'alimente encore rien (ne casse pas un materiau fait a la main) ---
        if created_chain or not tex.outputs['Color'].is_linked:
            out = next((n for n in tree.nodes if n.type == 'OUTPUT_MATERIAL'), None)
            if out is None:
                out = tree.nodes.new('ShaderNodeOutputMaterial')
                out.location = (300, 300)

            for n in [n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED']:
                tree.nodes.remove(n)

            if scene.expr_emit:
                shader = tree.nodes.new('ShaderNodeEmission')
                shader.inputs['Strength'].default_value = 1.0
            else:
                shader = tree.nodes.new('ShaderNodeBsdfDiffuse')
            shader.location = (-40, 360)
            tree.links.new(tex.outputs['Color'], shader.inputs['Color'])

            transp = tree.nodes.new('ShaderNodeBsdfTransparent')
            transp.location = (-40, 200)

            mix = tree.nodes.new('ShaderNodeMixShader')
            mix.location = (140, 300)
            tree.links.new(tex.outputs['Alpha'], mix.inputs['Fac'])
            tree.links.new(transp.outputs['BSDF'], mix.inputs[1])
            tree.links.new(shader.outputs[0], mix.inputs[2])
            tree.links.new(mix.outputs['Shader'], out.inputs['Surface'])

        # --- Transparence (noms variables selon la version de Blender) ---
        for attr, value in (("blend_method", 'BLEND'), ("surface_render_method", 'BLENDED')):
            try:
                setattr(mat, attr, value)
            except Exception:
                pass

        # --- Grille du sprite sheet ---
        cols = max(1, scene.expr_cols)
        rows = max(1, scene.expr_rows)
        mapping.inputs[3].default_value = (1.0 / cols, 1.0 / rows, 1.0)

        tex.extension = 'CLIP'
        tex.interpolation = 'Closest' if scene.expr_pixel else 'Linear'

        # Memorise les reglages sur le plan pour les retrouver au prochain clic
        _store_on_object(scene, obj)

        if tex.image is None:
            self.report({'WARNING'}, "Materiau pret, mais aucune image dans le noeud Image Texture")
        else:
            self.report({'INFO'}, "Materiau pret (grille {}x{})".format(cols, rows))
        return {'FINISHED'}


class EXPR_OT_apply(bpy.types.Operator):
    bl_idname = "expr.apply"
    bl_label = "Appliquer"
    bl_description = "Place la texture sur l'expression choisie"

    keyframe: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        scene = context.scene
        obj = scene.expr_target or context.active_object

        ok, msg = _apply_expression(scene, obj, scene.expr_current, keyframe=self.keyframe)
        if not ok:
            self.report({'ERROR'}, msg)
            return {'CANCELLED'}

        self.report({'INFO'}, msg)
        _store_on_object(scene, obj)
        return {'FINISHED'}


class EXPR_OT_thumbs(bpy.types.Operator):
    bl_idname = "expr.thumbs"
    bl_label = "Generer les vignettes"
    bl_description = ("Decoupe le sprite sheet en une vignette par expression et les met "
                      "en cache, pour les afficher a la place des noms")

    def execute(self, context):
        scene = context.scene

        if not scene.expr_items:
            self.report({'ERROR'}, "Aucune expression chargee")
            return {'CANCELLED'}

        done, err = _slice_sheet(scene)
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}

        scene.expr_thumbs_view = True
        self.report({'INFO'}, "{} vignette(s) generee(s)".format(done))
        return {'FINISHED'}


class EXPR_OT_reload(bpy.types.Operator):
    bl_idname = "expr.reload"
    bl_label = "Recharger depuis le plan"
    bl_description = "Recharge les reglages memorises sur le plan selectionne"

    def execute(self, context):
        scene = context.scene
        obj = context.active_object or scene.expr_target

        if obj is None:
            self.report({'ERROR'}, "Aucun objet actif")
            return {'CANCELLED'}

        scene.expr_target = obj

        if data_key(scene) not in obj:
            self.report({'WARNING'}, "{} n'a pas de reglages memorises".format(obj.name))
            return {'CANCELLED'}

        if _restore_from_object(scene, obj):
            self.report({'INFO'}, "Reglages de {} recharges".format(obj.name))
            return {'FINISHED'}

        self.report({'ERROR'}, "Reglages illisibles")
        return {'CANCELLED'}


class EXPR_OT_forget(bpy.types.Operator):
    bl_idname = "expr.forget"
    bl_label = "Oublier ce plan"
    bl_description = "Retire les reglages memorises sur ce plan (le materiau reste intact)"

    def execute(self, context):
        obj = context.scene.expr_target or context.active_object
        key = data_key(context.scene)
        if obj is not None and key in obj:
            del obj[key]
            self.report({'INFO'}, "Reglages oublies pour {}".format(obj.name))
        return {'FINISHED'}


# ===========================================================================
# Panneau
# ===========================================================================
class EXPR_PT_panel(bpy.types.Panel):
    bl_label = "Expressions"
    bl_idname = "EXPR_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Expressions"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        obj = scene.expr_target or context.active_object

        # --- Cible ---
        box = layout.box()
        box.prop(scene, "expr_zone", expand=True)
        box.prop(scene, "expr_follow_selection")
        box.prop(scene, "expr_target", text="Maillage")

        mat = zone_material(obj, current_zone(scene))
        sub = box.row()
        sub.scale_y = 0.7
        if mat is not None:
            sub.label(text=mat.name, icon='MATERIAL')
        else:
            sub.alert = True
            sub.label(text="Aucun materiau de zone", icon='ERROR')

        stored = _stored_sheet_name(obj)
        if obj is not None and data_key(scene) in obj:
            row = box.row()
            row.scale_y = 0.7
            row.label(text="Memorise sur ce plan : " + (stored or "?"), icon='CHECKMARK')
            row = box.row(align=True)
            row.operator("expr.reload", text="Recharger", icon='FILE_REFRESH')
            row.operator("expr.forget", text="Oublier", icon='X')

        if stored and stored != scene.expr_name:
            warn = box.row()
            warn.alert = True
            warn.label(text="Panneau desynchronise : cliquer Recharger", icon='ERROR')

        layout.separator()

        # --- Sprite sheet ---
        box = layout.box()
        box.prop(scene, "expr_source", expand=True)

        if scene.expr_source == 'JSON':
            box.prop(scene, "expr_json", text="")
            box.operator("expr.load_json", icon='FILE_REFRESH')
        else:
            box.template_ID(scene, "expr_image", open="image.open")
            grid = box.row(align=True)
            grid.prop(scene, "expr_cols", text="Colonnes")
            grid.prop(scene, "expr_rows", text="Lignes")
            box.operator("expr.from_image", icon='FILE_REFRESH')

        if scene.expr_items:
            sub = box.column()
            sub.scale_y = 0.7
            sub.label(text="{} : {} expr. - grille {}x{}".format(
                scene.expr_name, len(scene.expr_items),
                scene.expr_cols, scene.expr_rows), icon='CHECKMARK')

            if scene.expr_source == 'JSON':
                if scene.expr_image:
                    sub.label(text=scene.expr_image.name, icon='IMAGE_DATA')
                else:
                    warn = box.row()
                    warn.alert = True
                    warn.label(text="Sprite sheet introuvable a cote du JSON", icon='ERROR')
                    box.template_ID(scene, "expr_image", open="image.open")

        layout.separator()

        # --- Materiau ---
        row = layout.row(align=True)
        row.prop(scene, "expr_emit")
        row.prop(scene, "expr_pixel")
        layout.operator("expr.setup", icon='NODETREE')

        layout.separator()

        # --- Choix de l'expression ---
        tree, mapping, tex = _find_nodes(obj)

        col = layout.column()
        col.enabled = bool(scene.expr_items)

        # Ce qui est reellement affiche a la frame courante
        current = _current_item(scene, obj)
        state = col.box().row()
        if current:
            icon = _icon_for(scene.expr_name, current.ident)
            if icon:
                state.label(text="Frame {} : {}".format(scene.frame_current, current.label),
                            icon_value=icon)
            else:
                state.label(text="Frame {} : {}".format(scene.frame_current, current.label),
                            icon='KEYTYPE_KEYFRAME_VEC')
        else:
            state.label(text="Frame {} : -".format(scene.frame_current), icon='BLANK1')

        row = col.row(align=True)
        row.prop(scene, "expr_auto_preview")
        row.prop(scene, "expr_thumbs_view")
        col.prop(scene, "expr_follow_frame")

        has_thumbs = bool(scene.expr_items) and _icon_for(scene.expr_name,
                                                          scene.expr_items[0].ident)

        if scene.expr_thumbs_view and has_thumbs:
            col.template_icon_view(scene, "expr_current", show_labels=True,
                                   scale=scene.expr_thumbs_scale,
                                   scale_popup=scene.expr_thumbs_scale)
            col.prop(scene, "expr_thumbs_scale")
        else:
            col.prop(scene, "expr_current", text="")

        if scene.expr_items and not has_thumbs:
            col.operator("expr.thumbs", icon='IMAGE_DATA')

        row = col.row(align=True)
        op = row.operator("expr.apply", text="Keyframe", icon='KEYTYPE_KEYFRAME_VEC')
        op.keyframe = True
        if not scene.expr_auto_preview:
            op = row.operator("expr.apply", text="Apercu")
            op.keyframe = False

        if scene.expr_items and mapping is None:
            warn = layout.row()
            warn.alert = True
            warn.label(text="Materiau non prepare", icon='ERROR')


# ===========================================================================
# Enregistrement
# ===========================================================================
classes = (
    EXPR_Item,
    EXPR_OT_load_json,
    EXPR_OT_from_image,
    EXPR_OT_setup,
    EXPR_OT_apply,
    EXPR_OT_thumbs,
    EXPR_OT_reload,
    EXPR_OT_forget,
    EXPR_PT_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    S = bpy.types.Scene
    S.expr_items = bpy.props.CollectionProperty(type=EXPR_Item)
    S.expr_zone = bpy.props.EnumProperty(
        name="Zone", items=FACE_ZONES, default='eyes',
        description="Zone du visage pilotee : chacune a son materiau, sa couche UV "
                    "et son sprite sheet")
    S.expr_json = bpy.props.StringProperty(
        name="Sprite sheet JSON", subtype='FILE_PATH', default="")
    S.expr_name = bpy.props.StringProperty(default="")
    S.expr_cols = bpy.props.IntProperty(default=1, min=1)
    S.expr_rows = bpy.props.IntProperty(default=1, min=1)
    S.expr_current = bpy.props.EnumProperty(
        name="Expression", items=_enum_items, update=_on_current_change)
    S.expr_follow_frame = bpy.props.BoolProperty(
        name="Suivre la timeline", default=True,
        description=("Au changement de frame, replace la liste sur l'expression "
                     "reellement affichee"))
    S.expr_thumbs_view = bpy.props.BoolProperty(
        name="Vignettes", default=True,
        description="Affiche les images des expressions a la place de leurs noms")
    S.expr_thumbs_scale = bpy.props.FloatProperty(
        name="Taille", default=6.0, min=2.0, max=14.0,
        description="Taille des vignettes")
    S.expr_auto_preview = bpy.props.BoolProperty(
        name="Apercu auto", default=True,
        description="Applique l'expression des sa selection dans la liste, sans keyframe")
    S.expr_source = bpy.props.EnumProperty(
        name="Source", default='JSON',
        items=[('JSON', "JSON", "Grille, noms et image lus depuis le fichier JSON"),
               ('IMAGE', "Image", "Sprite sheet seul, grille saisie a la main")])
    S.expr_image = bpy.props.PointerProperty(name="Sprite sheet", type=bpy.types.Image)
    S.expr_target = bpy.props.PointerProperty(
        name="Plan", type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'MESH')
    S.expr_emit = bpy.props.BoolProperty(
        name="Emission", default=True,
        description="Le visage garde sa couleur d'origine, sans etre assombri par l'eclairage")
    S.expr_pixel = bpy.props.BoolProperty(
        name="Pixel (Closest)", default=False,
        description="Interpolation Closest : bords nets")
    S.expr_follow_selection = bpy.props.BoolProperty(
        name="Suivre la selection", default=True,
        description=("En selectionnant un plan deja configure, recharge automatiquement "
                     "son sprite sheet et ses expressions"))

    if _on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)

    _subscribe_msgbus()


def unregister():
    global _previews
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None

    if bpy.app.timers.is_registered(_poll_active):
        bpy.app.timers.unregister(_poll_active)

    bpy.msgbus.clear_by_owner(_msgbus_owner)

    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)

    S = bpy.types.Scene
    for prop in ("expr_follow_frame", "expr_thumbs_scale", "expr_thumbs_view", "expr_auto_preview", "expr_follow_selection", "expr_pixel", "expr_emit", "expr_target",
                 "expr_image", "expr_source", "expr_current", "expr_rows",
                 "expr_cols", "expr_name", "expr_json", "expr_zone", "expr_items"):
        if hasattr(S, prop):
            delattr(S, prop)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
