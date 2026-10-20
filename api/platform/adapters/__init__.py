# -*- coding: utf-8 -*-
"""平台 adapter 包。

每个模块导出一个 ``Platform`` 子类并用 ``@register_platform`` 注册；
本文件负责 import 它们以触发注册（``api.platform`` 被导入时执行一次）。
"""
from api.platform.adapters.chaoxing import ChaoxingPlatform

__all__ = ["ChaoxingPlatform"]
