# Contrôles de la spec addon §5. Chaque contrôle renvoie des tuples (code, niveau, message).
import re

from . import core

ERROR, WARNING, INFO = 'ERROR', 'WARNING', 'INFO'

# Règles du schéma Zod (src/schema/glbi.ts, commit 028623f)
BLENDER_NAME_RE = re.compile(r"[A-Za-z0-9_]+")
ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]*", re.IGNORECASE)


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
    s = scene.glbg
    out = []
    sid = s.scene_id.strip()
    if not ID_RE.fullmatch(sid):
        out.append((8, ERROR, f"id de scène « {sid} » : lettres, chiffres, _ et -, sans commencer par _ ou -"))
    if not len(s.actions):
        out.append((8, ERROR, "aucune action : le schéma en exige au moins une"))
    seen = set()
    for i, it in enumerate(s.actions):
        ident = it.ident.strip()
        if not ID_RE.fullmatch(ident):
            out.append((8, ERROR, f"action {i + 1} : id « {ident} » invalide (lettres, chiffres, _ et -)"))
        elif ident in seen:
            out.append((8, ERROR, f"action {i + 1} : id « {ident} » en double"))
        seen.add(ident)
    return out


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
    for check in (check_1, check_2, check_3, check_4, check_8, check_13, check_10, check_11):
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
