bl_info = {
    "name": "Face Expressions",
    "author": "David",
    "version": (1, 0, 1),
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
import base64
import posixpath
import threading
import urllib.parse
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import time

# ===========================================================================
# Serveur local
# Sert expression-maker/ et le dossier des personnages, et accepte l'ecriture
# de fichiers. Le navigateur n'a donc besoin d'aucune permission disque.
# ===========================================================================
_server = None
_server_thread = None
_ctx = {"serial": 0}        # contexte demande par le crayon
_last_poll = 0.0            # derniere interrogation de la page

CREATIONS = "creations"
# La page vit dans l'addon : une seule copie, versionnee avec le code
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


def maker_root():
    """Racine ROBOTS, reprise de Robot Maker si l'addon est installe."""
    try:
        prefs = bpy.context.preferences.addons[__name__].preferences
        if prefs.root:
            return bpy.path.abspath(prefs.root)
    except Exception:
        pass

    try:
        addon = bpy.context.preferences.addons.get("robot_maker")
        if addon and addon.preferences.root:
            return bpy.path.abspath(addon.preferences.root)
    except Exception:
        pass

    return ""


def _server_port():
    try:
        return bpy.context.preferences.addons[__name__].preferences.port
    except Exception:
        return 8777


class _ExprHandler(SimpleHTTPRequestHandler):
    root = ""

    def log_message(self, fmt, *args):
        pass                                   # console Blender deja bavarde

    def _under_root(self, relative):
        """Chemin absolu, refuse s'il sort de la racine."""
        base = os.path.normpath(self.root)
        target = os.path.normpath(os.path.join(base, relative.replace("/", os.sep)))
        return target if target.startswith(base) else None

    def translate_path(self, path):
        clean = posixpath.normpath(urllib.parse.urlparse(path).path).lstrip("/")
        return os.path.join(WEB_DIR, clean.replace("/", os.sep))

    def do_GET(self):
        global _last_poll
        parsed = urllib.parse.urlparse(self.path)

        # La page ouverte interroge cette route : elle se met a jour sans
        # qu'un nouvel onglet soit necessaire
        if parsed.path == "/context":
            _last_poll = time.time()
            self._json(_ctx)
            return

        # /files/<chemin sous creations/> : assets et sprite sheets
        # /open?path=... : ouvre le dossier dans l'explorateur
        if parsed.path == "/open":
            rel = urllib.parse.parse_qs(parsed.query).get("path", [""])[0]
            target = self._under_root(os.path.join(CREATIONS, rel))
            if target is None:
                self._json({"ok": False, "error": "chemin refuse"}, 403)
                return
            try:
                os.makedirs(target, exist_ok=True)
                os.startfile(target)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
                return
            self._json({"ok": True})
            return

        if parsed.path.startswith("/files/"):
            target = self._under_root(os.path.join(
                CREATIONS, posixpath.normpath(parsed.path[7:]).lstrip("/")))
            if target is None or not os.path.isfile(target):
                self.send_error(404)
                return

            with open(target, "rb") as f:
                payload = f.read()

            self.send_response(200)
            self.send_header("Content-Type", self.guess_type(target))
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
            return

        # /list/<chemin> : contenu d'un dossier, en JSON
        # /robots : personnages disponibles
        if parsed.path == "/robots":
            base = os.path.join(self.root, CREATIONS)
            names = []
            if os.path.isdir(base):
                names = sorted(n for n in os.listdir(base)
                               if os.path.isdir(os.path.join(base, n))
                               and not n.startswith("_"))
            self._json({"robots": names})
            return

        if parsed.path.startswith("/list/"):
            target = self._under_root(os.path.join(
                CREATIONS, posixpath.normpath(parsed.path[6:]).lstrip("/")))
            if target is None:
                self.send_error(403)
                return

            if not os.path.isdir(target):
                self._json({"files": []})     # dossier absent : liste vide
                return

            names = sorted(f for f in os.listdir(target)
                           if os.path.isfile(os.path.join(target, f)))
            self._json({"files": names})
            return

        SimpleHTTPRequestHandler.do_GET(self)

    def do_POST(self):
        route = urllib.parse.urlparse(self.path).path
        if route not in ("/save", "/delete"):
            self.send_error(404)
            return

        try:
            size = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(size).decode("utf-8"))
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, 400)
            return

        target = self._under_root(os.path.join(CREATIONS, data.get("path", "")))
        if target is None:
            self._json({"ok": False, "error": "chemin refuse"}, 403)
            return

        if route == "/delete":
            try:
                if os.path.isfile(target):
                    os.remove(target)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
                return
            self._json({"ok": True, "path": target})
            return

        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if "b64" in data:
                payload = base64.b64decode(data["b64"].split(",")[-1])
                with open(target, "wb") as f:
                    f.write(payload)
            else:
                with open(target, "w", encoding="utf-8") as f:
                    f.write(data.get("text", ""))
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, 500)
            return

        self._json({"ok": True, "path": target})

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_server():
    """Demarre le serveur si la racine existe. Retourne (ok, message)."""
    global _server, _server_thread

    if _server is not None:
        return True, "deja demarre"

    root = maker_root()
    if not root or not os.path.isdir(root):
        return False, "racine ROBOTS introuvable"

    _ExprHandler.root = root
    port = _server_port()

    try:
        _server = ThreadingHTTPServer(("127.0.0.1", port), _ExprHandler)
    except Exception as e:
        _server = None
        return False, "port {} indisponible ({})".format(port, e)

    _server_thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _server_thread.start()
    return True, "http://127.0.0.1:{}".format(port)


def stop_server():
    global _server, _server_thread

    if _server is not None:
        try:
            _server.shutdown()
            _server.server_close()
        except Exception:
            pass
    _server = None
    _server_thread = None


def robot_from_path(path):
    """Nom du personnage deduit d'un chemin sous creations/<perso>/..."""
    parts = os.path.normpath(bpy.path.abspath(path or "")).split(os.sep)
    if CREATIONS in parts:
        index = parts.index(CREATIONS)
        if index + 1 < len(parts):
            return parts[index + 1]
    return ""


class EXPR_Preferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    root: bpy.props.StringProperty(
        name="Dossier ROBOTS", subtype='DIR_PATH', default="",
        description="Laisser vide pour reprendre celui de Robot Maker")
    port: bpy.props.IntProperty(
        name="Port du serveur local", default=8777, min=1024, max=65535)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "root")
        layout.prop(self, "port")

        row = layout.row()
        row.scale_y = 0.7
        row.label(text="Serveur : " + ("actif" if _server is not None else "arrete"),
                  icon='CHECKMARK' if _server is not None else 'DOT')

def _head_hex(obj):
    """Base Color du materiau de zone, en hexadecimal sRGB."""
    tree, mapping, tex = _find_nodes(obj)
    if tree is None:
        return ""

    node = next((n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if node is None:
        return ""

    def enc(v):
        v = max(0.0, min(1.0, v))
        v = v * 12.92 if v <= 0.0031308 else 1.055 * (v ** (1 / 2.4)) - 0.055
        return int(round(v * 255))

    c = node.inputs["Base Color"].default_value
    return "#{:02X}{:02X}{:02X}".format(enc(c[0]), enc(c[1]), enc(c[2]))


class EXPR_OT_open_editor(bpy.types.Operator):
    bl_idname = "expr.open_editor"
    bl_label = "Editer les expressions"
    bl_description = ("Ouvre expressions.html dans le navigateur, sur le sprite sheet "
                      "du personnage. Les enregistrements reviennent directement "
                      "dans son dossier")

    expression: bpy.props.StringProperty(default="", options={'SKIP_SAVE'})

    def execute(self, context):
        scene = context.scene

        ok, msg = start_server()
        if not ok:
            self.report({'ERROR'}, "Serveur local : {}".format(msg))
            return {'CANCELLED'}

        page = os.path.join(WEB_DIR, "expressions.html")
        if not os.path.isfile(page):
            self.report({'ERROR'}, "expressions.html absent de {}".format(WEB_DIR))
            return {'CANCELLED'}

        global _ctx

        current = self.expression or scene.expr_current
        params = {
            "robot": robot_from_path(scene.expr_json),
            "zone": current_zone(scene),
            "sheet": os.path.basename(bpy.path.abspath(scene.expr_json or "")),
            "expr": current if current and current != 'NONE' else "",
            "head": _head_hex(scene.expr_target or context.active_object),
        }

        _ctx = dict(params, serial=_ctx.get("serial", 0) + 1)

        # Une page qui interroge encore le serveur est consideree ouverte :
        # elle se mettra a jour d'elle-meme, pas besoin d'un nouvel onglet
        if time.time() - _last_poll < 4.0:
            self.report({'INFO'}, "Onglet mis a jour : {} / {}".format(
                params["zone"], params["expr"] or "-"))
            return {'FINISHED'}

        url = "http://127.0.0.1:{}/expressions.html?{}".format(
            _server_port(), urllib.parse.urlencode(
                {k: v for k, v in params.items() if v}))

        webbrowser.open(url)
        self.report({'INFO'}, url)
        return {'FINISHED'}


# ===========================================================================
# Vignettes
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
    return _safe_name(sheet_name) + "/" + _safe_name(ident)


def _icon_for(sheet_name, ident):
    """icon_value de la vignette, ou 0 si elle n'existe pas."""
    global _previews
    if _previews is None:
        return 0
    prev = _previews.get(_preview_key(sheet_name, ident))
    return prev.icon_id if prev else 0


def _load_cached_previews(scene):
    """Charge dans la collection les vignettes deja presentes sur disque."""
    global _previews, _thumb_key

    if _previews is None:
        return 0

    sheet = scene.expr_name
    if not sheet:
        return 0

    folder = _thumb_dir(sheet, create=False)
    if not folder or not os.path.isdir(folder):
        _thumb_key = ""
        return 0

    on_disk = {}
    for fname in os.listdir(folder):
        if fname.lower().endswith(".png"):
            on_disk[os.path.splitext(fname)[0]] = os.path.join(folder, fname)

    count = 0
    for item in scene.expr_items:
        key = _preview_key(sheet, item.ident)
        if key in _previews:
            count += 1
            continue

        path = on_disk.get(_safe_name(item.ident)) or on_disk.get(item.ident)
        if path and os.path.isfile(path):
            try:
                _previews.load(key, path, 'IMAGE')
                count += 1
            except Exception:
                pass

    _thumb_key = sheet if count else ""
    return count


def _srgb_encode(a):
    import numpy as np
    return np.where(a <= 0.0031308, a * 12.92, 1.055 * np.power(np.clip(a, 0, None), 1/2.4) - 0.055)


def _thumbs_outdated(scene):
    """Vrai si les vignettes manquent ou datent d'avant la planche."""
    if not scene.expr_items:
        return False

    if not any(_icon_for(scene.expr_name, i.ident) for i in scene.expr_items):
        return True

    img = scene.expr_image
    if img is None:
        return False

    folder = _thumb_dir(scene.expr_name)
    if not os.path.isdir(folder):
        return True

    try:
        source = os.path.getmtime(bpy.path.abspath(img.filepath))
        newest = max(os.path.getmtime(os.path.join(folder, f))
                     for f in os.listdir(folder) if f.lower().endswith(".png"))
    except Exception:
        return False

    return source > newest + 1.0


def _slice_sheet(scene, report=None):
    """Decoupe le sprite sheet en une image par expression."""
    global _previews
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

    try:
        img.reload()
    except Exception:
        pass

    buf = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(buf)
    buf = buf.reshape(h, w, 4)

    step = max(1, int(max(cw, ch) / THUMB_MAX))
    folder = _thumb_dir(scene.expr_name, create=True)
    done = 0

    for item in scene.expr_items:
        x0 = item.col * cw
        y0 = h - (item.row + 1) * ch
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


_enum_cache = []
_enum_sig = None
_enum_strings = []      # garde une reference sur les chaines remises a Blender


def _enum_items(self, context):
    """Blender ne copie pas les chaines des enums dynamiques : la liste doit
    rester identique entre deux redessins, sinon l'affichage se corrompt."""
    global _enum_cache, _enum_sig, _thumb_key

    scene = context.scene if context else None
    if scene is None:
        return [('NONE', "(aucun sprite sheet charge)", "", 0, 0)]

    sheet = scene.expr_name
    if sheet and _thumb_key != sheet:
        _load_cached_previews(scene)

    icons = tuple(_icon_for(sheet, i.ident) for i in scene.expr_items)
    sig = (sheet, tuple(i.ident for i in scene.expr_items), icons)

    if sig == _enum_sig and _enum_cache:
        return _enum_cache

    items = []
    for index, item in enumerate(scene.expr_items):
        ident = str(item.ident)
        label = str(item.label)
        _enum_strings.append((ident, label))
        items.append((ident, label, label, icons[index], index))

    if not items:
        items = [('NONE', "(aucun sprite sheet charge)", "", 0, 0)]

    # Evite une croissance sans fin au fil des changements de sheet
    if len(_enum_strings) > 4000:
        del _enum_strings[:2000]

    _enum_cache = items
    _enum_sig = sig
    return _enum_cache


# ===========================================================================
# Memorisation par plan
# ===========================================================================
_restoring = False
_last_active = None


def current_zone(scene=None):
    scene = scene or bpy.context.scene
    return getattr(scene, "expr_zone", 'eyes')


def data_key(scene=None):
    return "expr_data_" + current_zone(scene)


def zone_material(obj, zone):
    if obj is None or obj.data is None:
        return getattr(obj, "active_material", None)

    prefix = "FACE_" + zone
    mats = list(getattr(obj.data, "materials", []))

    # Un doublon .001 peut trainer sans porter aucune face : on privilegie
    # celui qui est reellement assigne a de la geometrie
    used = set()
    for poly in getattr(obj.data, "polygons", []):
        used.add(poly.material_index)

    for index, mat in enumerate(mats):
        if mat is not None and mat.name.startswith(prefix) and index in used:
            return mat

    for mat in mats:
        if mat is not None and mat.name.startswith(prefix):
            return mat

    # Pas de repli sur le materiau actif : on ecraserait celui d'un objet
    # quelconque selectionne dans la scene
    return None


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
    key = data_key()
    if obj is None or key not in obj:
        return ""
    try:
        return json.loads(obj[key]).get("name", "")
    except Exception:
        return ""


def _restore_from_object(scene, obj):
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

def _zone_json_folder(obj):
    """Dossier des sprite sheets, deduit d'une zone deja configuree."""
    for zone, _label, _desc in FACE_ZONES:
        raw = obj.get("expr_data_" + zone)
        if not raw:
            continue
        try:
            path = bpy.path.abspath(json.loads(raw).get("json", ""))
        except Exception:
            continue
        if path and os.path.isdir(os.path.dirname(path)):
            return os.path.dirname(path)
    return ""


def robot_of(obj):
    """Personnage auquel appartient ce maillage : par son materiau de zone,
    sinon par sa collection ROBOT_<nom>_NN."""
    if obj is None:
        return ""

    for mat in (getattr(obj.data, "materials", None) or []):
        if mat is not None and mat.name.startswith("FACE_"):
            parts = mat.name.split("_", 2)
            if len(parts) == 3:
                return parts[2]

    for coll in obj.users_collection:
        if coll.name.startswith("ROBOT_"):
            return re.sub(r"_\d+$", "", coll.name[len("ROBOT_"):])

    return ""


def expressions_dir(robot):
    root = maker_root()
    if not root or not robot:
        return ""
    return os.path.join(root, CREATIONS, robot, "expressions")


def find_sheet(robot, zone):
    """JSON de cette zone dans le dossier du personnage."""
    folder = expressions_dir(robot)
    if not folder or not os.path.isdir(folder):
        return ""

    # style.json, overrides.json et extras.json ne sont pas des planches
    skip = {"style.json", "overrides.json", "extras.json", "removed.json"}

    match = next((f for f in sorted(os.listdir(folder))
                  if f.lower().endswith(".json")
                  and f.lower() not in skip
                  and zone in f.lower()), None)
    return os.path.join(folder, match) if match else ""


def clear_sheet(scene):
    """Vide le panneau : le personnage courant n'a pas de sprite sheet."""
    global _restoring
    _restoring = True
    try:
        scene.expr_items.clear()
        scene.expr_name = ""
        scene.expr_json = ""
        scene.expr_image = None
        scene.expr_fit_w = 1.0
        scene.expr_fit_h = 1.0
        scene.expr_off_x = 0.0
        scene.expr_off_y = 0.0
    finally:
        _restoring = False


def _auto_load_zone(scene, obj):
    """Met le panneau a jour pour la zone courante, sans intervention."""
    if obj is None:
        return False

    # Le personnage se deduit de l'objet, pas du JSON encore charge
    path = find_sheet(robot_of(obj), current_zone(scene))

    # Plus de planche sur disque : on repart d'un panneau vierge
    if not path and robot_of(obj):
        clear_sheet(scene)
        return False

    key = data_key(scene)
    same = (os.path.normcase(os.path.normpath(bpy.path.abspath(scene.expr_json)))
            == os.path.normcase(os.path.normpath(path))) if path else False

    if key in obj and same:
        _fit_read(scene, obj)
        if _stored_sheet_name(obj) == scene.expr_name:
            _sync_current_from_material(scene, force=True)
            return True                      # deja en place

        ok = _restore_from_object(scene, obj)
        _sync_current_from_material(scene, force=True)
        return ok
    if not path:
        clear_sheet(scene)
        return False

    scene.expr_json = path
    scene.expr_target = obj
    try:
        bpy.ops.expr.load_json()
    except Exception:
        return False

    # Le cadrage vit dans le materiau de la zone : il doit etre relu a
    # chaque bascule, sinon les curseurs gardent ceux de la zone precedente
    _fit_read(scene, obj)
    return True


def _on_zone_change(self, context):
    if _restoring or context is None:
        return
    scene = context.scene
    try:
        _auto_load_zone(scene, scene.expr_target or context.active_object)
    except Exception:
        pass

def _sync_active_object():
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

    # Seuls les maillages portant un materiau de visage sont concernes
    if not any(zone_material(obj, z) for z, _l, _d in FACE_ZONES):
        return

    _restoring = True
    try:
        scene.expr_target = obj
    finally:
        _restoring = False

    _auto_load_zone(scene, obj)


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
    global _last_active, _previews, _thumb_key
    _last_active = None
    _thumb_key = ""

    if _previews is not None:
        _previews.clear()
    else:
        _previews = bpy.utils.previews.new()

    _subscribe_msgbus()

    if not bpy.app.timers.is_registered(_poll_active):
        bpy.app.timers.register(_poll_active, first_interval=1.0, persistent=True)


@bpy.app.handlers.persistent
def _on_depsgraph(scene, depsgraph=None):
    _sync_active_object()


FACE_ZONES = [('eyes', "Yeux", "Zone des yeux"),
              ('mouth', "Bouche", "Zone de la bouche")]


# ===========================================================================
# Materiau
# ===========================================================================
def _find_nodes_zone(obj, zone):
    if obj is None:
        return None, None, None

    mat = zone_material(obj, zone)
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return None, None, None

    tree = mat.node_tree
    mapping = tree.nodes.get("EXPR_Mapping")
    if mapping is None or mapping.type != 'MAPPING':
        mapping = next((n for n in tree.nodes if n.type == 'MAPPING'), None)
    tex = next((n for n in tree.nodes if n.type == 'TEX_IMAGE'), None)
    return tree, mapping, tex


def _find_nodes(obj):
    if obj is None:
        return None, None, None

    return _find_nodes_zone(obj, current_zone())


def _adjust_node(tree, create=False):
    """Noeud de cadrage, insere avant celui des cellules. Il agit sur
    l'ensemble du sprite sans interferer avec le choix de l'expression."""
    node = tree.nodes.get("FACE_Adjust")
    if node is not None or not create:
        return node

    cell = tree.nodes.get("EXPR_Mapping")
    if cell is None:
        return None

    node = tree.nodes.new('ShaderNodeMapping')
    node.name = "FACE_Adjust"
    node.label = "Cadrage"
    node.location = (cell.location.x - 240, cell.location.y - 170)

    if cell.inputs['Vector'].is_linked:
        tree.links.new(cell.inputs['Vector'].links[0].from_socket,
                       node.inputs['Vector'])
    tree.links.new(node.outputs['Vector'], cell.inputs['Vector'])
    return node


def _fit_update(self, context):
    if _restoring:
        return                       # lecture en cours : ne pas reecrire

    scene = context.scene
    tree, mapping, tex = _find_nodes(scene.expr_target or context.active_object)
    if tree is None:
        return

    node = _adjust_node(tree, create=True)
    if node is None:
        return

    node.inputs[3].default_value = (1.0 / max(.05, scene.expr_fit_w),
                                    1.0 / max(.05, scene.expr_fit_h), 1.0)
    node.inputs[1].default_value = (-scene.expr_off_x, -scene.expr_off_y, 0.0)


def _fit_read(scene, obj):
    """Recopie le cadrage du materiau dans les curseurs."""
    global _restoring
    tree, mapping, tex = _find_nodes(obj)
    node = _adjust_node(tree) if tree is not None else None
    if node is None:
        return

    _restoring = True
    try:
        sc = node.inputs[3].default_value
        lo = node.inputs[1].default_value
        scene.expr_fit_w = 1.0 / max(.05, sc[0])
        scene.expr_fit_h = 1.0 / max(.05, sc[1])
        scene.expr_off_x = -lo[0]
        scene.expr_off_y = -lo[1]
    finally:
        _restoring = False


class EXPR_OT_fit_reset(bpy.types.Operator):
    bl_idname = "expr.fit_reset"
    bl_label = "Recadrer"
    bl_description = "Remet le sprite a sa taille et sa position d'origine"

    def execute(self, context):
        scene = context.scene
        scene.expr_fit_w = 1.0
        scene.expr_fit_h = 1.0
        scene.expr_off_x = 0.0
        scene.expr_off_y = 0.0
        _fit_update(self, context)
        return {'FINISHED'}


def _set_constant(tree, mapping, frame):
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


def _sync_current_from_material(scene, force=False):
    global _restoring

    if _restoring or (not force and not scene.expr_follow_frame):
        return

    obj = scene.expr_target
    item = _current_item(scene, obj)
    if item is None or item.ident == scene.expr_current:
        return

    _restoring = True
    try:
        scene.expr_current = item.ident
    except Exception:
        pass
    finally:
        _restoring = False


def _apply_expression(scene, obj, ident, keyframe=False):
    tree, mapping, tex = _find_nodes(obj)
    if tree is None or mapping is None:
        return False, "Materiau non prepare : utiliser 'Preparer le materiau'"

    item = next((i for i in scene.expr_items if i.ident == ident), None)
    if item is None:
        return False, "Aucune expression selectionnee"

    cols = max(1, scene.expr_cols)
    rows = max(1, scene.expr_rows)

    mapping.inputs[3].default_value = (1.0 / cols, 1.0 / rows, 1.0)
    mapping.inputs[1].default_value = (item.col / cols, (rows - 1 - item.row) / rows, 0.0)

    if keyframe:
        frame = scene.frame_current
        mapping.inputs[1].keyframe_insert('default_value', frame=frame)
        _set_constant(tree, mapping, frame)
        return True, "{} - keyframe a la frame {}".format(item.label, frame)

    return True, item.label


def _on_current_change(self, context):
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
    bl_label = "Recharger les planches"
    bl_description = ("Relit le sprite sheet du personnage : a utiliser apres une "
                      "modification dans l'editeur")

    both: bpy.props.BoolProperty(default=True, options={'SKIP_SAVE'})

    def execute(self, context):
        global _restoring
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
        # Deux personnages peuvent avoir des planches de meme nom : la cle du
        # cache de vignettes doit les distinguer
        owner = robot_from_path(path)
        scene.expr_name = (owner + "_" if owner else "") + os.path.basename(path)

        scene.expr_items.clear()
        for fr in frames:
            item = scene.expr_items.add()
            item.ident = str(fr.get("id", ""))
            item.label = str(fr.get("name", fr.get("id", "")))
            item.col = int(fr.get("col", 0))
            item.row = int(fr.get("row", 0))

        # La valeur memorisee peut pointer une expression absente du nouveau sheet
        # Remise a une valeur valide, sans declencher l'apercu automatique :
        # il ecraserait la valeur animee que le realignement doit relire
        _restoring = True
        try:
            scene.expr_current = scene.expr_items[0].ident
        except Exception:
            pass
        finally:
            _restoring = False

        img_msg = ""
        img_name = data.get("image", "")
        if img_name:
            img_path = os.path.join(os.path.dirname(path), img_name)
            if os.path.isfile(img_path):
                try:
                    scene.expr_image = bpy.data.images.load(img_path, check_existing=True)
                    # Le datablock peut dater d'un export precedent
                    scene.expr_image.reload()
                    img_msg = " - image rechargee"
                except Exception as e:
                    img_msg = " - image illisible ({})".format(e)
            else:
                img_msg = " - image absente du dossier ({})".format(img_name)

        # Realigne le materiau sur l'image qu'on vient de recharger
        tree, mapping, tex = _find_nodes(scene.expr_target)
        if tex is not None and scene.expr_image is not None:
            tex.image = scene.expr_image

        _load_cached_previews(scene)

        # Vignettes absentes ou plus anciennes que la planche : on redecoupe
        if scene.expr_image is not None and _thumbs_outdated(scene):
            _previews.clear()
            _slice_sheet(scene)

        _sync_current_from_material(scene, force=True)

        # Le sprite sheet de l'autre zone est relu aussi

        # Planche jamais decoupee : on genere les vignettes maintenant
        if scene.expr_image is not None and not any(
                _icon_for(scene.expr_name, i.ident) for i in scene.expr_items):
            _slice_sheet(scene)

        # La liste se cale sur ce que le materiau affiche a la frame courante
        _sync_current_from_material(scene, force=True)

        # Le sprite sheet de l'autre zone est relu aussi : une modification
        # dans l'editeur touche souvent les deux
        if self.both:
            other = 'mouth' if current_zone(scene) == 'eyes' else 'eyes'
            path2 = find_sheet(robot_of(scene.expr_target), other)
            if path2:
                img2 = None
                try:
                    with open(path2, "r", encoding="utf-8") as f:
                        name2 = json.load(f).get("image", "")
                    img_path2 = os.path.join(os.path.dirname(path2), name2)
                    if name2 and os.path.isfile(img_path2):
                        img2 = bpy.data.images.load(img_path2, check_existing=True)
                        img2.reload()
                except Exception:
                    pass

                tree2, map2, tex2 = _find_nodes_zone(scene.expr_target, other)
                if tex2 is not None and img2 is not None:
                    tex2.image = img2

        self.report({'INFO'}, "{} expression(s) - grille {}x{}{}".format(
            len(frames), scene.expr_cols, scene.expr_rows, img_msg))
        return {'FINISHED'}


class EXPR_OT_from_image(bpy.types.Operator):
    bl_idname = "expr.from_image"
    bl_label = "Construire depuis l'image"

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

        owner = robot_from_path(img.filepath)
        scene.expr_name = (owner + "_" if owner else "") + img.name
        _load_cached_previews(scene)
        msg = "{} cellule(s) - grille {}x{}".format(cols * rows, cols, rows)
        if names:
            msg += " - noms repris du JSON"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class EXPR_OT_setup(bpy.types.Operator):
    bl_idname = "expr.setup"
    bl_label = "Preparer le materiau"

    def execute(self, context):
        scene = context.scene
        obj = scene.expr_target or context.active_object

        if obj is None or obj.type != 'MESH':
            self.report({'ERROR'}, "Selectionner un plan (mesh) comme cible")
            return {'CANCELLED'}

        zone = current_zone(scene)
        mat = zone_material(obj, zone)
        if mat is None:
            mat = bpy.data.materials.new("FACE_{}_{}".format(zone, obj.name))
            obj.data.materials.append(mat)
        mat.use_nodes = True
        tree = mat.node_tree

        tex = next((n for n in tree.nodes if n.type == 'TEX_IMAGE'), None)
        created_chain = False
        if tex is None:
            tex = tree.nodes.new('ShaderNodeTexImage')
            tex.location = (-320, 300)
            created_chain = True

        if scene.expr_image:
            # Le noeud peut pointer un doublon du meme fichier : on le realigne
            if tex.image is not scene.expr_image:
                tex.image = scene.expr_image
            try:
                tex.image.reload()
            except Exception:
                pass

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

        for attr, value in (("blend_method", 'BLEND'), ("surface_render_method", 'BLENDED')):
            try:
                setattr(mat, attr, value)
            except Exception:
                pass

        cols = max(1, scene.expr_cols)
        rows = max(1, scene.expr_rows)
        mapping.inputs[3].default_value = (1.0 / cols, 1.0 / rows, 1.0)

        tex.extension = 'CLIP'
        tex.interpolation = 'Closest' if scene.expr_pixel else 'Linear'

        _store_on_object(scene, obj)

        if tex.image is None:
            self.report({'WARNING'}, "Materiau pret, mais aucune image dans le noeud Image Texture")
        else:
            self.report({'INFO'}, "Materiau pret (grille {}x{})".format(cols, rows))
        return {'FINISHED'}


class EXPR_OT_apply(bpy.types.Operator):
    bl_idname = "expr.apply"
    bl_label = "Appliquer"
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
        folder = _thumb_dir(scene.expr_name)
        on_disk = len([f for f in os.listdir(folder)
                       if f.lower().endswith(".png")]) if os.path.isdir(folder) else -1
        loaded = sum(1 for i in scene.expr_items if _icon_for(scene.expr_name, i.ident))
        self.report({'INFO'}, "{} generee(s) - {} chargee(s)".format(done, loaded))
        return {'FINISHED'}


class EXPR_OT_reload(bpy.types.Operator):
    bl_idname = "expr.reload"
    bl_label = "Recharger depuis le plan"

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
class EXPR_OT_init_sheets(bpy.types.Operator):
    bl_idname = "expr.init_sheets"
    bl_label = "Creer les sprite sheets"
    bl_description = ("Ouvre l'editeur, qui genere les deux planches du personnage "
                      "a partir de la reference par defaut")

    def execute(self, context):
        scene = context.scene
        obj = scene.expr_target or context.active_object
        robot = robot_of(obj)

        if not robot:
            self.report({'ERROR'}, "Personnage non identifie : le maillage doit etre "
                                   "dans une collection ROBOT_ ou porter un materiau FACE_")
            return {'CANCELLED'}

        ok, msg = start_server()
        if not ok:
            self.report({'ERROR'}, "Serveur local : {}".format(msg))
            return {'CANCELLED'}

        try:
            os.makedirs(expressions_dir(robot), exist_ok=True)
        except Exception as e:
            self.report({'ERROR'}, "Dossier impossible : {}".format(e))
            return {'CANCELLED'}

        url = "http://127.0.0.1:{}/expressions.html?{}".format(
            _server_port(), urllib.parse.urlencode(
                {"robot": robot, "zone": current_zone(scene),
                 "head": _head_hex(obj), "init": "1"}))

        webbrowser.open(url)
        self.report({'INFO'}, "Generation en cours dans le navigateur, "
                              "puis Charger le JSON")
        return {'FINISHED'}


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
            doubles = [m for m in (obj.data.materials if obj else [])
                       if m is not None and m.name.startswith("FACE_" + current_zone(scene))]
            sub.label(text=mat.name
                      + ("  ({} slots)".format(len(doubles)) if len(doubles) > 1 else ""),
                      icon='MATERIAL' if len(doubles) < 2 else 'ERROR')
        else:
            sub.alert = True
            sub.label(text="Aucun materiau de zone", icon='ERROR')

        if obj is not None and data_key(scene) in obj:
            row = box.row(align=True)
            row.scale_y = 0.7
            row.label(text=_stored_sheet_name(obj) or "?", icon='CHECKMARK')
            row.operator("expr.forget", text="", icon='X')

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

        if not scene.expr_items:
            robot = robot_of(obj)
            info = box.column(align=True)

            if robot:
                info.label(text="Personnage : " + robot, icon='OUTLINER_OB_ARMATURE')

                missing = [z for z, _l, _d in FACE_ZONES
                           if zone_material(obj, z) is None
                           or not zone_material(obj, z).name.startswith("FACE_" + z)]
                if missing:
                    warn = box.row()
                    warn.alert = True
                    warn.label(text="Faces absentes : " + ", ".join(missing)
                               + " (Robot Maker > Visage)", icon='ERROR')

                info.label(text="Aucun sprite sheet dans son dossier", icon='INFO')
                box.operator("expr.init_sheets", icon='ADD')
            else:
                info.label(text="Selectionner le maillage d'un personnage", icon='INFO')

        if scene.expr_items:
            sub = box.column()
            sub.scale_y = 0.7
            sub.label(text="{} : {} expr. - grille {}x{}".format(
                scene.expr_name, len(scene.expr_items),
                scene.expr_cols, scene.expr_rows), icon='CHECKMARK')

            if scene.expr_source == 'JSON':
                if scene.expr_image:
                    tree2, map2, tex2 = _find_nodes(obj)
                    same = (tex2 is not None and tex2.image is not None
                            and bpy.path.abspath(tex2.image.filepath)
                            == bpy.path.abspath(scene.expr_image.filepath))
                    sub.label(text=scene.expr_image.name
                              + ("" if same else "  (materiau desynchronise)"),
                              icon='IMAGE_DATA' if same else 'ERROR')
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

        box = layout.box()
        row = box.row(align=True)
        row.label(text="Cadrage du sprite", icon='MOD_UVPROJECT')
        row.operator("expr.fit_reset", text="", icon='LOOP_BACK')

        col = box.column(align=True)
        r = col.row(align=True)
        r.prop(scene, "expr_fit_w")
        r.prop(scene, "expr_fit_h")
        r = col.row(align=True)
        r.prop(scene, "expr_off_x")
        r.prop(scene, "expr_off_y")

        layout.separator()

        # --- Choix de l'expression ---
        tree, mapping, tex = _find_nodes(obj)

        col = layout.column()
        col.enabled = bool(scene.expr_items)

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

        loaded = sum(1 for i in scene.expr_items if _icon_for(scene.expr_name, i.ident))
        has_thumbs = loaded > 0

        if scene.expr_items:
            diag = col.row()
            diag.scale_y = 0.7
            diag.label(text="Vignettes : {} / {}  ({})".format(
                loaded, len(scene.expr_items), scene.expr_name or "?"))

        if scene.expr_thumbs_view:
            if has_thumbs:
                col.template_icon_view(scene, "expr_current", show_labels=True,
                                       scale=scene.expr_thumbs_scale,
                                       scale_popup=scene.expr_thumbs_scale)
                col.prop(scene, "expr_thumbs_scale")
            else:
                warn = col.box()
                warn.alert = True
                warn.label(text="Vignettes non générées pour cette texture", icon='INFO')
                col.prop(scene, "expr_current", text="")
        else:
            col.prop(scene, "expr_current", text="")

        if scene.expr_items:
            col.operator("expr.thumbs",
                         text="Regenerer les vignettes" if has_thumbs
                         else "Generer les vignettes",
                         icon='IMAGE_DATA')

        row = col.row(align=True)
        op = row.operator("expr.apply", text="Keyframe", icon='KEYTYPE_KEYFRAME_VEC')
        op.keyframe = True
        if not scene.expr_auto_preview:
            op = row.operator("expr.apply", text="Apercu")
            op.keyframe = False
            
        row.operator("expr.open_editor", text="", icon='GREASEPENCIL')

        if scene.expr_items and mapping is None:
            warn = layout.row()
            warn.alert = True
            warn.label(text="Materiau non prepare", icon='ERROR')


# ===========================================================================
# Enregistrement
# ===========================================================================
classes = (
    EXPR_Preferences,
    EXPR_OT_open_editor,
    EXPR_Item,
    EXPR_OT_load_json,
    EXPR_OT_from_image,
    EXPR_OT_setup,
    EXPR_OT_apply,
    EXPR_OT_thumbs,
    EXPR_OT_reload,
    EXPR_OT_forget,
    EXPR_PT_panel,
    EXPR_OT_fit_reset,
    EXPR_OT_init_sheets,
)


def register():
    global _previews
    if _previews is None:
        _previews = bpy.utils.previews.new()

    for cls in classes:
        bpy.utils.register_class(cls)

    S = bpy.types.Scene
    S.expr_items = bpy.props.CollectionProperty(type=EXPR_Item)
    S.expr_zone = bpy.props.EnumProperty(
        name="Zone", items=FACE_ZONES, default='eyes',
        update=_on_zone_change,
        description="Zone du visage pilotee")
    S.expr_json = bpy.props.StringProperty(
        name="Sprite sheet JSON", subtype='FILE_PATH', default="")
    S.expr_name = bpy.props.StringProperty(default="")
    S.expr_cols = bpy.props.IntProperty(default=1, min=1)
    S.expr_rows = bpy.props.IntProperty(default=1, min=1)
    S.expr_current = bpy.props.EnumProperty(
        name="Expression", items=_enum_items, update=_on_current_change)
    S.expr_fit_w = bpy.props.FloatProperty(
        name="Largeur", default=1.0, min=.05, max=8.0, update=_fit_update)
    S.expr_fit_h = bpy.props.FloatProperty(
        name="Hauteur", default=1.0, min=.05, max=8.0, update=_fit_update)
    S.expr_off_x = bpy.props.FloatProperty(
        name="Horizontal", default=0.0, min=-3.0, max=3.0, update=_fit_update)
    S.expr_off_y = bpy.props.FloatProperty(
        name="Vertical", default=0.0, min=-3.0, max=3.0, update=_fit_update)
    S.expr_follow_frame = bpy.props.BoolProperty(
        name="Suivre la timeline", default=True)
    S.expr_thumbs_view = bpy.props.BoolProperty(
        name="Vignettes", default=True,
        description="Affiche les images des expressions a la place de leurs noms")
    S.expr_thumbs_scale = bpy.props.FloatProperty(
        name="Taille", default=6.0, min=2.0, max=14.0)
    S.expr_auto_preview = bpy.props.BoolProperty(
        name="Apercu auto", default=True)
    S.expr_source = bpy.props.EnumProperty(
        name="Source", default='JSON',
        items=[('JSON', "JSON", "Grille, noms et image lus depuis le fichier JSON"),
               ('IMAGE', "Image", "Sprite sheet seul, grille saisie a la main")])
    S.expr_image = bpy.props.PointerProperty(name="Sprite sheet", type=bpy.types.Image)
    S.expr_target = bpy.props.PointerProperty(
        name="Plan", type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'MESH')
    S.expr_emit = bpy.props.BoolProperty(name="Emission", default=True)
    S.expr_pixel = bpy.props.BoolProperty(name="Pixel (Closest)", default=False)
    S.expr_follow_selection = bpy.props.BoolProperty(
        name="Suivre la selection", default=True)

    if _on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)

    _subscribe_msgbus()

    if not bpy.app.timers.is_registered(_poll_active):
        bpy.app.timers.register(_poll_active, first_interval=1.0, persistent=True)


def unregister():
    global _previews
    stop_server()
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
    for prop in ("expr_follow_frame", "expr_thumbs_scale", "expr_thumbs_view", "expr_auto_preview",
                 "expr_follow_selection", "expr_pixel", "expr_emit", "expr_target",
                 "expr_image", "expr_source", "expr_current", "expr_rows",
                 "expr_cols", "expr_name", "expr_json", "expr_zone", "expr_items",
                 "expr_fit_w", "expr_fit_h", "expr_off_x", "expr_off_y",):
        if hasattr(S, prop):
            delattr(S, prop)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()