# -*- coding: utf-8 -*-
"""
backend.app.services.captcha_service —— 登录滑块验证码业务服务
==============================================================
本文件用途：实现「登录滑块拼图验证码」的生成与校验逻辑，供 captcha 路由与
认证服务复用，作为登录前的人机校验（参照 xianyu-auto-reply-wangpan 登录页
的滑块验证形态，但本实现完全自包含、不依赖任何第三方验证码服务与外网）：

- ``generate_challenge()``：使用 Pillow 生成一张带「拼图缺口」的背景图与一块
  可拖动的拼图块，返回二者的 base64（PNG）、缺口纵向位置与拼图块宽度，并在
  内存中记录本次挑战的正确横向位移（带过期），返回挑战标识 ``challenge_id``。
- ``verify_challenge(challenge_id, distance)``：校验用户拖动位移是否与正确位置
  匹配（含容差）；通过则签发一次性「验证票据」(ticket) 并返回，挑战随即作废。
- ``consume_ticket(ticket)``：登录时调用，校验并一次性消费票据；票据有效返回
  True 后立即失效，防止重放。

设计说明：
- 验证状态（挑战与票据）保存在进程内内存字典中并带过期清理，参照参考项目
  极验路由的 ``geetest_status_store`` 思路；本项目为单实例后端，满足登录前
  人机校验诉求即可，无需落库（规范 11：不涉及业务数据删除）。
- 图片生成不依赖任何外部网络资源，纯本地绘制，符合「禁止写死外部地址 / 不依赖
  第三方」的部署要求（规范 21 / 46）。

实现约束（开发规范）：
- 统一响应由路由层用 common.schemas.common 构造；本服务返回纯数据结构。
- 导入置顶（规范 51）；中文注释完善（规范 37）；单文件 ≤500 行（规范 35）。
- 时间统一使用北京时间记录（规范 17）。
"""
from __future__ import annotations

import base64
import io
import random
import secrets
import threading
import time
from typing import Dict, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter

# ----------------------------------------------------------------------
# 验证码图片尺寸与拼图参数（像素）
# ----------------------------------------------------------------------
# 背景图宽 / 高：与前端组件展示宽度保持一致，确保「拖动像素」与「缺口像素」同尺度。
BG_WIDTH: int = 280
BG_HEIGHT: int = 155
# 拼图块边长（正方形主体）。
PIECE_SIZE: int = 48
# 拼图凸起半圆半径（用于绘制经典拼图凸起形状）。
PIECE_BUMP_R: int = 9
# 校验容差（像素）：拖动位移与正确位置之差在该范围内视为通过。
TOLERANCE: int = 6

# 挑战与票据的有效期（秒）。
CHALLENGE_TTL: int = 120
TICKET_TTL: int = 120

# ----------------------------------------------------------------------
# 进程内验证状态存储（带过期），并用锁保证多线程安全
# ----------------------------------------------------------------------
# 挑战存储：{challenge_id: {"answer": int, "expires_at": float, "used": bool}}
_challenge_store: Dict[str, Dict[str, float]] = {}
# 票据存储：{ticket: {"expires_at": float}}
_ticket_store: Dict[str, Dict[str, float]] = {}
# 全局互斥锁：保护两个内存字典的并发读写。
_store_lock = threading.Lock()


def _cleanup_expired(now: float) -> None:
    """清理已过期的挑战与票据（调用方需已持有 _store_lock）。"""
    expired_challenges = [
        key for key, val in _challenge_store.items() if val["expires_at"] < now
    ]
    for key in expired_challenges:
        _challenge_store.pop(key, None)
    expired_tickets = [
        key for key, val in _ticket_store.items() if val["expires_at"] < now
    ]
    for key in expired_tickets:
        _ticket_store.pop(key, None)


# ----------------------------------------------------------------------
# 图片生成辅助
# ----------------------------------------------------------------------
def _random_background() -> Image.Image:
    """生成带随机纹理的背景图，确保缺口处有可辨识的参照（避免纯色无参照）。

    Returns:
        RGBA 背景图像。
    """
    base = Image.new("RGB", (BG_WIDTH, BG_HEIGHT))
    draw = ImageDraw.Draw(base)

    # 垂直渐变底色（蓝白主色调附近的随机柔和色，规范 25）。
    top = (
        random.randint(120, 200),
        random.randint(150, 210),
        random.randint(200, 245),
    )
    bottom = (
        random.randint(60, 120),
        random.randint(90, 150),
        random.randint(150, 210),
    )
    for y in range(BG_HEIGHT):
        ratio = y / BG_HEIGHT
        color = tuple(
            int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3)
        )
        draw.line([(0, y), (BG_WIDTH, y)], fill=color)

    # 叠加若干半透明随机几何图形，丰富纹理，增加机器识别难度。
    overlay = Image.new("RGBA", (BG_WIDTH, BG_HEIGHT), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    for _ in range(6):
        x0 = random.randint(-20, BG_WIDTH)
        y0 = random.randint(-20, BG_HEIGHT)
        x1 = x0 + random.randint(30, 90)
        y1 = y0 + random.randint(30, 90)
        col = (
            random.randint(180, 255),
            random.randint(180, 255),
            random.randint(180, 255),
            random.randint(40, 90),
        )
        if random.random() < 0.5:
            odraw.ellipse([x0, y0, x1, y1], fill=col)
        else:
            odraw.rectangle([x0, y0, x1, y1], fill=col)

    base = Image.alpha_composite(base.convert("RGBA"), overlay)
    return base


def _piece_mask() -> Image.Image:
    """生成拼图块形状的灰度蒙版（白=拼图实体，黑=透明区域）。

    形状为经典拼图：正方形主体 + 上边外凸半圆 + 右边外凸半圆，便于视觉辨识。

    Returns:
        尺寸为 (PIECE_SIZE, PIECE_SIZE) 的 "L" 模式蒙版图。
    """
    mask = Image.new("L", (PIECE_SIZE, PIECE_SIZE), 0)
    mdraw = ImageDraw.Draw(mask)
    # 主体正方形（留出凸起空间，主体略小并居中偏移）。
    body_margin = PIECE_BUMP_R
    mdraw.rectangle(
        [body_margin, body_margin, PIECE_SIZE - 1, PIECE_SIZE - 1], fill=255
    )
    # 上边凸起半圆。
    cx = PIECE_SIZE // 2
    mdraw.ellipse(
        [cx - PIECE_BUMP_R, 0, cx + PIECE_BUMP_R, 2 * PIECE_BUMP_R], fill=255
    )
    # 右边凸起半圆。
    cy = (PIECE_SIZE + body_margin) // 2
    mdraw.ellipse(
        [
            PIECE_SIZE - 2 * PIECE_BUMP_R,
            cy - PIECE_BUMP_R,
            PIECE_SIZE,
            cy + PIECE_BUMP_R,
        ],
        fill=255,
    )
    return mask


def _build_images(answer_x: int, gap_y: int) -> Tuple[str, str]:
    """根据正确缺口横坐标与纵坐标生成背景图与拼图块的 base64（PNG）。

    Args:
        answer_x: 缺口左上角横坐标（即正确拖动位移）。
        gap_y: 缺口左上角纵坐标。

    Returns:
        (背景图 base64, 拼图块 base64)，均为 ``data:image/png;base64,...`` 前缀。
    """
    background = _random_background()
    mask = _piece_mask()

    # 拼图块：从背景图缺口处「抠出」对应像素，套用拼图形状蒙版。
    region = background.crop(
        (answer_x, gap_y, answer_x + PIECE_SIZE, gap_y + PIECE_SIZE)
    )
    piece = Image.new("RGBA", (PIECE_SIZE, PIECE_SIZE), (0, 0, 0, 0))
    piece.paste(region, (0, 0))
    piece.putalpha(mask)
    # 拼图块描边，增强立体感与可见性。
    pdraw = ImageDraw.Draw(piece)
    edge = mask.filter(ImageFilter.FIND_EDGES)
    piece.paste((255, 255, 255, 200), (0, 0), edge)

    # 背景图缺口：在原位置把形状区域压暗，形成可见缺口。
    shadow = Image.new("RGBA", (PIECE_SIZE, PIECE_SIZE), (0, 0, 0, 120))
    background.paste(shadow, (answer_x, gap_y), mask)

    return _to_data_url(background), _to_data_url(piece)


def _to_data_url(image: Image.Image) -> str:
    """将 PIL 图像编码为 PNG 的 data URL 字符串。"""
    buffer = io.BytesIO()
    image.convert("RGBA").save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


# ----------------------------------------------------------------------
# 对外业务方法
# ----------------------------------------------------------------------
def generate_challenge() -> Dict[str, object]:
    """生成一次滑块验证码挑战。

    Returns:
        字典：{
            "challenge_id": str,        # 挑战标识，校验时回传
            "bg_image": str,            # 背景图（含缺口）data URL
            "puzzle_image": str,        # 拼图块 data URL
            "y": int,                   # 拼图块纵向位置（前端定位用）
            "piece_size": int,          # 拼图块边长
            "bg_width": int,            # 背景图宽度（前端按此宽度等比展示）
            "bg_height": int,           # 背景图高度
        }
    """
    # 正确缺口横坐标：留出左右边距，确保拼图完整可见且需要明显拖动。
    answer_x = random.randint(PIECE_SIZE + 20, BG_WIDTH - PIECE_SIZE - 10)
    gap_y = random.randint(10, BG_HEIGHT - PIECE_SIZE - 10)

    bg_image, puzzle_image = _build_images(answer_x, gap_y)

    challenge_id = secrets.token_urlsafe(24)
    now = time.time()
    with _store_lock:
        _cleanup_expired(now)
        _challenge_store[challenge_id] = {
            "answer": float(answer_x),
            "expires_at": now + CHALLENGE_TTL,
            "used": 0.0,
        }

    return {
        "challenge_id": challenge_id,
        "bg_image": bg_image,
        "puzzle_image": puzzle_image,
        # 拼图块在背景图中的纵向位置（拼图块初始置于左侧同一高度）。
        "y": gap_y,
        "piece_size": PIECE_SIZE,
        "bg_width": BG_WIDTH,
        "bg_height": BG_HEIGHT,
    }


def verify_challenge(challenge_id: str, distance: float) -> Tuple[bool, str, Optional[str]]:
    """校验拖动位移是否匹配正确缺口位置。

    Args:
        challenge_id: 生成阶段返回的挑战标识。
        distance: 用户拖动的横向位移（像素，相对背景图实际宽度）。

    Returns:
        (是否通过, 中文提示, 票据)。通过时票据为一次性凭据，登录时回传；
        未通过时票据为 None。
    """
    if not challenge_id:
        return False, "验证已失效，请刷新后重试", None

    now = time.time()
    with _store_lock:
        _cleanup_expired(now)
        record = _challenge_store.get(challenge_id)
        # 挑战不存在 / 已过期 / 已被使用：一律判定失效，需重新获取。
        if record is None or record["expires_at"] < now or record["used"]:
            _challenge_store.pop(challenge_id, None)
            return False, "验证已失效，请刷新后重试", None

        answer = record["answer"]
        # 校验位移：与正确位置之差在容差内视为通过。
        if abs(float(distance) - answer) > TOLERANCE:
            # 失败不立即删除挑战，允许用户在有效期内重试同一张图。
            return False, "拼图未对齐，请重试", None

        # 通过：挑战立即作废，签发一次性票据。
        record["used"] = 1.0
        _challenge_store.pop(challenge_id, None)
        ticket = secrets.token_urlsafe(24)
        _ticket_store[ticket] = {"expires_at": now + TICKET_TTL}

    return True, "验证通过", ticket


def consume_ticket(ticket: Optional[str]) -> bool:
    """校验并一次性消费验证票据（登录时调用）。

    Args:
        ticket: 滑块验证通过后签发的票据。

    Returns:
        票据有效返回 True（并立即失效，防止重放）；无效 / 过期返回 False。
    """
    if not ticket:
        return False
    now = time.time()
    with _store_lock:
        _cleanup_expired(now)
        record = _ticket_store.pop(ticket, None)
        if record is None or record["expires_at"] < now:
            return False
    return True


__all__ = [
    "generate_challenge",
    "verify_challenge",
    "consume_ticket",
    "BG_WIDTH",
    "BG_HEIGHT",
    "PIECE_SIZE",
    "TOLERANCE",
]
