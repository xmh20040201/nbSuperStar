# -*- coding: utf-8 -*-
"""账号模型（从 api/base.py 迁入，api/base.py 继续 re-export 保持兼容）。"""


class Account:
    username = None
    password = None
    last_login = None
    isSuccess = None

    def __init__(self, _username=None, _password=None):
        self.username = _username
        self.password = _password

    def __repr__(self):  # 避免密码被打进日志
        return f"Account(username={self.username!r}, password=***)"


__all__ = ["Account"]
