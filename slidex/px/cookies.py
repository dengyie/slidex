"""PX cookie → 四件套（px3/pxde/pxvid/pxcts）的纯逻辑。

只做三件事：从浏览器 cookie jar 里挑出 PX 键、判定 ``:1000:`` 通过级、
把打包串解成四个字段。不碰页面、不碰网络，单测直接喂 dict。

PX 的 cookie 名带 ``_`` 前缀（``_px3``），部分站点/版本去掉前缀或换成
驼峰，一律按「去掉前导下划线、忽略大小写」归一化后再查。
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import unquote

# 四件套的规范名（对外输出的键）
TOKEN_FIELDS: Tuple[str, ...] = ("px3", "pxde", "pxvid", "pxcts")

# 判定「通过级」的段位：被 PX 拒绝的 px3 段位低（如 :200:），通过级为 :1000:
PASS_SCORE_MARKER = ":1000:"

# 已知的非四件套 PX 键，避免把 _pxhd 之类误当 token 回传
_IGNORED_PX_KEYS = frozenset({"pxhd", "pxcts_h", "px2"})


def _norm_key(name: Any) -> str:
    return str(name or "").strip().lstrip("_").lower()


def redact_token(value: str, *, head: int = 6) -> str:
    """日志脱敏：只留前缀与长度，绝不打全值（需求单 §6 不落盘/不打印）。"""
    text = value or ""
    if len(text) <= head + 4:
        return "***"
    return f"{text[:head]}…({len(text)}B)"


def is_pass_token(px3: str) -> bool:
    """需求单 §3.3：px3 必须含 ``:1000:`` 才算通过级。"""
    return bool(px3) and PASS_SCORE_MARKER in str(px3)


def _cookie_items(cookies: Iterable[Any]) -> List[Tuple[str, str]]:
    """接受 Playwright cookie dict、``{name: value}`` 映射或 (name, value) 对。"""
    items: List[Tuple[str, str]] = []
    if isinstance(cookies, Mapping):
        for name, value in cookies.items():
            items.append((str(name), str(value if value is not None else "")))
        return items
    for item in cookies or ():
        if isinstance(item, Mapping):
            name = item.get("name")
            value = item.get("value")
            if name is not None:
                items.append((str(name), str(value if value is not None else "")))
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            items.append((str(item[0]), str(item[1])))
    return items


def extract_px_tokens(
    cookies: Iterable[Any],
    *,
    extra: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """从 cookie 集合里抽四件套；抽不到的字段留空由上层判定。

    ``_px3`` 的 cookie 值就是 px3 token 本体（collector 下发、含 ``:1000:``
    段位）；``_pxde`` 是 JSON 串，保持原样最安全——risk/verify 只透传。
    """
    found: Dict[str, str] = {}
    for raw_name, value in _cookie_items(cookies):
        key = _norm_key(raw_name)
        if not value or key in _IGNORED_PX_KEYS:
            continue
        if key in TOKEN_FIELDS and key not in found:
            found[key] = value
    for raw_name, value in (extra or {}).items():
        key = _norm_key(raw_name)
        if value and key in TOKEN_FIELDS and key not in found:
            found[key] = str(value)
    return {field: found.get(field, "") for field in TOKEN_FIELDS}


def looks_blocked(*, status: Optional[int] = None, body: str = "", title: str = "") -> bool:
    """粗判「被 PX 拦截/拉黑」的确定性失败，便于 fail-fast 而不是等超时。"""
    if status in {403, 429}:
        return True
    haystack = f"{title}\n{body}".lower()
    for needle in (
        "access to this page has been denied",
        "please verify you are a human",
        "px-block",
        "px-captcha",
        "captcha-frame",
        "sorry, you have been blocked",
    ):
        if needle in haystack:
            return True
    return False


def _try_json(text: str) -> Optional[Any]:
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        return json.loads(stripped)
    except (ValueError, TypeError):
        return None


def _try_base64_json(text: str) -> Optional[Any]:
    """packed 串常见形态：base64(JSON) 或 url-safe base64。"""
    stripped = text.strip()
    if len(stripped) < 8 or not stripped[0].isalnum():
        return None
    padded = stripped + "=" * (-len(stripped) % 4)
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            raw = decoder(padded.encode("ascii", "ignore"))
        except (binascii.Error, ValueError):
            continue
        if not raw:
            continue
        parsed = _try_json(raw.decode("utf-8", errors="ignore"))
        if parsed is not None:
            return parsed
    return None


def _collect_from_object(obj: Any, into: Dict[str, str]) -> None:
    if isinstance(obj, Mapping):
        for name, value in obj.items():
            key = _norm_key(name)
            if key in TOKEN_FIELDS and isinstance(value, str) and value and key not in into:
                into[key] = value
            elif isinstance(value, (Mapping, list, tuple)):
                _collect_from_object(value, into)
        return
    if isinstance(obj, (list, tuple)):
        # [{"name":"_px3","value":"..."}, ...] 形式
        for item in obj:
            if isinstance(item, Mapping) and "name" in item and "value" in item:
                key = _norm_key(item.get("name"))
                value = item.get("value")
                if key in TOKEN_FIELDS and isinstance(value, str) and value and key not in into:
                    into[key] = value
            elif isinstance(item, Mapping):
                _collect_from_object(item, into)


def _collect_from_querystring(text: str, into: Dict[str, str]) -> None:
    """``_px3=...&_pxde=...`` 形态（含被 quote 过的值）。"""
    if "=" not in text or ";" in text[:1]:
        return
    for pair in text.split("&"):
        name, _, value = pair.partition("=")
        key = _norm_key(unquote(name))
        if key in TOKEN_FIELDS and value and key not in into:
            into[key] = unquote(value)


def parse_packed_token(text: str) -> Dict[str, str]:
    """把打包串解成四件套；解不出返回空 dict（绝不抛）。"""
    if not isinstance(text, str) or not text.strip():
        return {}
    stripped = text.strip().strip('"').strip("'")
    found: Dict[str, str] = {}

    for candidate in (_try_json(stripped), _try_base64_json(stripped)):
        if candidate is not None:
            _collect_from_object(candidate, found)
            if found:
                return {field: found.get(field, "") for field in TOKEN_FIELDS}
    _collect_from_querystring(stripped, found)
    if found:
        return {field: found.get(field, "") for field in TOKEN_FIELDS}

    # 兜底：形如 "1:a:1000:xxx::" 的 pxvid 里嵌 px3，或 "xxx:::yyy" 三段拼接
    parts = stripped.split("~~~")
    if len(parts) >= 4:
        for field, value in zip(TOKEN_FIELDS, parts):
            found[field] = value.strip()
        return {field: found.get(field, "") for field in TOKEN_FIELDS}
    return {}


def missing_fields(tokens: Sequence[Mapping[str, str]] | Mapping[str, str]) -> List[str]:
    """列出空字段，便于 error message 精确到缺哪一个。"""
    if isinstance(tokens, Mapping):
        return [field for field in TOKEN_FIELDS if not tokens.get(field)]
    merged: Dict[str, str] = {}
    for item in tokens:
        for field, value in (item or {}).items():
            if value and not merged.get(field):
                merged[field] = value
    return [field for field in TOKEN_FIELDS if not merged.get(field)]
