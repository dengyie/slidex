"""0.12.2 回归：登录页 iframe 可见性判据 + punish 后补提交。

2026-11-14 线上连续 93 次账密登录全部停在「alibaba-login-box 是普通登录页 →
请扫码」，两次白等 450 秒人工验证。两个缺陷叠加：

1. `alibaba-login-box` 只按 id 判定，不校验可见性——滑块/punish 通过后页面上
   残留的隐藏登录框被当成「待扫码」；
2. punish 页滑块通过后页面会退回登录表单，需要再提交一次，旧流程直接转人工。

这里锁定两处判据，避免修一处被另一条路径绕过。
"""

from slidex.stealth import XianyuSliderStealth


class _FakeFrame:
    def __init__(self, url="https://passport.goofish.com/mini_login.htm", parent=None):
        self.url = url
        self._parent = parent

    def query_selector(self, selector):
        return None

    def frame_element(self):
        return self._parent


class _FakeIframe:
    def __init__(self, visible, frame):
        self._visible = visible
        self._frame = frame

    def get_attribute(self, name):
        return "alibaba-login-box" if name == "id" else None

    def is_visible(self):
        return self._visible

    def content_frame(self):
        return self._frame


class _FakePage:
    def __init__(self, iframes=(), frames=()):
        self.frames = list(frames)
        self._iframes = list(iframes)

    def query_selector(self, selector):
        return None

    def query_selector_all(self, selector):
        return list(self._iframes) if selector == "iframe" else []


def _stealth():
    # 该分支是纯判定逻辑，不碰浏览器资源，也不该占用并发槽位
    s = object.__new__(XianyuSliderStealth)
    s.pure_user_id = "regress"
    s._safe_page_url = lambda page: "https://www.goofish.com/im"
    s._detect_verification_type = lambda target: "login_page"
    s._page_has_login_form = lambda page: False
    s._looks_like_verification_url = lambda url: False
    s._capture_verification_screenshot = lambda page, frame=None, iframe_selector=None: None
    return s


def test_hidden_login_box_iframe_is_not_treated_as_pending_scan():
    """隐藏的残留登录框不能上抛成「请扫码」，否则白等整段人工验证时间。"""
    frame = _FakeFrame()
    iframe = _FakeIframe(visible=False, frame=frame)
    frame._parent = iframe  # 第二条父 iframe 扫描路径同样要复用可见性判据
    page = _FakePage(iframes=[iframe], frames=[frame])

    has_verification, wrapper = _stealth()._detect_qr_code_verification(page)

    assert has_verification is False
    assert wrapper is None


def test_visible_login_box_iframe_still_raises_pending_scan():
    """真正可见的登录框仍要交给人工验证流程，不能被可见性判据误伤。"""
    frame = _FakeFrame()
    iframe = _FakeIframe(visible=True, frame=frame)
    frame._parent = iframe
    page = _FakePage(iframes=[iframe], frames=[frame])

    has_verification, wrapper = _stealth()._detect_qr_code_verification(page)

    assert has_verification is True
    assert getattr(wrapper, "verification_type", None) == "login_page"


def test_is_handle_visible_swallows_closed_handle_errors():
    s = _stealth()

    class _Closed:
        def is_visible(self):
            raise RuntimeError("Target page, context or browser has been closed")

    assert s._is_handle_visible(_Closed()) is False
    assert s._is_handle_visible(None) is False


def test_resubmit_login_after_punish_clicks_submit_once():
    """滑块过了、登录表单还在 → 补点一次登录按钮。"""
    s = _stealth()
    clicked = []

    class _Button:
        def click(self):
            clicked.append("submit")

    s._query_first_visible = lambda frame, selectors: (_Button(), "button.password-login")

    assert s._resubmit_login_after_punish(_FakePage(frames=[_FakeFrame()]), None, s._get_password_login_selectors())
    assert clicked == ["submit"]


def test_resubmit_login_after_punish_falls_back_to_enter():
    """登录按钮找不到时用回车补提交，不能静默放弃。"""
    s = _stealth()
    pressed = []
    selectors = {"password": ["#pw"], "submit": ["#submit"]}

    class _Input:
        def press(self, key):
            pressed.append(key)

    def _query(frame, wanted):
        # 密码框可见，按钮不可见
        if wanted is selectors["submit"]:
            return None, None
        return _Input(), "#fm-login-password"

    s._query_first_visible = _query

    assert s._resubmit_login_after_punish(_FakePage(frames=[_FakeFrame()]), None, selectors)
    assert pressed == ["Enter"]


def test_resubmit_login_after_punish_noop_without_visible_password_input():
    """表单已经不在页面上时不得乱点，避免把人脸/短信验证页点坏。"""
    s = _stealth()
    s._query_first_visible = lambda frame, selectors: (None, None)

    assert s._resubmit_login_after_punish(_FakePage(frames=[_FakeFrame()]), None, s._get_password_login_selectors()) is False
