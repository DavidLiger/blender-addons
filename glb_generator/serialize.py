# Données de l'addon → couche 3D de glbi_config (spec-atelier-glbi.md §3, D-8.9), écrite sur GLBI_ROOT.
import json

from . import core


def _name(id_block):
    return id_block.name if id_block is not None else ""


def build_config(scene):
    """Couche 3D, clés dans l'ordre du contrat ; sans cible caméra, pas de module caméra."""
    s = scene.glbg
    actions = [{
        "id": item.ident.strip(),
        "object": _name(item.obj),
        "type": "armature",
        "clip": _name(item.action),
        "trigger": "button",
        "loop": bool(item.loop),
    } for item in s.actions]

    modules = {}
    if s.camera_target is not None:
        modules["camera"] = {"target": s.camera_target.name}
    modules["animations"] = {
        "mode": "buttons",
        "idle": {"object": _name(s.idle.obj), "clip": _name(s.idle.action), "loop": True},
        "actions": actions,
    }
    return {"version": core.GLBI_VERSION, "scene": {"id": s.scene_id.strip()}, "modules": modules}


def to_json(config):
    return json.dumps(config, ensure_ascii=False, separators=(",", ":"))


def declared_clips(scene):
    """Noms des Actions déclarées (idle + actions), sans doublon."""
    s = scene.glbg
    names = [a.name for a in [s.idle.action, *(i.action for i in s.actions)] if a is not None]
    return list(dict.fromkeys(names))


def declared_armature_actions(scene):
    """[(armature, [Actions déclarées qui l'animent])] pour les armatures déclarées (idle et actions)."""
    s = scene.glbg
    actions = [a for a in dict.fromkeys([s.idle.action, *(i.action for i in s.actions)]) if a is not None]
    arms = [o for o in dict.fromkeys([s.idle.obj, *(i.obj for i in s.actions)])
            if o is not None and o.type == 'ARMATURE']
    return [(arm, [a for a in actions if core.is_armature_action(a, arm)]) for arm in arms]


def write_root(root, scene):
    """Écrit glbi_version (entier 2) et glbi_config (chaîne JSON) ; renvoie la chaîne écrite."""
    text = to_json(build_config(scene))
    root["glbi_version"] = core.GLBI_VERSION
    root["glbi_config"] = text
    # Piège de JSON.parse("1.0") : la relecture doit donner un objet.
    if not isinstance(json.loads(root["glbi_config"]), dict) or root["glbi_version"] != core.GLBI_VERSION:
        raise RuntimeError("relecture de glbi_config incorrecte")
    return text
