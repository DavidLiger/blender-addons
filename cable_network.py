bl_info = {
    "name": "Cable Network",
    "author": "David",
    "version": (1, 0, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar (N) > Cables",
    "description": ("Relie une serie d'objets par des cables : les groupes de "
                    "vertices donnent les points d'accroche, le nom des objets "
                    "donne l'ordre"),
    "category": "Object",
}

import bpy
import re

from mathutils import Matrix, Vector


CABLE_PREFIX = "CABLE_"

K_CABLE = "cable_net"       # marque une courbe generee
K_STRAND = "cable_strand"   # brin auquel elle appartient
K_FROM = "cable_from"
K_TO = "cable_to"


# ---------------------------------------------------------------------------
# Reperage
# ---------------------------------------------------------------------------
def natural_key(name):
    """Tri naturel : poteau_002 vient avant poteau_010."""
    return [int(p) if p.isdigit() else p.lower()
            for p in re.split(r"(\d+)", name)]


def strand_of(group_name, prefix):
    """cable_L -> 'L', cable -> '' (brin unique)."""
    rest = group_name[len(prefix):]
    return rest.lstrip("_-. ").strip()


def group_center(obj, group):
    """Centre du groupe de vertices, en coordonnees monde."""
    index = group.index
    total = Vector((0.0, 0.0, 0.0))
    count = 0

    for v in obj.data.vertices:
        for vg in v.groups:
            if vg.group == index:
                total += v.co
                count += 1
                break

    if count == 0:
        return None
    return obj.matrix_world @ (total / count)


def scan_network(context, only_selected=False):
    """Retourne {brin: [(objet, point monde), ...]} dans l'ordre des noms."""
    scene = context.scene
    prefix = scene.cn_prefix.strip() or CABLE_PREFIX

    source = context.selected_objects if only_selected else None
    if source is None:
        coll = scene.cn_collection
        source = coll.all_objects if coll is not None else scene.objects

    strands = {}
    for obj in source:
        if obj.type != 'MESH':
            continue

        for group in obj.vertex_groups:
            if not group.name.lower().startswith(prefix.lower()):
                continue

            point = group_center(obj, group)
            if point is None:
                continue

            key = strand_of(group.name, prefix) or "principal"
            strands.setdefault(key, []).append((obj, point))

    for key in strands:
        strands[key].sort(key=lambda pair: natural_key(pair[0].name))

    return strands


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------
def sag_points(a, b, sag, segments):
    """Points d'une chainette approchee entre deux accroches. Une courbe POLY
    n'a pas de poignees : rien ne peut se retourner."""
    pts = []
    drop = (b - a).length * sag

    for i in range(segments + 1):
        t = i / segments
        p = a.lerp(b, t)
        # Parabole nulle aux extremites, maximale au milieu
        p.z -= drop * 4.0 * t * (1.0 - t)
        pts.append(p)

    return pts


def make_cable(context, obj_a, pt_a, obj_b, pt_b, strand):
    scene = context.scene
    coll = scene.cn_output or context.collection

    name = "{}{}_{}".format(CABLE_PREFIX, strand, obj_a.name)

    curve = bpy.data.curves.new(name, 'CURVE')
    curve.dimensions = '3D'
    curve.bevel_depth = scene.cn_radius
    curve.bevel_resolution = scene.cn_resolution
    curve.use_fill_caps = True

    if scene.cn_material is not None:
        curve.materials.append(scene.cn_material)

    pts = sag_points(pt_a, pt_b, scene.cn_sag, max(2, scene.cn_segments))

    spline = curve.splines.new('POLY')
    spline.points.add(len(pts) - 1)
    for i, p in enumerate(pts):
        spline.points[i].co = (p.x, p.y, p.z, 1.0)

    obj = bpy.data.objects.new(name, curve)
    obj.matrix_world = Matrix.Identity(4)
    obj[K_CABLE] = True
    obj[K_STRAND] = strand
    obj[K_FROM] = obj_a.name
    obj[K_TO] = obj_b.name

    coll.objects.link(obj)

    # Les extremites suivent leurs poteaux ; le milieu du cable pend librement
    if scene.cn_hooks:
        for index, target in ((0, obj_a), (len(pts) - 1, obj_b)):
            mod = obj.modifiers.new("Hook_{}".format(index), 'HOOK')
            mod.object = target
            mod.matrix_inverse = target.matrix_world.inverted()
            mod.vertex_indices_set([index])

    return obj


def build(context, strands):
    """Retourne (crees, ignores) — ignores = paires trop eloignees."""
    scene = context.scene
    limit = scene.cn_max_span

    created, skipped = 0, []

    for strand, entries in sorted(strands.items()):
        for i in range(len(entries) - 1):
            (obj_a, pt_a), (obj_b, pt_b) = entries[i], entries[i + 1]

            if limit > 0.0 and (pt_b - pt_a).length > limit:
                skipped.append("{} -> {}".format(obj_a.name, obj_b.name))
                continue

            make_cable(context, obj_a, pt_a, obj_b, pt_b, strand)
            created += 1

    return created, skipped


def existing_cables(context):
    coll = context.scene.cn_output
    source = coll.all_objects if coll is not None else context.scene.objects
    return [o for o in source if o.get(K_CABLE)]


# ---------------------------------------------------------------------------
# Operateurs
# ---------------------------------------------------------------------------
class CN_OT_scan(bpy.types.Operator):
    bl_idname = "cn.scan"
    bl_label = "Analyser le reseau"
    bl_description = ("Repere les groupes de vertices et compte les brins, "
                      "sans rien creer")

    def execute(self, context):
        strands = scan_network(context)

        if not strands:
            self.report({'WARNING'},
                        "Aucun groupe de vertices commencant par '{}'".format(
                            context.scene.cn_prefix))
            return {'CANCELLED'}

        detail = ", ".join("{} ({} accroches)".format(k, len(v))
                           for k, v in sorted(strands.items()))
        self.report({'INFO'}, "{} brin(s) : {}".format(len(strands), detail))
        return {'FINISHED'}


class CN_OT_test(bpy.types.Operator):
    bl_idname = "cn.test"
    bl_label = "Tester sur la selection"
    bl_description = ("Genere les cables entre les objets selectionnes seulement : "
                      "de quoi juger l'affaissement avant de traiter tout le reseau")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        selected = [o for o in context.selected_objects if o.type == 'MESH']
        if len(selected) < 2:
            self.report({'ERROR'}, "Selectionner au moins deux objets")
            return {'CANCELLED'}

        strands = scan_network(context, only_selected=True)
        if not strands:
            self.report({'ERROR'}, "Aucun groupe de vertices sur la selection")
            return {'CANCELLED'}

        created, skipped = build(context, strands)
        msg = "{} cable(s) de test".format(created)
        if skipped:
            msg += " - {} portee(s) trop longue(s)".format(len(skipped))

        self.report({'INFO'}, msg)
        return {'FINISHED'}


class CN_OT_build(bpy.types.Operator):
    bl_idname = "cn.build"
    bl_label = "Generer le reseau"
    bl_description = ("Genere tous les cables. Les cables existants sont "
                      "remplaces")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        old = existing_cables(context)
        for obj in old:
            bpy.data.objects.remove(obj)

        strands = scan_network(context)
        if not strands:
            self.report({'ERROR'}, "Aucun groupe de vertices trouve")
            return {'CANCELLED'}

        created, skipped = build(context, strands)

        msg = "{} cable(s) sur {} brin(s)".format(created, len(strands))
        if old:
            msg += " - {} remplace(s)".format(len(old))

        if skipped:
            self.report({'WARNING'}, msg + " - ignore : " + ", ".join(skipped[:3]))
        else:
            self.report({'INFO'}, msg)
        return {'FINISHED'}


class CN_OT_clear(bpy.types.Operator):
    bl_idname = "cn.clear"
    bl_label = "Supprimer les cables"
    bl_description = "Supprime tous les cables generes"
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        cables = existing_cables(context)
        for obj in cables:
            bpy.data.objects.remove(obj)

        self.report({'INFO'}, "{} cable(s) supprime(s)".format(len(cables)))
        return {'FINISHED'}


class CN_OT_update(bpy.types.Operator):
    bl_idname = "cn.update"
    bl_label = "Mettre a jour l'aspect"
    bl_description = "Applique rayon, lissage et materiau aux cables existants"

    def execute(self, context):
        scene = context.scene
        count = 0

        for obj in existing_cables(context):
            if obj.type != 'CURVE':
                continue
            obj.data.bevel_depth = scene.cn_radius
            obj.data.bevel_resolution = scene.cn_resolution
            if scene.cn_material is not None:
                obj.data.materials.clear()
                obj.data.materials.append(scene.cn_material)
            count += 1

        self.report({'INFO'}, "{} cable(s) mis a jour".format(count))
        return {'FINISHED'}


class CN_OT_add_group(bpy.types.Operator):
    bl_idname = "cn.add_group"
    bl_label = "Marquer la selection"
    bl_description = ("Cree un groupe de vertices sur les sommets selectionnes "
                      "en Edit Mode : c'est le point d'accroche du cable")

    strand: bpy.props.StringProperty(default="")

    def execute(self, context):
        scene = context.scene
        obj = context.active_object

        if obj is None or obj.type != 'MESH':
            self.report({'ERROR'}, "Selectionner un maillage")
            return {'CANCELLED'}

        was_edit = (obj.mode == 'EDIT')
        if was_edit:
            bpy.ops.object.mode_set(mode='OBJECT')

        verts = [v.index for v in obj.data.vertices if v.select]
        if not verts:
            if was_edit:
                bpy.ops.object.mode_set(mode='EDIT')
            self.report({'ERROR'}, "Aucun sommet selectionne")
            return {'CANCELLED'}

        prefix = scene.cn_prefix.strip() or CABLE_PREFIX
        strand = (self.strand or scene.cn_new_strand).strip()
        name = prefix + ("_" + strand if strand else "")

        group = obj.vertex_groups.get(name) or obj.vertex_groups.new(name=name)
        group.add(verts, 1.0, 'REPLACE')

        if was_edit:
            bpy.ops.object.mode_set(mode='EDIT')

        self.report({'INFO'}, "'{}' pose sur {} sommet(s)".format(name, len(verts)))
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Panneau
# ---------------------------------------------------------------------------
class CN_PT_panel(bpy.types.Panel):
    bl_label = "Cable Network"
    bl_idname = "CN_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BD"
    bl_order = 60

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # --- Reperage ---
        box = layout.box()
        box.label(text="Points d'accroche", icon='GROUP_VERTEX')
        box.prop(scene, "cn_prefix")
        box.prop(scene, "cn_collection")

        col = box.column(align=True)
        col.label(text="Marquer en Edit Mode :")
        r = col.row(align=True)
        r.prop(scene, "cn_new_strand", text="")
        r.operator("cn.add_group", text="", icon='ADD')

        sub = box.column(align=True)
        sub.scale_y = 0.7
        sub.label(text="Un suffixe par brin : L, R, C...")
        sub.label(text="L'ordre suit le nom des objets")

        box.operator("cn.scan", icon='VIEWZOOM')

        # --- Aspect ---
        box = layout.box()
        box.label(text="Cables", icon='CURVE_PATH')

        col = box.column(align=True)
        col.prop(scene, "cn_sag")
        col.prop(scene, "cn_segments")
        r = col.row(align=True)
        r.prop(scene, "cn_radius")
        r.prop(scene, "cn_resolution")

        box.template_ID(scene, "cn_material", new="material.new")
        box.prop(scene, "cn_hooks")
        box.prop(scene, "cn_max_span")
        box.prop(scene, "cn_output")

        # --- Generation ---
        box = layout.box()
        box.label(text="Generation", icon='PLAY')

        n_sel = len([o for o in context.selected_objects if o.type == 'MESH'])
        col = box.column()
        col.enabled = (n_sel >= 2)
        col.operator("cn.test", icon='CHECKMARK')
        if n_sel < 2:
            sub = box.row()
            sub.scale_y = 0.7
            sub.label(text="{} objet(s) selectionne(s) sur 2".format(n_sel))

        box.operator("cn.build", icon='FILE_REFRESH')

        cables = existing_cables(context)
        if cables:
            sub = box.row()
            sub.scale_y = 0.7
            sub.label(text="{} cable(s) en place".format(len(cables)),
                      icon='CHECKMARK')
            r = box.row(align=True)
            r.operator("cn.update", icon='MATERIAL')
            r.operator("cn.clear", text="", icon='TRASH')


# ---------------------------------------------------------------------------
# Enregistrement
# ---------------------------------------------------------------------------
classes = (
    CN_OT_scan,
    CN_OT_test,
    CN_OT_build,
    CN_OT_clear,
    CN_OT_update,
    CN_OT_add_group,
    CN_PT_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    S = bpy.types.Scene
    S.cn_prefix = bpy.props.StringProperty(
        name="Prefixe", default=CABLE_PREFIX,
        description="Groupes de vertices pris en compte")
    S.cn_new_strand = bpy.props.StringProperty(
        name="Brin", default="L",
        description="Suffixe du groupe a creer : L, R, C... vide pour un brin unique")
    S.cn_collection = bpy.props.PointerProperty(
        name="Collection", type=bpy.types.Collection,
        description="Ou chercher les objets. Vide = toute la scene")
    S.cn_output = bpy.props.PointerProperty(
        name="Sortie", type=bpy.types.Collection,
        description="Ou ranger les cables. Vide = collection active")

    S.cn_sag = bpy.props.FloatProperty(
        name="Affaissement", default=0.08, min=0.0, max=1.0,
        description="Creux du cable, en fraction de la portee")
    S.cn_segments = bpy.props.IntProperty(
        name="Segments", default=12, min=2, max=64,
        description="Points intermediaires : plus il y en a, plus la courbe est douce")
    S.cn_radius = bpy.props.FloatProperty(
        name="Rayon", default=0.02, min=0.0, max=2.0)
    S.cn_resolution = bpy.props.IntProperty(
        name="Lissage", default=2, min=0, max=16)
    S.cn_material = bpy.props.PointerProperty(
        name="Materiau", type=bpy.types.Material)

    S.cn_hooks = bpy.props.BoolProperty(
        name="Suivre les objets", default=True,
        description="Les extremites restent accrochees : deplacer un poteau "
                    "deplace le cable")
    S.cn_max_span = bpy.props.FloatProperty(
        name="Portee maximale", default=0.0, min=0.0, unit='LENGTH',
        description="Au-dela, la paire est ignoree et signalee. 0 = sans limite")


def unregister():
    S = bpy.types.Scene
    for prop in ("cn_max_span", "cn_hooks", "cn_material", "cn_resolution",
                 "cn_radius", "cn_segments", "cn_sag",
                 "cn_output", "cn_collection", "cn_new_strand", "cn_prefix"):
        if hasattr(S, prop):
            delattr(S, prop)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
