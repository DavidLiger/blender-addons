# Validateur JSON Schema générique, sous-ensemble (spec-atelier-glbi.md §6) : type, properties, required,
# additionalProperties, pattern, enum, const, minimum, maximum, minItems, items ; $defs (schémas nommés d'un même
# fichier, choisis par definition()). `default` et les annotations
# sont ignorés ; tout autre mot-clé rend le schéma refusé, pour qu'une règle ne soit jamais ignorée en silence.
# Aucune règle du contrat n'est écrite ici : elles viennent toutes des fichiers schema/glbi-layer3d.schema.json (scène) et
# schema/glbi-game3d.schema.json (jeu : un schéma par $defs, entity et arena), produits par
# `npm run schema:export -- --addon <dossier glb_generator>` dans GLBInterpreter.
import json
import os
import re

_SCHEMA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema")
SCHEMA_PATH = os.path.join(_SCHEMA_DIR, "glbi-layer3d.schema.json")
GAME_SCHEMA_PATH = os.path.join(_SCHEMA_DIR, "glbi-game3d.schema.json")

_SUPPORTED = {"type", "properties", "required", "additionalProperties", "pattern", "enum", "const",
              "minimum", "maximum", "minItems", "items", "$defs"}
_ANNOTATIONS = {"$schema", "$id", "$comment", "default", "title", "description"}


class SchemaError(Exception):
    """Schéma absent, illisible ou utilisant un mot-clé non pris en charge."""


def load(path=SCHEMA_PATH):
    try:
        with open(path, encoding="utf-8") as f:
            schema = json.load(f)
    except FileNotFoundError:
        raise SchemaError(f"fichier absent : {path}") from None
    except (OSError, ValueError) as exc:
        raise SchemaError(f"fichier illisible : {path} ({exc})") from None
    if not isinstance(schema, dict):
        raise SchemaError(f"{path} : un objet JSON est attendu")
    unknown = sorted(_unsupported(schema, ""))
    if unknown:
        raise SchemaError("mot(s)-clé(s) non pris en charge par l'addon : " + ", ".join(unknown))
    return schema


def _unsupported(schema, path):
    for key, sub in schema.items():
        if key in _ANNOTATIONS:
            continue
        if key not in _SUPPORTED:
            yield f"{key} ({path or 'racine'})"
        elif key == "properties":
            for name, prop in sub.items():
                yield from _unsupported(prop, f"{path}.{name}".lstrip("."))
        elif key in ("items", "additionalProperties") and isinstance(sub, dict):
            yield from _unsupported(sub, f"{path}.{key}".lstrip("."))
        elif key == "$defs":
            for name, sub_schema in sub.items():
                yield from _unsupported(sub_schema, f"$defs.{name}")


def definition(schema, name):
    """Schéma $defs.<name> d'un fichier à plusieurs schémas (jeu : entity, arena)."""
    sub = schema.get("$defs", {}).get(name) if isinstance(schema.get("$defs"), dict) else None
    if not isinstance(sub, dict):
        raise SchemaError(f"$defs.{name} absent du schéma {schema.get('$id', '')}".rstrip())
    return sub


def _ecma(pattern):
    """Motif JSON Schema (ECMA-262) pour `re` : hors classe de caractères, `$` ne doit valoir que la fin du texte
    (en Python, `$` accepte aussi la position avant un retour à la ligne final)."""
    out, i, in_class = [], 0, False
    while i < len(pattern):
        c = pattern[i]
        if c == "\\":
            out.append(pattern[i:i + 2])
            i += 2
            continue
        if in_class:
            in_class = c != "]"
        elif c == "[":
            in_class = True
        elif c == "$":
            c = r"\Z"
        out.append(c)
        i += 1
    return "".join(out)


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "number": _is_number,
    "integer": lambda v: _is_number(v) and float(v).is_integer(),
}


def _equal(a, b):
    """Égalité JSON : un booléen n'est jamais égal à un nombre (en Python, True == 1)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if _is_number(a) and _is_number(b):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_equal(a[k], b[k]) for k in a)
    return type(a) is type(b) and a == b


def _show(v):
    return json.dumps(v, ensure_ascii=False)


def validate(value, schema, path=()):
    """Liste de (chemin, message) ; vide si la valeur est conforme."""
    where = ".".join(map(str, path)) or "(racine)"
    errors = []
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_TYPES[t](value) for t in types):
            return [(where, f"type attendu {' ou '.join(types)}, trouvé {_show(value)}")]
    if "const" in schema and not _equal(value, schema["const"]):
        errors.append((where, f"valeur attendue {_show(schema['const'])}, trouvé {_show(value)}"))
    if "enum" in schema and not any(_equal(value, e) for e in schema["enum"]):
        errors.append((where, f"valeur parmi {', '.join(map(_show, schema['enum']))} attendue, trouvé {_show(value)}"))
    if isinstance(value, str) and "pattern" in schema and not re.search(_ecma(schema["pattern"]), value):
        errors.append((where, f"« {value} » ne respecte pas le motif {schema['pattern']}"))
    if _is_number(value):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append((where, f"{value} inférieur au minimum {schema['minimum']}"))
        if "maximum" in schema and value > schema["maximum"]:
            errors.append((where, f"{value} supérieur au maximum {schema['maximum']}"))
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append((where, f"clé « {key} » obligatoire absente"))
        extra = schema.get("additionalProperties", True)
        for key, sub in value.items():
            if key in props:
                errors += validate(sub, props[key], path + (key,))
            elif extra is False:
                errors.append((where, f"clé « {key} » non admise"))
            elif isinstance(extra, dict):
                errors += validate(sub, extra, path + (key,))
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append((where, f"au moins {schema['minItems']} élément(s) attendu(s), trouvé {len(value)}"))
        if isinstance(schema.get("items"), dict):
            for i, item in enumerate(value):
                errors += validate(item, schema["items"], path + (i,))
    return errors
