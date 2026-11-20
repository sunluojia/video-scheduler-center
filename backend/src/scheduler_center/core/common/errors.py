class NotFound(LookupError):
    """请求的记录不存在。"""


class Invalid(ValueError):
    """提交的内容无法处理，原因可以直接展示给用户。"""


class Conflict(RuntimeError):
    """当前状态不允许这个操作。"""


class Unavailable(RuntimeError):
    """执行程序不在线或无法访问。"""


class Rejected(RuntimeError):
    """执行程序拒绝了转发的请求。"""
