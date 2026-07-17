"""PersPress オペレーター (Setup / Add / RemoveMember / RemoveGroup / 形状切替)."""
import bpy
from mathutils import Vector

from . import core_nodes

MODIFIER_NAME = "PersPress"
PREVIEW_MODIFIER_NAME = "PersPressPreview"
ANCHOR_PREFIX = "PP_anchor"
PREVIEW_PREFIX = "PP_preview"
GIZMO_SPECS = (("PP_FalloffStart", "PP_falloff_start"),
               ("PP_FalloffEnd", "PP_falloff_end"))
SHAPE_DISPLAY = {0: "SPHERE", 1: "CUBE"}

# アンカー idprop 名 <-> ノードグループ入力名 (ドライバー対象の連続量)
PROP_TO_INPUT = {
    "PP_Ratio": "Ratio",
    "PP_Influence": "Influence",
    "PP_FalloffStart": "FalloffStart",
    "PP_FalloffEnd": "FalloffEnd",
}


# ---------------------------------------------------------------- 参照系


def is_anchor(ob):
    return (ob is not None and ob.type == "EMPTY"
            and ob.name.startswith(ANCHOR_PREFIX) and "PP_Ratio" in ob.keys())


def anchor_of(ob):
    """オブジェクトの PersPress モディファイアが参照するアンカーを返す."""
    if ob is None:
        return None
    if is_anchor(ob):
        return ob
    mod = ob.modifiers.get(MODIFIER_NAME) if hasattr(ob, "modifiers") else None
    if mod is None or mod.node_group is None:
        return None
    sock = core_nodes.socket_identifiers(mod.node_group)
    try:
        return getattr(mod.properties.inputs, sock["Anchor"]).value
    except (AttributeError, KeyError):
        return None


def members_of(anchor):
    """アンカーに紐づくメンバーメッシュの一覧."""
    result = []
    for ob in bpy.data.objects:
        if ob.type != "MESH":
            continue
        mod = ob.modifiers.get(MODIFIER_NAME)
        if mod is not None and anchor_of(ob) is anchor:
            result.append(ob)
    return result


def scene_anchors(context):
    return [ob for ob in context.scene.objects if is_anchor(ob)]


def active_anchor(context):
    a = context.scene.perspress.active_anchor
    return a if is_anchor(a) else None


def gizmos_of(anchor):
    return sorted((c for c in anchor.children if c.name.startswith("PP_falloff")),
                  key=lambda c: c.name)


def preview_of(anchor):
    return next((c for c in anchor.children
                 if c.name.startswith(PREVIEW_PREFIX)), None)


def refresh_list(scene):
    """Groups UIList の項目をシーンの実態から再構築する."""
    settings = scene.perspress
    prev_anchor = settings.active_anchor
    settings.list_items.clear()
    active_index = -1
    for anchor in (ob for ob in scene.objects if is_anchor(ob)):
        item = settings.list_items.add()
        item.name = anchor.name
        item.kind = "GROUP"
        item.anchor_name = anchor.name
        if anchor is prev_anchor:
            active_index = len(settings.list_items) - 1
        if anchor.get("PP_ui_open", True):
            for member in members_of(anchor):
                mi = settings.list_items.add()
                mi.name = member.name
                mi.kind = "MEMBER"
                mi.anchor_name = anchor.name
                mi.object_name = member.name
    if active_index >= 0:
        settings.list_index = active_index


# ---------------------------------------------------------------- 内部処理


def _selected_meshes(context):
    return [ob for ob in context.selected_objects if ob.type == "MESH"]


def _world_bbox(objects):
    points = []
    for ob in objects:
        points.extend(ob.matrix_world @ Vector(c) for c in ob.bound_box)
    lo = Vector((min(p[i] for p in points) for i in range(3)))
    hi = Vector((max(p[i] for p in points) for i in range(3)))
    return (lo + hi) / 2.0, (hi - lo).length / 2.0


def _add_driver(owner, path, anchor, prop_name):
    fc = owner.driver_add(path)
    drv = fc.driver
    drv.type = "AVERAGE"
    for var in list(drv.variables):
        drv.variables.remove(var)
    var = drv.variables.new()
    var.name = "v"
    var.type = "SINGLE_PROP"
    target = var.targets[0]
    target.id_type = "OBJECT"
    target.id = anchor
    target.data_path = f'["{prop_name}"]'


def _attach(ob, anchor, tree, sock):
    """メンバー 1 体へのモディファイア付与＋配線."""
    mod = ob.modifiers.new(MODIFIER_NAME, "NODES")
    mod.node_group = tree
    mod.use_pin_to_last = True  # Armature/SubSurf/DataTransfer より常に後段
    getattr(mod.properties.inputs, sock["Anchor"]).value = anchor
    getattr(mod.properties.inputs, sock["CubeFalloff"]).value = bool(
        anchor.get("PP_FalloffShape", 0))
    for prop_name, input_name in PROP_TO_INPUT.items():
        _add_driver(getattr(mod.properties.inputs, sock[input_name]),
                    "value", anchor, prop_name)


def _detach(ob):
    """メンバー 1 体からドライバー＋モディファイアを除去."""
    mod = ob.modifiers.get(MODIFIER_NAME)
    if mod is None:
        return False
    if mod.node_group is not None:
        sock = core_nodes.socket_identifiers(mod.node_group)
        for input_name in PROP_TO_INPUT.values():
            if input_name not in sock:
                continue
            sock_struct = getattr(mod.properties.inputs, sock[input_name])
            try:
                sock_struct.driver_remove("value")
            except RuntimeError:
                pass
    ob.modifiers.remove(mod)
    return True


# ---------------------------------------------------------------- オペレーター


class PERSPRESS_OT_setup(bpy.types.Operator):
    """選択メッシュから新規グループを作成する"""

    bl_idname = "perspress.setup"
    bl_label = "Setup New Group"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(ob.type == "MESH" for ob in context.selected_objects)

    def execute(self, context):
        scene = context.scene
        if scene.camera is None:
            self.report({"ERROR"}, "シーンにアクティブカメラがありません")
            return {"CANCELLED"}

        meshes = _selected_meshes(context)
        targets = [ob for ob in meshes if MODIFIER_NAME not in ob.modifiers]
        skipped = len(meshes) - len(targets)
        if not targets:
            self.report({"WARNING"}, "対象なし (全てセットアップ済み)")
            return {"CANCELLED"}

        # アンカー位置・フォールオフ既定値は「アクティブオブジェクト (=顔メッシュ)」
        # の bbox のみから決める。選択全体の合成 bbox は胴体系メッシュで中心が
        # 引きずられるため使わない
        head_ref = (context.active_object
                    if context.active_object in meshes else meshes[0])
        center, radius = _world_bbox([head_ref])

        # Follow Target 指定があれば Child Of で追従。
        # ボーン指定時はポーズボーン中点をアンカー位置に使う (中心を取るのが楽なため)
        settings = scene.perspress
        follow = settings.follow_target
        pose_bone = None
        if follow is not None and follow.type == "ARMATURE" and settings.follow_bone:
            pose_bone = follow.pose.bones.get(settings.follow_bone)
            if pose_bone is None:
                self.report({"WARNING"},
                            f"ボーン '{settings.follow_bone}' が見つからず bbox 配置に切替")
            else:
                center = follow.matrix_world @ (
                    (pose_bone.head + pose_bone.tail) / 2.0)

        # アンカー生成 — idprop はリンク前に設定 (5.2 の鉄則, 計画書基盤 #3)
        anchor = bpy.data.objects.new(f"{ANCHOR_PREFIX}.{head_ref.name}", None)
        anchor.empty_display_type = "PLAIN_AXES"
        anchor.empty_display_size = radius * 0.6
        anchor["PP_Ratio"] = 3.0
        anchor["PP_Influence"] = 1.0
        anchor["PP_FalloffStart"] = round(radius, 3)
        anchor["PP_FalloffEnd"] = round(radius * 1.5, 3)
        anchor["PP_FalloffShape"] = 0
        anchor["PP_ui_open"] = True  # UIList の展開状態 (リンク前に設定)
        anchor.id_properties_ui("PP_Ratio").update(
            min=0.01, soft_min=0.2, soft_max=10.0, default=3.0,
            description="圧縮率 (仮想望遠焦点 / 実焦点)")
        anchor.id_properties_ui("PP_Influence").update(
            min=0.0, max=1.0, default=1.0, description="補正の効き (0-1)")
        anchor.id_properties_ui("PP_FalloffStart").update(
            min=0.0, default=anchor["PP_FalloffStart"],
            description="この距離まで補正 100% [アンカーローカル]")
        anchor.id_properties_ui("PP_FalloffEnd").update(
            min=0.0, default=anchor["PP_FalloffEnd"],
            description="この距離で補正 0% [アンカーローカル]")
        anchor.id_properties_ui("PP_FalloffShape").update(
            min=0, max=1, default=0, description="減衰形状 (0=球, 1=立方体)")
        anchor.location = center
        # ヘルパーはアクティブコレクションの状態に依存させない (シーンルート固定)
        scene.collection.objects.link(anchor)

        # Child Of (計画書基盤 #14 のレシピ)。対象は任意オブジェクト
        if follow is not None:
            # 生成直後のターゲットでも matrix_world を確定させてから inverse を計算
            context.view_layer.update()
            con = anchor.constraints.new("CHILD_OF")
            con.target = follow
            if pose_bone is not None:
                con.subtarget = settings.follow_bone
                con.inverse_matrix = (
                    follow.matrix_world @ pose_bone.matrix).inverted()
            else:
                con.inverse_matrix = follow.matrix_world.inverted()

        # フォールオフ可視化ギズモ (ワイヤー Empty = レンダリング非対象)
        # 向きは減衰フレームと同じくカメラに整列させる (Copy Rotation)
        for prop_name, gizmo_prefix in GIZMO_SPECS:
            giz = bpy.data.objects.new(f"{gizmo_prefix}.{head_ref.name}", None)
            giz.empty_display_type = SHAPE_DISPLAY[0]
            giz.parent = anchor
            giz.hide_select = True
            scene.collection.objects.link(giz)
            con = giz.constraints.new("COPY_ROTATION")
            con.target = scene.camera
            _add_driver(giz, "empty_display_size", anchor, prop_name)

        # 歪みプレビュー (Start 面のワイヤーメッシュを補正式で変形して表示)
        prev_mesh = bpy.data.meshes.new(f"{PREVIEW_PREFIX}.{head_ref.name}")
        preview = bpy.data.objects.new(f"{PREVIEW_PREFIX}.{head_ref.name}",
                                       prev_mesh)
        preview.parent = anchor
        preview.hide_select = True
        preview.hide_render = True  # 実メッシュのため明示必須 (計画書 R21)
        preview.display_type = "WIRE"
        scene.collection.objects.link(preview)
        ptree = core_nodes.ensure_preview_group()
        psock = core_nodes.socket_identifiers(ptree)
        pmod = preview.modifiers.new(PREVIEW_MODIFIER_NAME, "NODES")
        pmod.node_group = ptree
        getattr(pmod.properties.inputs, psock["Anchor"]).value = anchor
        for prop_name, input_name in PROP_TO_INPUT.items():
            _add_driver(getattr(pmod.properties.inputs, psock[input_name]),
                        "value", anchor, prop_name)

        tree = core_nodes.ensure_node_group()
        sock = core_nodes.socket_identifiers(tree)
        for ob in targets:
            _attach(ob, anchor, tree, sock)

        settings.active_anchor = anchor
        refresh_list(scene)
        context.view_layer.update()
        msg = (f"{len(targets)} オブジェクトで新規グループ作成 "
               f"(アンカー: {anchor.name} / 位置基準: "
               f"{'ボーン ' + settings.follow_bone if pose_bone else head_ref.name})")
        if skipped:
            msg += f" / {skipped} 件はセットアップ済みのためスキップ"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class PERSPRESS_OT_add_members(bpy.types.Operator):
    """選択中の未セットアップメッシュをアクティブグループへ追加する"""

    bl_idname = "perspress.add_members"
    bl_label = "Add Selected"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (active_anchor(context) is not None
                and any(ob.type == "MESH" for ob in context.selected_objects))

    def execute(self, context):
        anchor = active_anchor(context)
        meshes = _selected_meshes(context)
        targets = [ob for ob in meshes if MODIFIER_NAME not in ob.modifiers]
        skipped = len(meshes) - len(targets)
        if not targets:
            self.report({"WARNING"},
                        "追加対象なし (選択メッシュは全て既存グループ所属)")
            return {"CANCELLED"}

        tree = core_nodes.ensure_node_group()
        sock = core_nodes.socket_identifiers(tree)
        for ob in targets:
            _attach(ob, anchor, tree, sock)
        refresh_list(context.scene)
        context.view_layer.update()
        msg = f"{len(targets)} オブジェクトを {anchor.name} へ追加"
        if skipped:
            msg += f" / {skipped} 件は既存グループ所属のためスキップ"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class PERSPRESS_OT_remove_member(bpy.types.Operator):
    """指定メンバーだけをグループから解除する"""

    bl_idname = "perspress.remove_member"
    bl_label = "Remove Member"
    bl_options = {"REGISTER", "UNDO"}

    object_name: bpy.props.StringProperty()

    def execute(self, context):
        ob = bpy.data.objects.get(self.object_name)
        if ob is None or not _detach(ob):
            self.report({"WARNING"}, f"'{self.object_name}' は解除対象ではありません")
            return {"CANCELLED"}
        refresh_list(context.scene)
        self.report({"INFO"}, f"{ob.name} をグループから解除")
        return {"FINISHED"}


class PERSPRESS_OT_remove(bpy.types.Operator):
    """グループ一式 (メンバー・ギズモ・アンカー) を解除する"""

    bl_idname = "perspress.remove"
    bl_label = "Remove Group"
    bl_options = {"REGISTER", "UNDO"}

    anchor_name: bpy.props.StringProperty(default="")

    @classmethod
    def poll(cls, context):
        return (bool(scene_anchors(context))
                or anchor_of(context.active_object) is not None)

    def execute(self, context):
        anchor = None
        if self.anchor_name:
            candidate = bpy.data.objects.get(self.anchor_name)
            if is_anchor(candidate):
                anchor = candidate
        if anchor is None:
            anchor = active_anchor(context) or anchor_of(context.active_object)
        if anchor is None:
            self.report({"WARNING"}, "解除対象のグループがありません")
            return {"CANCELLED"}
        was_active = context.scene.perspress.active_anchor is anchor
        removed = sum(1 for ob in members_of(anchor) if _detach(ob))
        for child in list(anchor.children):
            if child.name.startswith("PP_"):
                data = child.data
                bpy.data.objects.remove(child, do_unlink=True)
                if isinstance(data, bpy.types.Mesh) and data.users == 0:
                    bpy.data.meshes.remove(data)
        bpy.data.objects.remove(anchor, do_unlink=True)
        if was_active:
            context.scene.perspress.active_anchor = None
        refresh_list(context.scene)
        self.report({"INFO"}, f"{removed} オブジェクトから解除しグループを削除")
        return {"FINISHED"}


class PERSPRESS_OT_set_active_group(bpy.types.Operator):
    """パネルの操作対象グループを切り替える"""

    bl_idname = "perspress.set_active_group"
    bl_label = "Set Active Group"
    bl_options = {"INTERNAL"}

    anchor_name: bpy.props.StringProperty()

    def execute(self, context):
        context.scene.perspress.active_anchor = bpy.data.objects.get(
            self.anchor_name)
        refresh_list(context.scene)
        return {"FINISHED"}


class PERSPRESS_OT_toggle_expand(bpy.types.Operator):
    """UIList 上のグループの展開/折りたたみを切り替える"""

    bl_idname = "perspress.toggle_expand"
    bl_label = "Toggle Group Expand"
    bl_options = {"INTERNAL"}

    anchor_name: bpy.props.StringProperty()

    def execute(self, context):
        anchor = bpy.data.objects.get(self.anchor_name)
        if not is_anchor(anchor):
            return {"CANCELLED"}
        anchor["PP_ui_open"] = not anchor.get("PP_ui_open", True)
        refresh_list(context.scene)
        return {"FINISHED"}


class PERSPRESS_OT_set_falloff_shape(bpy.types.Operator):
    """アクティブグループの減衰形状を切り替える (球 / 立方体)"""

    bl_idname = "perspress.set_falloff_shape"
    bl_label = "Set Falloff Shape"
    bl_options = {"REGISTER", "UNDO"}

    shape: bpy.props.IntProperty(min=0, max=1)

    @classmethod
    def poll(cls, context):
        return active_anchor(context) is not None

    def execute(self, context):
        anchor = active_anchor(context)
        anchor["PP_FalloffShape"] = self.shape
        for ob in members_of(anchor):
            mod = ob.modifiers[MODIFIER_NAME]
            sock = core_nodes.socket_identifiers(mod.node_group)
            getattr(mod.properties.inputs, sock["CubeFalloff"]).value = bool(
                self.shape)
            ob.update_tag()
        preview = preview_of(anchor)
        if preview is not None:
            pmod = preview.modifiers.get(PREVIEW_MODIFIER_NAME)
            if pmod is not None and pmod.node_group is not None:
                psock = core_nodes.socket_identifiers(pmod.node_group)
                getattr(pmod.properties.inputs,
                        psock["CubeFalloff"]).value = bool(self.shape)
                preview.update_tag()
        for giz in gizmos_of(anchor):
            giz.empty_display_type = SHAPE_DISPLAY[self.shape]
        context.view_layer.update()
        return {"FINISHED"}


CLASSES = (
    PERSPRESS_OT_setup,
    PERSPRESS_OT_add_members,
    PERSPRESS_OT_remove_member,
    PERSPRESS_OT_remove,
    PERSPRESS_OT_set_active_group,
    PERSPRESS_OT_toggle_expand,
    PERSPRESS_OT_set_falloff_shape,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
