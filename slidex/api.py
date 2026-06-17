"""
刮刮乐远程控制 API 路由
提供 WebSocket 和 HTTP 接口用于远程操作滑块验证
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Header, Query
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import Optional, List
import asyncio
import json
import os
from loguru import logger

from slidex.remote import captcha_controller
from slidex._trajectory_pool import SliderTrajectoryPool

trajectory_pool = SliderTrajectoryPool()

# 创建路由器
router = APIRouter(prefix="/api/captcha", tags=["captcha"])


def _verify_session_or_404(session_id: str, token: Optional[str]) -> None:
    if session_id not in captcha_controller.active_sessions:
        raise HTTPException(status_code=404, detail="会话不存在")
    if not captcha_controller.verify_session_token(session_id, token):
        raise HTTPException(status_code=403, detail="无效会话令牌")


def _json_for_script(value: Optional[str]) -> str:
    return json.dumps(value).replace("</", "<\\/")


class MouseEvent(BaseModel):
    """鼠标事件模型"""
    session_id: str
    event_type: str  # down, move, up
    x: int
    y: int


class TrajectorySubmitRequest(BaseModel):
    """轨迹提交请求模型"""
    session_id: str
    cookie_id: str
    points: List[List[float]]  # [[x, y, delay_ms], ...]
    distance: float
    verify_url: str = ""


class SessionCheckRequest(BaseModel):
    """会话检查请求"""
    session_id: str


# =============================================================================
# WebSocket 端点 - 实时通信
# =============================================================================

@router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str, token: Optional[str] = Query(default=None)):
    await websocket.accept()
    logger.info(f"WebSocket 连接建立: {session_id}")

    if not captcha_controller.verify_session_token(session_id, token):
        await websocket.send_json({
            'type': 'error',
            'message': '无效会话令牌'
        })
        await websocket.close(code=1008)
        return

    captcha_controller.websocket_connections[session_id] = websocket

    try:
        if session_id in captcha_controller.active_sessions:
            session_data = captcha_controller.active_sessions[session_id]
            await websocket.send_json({
                'type': 'session_info',
                'screenshot': session_data['screenshot'],
                'captcha_info': session_data['captcha_info'],
                'viewport': session_data['viewport'],
                'challenge_type': session_data.get('challenge_type', 'slider_captcha'),
                'audit': session_data.get('audit', []),
            })
        else:
            await websocket.send_json({
                'type': 'error',
                'message': '会话不存在'
            })
            await websocket.close()
            return

        while True:
            data = await websocket.receive_json()
            msg_type = data.get('type')

            if msg_type == 'mouse_event':
                event_type = data.get('event_type')
                x = data.get('x')
                y = data.get('y')

                success = await captcha_controller.handle_mouse_event(
                    session_id, event_type, x, y
                )

                if success:
                    if event_type == 'up':
                        await asyncio.sleep(1.0)
                        completed = await captcha_controller.check_completion(session_id)

                        if completed:
                            await asyncio.sleep(0.5)
                            completed = await captcha_controller.check_completion(session_id)

                        if completed:
                            await websocket.send_json({
                                'type': 'completed',
                                'message': '验证成功！'
                            })
                            logger.success(f"验证完成: {session_id}")
                            break
                        else:
                            screenshot = await captcha_controller.update_screenshot(session_id)
                            if screenshot:
                                await websocket.send_json({
                                    'type': 'screenshot_update',
                                    'screenshot': screenshot
                                })
                    else:
                        if event_type in ['down', 'move']:
                            screenshot = await captcha_controller.update_screenshot(session_id, quality=30)
                            if screenshot:
                                await websocket.send_json({
                                    'type': 'screenshot_update',
                                    'screenshot': screenshot
                                })

            elif msg_type == 'check_completion':
                completed = await captcha_controller.check_completion(session_id)
                await websocket.send_json({
                    'type': 'completion_status',
                    'completed': completed
                })

                if completed:
                    break

            elif msg_type == 'ping':
                await websocket.send_json({'type': 'pong'})

    except WebSocketDisconnect:
        logger.info(f"WebSocket 连接断开: {session_id}")

    except Exception as e:
        logger.error(f"WebSocket 错误: {e}")
        import traceback
        logger.error(traceback.format_exc())

    finally:
        if session_id in captcha_controller.websocket_connections:
            del captcha_controller.websocket_connections[session_id]
        logger.info(f"WebSocket 会话结束: {session_id}")


# =============================================================================
# HTTP 端点 - REST API
# =============================================================================

@router.get("/sessions")
async def get_active_sessions():
    return {
        'count': len(captcha_controller.active_sessions),
    }


@router.get("/session/{session_id}")
async def get_session_info(session_id: str, x_captcha_token: Optional[str] = Header(default=None)):
    _verify_session_or_404(session_id, x_captcha_token)

    session_data = captcha_controller.active_sessions[session_id]

    return {
        'session_id': session_id,
        'screenshot': session_data['screenshot'],
        'captcha_info': session_data['captcha_info'],
        'viewport': session_data['viewport'],
        'challenge_type': session_data.get('challenge_type', 'slider_captcha'),
        'audit': session_data.get('audit', []),
        'completed': session_data.get('completed', False)
    }


@router.get("/screenshot/{session_id}")
async def get_screenshot(session_id: str, x_captcha_token: Optional[str] = Header(default=None)):
    _verify_session_or_404(session_id, x_captcha_token)
    screenshot = await captcha_controller.update_screenshot(session_id)

    if not screenshot:
        raise HTTPException(status_code=404, detail="无法获取截图")

    return {'screenshot': screenshot}


@router.post("/mouse_event")
async def handle_mouse_event(event: MouseEvent, x_captcha_token: Optional[str] = Header(default=None)):
    _verify_session_or_404(event.session_id, x_captcha_token)
    success = await captcha_controller.handle_mouse_event(
        event.session_id,
        event.event_type,
        event.x,
        event.y
    )

    if not success:
        raise HTTPException(status_code=400, detail="处理失败")

    completed = await captcha_controller.check_completion(event.session_id)

    return {
        'success': True,
        'completed': completed
    }


@router.post("/check_completion")
async def check_completion(request: SessionCheckRequest, x_captcha_token: Optional[str] = Header(default=None)):
    _verify_session_or_404(request.session_id, x_captcha_token)
    completed = await captcha_controller.check_completion(request.session_id)

    return {
        'session_id': request.session_id,
        'completed': completed
    }


@router.post("/trajectory")
async def submit_trajectory(request: TrajectorySubmitRequest, x_captcha_token: Optional[str] = Header(default=None)):
    _verify_session_or_404(request.session_id, x_captcha_token)
    session_data = captcha_controller.active_sessions[request.session_id]
    cookie_id = session_data.get('cookie_id') or request.session_id
    try:
        if not request.points or len(request.points) < 3:
            raise HTTPException(status_code=400, detail="too few points")
        trajectory_pool.save_trajectory(request.points, cookie_id, request.distance, True, request.verify_url or "")
        logger.success(f"trajectory saved: cookie={cookie_id}")
        return {"success": True, "message": "saved", "cookie_id": cookie_id}
    except Exception as e:
        logger.error(f"trajectory save failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/session/{session_id}")
async def close_session(session_id: str, x_captcha_token: Optional[str] = Header(default=None)):
    _verify_session_or_404(session_id, x_captcha_token)
    await captcha_controller.close_session(session_id)
    return {'success': True}


# =============================================================================
# 前端页面
# =============================================================================

_HTML_FILE = os.path.join(os.path.dirname(__file__), "_html", "captcha_control.html")


@router.get("/status/{session_id}")
async def get_captcha_status(session_id: str, x_captcha_token: Optional[str] = Header(default=None)):
    _verify_session_or_404(session_id, x_captcha_token)
    try:
        is_completed = captcha_controller.is_completed(session_id)
        session_exists = captcha_controller.session_exists(session_id)

        return {
            "success": True,
            "completed": is_completed,
            "session_exists": session_exists,
            "session_id": session_id
        }
    except Exception as e:
        logger.error(f"获取验证状态失败: {e}")
        return {
            "success": False,
            "completed": False,
            "session_exists": False,
            "session_id": session_id,
            "error": str(e)
        }


@router.get("/control", response_class=HTMLResponse)
async def captcha_control_page():
    if os.path.exists(_HTML_FILE):
        return FileResponse(_HTML_FILE, media_type="text/html")
    else:
        return HTMLResponse(content="""
        <!DOCTYPE html>
        <html>
        <head>
            <title>验证码控制面板</title>
        </head>
        <body>
            <h1>验证码控制面板</h1>
            <p>前端页面文件 captcha_control.html 不存在</p>
            <p>请查看文档了解如何创建前端页面</p>
        </body>
        </html>
        """)


@router.get("/control/{session_id}", response_class=HTMLResponse)
async def captcha_control_page_with_session(session_id: str, token: Optional[str] = Query(default=None)):
    _verify_session_or_404(session_id, token)
    if os.path.exists(_HTML_FILE):
        with open(_HTML_FILE, 'r', encoding='utf-8') as f:
            html_content = f.read()
            initial_session_id = _json_for_script(session_id)
            initial_session_token = _json_for_script(token)
            initial_session_script = (
                f"<script>window.INITIAL_SESSION_ID = {initial_session_id}; "
                f"window.INITIAL_SESSION_TOKEN = {initial_session_token};</script>"
            )
            html_content = html_content.replace(
                '</body>',
                f'{initial_session_script}</body>'
            )
            return HTMLResponse(content=html_content)
    else:
        raise HTTPException(status_code=404, detail="前端页面不存在")
