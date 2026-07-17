"""PersPress GN ノードグループの構築 (本体 + プレビュー).

数理 (docs/implementation-plan.md §4, 検証済み):
    カメラ空間 (前方 -Z) で
        d      = -P.z, za = アンカー深度, Δ = za·(R-1)
        factor = R·d / (d + Δ)      # アンカー平面上で 1
        P'.xy  = P.xy · factor, P'.z = P.z   (深度保持)
    フォールオフは「アンカー位置 + カメラ回転 + アンカースケール」のフレーム F で
    形状メトリックを取る (0.3.0: 変形方向と範囲表示の直感を一致させるカメラ整列):
        p = F⁻¹ @ P_world
        SPHERE: |p| / CUBE: max(|x|,|y|,|z|)
        weight = Influence · smoothstep(FalloffStart..FalloffEnd -> 1..0)(metric)
    PreserveNormals ON: 変形前法線を Capture し SetMeshNormal(FREE) で書き戻す。
    本体の出力には常に pp_rest_position / pp_rest_normal を Store する (AOV 用途)。

プレビューグループ: Start 面の球/箱をフレーム F 上に生成し、本体と同じ式で
変形して表示する (「この範囲がこう潰れる」の可視化)。
"""
import math

import bpy

# 世代 = グループ内容の変更 (ソケット構成または内部ロジック) で +1 (計画書・版数規約)
GROUP_INTERFACE_VERSION = 3
GROUP_NAME = f"PersPress_v{GROUP_INTERFACE_VERSION}"
PREVIEW_GROUP_VERSION = 2
PREVIEW_GROUP_NAME = f"PersPressPreview_v{PREVIEW_GROUP_VERSION}"

REST_POSITION_ATTR = "pp_rest_position"
REST_NORMAL_ATTR = "pp_rest_normal"

INPUT_NAMES = ("Anchor", "Ratio", "Influence", "FalloffStart", "FalloffEnd",
               "PreserveNormals", "CubeFalloff")
PREVIEW_INPUT_NAMES = ("Anchor", "Ratio", "Influence", "FalloffStart",
                       "FalloffEnd", "CubeFalloff")
DRIVEN_INPUTS = ("Ratio", "Influence", "FalloffStart", "FalloffEnd")


def ensure_node_group():
    return _ensure(GROUP_NAME, INPUT_NAMES, _build_main)


def ensure_preview_group():
    return _ensure(PREVIEW_GROUP_NAME, PREVIEW_INPUT_NAMES, _build_preview)


def socket_identifiers(tree):
    """interface 名 -> socket identifier の辞書."""
    return {
        item.name: item.identifier
        for item in tree.interface.items_tree
        if item.item_type == "SOCKET" and item.in_out == "INPUT"
    }


def _ensure(name, required_inputs, builder):
    """現行世代のグループを取得 (無ければ構築).

    世代名で引くため旧世代とは衝突しない。同名グループのソケット構成が
    期待と異なる場合 (手編集等) のみ .broken 退避して再構築する。
    """
    tree = bpy.data.node_groups.get(name)
    if tree is not None and tree.bl_idname == "GeometryNodeTree":
        existing = {
            item.name for item in tree.interface.items_tree
            if item.item_type == "SOCKET" and item.in_out == "INPUT"
        }
        if all(n in existing for n in required_inputs):
            return tree
        tree.name = name + ".broken"
    return builder(name)


# ---------------------------------------------------------------- 共通部品


def _new_param_interface(iface, with_preserve):
    iface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    iface.new_socket("Anchor", in_out="INPUT", socket_type="NodeSocketObject")
    s = iface.new_socket("Ratio", in_out="INPUT", socket_type="NodeSocketFloat")
    s.default_value = 3.0
    s.min_value = 0.01
    s.max_value = 100.0
    s = iface.new_socket("Influence", in_out="INPUT", socket_type="NodeSocketFloat")
    s.default_value = 1.0
    s.min_value = 0.0
    s.max_value = 1.0
    s.subtype = "FACTOR"
    s = iface.new_socket("FalloffStart", in_out="INPUT", socket_type="NodeSocketFloat")
    s.default_value = 100.0
    s.min_value = 0.0
    s.subtype = "DISTANCE"
    s = iface.new_socket("FalloffEnd", in_out="INPUT", socket_type="NodeSocketFloat")
    s.default_value = 101.0
    s.min_value = 0.0
    s.subtype = "DISTANCE"
    if with_preserve:
        iface.new_socket("PreserveNormals", in_out="INPUT",
                         socket_type="NodeSocketBool")
    iface.new_socket("CubeFalloff", in_out="INPUT", socket_type="NodeSocketBool")
    iface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")


class _Ctx:
    """1 つのツリーに対するノード構築ヘルパー."""

    def __init__(self, tree):
        self.tree = tree
        self.n = tree.nodes
        self.ln = tree.links.new

    def new(self, type_name):
        return self.n.new(type_name)

    def fmath(self, op, a, b=None):
        node = self.new("ShaderNodeMath")
        node.operation = op
        if isinstance(a, (int, float)):
            node.inputs[0].default_value = a
        else:
            self.ln(a, node.inputs[0])
        if b is not None:
            if isinstance(b, (int, float)):
                node.inputs[1].default_value = b
            else:
                self.ln(b, node.inputs[1])
        return node.outputs[0]

    def transform_point(self, vec, matrix):
        node = self.new("FunctionNodeTransformPoint")
        self.ln(vec, node.inputs["Vector"])
        self.ln(matrix, node.inputs["Transform"])
        return node.outputs["Vector"]

    def invert(self, matrix):
        node = self.new("FunctionNodeInvertMatrix")
        self.ln(matrix, node.inputs["Matrix"])
        return node.outputs["Matrix"]


def _build_frames(c, anchor_socket):
    """カメラ/アンカー参照と減衰フレーム F を構築して dict で返す."""
    cam_info = c.new("GeometryNodeObjectInfo")
    c.ln(c.new("GeometryNodeInputActiveCamera").outputs["Active Camera"],
         cam_info.inputs["Object"])
    anchor_info = c.new("GeometryNodeObjectInfo")
    c.ln(anchor_socket, anchor_info.inputs["Object"])

    sep_cam = c.new("FunctionNodeSeparateTransform")
    c.ln(cam_info.outputs["Transform"], sep_cam.inputs["Transform"])
    frame = c.new("FunctionNodeCombineTransform")
    c.ln(anchor_info.outputs["Location"], frame.inputs["Translation"])
    c.ln(sep_cam.outputs["Rotation"], frame.inputs["Rotation"])
    c.ln(anchor_info.outputs["Scale"], frame.inputs["Scale"])

    return {
        "cam": cam_info.outputs["Transform"],
        "inv_cam": c.invert(cam_info.outputs["Transform"]),
        "anchor_loc": anchor_info.outputs["Location"],
        "frame": frame.outputs["Transform"],
        "inv_frame": c.invert(frame.outputs["Transform"]),
    }


def _build_remap(c, gi, refs, world_pos):
    """world_pos (field) -> 変形後ワールド位置 (field)."""
    to_cam = c.transform_point(world_pos, refs["inv_cam"])
    anchor_cam = c.transform_point(refs["anchor_loc"], refs["inv_cam"])

    sep_p = c.new("ShaderNodeSeparateXYZ")
    c.ln(to_cam, sep_p.inputs["Vector"])
    sep_a = c.new("ShaderNodeSeparateXYZ")
    c.ln(anchor_cam, sep_a.inputs["Vector"])

    d = c.fmath("MULTIPLY", sep_p.outputs["Z"], -1.0)
    za = c.fmath("MULTIPLY", sep_a.outputs["Z"], -1.0)
    delta = c.fmath("MULTIPLY", za,
                    c.fmath("SUBTRACT", gi.outputs["Ratio"], 1.0))
    factor = c.fmath("DIVIDE",
                     c.fmath("MULTIPLY", gi.outputs["Ratio"], d),
                     c.fmath("ADD", d, delta))
    comb = c.new("ShaderNodeCombineXYZ")
    c.ln(c.fmath("MULTIPLY", sep_p.outputs["X"], factor), comb.inputs["X"])
    c.ln(c.fmath("MULTIPLY", sep_p.outputs["Y"], factor), comb.inputs["Y"])
    c.ln(sep_p.outputs["Z"], comb.inputs["Z"])

    # フォールオフ: フレーム F 内の形状メトリック
    p_frame = c.transform_point(world_pos, refs["inv_frame"])
    sphere = c.new("ShaderNodeVectorMath")
    sphere.operation = "LENGTH"
    c.ln(p_frame, sphere.inputs[0])
    abs_p = c.new("ShaderNodeVectorMath")
    abs_p.operation = "ABSOLUTE"
    c.ln(p_frame, abs_p.inputs[0])
    sep_abs = c.new("ShaderNodeSeparateXYZ")
    c.ln(abs_p.outputs["Vector"], sep_abs.inputs["Vector"])
    cube = c.fmath("MAXIMUM",
                   c.fmath("MAXIMUM", sep_abs.outputs["X"], sep_abs.outputs["Y"]),
                   sep_abs.outputs["Z"])
    metric_switch = c.new("GeometryNodeSwitch")
    metric_switch.input_type = "FLOAT"
    c.ln(gi.outputs["CubeFalloff"], metric_switch.inputs["Switch"])
    c.ln(sphere.outputs["Value"], metric_switch.inputs["False"])
    c.ln(cube, metric_switch.inputs["True"])

    falloff = c.new("ShaderNodeMapRange")
    falloff.interpolation_type = "SMOOTHSTEP"
    c.ln(metric_switch.outputs["Output"], falloff.inputs["Value"])
    c.ln(gi.outputs["FalloffStart"], falloff.inputs["From Min"])
    c.ln(gi.outputs["FalloffEnd"], falloff.inputs["From Max"])
    falloff.inputs["To Min"].default_value = 1.0
    falloff.inputs["To Max"].default_value = 0.0
    weight = c.fmath("MULTIPLY", gi.outputs["Influence"],
                     falloff.outputs["Result"])

    mix = c.new("ShaderNodeMix")
    mix.data_type = "VECTOR"
    c.ln(weight, mix.inputs["Factor"])
    c.ln(to_cam, mix.inputs[4])                 # A
    c.ln(comb.outputs["Vector"], mix.inputs[5])  # B
    return c.transform_point(mix.outputs[1], refs["cam"])


# ---------------------------------------------------------------- 本体


def _build_main(name):
    tree = bpy.data.node_groups.new(name, "GeometryNodeTree")
    tree.is_modifier = True
    _new_param_interface(tree.interface, with_preserve=True)
    c = _Ctx(tree)
    gi = c.new("NodeGroupInput")
    go = c.new("NodeGroupOutput")

    self_info = c.new("GeometryNodeObjectInfo")
    c.ln(c.new("GeometryNodeSelfObject").outputs["Self Object"],
         self_info.inputs["Object"])
    inv_obj = c.invert(self_info.outputs["Transform"])
    refs = _build_frames(c, gi.outputs["Anchor"])

    # rest 属性 (変形前 = ポーズ後の値)
    pos = c.new("GeometryNodeInputPosition")
    nrm = c.new("GeometryNodeInputNormal")
    store_pos = c.new("GeometryNodeStoreNamedAttribute")
    store_pos.data_type = "FLOAT_VECTOR"
    store_pos.domain = "POINT"
    store_pos.inputs["Name"].default_value = REST_POSITION_ATTR
    c.ln(gi.outputs["Geometry"], store_pos.inputs["Geometry"])
    c.ln(pos.outputs["Position"], store_pos.inputs["Value"])
    store_nrm = c.new("GeometryNodeStoreNamedAttribute")
    store_nrm.data_type = "FLOAT_VECTOR"
    store_nrm.domain = "POINT"
    store_nrm.inputs["Name"].default_value = REST_NORMAL_ATTR
    c.ln(store_pos.outputs["Geometry"], store_nrm.inputs["Geometry"])
    c.ln(nrm.outputs["Normal"], store_nrm.inputs["Value"])
    base_geo = store_nrm.outputs["Geometry"]

    world_pos = c.transform_point(pos.outputs["Position"],
                                  self_info.outputs["Transform"])
    deformed_world = _build_remap(c, gi, refs, world_pos)
    new_pos = c.transform_point(deformed_world, inv_obj)

    # 経路 A: 素通し
    set_pos_plain = c.new("GeometryNodeSetPosition")
    c.ln(base_geo, set_pos_plain.inputs["Geometry"])
    c.ln(new_pos, set_pos_plain.inputs["Position"])

    # 経路 B: レスト法線保持
    cap = c.new("GeometryNodeCaptureAttribute")
    cap.domain = "POINT"
    cap.capture_items.new("VECTOR", "RestNormal")
    c.ln(base_geo, cap.inputs["Geometry"])
    c.ln(nrm.outputs["Normal"], cap.inputs["RestNormal"])
    set_pos_nfix = c.new("GeometryNodeSetPosition")
    c.ln(cap.outputs["Geometry"], set_pos_nfix.inputs["Geometry"])
    c.ln(new_pos, set_pos_nfix.inputs["Position"])
    set_nrm = c.new("GeometryNodeSetMeshNormal")
    set_nrm.mode = "FREE"
    set_nrm.domain = "POINT"
    c.ln(set_pos_nfix.outputs["Geometry"], set_nrm.inputs["Mesh"])
    nrm_in = next(s for s in set_nrm.inputs if s.type == "VECTOR")
    c.ln(cap.outputs["RestNormal"], nrm_in)

    switch = c.new("GeometryNodeSwitch")
    switch.input_type = "GEOMETRY"
    c.ln(gi.outputs["PreserveNormals"], switch.inputs["Switch"])
    c.ln(set_pos_plain.outputs["Geometry"], switch.inputs["False"])
    c.ln(set_nrm.outputs["Mesh"], switch.inputs["True"])
    c.ln(switch.outputs["Output"], go.inputs["Geometry"])
    return tree


# ---------------------------------------------------------------- プレビュー


def _build_preview(name):
    tree = bpy.data.node_groups.new(name, "GeometryNodeTree")
    tree.is_modifier = True
    _new_param_interface(tree.interface, with_preserve=False)
    c = _Ctx(tree)
    gi = c.new("NodeGroupInput")
    go = c.new("NodeGroupOutput")

    self_info = c.new("GeometryNodeObjectInfo")
    c.ln(c.new("GeometryNodeSelfObject").outputs["Self Object"],
         self_info.inputs["Object"])
    inv_obj = c.invert(self_info.outputs["Transform"])
    refs = _build_frames(c, gi.outputs["Anchor"])

    # Start 面のプリミティブ (フレームローカル)
    # 球: 直交する円 3 つ (Empty の球表示と同じ見た目。UV 球のワイヤー網は使わない)
    join = c.new("GeometryNodeJoinGeometry")
    for euler in ((0.0, 0.0, 0.0), (math.pi / 2, 0.0, 0.0),
                  (0.0, math.pi / 2, 0.0)):
        circ = c.new("GeometryNodeCurvePrimitiveCircle")
        circ.inputs["Resolution"].default_value = 48
        c.ln(gi.outputs["FalloffStart"], circ.inputs["Radius"])
        xform = c.new("GeometryNodeTransform")
        c.ln(circ.outputs["Curve"], xform.inputs["Geometry"])
        xform.inputs["Rotation"].default_value = euler
        c.ln(xform.outputs["Geometry"], join.inputs["Geometry"])

    cube_size = c.new("ShaderNodeCombineXYZ")
    side = c.fmath("MULTIPLY", gi.outputs["FalloffStart"], 2.0)
    c.ln(side, cube_size.inputs["X"])
    c.ln(side, cube_size.inputs["Y"])
    c.ln(side, cube_size.inputs["Z"])
    cube = c.new("GeometryNodeMeshCube")
    c.ln(cube_size.outputs["Vector"], cube.inputs["Size"])

    prim_switch = c.new("GeometryNodeSwitch")
    prim_switch.input_type = "GEOMETRY"
    c.ln(gi.outputs["CubeFalloff"], prim_switch.inputs["Switch"])
    c.ln(join.outputs["Geometry"], prim_switch.inputs["False"])
    c.ln(cube.outputs["Mesh"], prim_switch.inputs["True"])

    # フレームローカル -> ワールド -> 補正 -> プレビューオブジェクトローカル
    pos = c.new("GeometryNodeInputPosition")
    world_pos = c.transform_point(pos.outputs["Position"], refs["frame"])
    deformed_world = _build_remap(c, gi, refs, world_pos)
    local_pos = c.transform_point(deformed_world, inv_obj)

    set_pos = c.new("GeometryNodeSetPosition")
    c.ln(prim_switch.outputs["Output"], set_pos.inputs["Geometry"])
    c.ln(local_pos, set_pos.inputs["Position"])
    c.ln(set_pos.outputs["Geometry"], go.inputs["Geometry"])
    return tree
