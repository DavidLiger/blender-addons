# Contrôles de la spec addon §5. Chaque contrôle renvoie des tuples (code, niveau, message).
import json
import os
import re

import bpy

from . import core, posefix, prefs, schema_check, serialize

ERROR, WARNING, INFO = 'ERROR', 'WARNING', 'INFO'

# Règle de nommage Blender de spec-extras-blender.md §3 (contrôle 2, sur tous les objets de la scène).
BLENDER_NAME_RE = re.compile(r"[A-Za-z0-9_]+")


def normalize_name(name):
    """Nom proposé au renommage : tout caractère hors lettres, chiffres et _ devient _."""
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def _declared(s):
    """(libellé, objet, Action) pour idle puis chaque action."""
    yield "idle", s.idle.obj, s.idle.action
    for i, item in enumerate(s.actions):
        yield f"action {i + 1} « {item.ident or '?'} »", item.obj, item.action


def check_1(scene):
    cands = core.root_candidates()
    if not cands:
        return [(1, ERROR, "Aucun GLBI_ROOT dans le .blend (bouton Créer / réparer)")]
    if len(cands) > 1:
        return [(1, ERROR, f"{len(cands)} GLBI_ROOT dans le .blend : " + ", ".join(o.name for o in cands))]
    root = cands[0]
    out = []
    if root.name != core.ROOT_NAME:
        out.append((1, ERROR, f"« {root.name} » doit s'appeler {core.ROOT_NAME} (bouton Créer / réparer)"))
    if root.type != 'EMPTY':
        out.append((1, ERROR, f"« {root.name} » n'est pas un empty"))
    if root.name not in scene.objects:
        out.append((1, ERROR, f"« {root.name} » n'est pas dans la scène active (bouton Créer / réparer)"))
    return out


def check_2(scene):
    out = []
    groups = (("objet", list(scene.objects)),
              ("Action", [a for a in dict.fromkeys(a for _, _, a in _declared(scene.glbg)) if a is not None]))
    for kind, ids in groups:
        seen = {}
        for id_block in ids:
            name = id_block.name
            if not BLENDER_NAME_RE.fullmatch(name):
                out.append((2, ERROR, f"{kind} « {name} » : lettres, chiffres et _ uniquement"))
            seen.setdefault(normalize_name(name), []).append(name)
        for norm, names in seen.items():
            if len(names) > 1:
                out.append((2, ERROR, f"{kind}s en collision après normalisation ({norm}) : " + ", ".join(names)))
    return out


def check_3(scene):
    out = []
    for label, obj, _action in _declared(scene.glbg):
        if obj is None:
            out.append((3, ERROR, f"{label} : aucun objet"))
        elif obj.name not in scene.objects:
            out.append((3, ERROR, f"{label} : l'objet « {obj.name} » n'est pas dans la scène"))
        elif obj.type != 'ARMATURE':
            out.append((3, ERROR, f"{label} : « {obj.name} » n'est pas une armature"))
    target = scene.glbg.camera_target
    if target is not None:
        if target.name not in scene.objects:
            out.append((3, ERROR, f"cible caméra : l'objet « {target.name} » n'est pas dans la scène"))
        elif target.type != 'EMPTY':
            out.append((3, ERROR, f"cible caméra : « {target.name} » n'est pas un empty"))
    return out


def check_4(scene):
    out = []
    for label, obj, action in _declared(scene.glbg):
        if action is None:
            out.append((4, ERROR, f"{label} : aucun clip"))
        elif obj is not None and obj.type == 'ARMATURE':
            if not core.is_armature_action(action, obj):
                out.append((4, ERROR, f"{label} : « {action.name} » n'est pas une Action de « {obj.name} »"))
            elif obj.animation_data is None:
                out.append((4, ERROR, f"{label} : « {obj.name} » n'a aucune donnée d'animation, "
                                      "l'exportateur n'exporterait aucun clip"))
    return out


def check_8(scene):
    """Identifiants d'action en double (règle du Zod perdue par le JSON Schema) ; la forme est vérifiée par le 16."""
    out, seen = [], set()
    for i, it in enumerate(scene.glbg.actions):
        ident = it.ident.strip()
        if ident and ident in seen:
            out.append((8, ERROR, f"action {i + 1} : id « {ident} » en double"))
        seen.add(ident)
    return out


def check_16(scene):
    """Couche 3D validée par le JSON Schema exporté de GLBInterpreter (spec-atelier-glbi.md §6)."""
    try:
        schema = schema_check.load()
    except schema_check.SchemaError as exc:
        return [(16, ERROR, f"JSON Schema : {exc} ; dans GLBInterpreter : "
                            "npm run schema:export -- --addon <dossier glb_generator>")]
    out = [(16, INFO, f"JSON Schema : {schema.get('$id', '(sans $id)')}")]
    for where, message in schema_check.validate(serialize.build_config(scene), schema):
        out.append((16, ERROR, f"couche 3D {where} : {message}"))
    return out


def _same_path(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def check_15(scene):
    """Fichier de sortie = <dossier du livre>/scenes/<scene.id>.glb, seul emplacement lu par le build (spec extras §11)."""
    root = prefs.book_root()
    if not root:
        return [(15, ERROR, "dossier du livre non trouvé ni défini : " + prefs.NOT_FOUND_HINT)]
    if not os.path.isdir(root):
        return [(15, ERROR, f"dossier du livre introuvable : {root}")]
    sid = scene.glbg.scene_id.strip()
    if not sid:
        return []  # id manquant : contrôle 16
    expected = prefs.default_output_path(sid)
    actual = prefs.output_path(scene.glbg)
    if _same_path(actual, expected):
        return []
    return [(15, ERROR, f"fichier de sortie « {actual} » : le build lit « {expected} » (vider le champ Fichier GLB)")]


def check_17(scene):
    """Lecture seule de scenes/<id>.edit.json : id d'actions édités dans l'atelier mais absents de Blender."""
    root = prefs.book_root()
    sid = scene.glbg.scene_id.strip()
    if not root or not sid:
        return []
    rel = f"scenes/{sid}.edit.json"
    path = os.path.join(root, "scenes", sid + ".edit.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        return [(17, WARNING, f"{rel} illisible ({exc}) : id orphelins non vérifiés")]
    actions = data.get("modules") if isinstance(data, dict) else None
    actions = actions.get("animations") if isinstance(actions, dict) else None
    actions = actions.get("actions") if isinstance(actions, dict) else None
    if not isinstance(actions, dict):
        return []
    idents = {item.ident.strip() for item in scene.glbg.actions}
    return [(17, WARNING, f"{rel} : action « {key} » absente de Blender (à rattacher ou supprimer dans l'atelier)")
            for key in actions if key not in idents]


def check_18(scene):
    """D-8.10 : l'éclairage vient du GLB ; sans lampe exportable, le lecteur pose un éclairage neutre de secours."""
    out = []
    ignored = [o.name for o in scene.objects if o.type == 'LIGHT' and o.data is not None
               and o.data.type not in core.EXPORTED_LIGHT_TYPES]
    if ignored:
        out.append((18, WARNING, "lampe(s) Area ou Hemi non exportée(s) par glTF : " + ", ".join(ignored)))
    if not core.scene_lights(scene):
        out.append((18, WARNING, "scène sans lumière (Point, Spot ou Sun) : éclairage neutre de secours du lecteur"))
    return out


def check_19(scene):
    """Pose non enregistrée dans les Actions (posefix). Les canaux statiques sont enregistrés automatiquement à
    l'export (information après export) ; un canal hors repos animé par une autre Action mais pas par l'Action
    exportée sort au repos : avertissement."""
    out = []
    for arm, acts in serialize.declared_armature_actions(scene):
        for act, (_static, ambiguous) in posefix.analyse(arm, acts).items():
            if ambiguous:
                bones = sorted({path.split('"')[1] for path, _i in ambiguous})
                out.append((19, WARNING, f"Action « {act.name} » : {len(ambiguous)} valeur(s) de pose hors repos animée(s) "
                                         f"par une autre Action mais pas par celle-ci, exportée(s) au repos : "
                                         + ", ".join(bones[:6]) + (" …" if len(bones) > 6 else "")))
    return out


def check_20(scene):
    """DP-2 : le .blend de la scène <id> est blender/<id>.blend (le studio l'ouvre par ce nom). Seulement pour un
    .blend rangé directement dans <livre>/blender/ (ni sous-dossier, ni repli sur la préférence)."""
    sid = scene.glbg.scene_id.strip()
    if not sid or not prefs.blend_in_book_blender_dir():
        return []  # id manquant : contrôle 16
    name = os.path.basename(bpy.data.filepath)
    stem = os.path.splitext(name)[0]
    if stem == sid:
        return []
    return [(20, WARNING, f"fichier « {name} » : le .blend de la scène « {sid} » doit s'appeler "
                          f"blender/{sid}.blend (Enregistrer sous, Remap Relative coché)")]


def check_13(scene):
    arms = core.scene_armatures(scene)
    if len(arms) > 1:
        return [(13, ERROR, f"{len(arms)} armatures dans la scène (une seule en v0.1) : "
                            + ", ".join(a.name for a in arms))]
    return []


def check_10(scene):
    s = scene.glbg
    declared = {a for _, _, a in _declared(s) if a is not None}
    arms = {obj for _, obj, _ in _declared(s) if obj is not None and obj.type == 'ARMATURE'}
    out = []
    for arm in sorted(arms, key=lambda o: o.name):
        for action in core.armature_actions(arm):
            if action not in declared:
                out.append((10, WARNING, f"Action « {action.name} » de « {arm.name} » non déclarée : non exportée"))
    return out


def check_11(scene):
    out = []
    for obj in scene.objects:
        if obj.type != 'MESH':
            continue
        if obj.vertex_groups:
            mods = [m for m in obj.modifiers if m.type == 'ARMATURE']
            if not any(m.object is not None and m.object.name in scene.objects for m in mods):
                out.append((11, WARNING, f"« {obj.name} » : skin orphelin (groupes de sommets sans armature)"))
        if obj.data is not None and not len(obj.data.vertices):
            out.append((11, WARNING, f"« {obj.name} » : maillage vide"))
    return out


_NOT_APPLICABLE = [
    (9, INFO, "réglages d'export : imposés par Exporter, contrôlés lors d'un export manuel"),
]


def run_checks(scene):
    results = []
    for check in (check_1, check_2, check_3, check_4, check_8, check_13, check_15, check_16,
                  check_10, check_11, check_17, check_18, check_19, check_20):
        results += check(scene)
    results += _NOT_APPLICABLE
    order = {ERROR: 0, WARNING: 1, INFO: 2}
    results.sort(key=lambda r: (order[r[1]], r[0]))
    return results


def store_report(scene, results):
    report = scene.glbg.report
    report.clear()
    for code, level, message in results:
        item = report.add()
        item.code, item.level, item.message = code, level, message


def errors(results):
    return [r for r in results if r[1] == ERROR]
