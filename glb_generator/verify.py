# Relecture du GLB écrit : vérifie ce que l'exportateur a réellement produit.
import json
import struct

from . import core


def read_glb_json(path):
    with open(path, "rb") as f:
        data = f.read()
    magic, _version, _length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF":
        raise ValueError("pas un fichier GLB")
    chunk_len, chunk_type = struct.unpack_from("<I4s", data, 12)
    if chunk_type != b"JSON":
        raise ValueError("premier bloc du GLB non JSON")
    return json.loads(data[20:20 + chunk_len].decode("utf-8"))


def _animated_objects(gltf, animation):
    """Noms des nœuds animés par le clip et de tous leurs ancêtres (un os animé compte pour son armature)."""
    nodes = gltf.get("nodes", [])
    parent = {c: i for i, n in enumerate(nodes) for c in n.get("children", [])}
    names = set()
    for ch in animation.get("channels", []):
        i = ch.get("target", {}).get("node")
        while i is not None:
            names.add(nodes[i].get("name"))
            i = parent.get(i)
    return names


EVENT_TIME_TOLERANCE_S = 0.0005  # comme le build (book:check)


def _clip_durations(gltf):
    """Durée de chaque clip = dernier instant de ses pistes (comme three.js et book:check)."""
    acc = gltf.get("accessors", [])
    out = {}
    for anim in gltf.get("animations", []):
        ends = [acc[s["input"]].get("max", [0])[0] for s in anim.get("samplers", [])]
        out[anim.get("name")] = max(ends, default=0.0)
    return out


def _event_problems(gltf, config_text):
    """Instants des événements de la couche 3D au-delà de la durée du clip dans le GLB (erreur du build)."""
    try:
        actions = json.loads(config_text)["modules"]["animations"]["actions"]
    except (ValueError, KeyError, TypeError):
        return []
    durations = _clip_durations(gltf)
    out = []
    for a in actions:
        d = durations.get(a.get("clip"))
        if d is None:
            continue
        for ev in a.get("events", []):
            if ev["time"] > d + EVENT_TIME_TOLERANCE_S:
                t, dur = f"{ev['time']:.3f}".replace(".", ","), f"{d:.3f}".replace(".", ",")
                out.append(f"action « {a['id']} » : événement « {ev['id']} » à {t} s, clip « {a['clip']} » de {dur} s "
                           "dans le GLB")
    return out


def _camera_problems(gltf, config_text, keyed):
    """Position de la caméra (DP-19) : l'empty cité par la couche 3D est un nœud du GLB (erreur du build sinon) ; s'il
    est animé par ses clés dans Blender, sa position est animée par le clip de l'action."""
    try:
        actions = json.loads(config_text)["modules"]["animations"]["actions"]
    except (ValueError, KeyError, TypeError):
        return []
    nodes = gltf.get("nodes", [])
    names = {n.get("name") for n in nodes}
    anims = {a.get("name"): a for a in gltf.get("animations", [])}
    out = []
    for a in actions:
        empty = a.get("cameraPosition")
        if not empty:
            continue
        if empty not in names:
            out.append(f"action « {a['id']} » : position de caméra « {empty} » absente du GLB")
            continue
        if a.get("id") not in keyed:
            continue
        anim = anims.get(a.get("clip"))
        moved = anim is not None and any(
            nodes[ch["target"]["node"]].get("name") == empty and ch["target"].get("path") == "translation"
            for ch in anim.get("channels", []) if "node" in ch.get("target", {}))
        if not moved:
            out.append(f"action « {a['id']} » : position de caméra « {empty} » non animée par le clip « {a['clip']} » "
                       "dans le GLB")
    return out


def verify_glb(path, clips, config_text, n_lights=0, clip_objects=None, keyed_camera_positions=()):
    """Liste des écarts entre le GLB et ce qui était attendu (vide si conforme).
    clip_objects (pistes NLA) : {clip: [armatures qui portent une piste de ce nom]}, chacune doit être animée par le clip
    commun (regroupement par nom de piste).
    keyed_camera_positions : id des actions dont l'empty de position est animé par ses clés dans le clip."""
    try:
        gltf = read_glb_json(path)
    except (OSError, ValueError, struct.error) as exc:
        return [f"GLB illisible : {exc}"]
    problems = []
    roots = [n for n in gltf.get("nodes", []) if n.get("name") == core.ROOT_NAME]
    if len(roots) != 1:
        problems.append(f"{len(roots)} nœud(s) {core.ROOT_NAME} dans le GLB (1 attendu)")
    else:
        extras = roots[0].get("extras") or {}
        version = extras.get("glbi_version")
        if type(version) is not int or version != core.GLBI_VERSION:
            problems.append(f"glbi_version vaut {json.dumps(version)} (entier {core.GLBI_VERSION} attendu)")
        if extras.get("glbi_config") != config_text:
            problems.append("glbi_config du GLB différent de celui écrit sur GLBI_ROOT")
    names = [a.get("name") for a in gltf.get("animations", [])]
    missing = sorted(set(clips) - set(names))
    extra = sorted(set(names) - set(clips))
    dup = sorted({n for n in names if names.count(n) > 1})
    if missing:
        problems.append("clips absents du GLB : " + ", ".join(missing))
    if extra:
        problems.append("clips non déclarés dans le GLB : " + ", ".join(map(str, extra)))
    if dup:
        problems.append("clips en double dans le GLB : " + ", ".join(dup))
    for anim in gltf.get("animations", []):
        expected = (clip_objects or {}).get(anim.get("name"))
        if expected:
            missing_objs = sorted(set(expected) - _animated_objects(gltf, anim))
            if missing_objs:
                problems.append(f"clip « {anim.get('name')} » : non commun, sans " + ", ".join(missing_objs))
    problems += _event_problems(gltf, config_text)
    problems += _camera_problems(gltf, config_text, set(keyed_camera_positions))
    lit = [n.get("name") for n in gltf.get("nodes", []) if "KHR_lights_punctual" in (n.get("extensions") or {})]
    if len(lit) != n_lights:
        problems.append(f"{len(lit)} lampe(s) dans le GLB (KHR_lights_punctual), {n_lights} attendue(s)")
    for scene in gltf.get("scenes", []):
        foreign = [k for k in (scene.get("extras") or {}) if not k.startswith("glbi_")]
        if foreign:
            problems.append("propriétés de scène hors glbi_ dans le GLB : " + ", ".join(foreign))
    return problems


def verify_game_glb(path, kind, clips, config_text, n_lights, expected_nodes):
    """Relecture d'un GLB du mini-jeu (v0.4) : GLBI_ROOT porte glbi_game_version (entier 1) et la couche du jeu écrite,
    sans couche de scène ; clips exactement ceux des rôles (entité) ou aucun (arène) ; nœuds attendus présents (arène :
    SPAWN_*, COL_* avec maillage ; entité : armature, COLLIDER) ; lampes ; extras de scène."""
    try:
        gltf = read_glb_json(path)
    except (OSError, ValueError, struct.error) as exc:
        return [f"GLB illisible : {exc}"]
    problems = []
    nodes = gltf.get("nodes", [])
    roots = [n for n in nodes if n.get("name") == core.ROOT_NAME]
    if len(roots) != 1:
        problems.append(f"{len(roots)} nœud(s) {core.ROOT_NAME} dans le GLB (1 attendu)")
    else:
        extras = roots[0].get("extras") or {}
        version = extras.get("glbi_game_version")
        if type(version) is not int or version != core.GAME3D_VERSION:
            problems.append(f"glbi_game_version vaut {json.dumps(version)} (entier {core.GAME3D_VERSION} attendu)")
        if extras.get("glbi_game_config") != config_text:
            problems.append("glbi_game_config du GLB différent de celui écrit sur GLBI_ROOT")
        stale = [k for k in ("glbi_version", "glbi_config") if k in extras]
        if stale:
            problems.append("couche de scène présente dans un GLB du jeu : " + ", ".join(stale))
    names = [a.get("name") for a in gltf.get("animations", [])]
    missing = sorted(set(clips) - set(names))
    extra = sorted(set(names) - set(clips))
    if missing:
        problems.append("clips absents du GLB : " + ", ".join(missing))
    if extra:
        problems.append(("clips non déclarés dans le GLB : " if kind == core.KIND_ENTITY
                         else "animations dans le GLB de l'arène : ") + ", ".join(map(str, extra)))
    by_name = {}
    for n in nodes:
        by_name.setdefault(n.get("name"), n)
    for name, needs_mesh in expected_nodes:
        n = by_name.get(name)
        if n is None:
            problems.append(f"nœud « {name} » absent du GLB")
        elif needs_mesh and "mesh" not in n:
            problems.append(f"nœud « {name} » sans maillage dans le GLB")
    lit = [n.get("name") for n in nodes if "KHR_lights_punctual" in (n.get("extensions") or {})]
    if len(lit) != n_lights:
        problems.append(f"{len(lit)} lampe(s) dans le GLB (KHR_lights_punctual), {n_lights} attendue(s)")
    for scene in gltf.get("scenes", []):
        foreign = [k for k in (scene.get("extras") or {}) if not k.startswith("glbi_")]
        if foreign:
            problems.append("propriétés de scène hors glbi_ dans le GLB : " + ", ".join(foreign))
    return problems
