"""PersPress — 逆パース補正アドオン.

広角レンズによる顔のパース歪みを、カメラ空間の深度リマップで補正する。
設計・検証記録: docs/implementation-plan.md
"""
import bpy
from bpy.app.handlers import persistent

from . import ops, props, ui

MODULES = (props, ops, ui)


@persistent
def _refresh_lists(*_args):
    """undo / ファイルロード後に Groups UIList をシーン実態へ同期する."""
    for scene in bpy.data.scenes:
        if getattr(scene, "perspress", None) is not None:
            try:
                ops.refresh_list(scene)
            except Exception:
                pass


def _refresh_once():
    _refresh_lists()
    return None  # タイマー解除


def register():
    for mod in MODULES:
        mod.register()
    bpy.app.handlers.undo_post.append(_refresh_lists)
    bpy.app.handlers.redo_post.append(_refresh_lists)
    bpy.app.handlers.load_post.append(_refresh_lists)
    # 有効化時点で既にグループが存在するシーンへの初期同期
    bpy.app.timers.register(_refresh_once, first_interval=0.1)


def unregister():
    for handler_list in (bpy.app.handlers.undo_post,
                         bpy.app.handlers.redo_post,
                         bpy.app.handlers.load_post):
        if _refresh_lists in handler_list:
            handler_list.remove(_refresh_lists)
    for mod in reversed(MODULES):
        mod.unregister()
