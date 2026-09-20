"""账号标识 → 安全文件名片段的单一清洗逻辑

所有把（可能不可信的）账号/cookie id 拼进文件名/目录/并发槽位身份的逻辑
都必须走这里，避免各模块各自实现近似清洗导致行为漂移。历史上有三份近似
实现（_concurrency 的槽位身份、solver 的 profile 目录、轨迹池的 cookie
子目录），已全部收敛到本模块。
"""

# Windows 保留设备名：作为文件名/目录名会触发 OSError（大小写不敏感，
# NT 内核把 CON/PRN/AUX/NUL/COM1-9/LPT1-9 一律视为设备名）。
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def sanitize_pure_user_id(user_id) -> str:
    """把账号标识清洗成只能作为单一路径段的安全文件名基调。

    清洗只保留字母数字与 `-_ .`，去掉全部路径分隔符并收敛 `..` 片段，
    结果不以 `.` 开头或结尾；空结果回落到 "default"；恰好命中 Windows
    保留名的追加 `_` 规避（如 `CON` → `CON_`），否则在 NTFS 上建目录会
    抛 OSError、在并发槽位里身份错乱。
    """
    cleaned = "".join(c for c in str(user_id or "") if c.isalnum() or c in "-_.")
    cleaned = cleaned.strip(".").replace("..", ".")
    if not cleaned:
        return "default"
    # NT 把 CON.txt / COM1.log 也当设备名；只比第一段（第一个点之前）。
    stem = cleaned.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED_NAMES:
        return cleaned + "_"
    return cleaned