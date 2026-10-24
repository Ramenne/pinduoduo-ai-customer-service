# -*- coding: utf-8 -*-
"""
backend.app.services.image_captcha_service —— 图形字符验证码业务服务
====================================================================
本文件用途：实现「图形字符验证码」的生成与校验逻辑，供 captcha 路由复用，作为
注册页（参照 xianyu-auto-reply-wangpan 注册页）邮箱验证码下发前的人机校验：

- ``generate(session_id)``：生成 4 位随机字符（排除易混淆字符）并绘制为带干扰
  线 / 干扰点的 PNG 图片，返回其 base64 data URL；同时在内存中按 ``session_id``
  记录正确答案（带 5 分钟过期）。
- ``verify(session_id, code)``：校验用户输入是否与记录答案一致（忽略大小写）；
  通过后立即作废该验证码（防重放）。

设计说明：
- 验证状态保存在进程内内存字典中并带过期清理，与本项目登录滑块验证码
  （captcha_service）一致的单实例内存方案，满足登录前人机校验诉求，无需落库
  （规范 11：不涉及业务数据删除）。
- 图片生成纯本地绘制（Pillow），不依赖任何外部网络资源（规范 21 / 46）。

实现约束（开发规范）：
- 统一响应由路由层用 common.schemas.common 构造；本服务返回纯数据结构 / 二元组。
- 导入置顶（规范 51）；中文注释完善（规范 37）；单文件 ≤500 行（规范 35）。
"""
from __future__ import annotations

import base64
import io
import random
import threading
import time
from typing import Dict, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

# ----------------------------------------------------------------------
# 验证码参数
# ----------------------------------------------------------------------
# 验证码字符个数。
CODE_LENGTH: int = 4
# 可选字符集合：排除易混淆字符（0/O/1/I/L）。
_CODE_CHARS: str = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
# 图片尺寸（像素）。
_IMG_WIDTH: int = 120
_IMG_HEIGHT: int = 40
# 验证码有效期（秒）。
CAPTCHA_TTL: int = 300

# ----------------------------------------------------------------------
# 进程内验证码存储（带过期），并用锁保证多线程安全
# ----------------------------------------------------------------------
# 图形验证码存储：{session_id: {"code": str(大写), "expires_at": float}}
_captcha_store: Dict[str, Dict[str, object]] = {}
# 全局互斥锁：保护内存字典的并发读写。
_store_lock = threading.Lock()


def _cleanup_expired(now: float) -> None:
    """清理已过期的图形验证码（调用方需已持有 _store_lock）。"""
    expired = [
        key for key, val in _captcha_store.items() if float(val["expires_at"]) < now
    ]
    for key in expired:
        _captcha_store.pop(key, None)


def _generate_text() -> str:
    """生成随机验证码文本（长度 CODE_LENGTH，取自 _CODE_CHARS）。"""
    return "".join(random.choices(_CODE_CHARS, k=CODE_LENGTH))


def _render_image(text: str) -> str:
    """将验证码文本绘制为带干扰的 PNG 图片，返回 data URL（失败返回空串）。

    Args:
        text: 验证码字符串。

    Returns:
        形如 ``data:image/png;base64,...`` 的字符串；绘制异常时返回空串。
    """
    try:
        image = Image.new("RGB", (_IMG_WIDTH, _IMG_HEIGHT), color=(255, 255, 255))
        draw = ImageDraw.Draw(image)

        # 干扰线：增加机器识别难度。
        for _ in range(5):
            start = (random.randint(0, _IMG_WIDTH), random.randint(0, _IMG_HEIGHT))
            end = (random.randint(0, _IMG_WIDTH), random.randint(0, _IMG_HEIGHT))
            draw.line(
                [start, end],
                fill=(
                    random.randint(0, 200),
                    random.randint(0, 200),
                    random.randint(0, 200),
                ),
            )

        # 干扰点。
        for _ in range(50):
            point = (random.randint(0, _IMG_WIDTH), random.randint(0, _IMG_HEIGHT))
            draw.point(
                point,
                fill=(
                    random.randint(0, 200),
                    random.randint(0, 200),
                    random.randint(0, 200),
                ),
            )

        # 字体：优先系统 TrueType，缺失时退回 Pillow 内置位图字体。
        try:
            font = ImageFont.truetype("arial.ttf", 28)
        except Exception:
            font = ImageFont.load_default()

        # 逐字符绘制并轻微抖动位置 / 颜色。
        for index, char in enumerate(text):
            x = 10 + index * 25 + random.randint(-3, 3)
            y = random.randint(2, 10)
            color = (
                random.randint(0, 150),
                random.randint(0, 150),
                random.randint(0, 150),
            )
            draw.text((x, y), char, font=font, fill=color)

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    except Exception:
        # 绘制异常不向上抛出，由路由层据空串返回中文失败提示。
        return ""


# ----------------------------------------------------------------------
# 对外业务方法
# ----------------------------------------------------------------------
def generate(session_id: str) -> Tuple[Optional[str], str]:
    """生成一张图形验证码并记录答案。

    Args:
        session_id: 前端会话标识（同一注册流程内复用，校验时回传）。

    Returns:
        二元组 (图片 data URL 或 None, 中文提示)。生成失败时图片为 None。
    """
    if not session_id:
        return None, "会话标识缺失"

    text = _generate_text()
    image = _render_image(text)
    if not image:
        return None, "图形验证码生成失败"

    now = time.time()
    with _store_lock:
        _cleanup_expired(now)
        _captcha_store[session_id] = {
            "code": text.upper(),
            "expires_at": now + CAPTCHA_TTL,
        }
    return image, "图形验证码生成成功"


def verify(session_id: str, code: str) -> Tuple[bool, str]:
    """校验图形验证码（忽略大小写），通过后立即作废（防重放）。

    Args:
        session_id: 生成阶段使用的会话标识。
        code: 用户输入的验证码。

    Returns:
        二元组 (是否通过, 中文提示)。
    """
    if not session_id or not code:
        return False, "请输入图形验证码"

    now = time.time()
    with _store_lock:
        _cleanup_expired(now)
        stored = _captcha_store.get(session_id)
        if stored is None:
            return False, "验证码不存在或已过期"
        if float(stored["expires_at"]) < now:
            _captcha_store.pop(session_id, None)
            return False, "验证码已过期"
        if str(stored["code"]) != code.strip().upper():
            return False, "验证码错误"
        # 校验通过：立即作废。
        _captcha_store.pop(session_id, None)
    return True, "验证码验证成功"


__all__ = [
    "generate",
    "verify",
    "CODE_LENGTH",
    "CAPTCHA_TTL",
]
