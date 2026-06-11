"""
刮刮乐远程控制模块
通过 WebSocket 实时传输页面截图到前端，并接收用户操作
"""

import asyncio
import base64
import json
from typing import Optional, Dict, Any
from loguru import logger
from playwright.async_api import Page


class CaptchaRemoteController:
    """刮刮乐远程控制器"""

    def __init__(self):
        self.active_sessions: Dict[str, Dict[str, Any]] = {}
        self.websocket_connections: Dict[str, Any] = {}
        self.recording_enabled: bool = True
        self.session_recordings: Dict[str, list] = {}

    async def create_session(self, session_id: str, page: Page) -> Dict[str, str]:
        session_info = await self._get_captcha_info(page)
        screenshot_bytes = await self._screenshot_captcha_area(page, session_info)
        screenshot_base64 = base64.b64encode(screenshot_bytes).decode('utf-8')

        try:
            viewport = page.viewport_size
            if viewport is None:
                viewport = await page.evaluate("() => ({width: window.innerWidth, height: window.innerHeight})")
        except Exception:
            viewport = {'width': 1280, 'height': 720}

        self.active_sessions[session_id] = {
            'page': page,
            'screenshot': screenshot_base64,
            'captcha_info': session_info,
            'completed': False,
            'viewport': viewport
        }

        logger.info(f"创建远程控制会话: {session_id}")

        return {
            'session_id': session_id,
            'screenshot': screenshot_base64,
            'captcha_info': session_info,
            'viewport': self.active_sessions[session_id]['viewport']
        }

    async def _screenshot_captcha_area(self, page: Page, captcha_info: Dict[str, Any]) -> bytes:
        try:
            if captcha_info and 'x' in captcha_info:
                x = max(0, captcha_info['x'] - 10)
                y = max(0, captcha_info['y'] - 10)
                width = captcha_info['width'] + 20
                height = captcha_info['height'] + 20

                screenshot_bytes = await page.screenshot(
                    type='jpeg',
                    quality=80,
                    clip={'x': x, 'y': y, 'width': width, 'height': height}
                )
                logger.info(f"截取验证码容器: {width}x{height}")
                return screenshot_bytes
            else:
                logger.warning("未找到滑块位置，截取整个页面")
                return await page.screenshot(type='jpeg', quality=75, full_page=False)

        except Exception as e:
            logger.warning(f"截取滑块区域失败，使用全页面: {e}")
            return await page.screenshot(type='jpeg', quality=75, full_page=False)

    async def _get_captcha_info(self, page: Page) -> Dict[str, Any]:
        try:
            container_selectors = [
                '#nocaptcha',
                '.scratch-captcha-container',
                '[id*="captcha"]',
                '.nc-container'
            ]

            for selector in container_selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        box = await element.bounding_box()
                        if box and box['width'] > 100 and box['height'] > 100:
                            logger.info(f"在主页面找到验证码容器: {selector}, 大小: {box['width']}x{box['height']}")
                            return {
                                'selector': selector,
                                'x': box['x'],
                                'y': box['y'],
                                'width': box['width'],
                                'height': box['height'],
                                'in_iframe': False
                            }
                except Exception as e:
                    logger.debug(f"检查选择器 {selector} 失败: {e}")
                    continue

            frames = page.frames
            for frame in frames:
                if frame != page.main_frame:
                    for selector in container_selectors:
                        try:
                            element = await frame.query_selector(selector)
                            if element:
                                box = await element.bounding_box()
                                if box and box['width'] > 100 and box['height'] > 100:
                                    logger.info(f"在iframe找到验证码容器: {selector}, 大小: {box['width']}x{box['height']}")
                                    return {
                                        'selector': selector,
                                        'x': box['x'],
                                        'y': box['y'],
                                        'width': box['width'],
                                        'height': box['height'],
                                        'in_iframe': True
                                    }
                        except Exception as e:
                            logger.debug(f"iframe检查选择器 {selector} 失败: {e}")
                            continue

            logger.warning("未找到验证码容器")
            return None

        except Exception as e:
            logger.error(f"获取滑块信息失败: {e}")
            return None

    async def update_screenshot(self, session_id: str, quality: int = 75) -> Optional[str]:
        if session_id not in self.active_sessions:
            return None

        try:
            page = self.active_sessions[session_id]['page']
            captcha_info = self.active_sessions[session_id].get('captcha_info')

            if captcha_info and 'x' in captcha_info:
                x = max(0, captcha_info['x'] - 10)
                y = max(0, captcha_info['y'] - 10)
                width = captcha_info['width'] + 20
                height = captcha_info['height'] + 20

                screenshot_bytes = await page.screenshot(
                    type='jpeg',
                    quality=quality,
                    clip={'x': x, 'y': y, 'width': width, 'height': height}
                )
            else:
                screenshot_bytes = await page.screenshot(
                    type='jpeg',
                    quality=quality,
                    full_page=False
                )

            screenshot_base64 = base64.b64encode(screenshot_bytes).decode('utf-8')
            self.active_sessions[session_id]['screenshot'] = screenshot_base64
            return screenshot_base64

        except Exception as e:
            logger.error(f"更新截图失败: {e}")
            return None

    async def handle_mouse_event(self, session_id: str, event_type: str, x: int, y: int) -> bool:
        if session_id not in self.active_sessions:
            logger.warning(f"会话不存在: {session_id}")
            return False

        try:
            page = self.active_sessions[session_id]['page']

            if event_type == 'down':
                await page.mouse.move(x, y)
                await page.mouse.down()
                logger.debug(f"鼠标按下: ({x}, {y})")
            elif event_type == 'move':
                await page.mouse.move(x, y)
                logger.debug(f"鼠标移动: ({x}, {y})")
            elif event_type == 'up':
                await page.mouse.up()
                logger.debug(f"鼠标释放: ({x}, {y})")
            else:
                logger.warning(f"未知事件类型: {event_type}")
                return False

            return True

        except Exception as e:
            logger.error(f"处理鼠标事件失败: {e}")
            return False

    async def check_completion(self, session_id: str) -> bool:
        if session_id not in self.active_sessions:
            return False

        try:
            page = self.active_sessions[session_id]['page']

            captcha_selectors = [
                '#nocaptcha',
                '#scratch-captcha-btn',
                '.scratch-captcha-container',
                '.scratch-captcha-slider'
            ]

            found_visible_captcha = False

            for selector in captcha_selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        is_visible = await element.is_visible()
                        if is_visible:
                            logger.debug(f"主页面发现可见滑块: {selector}")
                            found_visible_captcha = True
                            break
                except Exception:
                    continue

            if found_visible_captcha:
                return False

            frames = page.frames
            for frame in frames:
                if frame != page.main_frame:
                    for selector in captcha_selectors:
                        try:
                            element = await frame.query_selector(selector)
                            if element:
                                is_visible = await element.is_visible()
                                if is_visible:
                                    logger.debug(f"iframe中发现可见滑块: {selector}")
                                    found_visible_captcha = True
                                    break
                        except Exception:
                            continue
                    if found_visible_captcha:
                        break

            if found_visible_captcha:
                return False

            try:
                page_content = await page.content()
                captcha_keywords = ['scratch-captcha', 'nocaptcha', 'slider-btn']
                keyword_count = sum(1 for kw in captcha_keywords if kw in page_content)
                if keyword_count >= 2:
                    logger.debug(f"页面中仍有 {keyword_count} 个滑块关键词")
                    return False
            except Exception:
                pass

            logger.success(f"验证完成（所有滑块元素已消失）: {session_id}")
            self.active_sessions[session_id]['completed'] = True
            return True

        except Exception as e:
            logger.error(f"检查完成状态失败: {e}")
            return False

    def _record_event(self, session_id: str, event_type: str, x: int, y: int):
        if session_id not in self.session_recordings:
            self.session_recordings[session_id] = []
        now = asyncio.get_event_loop().time()
        self.session_recordings[session_id].append({
            "event_type": event_type,
            "x": x,
            "y": y,
            "timestamp": now,
        })

    def finish_recording(self, session_id: str) -> Optional[dict]:
        events = self.session_recordings.pop(session_id, [])
        if not events or len(events) < 3:
            return None

        down_events = [e for e in events if e["event_type"] == "down"]
        move_events = [e for e in events if e["event_type"] == "move"]
        up_events = [e for e in events if e["event_type"] == "up"]

        if not down_events or not up_events:
            return None

        down = down_events[0]
        up = up_events[-1]
        start_x, start_y = down["x"], down["y"]

        points = []
        prev_time = down["timestamp"]
        prev_x, prev_y = start_x, start_y

        points.append([0, 0, 120])

        for evt in move_events:
            dt_ms = max(5, (evt["timestamp"] - prev_time) * 1000)
            dx = evt["x"] - start_x
            dy = evt["y"] - start_y
            if abs(evt["x"] - prev_x) < 0.5 and abs(evt["y"] - prev_y) < 0.5 and dt_ms < 10:
                continue
            points.append([round(dx, 2), round(dy, 2), round(dt_ms, 1)])
            prev_time = evt["timestamp"]
            prev_x, prev_y = evt["x"], evt["y"]

        final_dt = max(50, (up["timestamp"] - prev_time) * 1000)
        final_dx = up["x"] - start_x
        final_dy = up["y"] - start_y
        points.append([round(final_dx, 2), round(final_dy, 2), round(final_dt, 1)])

        distance = abs(final_dx)
        duration_ms = (up["timestamp"] - down["timestamp"]) * 1000

        self.session_recordings.pop(session_id, None)

        return {
            "points": points,
            "distance": round(distance, 1),
            "duration_ms": round(duration_ms, 1),
        }

    def is_completed(self, session_id: str) -> bool:
        if session_id not in self.active_sessions:
            return False
        return self.active_sessions[session_id].get('completed', False)

    def session_exists(self, session_id: str) -> bool:
        return session_id in self.active_sessions

    async def close_session(self, session_id: str):
        if session_id in self.active_sessions:
            del self.active_sessions[session_id]
            logger.info(f"关闭远程控制会话: {session_id}")

    async def auto_refresh_screenshot(self, session_id: str, interval: float = 1.0):
        last_update_time = asyncio.get_event_loop().time()

        while session_id in self.active_sessions and not self.is_completed(session_id):
            try:
                current_time = asyncio.get_event_loop().time()

                if current_time - last_update_time >= interval:
                    screenshot = await self.update_screenshot(session_id, quality=55)

                    if screenshot and session_id in self.websocket_connections:
                        try:
                            ws = self.websocket_connections[session_id]
                            await ws.send_json({
                                'type': 'screenshot_update',
                                'screenshot': screenshot
                            })
                            last_update_time = current_time
                        except Exception:
                            break

                await asyncio.sleep(0.5)

            except Exception as e:
                logger.error(f"自动刷新截图失败: {e}")
                await asyncio.sleep(1)
