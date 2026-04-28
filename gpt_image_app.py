"""
GPT 图片生成器 v4 - 专业 UI 应用
支持：文字生成图片（流式显示）、基于已有图片修改、风格转换、背景移除/替换、
      图片放大、AI描述、前后对比、撤销重做、拖拽/粘贴上传、历史持久化、自动重试
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import json
import time
import os
import io
import base64
import hashlib
import math
import random
import shutil
import sys
import ctypes
from datetime import datetime
from pathlib import Path

import httpx
from PIL import Image, ImageTk, ImageDraw, ImageFilter

try:
    from PIL import ImageGrab
except ImportError:
    ImageGrab = None

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    BaseTk = TkinterDnD.Tk
    DND_ENABLED = True
except ImportError:
    DND_FILES = None
    TkinterDnD = None
    BaseTk = tk.Tk
    DND_ENABLED = False

# ─── 配置 ───────────────────────────────────────────────

DEFAULT_API_BASE = "http://localhost:5101/v1"
DEFAULT_MODEL = "gpt-image-2"
DEFAULT_AUTH = ""
DEFAULT_SIZE = "1024x1024"
DEFAULT_FORMAT = "png"
DEFAULT_QUALITY = "high"
ORIGINAL_SIZE_ID = "original"
ORIGINAL_SIZE_LABEL = "按原图尺寸"


def _get_windows_work_area():
    """Return the usable desktop work area (excluding taskbar) on Windows."""
    if sys.platform != "win32":
        return None
    try:
        class _Rect(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        rect = _Rect()
        SPI_GETWORKAREA = 48
        ok = ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
        )
        if not ok:
            return None
        return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        return None


def _write_text_atomic(path, text, encoding="utf-8"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{int(time.time() * 1000)}.tmp")
    tmp.write_text(text, encoding=encoding)
    tmp.replace(path)

def _is_supported_size_preset(width, height, min_area=655360, max_area=8294400, max_dim=3840):
    if width <= 0 or height <= 0:
        return False
    if width % 16 != 0 or height % 16 != 0:
        return False
    if max(width, height) > max_dim:
        return False
    if max(width, height) / float(min(width, height)) > 3:
        return False
    area = width * height
    return min_area <= area <= max_area


def _build_size_preset_options():
    """Curated presets for the gpt-5.4 /responses image-generation route.

    Keep the dropdown short and practical: 20 common sizes ordered from
    everyday square choices to common landscape and portrait outputs.
    Custom input remains available for any other legal size.
    """
    sizes = []
    seen = set()

    def add(width, height):
        if not _is_supported_size_preset(width, height):
            return
        value = f"{width}x{height}"
        if value in seen:
            return
        seen.add(value)
        sizes.append(value)

    preset_groups = (
        # Squares
        ((1024, 1024), (1536, 1536), (2048, 2048), (2880, 2880)),
        # Landscape
        (
            (1024, 768),
            (1280, 720),
            (1280, 960),
            (1536, 1024),
            (2048, 1152),
            (2048, 1536),
            (2560, 1440),
            (3840, 2160),
        ),
        # Portrait
        (
            (768, 1024),
            (720, 1280),
            (960, 1280),
            (1024, 1536),
            (1024, 1792),
            (1152, 2048),
            (1440, 2560),
            (2160, 3840),
        ),
    )

    for group in preset_groups:
        for width, height in group:
            add(width, height)

    return sizes


SIZE_PRESET_OPTIONS = _build_size_preset_options()
SIZE_DISPLAY_OPTIONS = [ORIGINAL_SIZE_LABEL] + SIZE_PRESET_OPTIONS
SIZE_INPUT_RULE_HINT = (
    "下拉只列 20 个常用尺寸，其他合法尺寸可直接输入；"
    "例如 960x688、1536x1024、3840x2160。"
    "宽高都需为 16 的倍数，宽高比不能超过 3:1，总像素需介于 655360 和 8294400。"
)
DEFAULT_COMPRESSION = 100
APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
HISTORY_DIR = APP_DIR / "history"
HISTORY_DB = APP_DIR / "history_db.json"
CONFIG_PATH = APP_DIR / "config.json"
THUMB_DIR = APP_DIR / "thumbs"
PLACEHOLDER_TEXT = "在此输入描述文字来生成图片...\n例如：一只可爱的小狗坐在草地上"
MAX_RETRIES = 5
RETRY_DELAY = 8
IMAGE_MODEL_CANDIDATES = ("gpt-image-2", "gpt-image-1.5", "gpt-image-1", "gpt-image-1-mini")
EDIT_MODEL_CANDIDATES = set(IMAGE_MODEL_CANDIDATES)
EDIT_API_SIZES = {"1024x1024", "1536x1024", "1024x1536"}

# ── 模型显示名映射 ──
MODEL_DISPLAY_NAMES = {
    "gpt-image-2": "gpt-image-2",
    "gpt-image-1.5": "gpt-image-1.5",
    "gpt-image-1": "gpt-image-1",
    "gpt-image-1-mini": "gpt-image-1-mini",
}
MODEL_API_IDS = {v: k for k, v in MODEL_DISPLAY_NAMES.items()}
MODEL_TYPO_FIXES = {
    "gpt5.4": "gpt-5.4", "gpt5.4-mini": "gpt-5.4-mini",
    "gpt5.5": "gpt-5.5", "gpt5.2": "gpt-5.2", "gpt5.3": "gpt-5.3",
    "gptimage1": "gpt-image-1", "gptimage2": "gpt-image-2",
}
DEFAULT_MODEL_OPTIONS = list(MODEL_DISPLAY_NAMES.values())
DEFAULT_MODEL_DISPLAY = MODEL_DISPLAY_NAMES[DEFAULT_MODEL]

# ── 参数中英文映射 ──
QUALITY_DISPLAY_NAMES = {
    "auto": "自动",
    "low": "低",
    "medium": "中",
    "high": "高",
}
QUALITY_DISPLAY_OPTIONS = [QUALITY_DISPLAY_NAMES[k] for k in ("auto", "low", "medium", "high")]
QUALITY_API_IDS = {
    "自动": "auto",
    "低": "low",
    "中": "medium",
    "高": "high",
    "高清": "high",
    "auto": "auto",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "standard": "medium",
    "hd": "high",
    # Backward-compatible aliases for older saved configs / UI values.
    "source": "high",
    "2k": "high",
    "4k": "high",
}

FORMAT_DISPLAY_NAMES = {
    "png": "PNG", "jpeg": "JPEG", "webp": "WebP",
}
FORMAT_API_IDS = {v: k for k, v in FORMAT_DISPLAY_NAMES.items()}

# 默认参数的中文显示名
DEFAULT_QUALITY_DISPLAY = QUALITY_DISPLAY_NAMES[DEFAULT_QUALITY]
DEFAULT_FORMAT_DISPLAY = FORMAT_DISPLAY_NAMES[DEFAULT_FORMAT]

STYLE_PRESETS = {
    "无（原始）": "",
    "水彩画": " in watercolor painting style, soft brush strokes, paper texture",
    "油画": " in oil painting style, rich impasto, canvas texture, classical technique",
    "像素艺术": " in pixel art style, 16-bit retro game aesthetic, crisp pixels",
    "赛博朋克": " in cyberpunk style, neon lights, dark futuristic cityscape, holographic",
    "日式动漫": " in Japanese anime style, cel-shaded, vibrant colors, detailed eyes",
    "水墨画": " in Chinese ink wash painting style, monochrome, rice paper, elegant brushwork",
    "素描": " in pencil sketch style, cross-hatching, graphite on white paper",
    "波普艺术": " in pop art style, bold colors, Ben-Day dots, comic book aesthetic",
    "低多边形": " in low-poly 3D style, geometric facets, clean edges, gradient lighting",
    "梵高风格": " in Van Gogh post-impressionist style, swirling brushstrokes, vivid colors",
    "宫崎骏风格": " in Studio Ghibli style, lush nature, whimsical, hand-painted backgrounds",
}

# ─── 主题色彩 ───────────────────────────────────────────────

C = {
    "bg":            "#0d1117",
    "surface":       "#151b24",
    "surface2":      "#1d2432",
    "surface3":      "#0f141d",
    "border":        "#283245",
    "text":          "#d7e0f3",
    "text_dim":      "#95a3c3",
    "text_muted":    "#697792",
    "accent":        "#6d6df6",
    "accent2":       "#4b90ff",
    "accent_glow":   "#334e91",
    "green":         "#26d96f",
    "red":           "#ff6b7a",
    "yellow":        "#ffbf47",
    "mauve":         "#9a7cff",
    "peach":         "#ff9a4d",
    "canvas_bg":     "#090d14",
    "toolbar_bg":    "#0a0f17",
    "toolbar_edge":  "#20283a",
    "btn_bg":        "#202838",
    "btn_hover":     "#2b3550",
    "btn_active":    "#4f84f7",
    "section_bar":   "#5f72ff",
    "strip_bg":      "#121a26",
}


class ImageGenerator:
    """封装图片生成/编辑的 API 调用"""

    def __init__(self, api_base, model, auth_token):
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.auth_token = auth_token
        self._cancel_event = threading.Event()
        self._request_lock = threading.Lock()
        self._active_client = None
        self._active_response = None

    def cancel(self):
        self._cancel_event.set()
        with self._request_lock:
            response = self._active_response
            client = self._active_client
        # Close client first — this kills the connection pool and
        # immediately interrupts any ongoing stream reads.
        # Then close response to clean up.
        for obj in (client, response):
            if obj is None:
                continue
            try:
                obj.close()
            except Exception:
                pass

    def _headers(self, extra=None):
        h = {
            "Authorization": self.auth_token,
            "Content-Type": "application/json",
        }
        if extra:
            h.update(extra)
        return h

    def _set_active_request(self, client=None, response=None):
        with self._request_lock:
            self._active_client = client
            self._active_response = response

    def _clear_active_request(self):
        with self._request_lock:
            self._active_client = None
            self._active_response = None

    def _cancelable_sleep(self, seconds):
        """Sleep that returns early if cancel is requested"""
        self._cancel_event.wait(timeout=seconds)
        return self._cancel_event.is_set()

    @staticmethod
    def _is_retryable_http_status(status_code, error_text=""):
        if status_code in (502, 503, 429):
            return True
        if status_code >= 500 and "upstream" in str(error_text or "").lower():
            return True
        return False

    @staticmethod
    def _is_retryable_request_exception(exc):
        return isinstance(
            exc,
            (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.RemoteProtocolError,
                httpx.TransportError,
            ),
        )

    @staticmethod
    def _retry_delay_seconds(attempt):
        return min(RETRY_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 3), 60)

    @staticmethod
    def _read_stream_error(resp):
        try:
            raw = resp.read()
            if isinstance(raw, bytes):
                return raw.decode("utf-8", "ignore")[:500]
            return str(raw)[:500]
        except Exception:
            return ""

    def _stream_worker(self, body, on_partial, on_done, on_error):
        self._cancel_event.clear()
        # ── Debug logging: request start ──
        try:
            body_size_kb = len(json.dumps(body)) / 1024
            input_type = "edit" if isinstance(body.get("input"), list) else "generate"
            model = body.get("model", "?")
            has_prev_id = "previous_response_id" in body
            input_images = 0
            if input_type == "edit":
                for msg in (body.get("input") or []):
                    if isinstance(msg, dict):
                        for item in (msg.get("content") or []):
                            if isinstance(item, dict) and item.get("type") == "input_image":
                                input_images += 1
            tools_info = {}
            for tool in body.get("tools", []):
                if tool.get("type") == "image_generation":
                    tools_info = {k: v for k, v in tool.items() if k != "type"}
            debug_log.log("request_start", {
                "input_type": input_type,
                "model": model,
                "body_size_kb": f"{body_size_kb:.1f}",
                "input_images": str(input_images),
                "has_previous_response_id": str(has_prev_id),
                "tools_config": str(tools_info),
                "prompt_preview": (body.get("input") if isinstance(body.get("input"), str) else
                    str([item for msg in (body.get("input") or [])
                         for item in (msg.get("content") if isinstance(msg, dict) else [])
                         if isinstance(item, dict) and item.get("type") == "input_text"])[:200])
                    if input_type == "edit" else str(body.get("input", ""))[:200],
            })
        except Exception as e:
            debug_log.log("request_start_error", str(e))

        # ── Proactive body size check: if body is too large, compress before first attempt ──
        # Typical 1024px image inputs are often 1-3MB as base64. Compressing them
        # before the first attempt hurts edit fidelity, so only shrink truly large
        # request bodies and keep retry-time degradation as the fallback.
        MAX_BODY_KB = 20480  # 20 MB — only compress when body is truly enormous
        # Per-image b64 limit: API实测可接受~17MB单图，留余量设15MB
        MAX_PER_IMAGE_B64_KB = 15360
        try:
            body_size_kb = len(json.dumps(body)) / 1024
            if body_size_kb > MAX_BODY_KB:
                input_type = "edit" if isinstance(body.get("input"), list) else "generate"
                if input_type == "edit":
                    # Proactively compress images only when the request is too large.
                    self._degrade_input_images(body, max_dim=3840, max_b64_kb=MAX_PER_IMAGE_B64_KB)
                    if "previous_response_id" in body:
                        del body["previous_response_id"]
                    new_size_kb = len(json.dumps(body)) / 1024
                    debug_log.log("proactive_compression", {
                        "original_size_kb": f"{body_size_kb:.1f}",
                        "compressed_size_kb": f"{new_size_kb:.1f}",
                        "action": "compressed_before_first_attempt",
                    })
        except Exception as e:
            debug_log.log("proactive_compression_error", str(e))

        attempt = 0
        while attempt < MAX_RETRIES:
            attempt += 1
            # Progressive degradation on retries:
            # - On attempt 2+: remove previous_response_id (may be stale/invalid)
            # - On attempt 3+: aggressively degrade image size
            degraded = []
            if attempt >= 2 and "previous_response_id" in body:
                del body["previous_response_id"]
                degraded.append("removed_previous_response_id")
            if attempt >= 3:
                try:
                    self._degrade_input_images(body, max_dim=3840, max_b64_kb=15360)
                    degraded.append("degraded_images")
                except Exception:
                    pass
            if attempt > 1:
                try:
                    new_size_kb = len(json.dumps(body)) / 1024
                except Exception:
                    new_size_kb = "?"
                debug_log.log("retry_attempt", {
                    "attempt": str(attempt),
                    "degradation": ", ".join(degraded) if degraded else "none",
                    "body_size_kb": str(new_size_kb),
                })
            try:
                with httpx.Client(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
                    self._set_active_request(client=client)
                    payload_meta = debug_log.save_request_payload(
                        route=f"{self.api_base}/responses",
                        request_kind="json",
                        request_body=body,
                        note="responses_stream",
                        attempt=attempt,
                    )
                    debug_log.log("request_payload_saved", payload_meta)
                    with client.stream(
                        "POST",
                        f"{self.api_base}/responses",
                        headers=self._headers({"Accept": "text/event-stream"}),
                        json=body,
                    ) as resp:
                        self._set_active_request(client=client, response=resp)
                        if resp.status_code != 200:
                            err_text = self._read_stream_error(resp) or f"HTTP {resp.status_code}"
                            # ── Debug logging: HTTP error response ──
                            debug_log.log("http_error", {
                                "attempt": str(attempt),
                                "status_code": str(resp.status_code),
                                "error_text": err_text[:500],
                                "response_headers": str(dict(resp.headers))[:300],
                            })
                            if resp.status_code in (502, 503, 429) and attempt < MAX_RETRIES:
                                if on_partial:
                                    on_partial(None, attempt)
                                # Exponential backoff with jitter for 502/503/429
                                delay = min(RETRY_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 3), 60)
                                debug_log.log("retry_scheduled", {
                                    "attempt": str(attempt),
                                    "delay_sec": f"{delay:.1f}",
                                    "status_code": str(resp.status_code),
                                    "retry_mode": "non_stream",
                                })
                                if self._cancelable_sleep(delay):
                                    return
                                self._non_stream_fallback(
                                    body,
                                    on_partial,
                                    on_done,
                                    on_error,
                                    start_attempt=attempt + 1,
                                )
                                return
                            if on_error:
                                on_error(f"HTTP {resp.status_code}: {err_text}")
                            return

                        buffer = ""
                        last_b64 = None
                        partial_count = 0
                        stream_error = None
                        revised_prompt = None
                        response_id = None
                        for chunk in resp.iter_text():
                            if self._cancel_event.is_set():
                                return
                            buffer += chunk
                            while "\n\n" in buffer:
                                event_block, buffer = buffer.split("\n\n", 1)
                                event_block = event_block.strip()
                                if not event_block:
                                    continue

                                # Extract metadata (revised_prompt, response_id)
                                rp, rid = self._extract_metadata_from_block(event_block)
                                if rp:
                                    revised_prompt = rp
                                if rid:
                                    response_id = rid

                                # Check for SSE error events before extracting b64
                                err_msg = self._check_sse_error(event_block)
                                if err_msg:
                                    stream_error = err_msg
                                    continue

                                b64 = self._extract_b64_from_block(event_block)
                                if b64:
                                    last_b64 = b64
                                    partial_count += 1
                                    if on_partial:
                                        on_partial(b64, partial_count)

                        # Process remaining buffer
                        if buffer.strip():
                            event_block = buffer.strip()
                            err_msg = self._check_sse_error(event_block)
                            if err_msg:
                                stream_error = err_msg
                            else:
                                b64 = self._extract_b64_from_block(event_block)
                                if b64:
                                    last_b64 = b64
                                    partial_count += 1
                                    if on_partial:
                                        on_partial(b64, partial_count)

                        if self._cancel_event.is_set():
                            return

                        # ── Debug logging: stream result ──
                        debug_log.log("stream_result", {
                            "attempt": str(attempt),
                            "has_b64": str(bool(last_b64)),
                            "partial_count": str(partial_count),
                            "has_stream_error": str(bool(stream_error)),
                            "stream_error_msg": (stream_error or "")[:200],
                            "has_revised_prompt": str(bool(revised_prompt)),
                            "response_id": response_id or "None",
                        })
                        if last_b64:
                            if on_done:
                                on_done(last_b64, partial_count, revised_prompt=revised_prompt, response_id=response_id)
                        elif stream_error:
                            # Stream reported an error event — retry if possible
                            if attempt < MAX_RETRIES:
                                if on_partial:
                                    on_partial(None, attempt)
                                delay = min(RETRY_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 3), 60)
                                debug_log.log("retry_scheduled", {
                                    "attempt": str(attempt),
                                    "delay_sec": f"{delay:.1f}",
                                    "reason": (stream_error or "")[:200],
                                    "retry_mode": "non_stream",
                                })
                                if self._cancelable_sleep(delay):
                                    return
                                self._non_stream_fallback(
                                    body,
                                    on_partial,
                                    on_done,
                                    on_error,
                                    start_attempt=attempt + 1,
                                )
                                return
                            if on_error:
                                on_error(f"流式错误: {stream_error}")
                        else:
                            # No b64 and no explicit error — retry if possible
                            if attempt < MAX_RETRIES:
                                if on_partial:
                                    on_partial(None, attempt)
                                delay = min(RETRY_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 3), 60)
                                debug_log.log("retry_scheduled", {
                                    "attempt": str(attempt),
                                    "delay_sec": f"{delay:.1f}",
                                    "reason": "no_image_in_stream_response",
                                    "retry_mode": "non_stream",
                                })
                                if self._cancelable_sleep(delay):
                                    return
                                self._non_stream_fallback(
                                    body,
                                    on_partial,
                                    on_done,
                                    on_error,
                                    start_attempt=attempt + 1,
                                )
                                return
                            if on_error:
                                on_error("未能从响应中提取到图片数据")
                        return
            except httpx.ConnectError as e:
                debug_log.log("connect_error", {"attempt": str(attempt), "error": str(e)[:200]})
                if attempt < MAX_RETRIES:
                    if on_partial:
                        on_partial(None, attempt)
                    delay = min(RETRY_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 3), 60)
                    debug_log.log("retry_scheduled", {
                        "attempt": str(attempt),
                        "delay_sec": f"{delay:.1f}",
                        "reason": str(e)[:200],
                        "retry_mode": "non_stream",
                    })
                    if self._cancelable_sleep(delay):
                        return
                    self._non_stream_fallback(
                        body,
                        on_partial,
                        on_done,
                        on_error,
                        start_attempt=attempt + 1,
                    )
                    return
                if on_error:
                    on_error(f"连接失败: {e}")
                return
            except httpx.ReadTimeout as e:
                debug_log.log("read_timeout", {"attempt": str(attempt), "error": str(e)[:200]})
                if attempt < MAX_RETRIES:
                    if on_partial:
                        on_partial(None, attempt)
                    delay = min(RETRY_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 3), 60)
                    debug_log.log("retry_scheduled", {
                        "attempt": str(attempt),
                        "delay_sec": f"{delay:.1f}",
                        "reason": str(e)[:200],
                        "retry_mode": "non_stream",
                    })
                    if self._cancelable_sleep(delay):
                        return
                    self._non_stream_fallback(
                        body,
                        on_partial,
                        on_done,
                        on_error,
                        start_attempt=attempt + 1,
                    )
                    return
                if on_error:
                    on_error(f"读取超时: {e}")
                return
            except httpx.RemoteProtocolError as e:
                debug_log.log("remote_protocol_error", {"attempt": str(attempt), "error": str(e)[:200]})
                if attempt < MAX_RETRIES:
                    if on_partial:
                        on_partial(None, attempt)
                    delay = min(RETRY_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 3), 60)
                    debug_log.log("retry_scheduled", {
                        "attempt": str(attempt),
                        "delay_sec": f"{delay:.1f}",
                        "reason": str(e)[:200],
                        "retry_mode": "non_stream",
                    })
                    if self._cancelable_sleep(delay):
                        return
                    self._non_stream_fallback(
                        body,
                        on_partial,
                        on_done,
                        on_error,
                        start_attempt=attempt + 1,
                    )
                    return
                if on_error:
                    on_error(f"连接中断: {e}")
                return
            except Exception as e:
                if self._cancel_event.is_set():
                    return
                import traceback as _tb
                _tb_text = _tb.format_exc()
                print(f"[_stream_worker exception] {type(e).__name__}: {e}\n{_tb_text}", file=sys.stderr)
                debug_log.log("exception", {
                    "attempt": str(attempt),
                    "exception_type": type(e).__name__,
                    "error": str(e)[:300],
                    "traceback": _tb_text[:500],
                })
                if attempt < MAX_RETRIES:
                    if on_partial:
                        on_partial(None, attempt)
                    delay = min(RETRY_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 3), 60)
                    debug_log.log("retry_scheduled", {
                        "attempt": str(attempt),
                        "delay_sec": f"{delay:.1f}",
                        "reason": str(e)[:200],
                        "retry_mode": "non_stream",
                    })
                    if self._cancelable_sleep(delay):
                        return
                    self._non_stream_fallback(
                        body,
                        on_partial,
                        on_done,
                        on_error,
                        start_attempt=attempt + 1,
                    )
                    return
                if on_error:
                    on_error(str(e))
                return
            finally:
                self._clear_active_request()

        # If the first streaming attempt cannot produce a usable image, hand off
        # the remaining retries to the non-streaming /responses path.
        debug_log.log("stream_exhausted_fallback", {
            "model": body.get("model", "?"),
            "retry_mode": "non_stream",
        })
        self._non_stream_fallback(
            body,
            on_partial,
            on_done,
            on_error,
            start_attempt=max(attempt + 1, 2),
        )

    def _non_stream_fallback(self, body, on_partial, on_done, on_error, start_attempt=2):
        """Retry the same /responses request with stream=False.

        This keeps the same model and endpoint as the original /responses request
        and only changes the delivery mode after the initial streaming attempt
        fails.
        """
        debug_log.log("non_stream_fallback_start", {
            "model": body.get("model", "?"),
            "input_type": "edit" if isinstance(body.get("input"), list) else "generate",
            "start_attempt": str(start_attempt),
        })
        fallback_body = {**body, "stream": False}
        request_model = str(fallback_body.get("model", "?"))
        try:
            first_attempt = int(start_attempt or 2)
        except Exception:
            first_attempt = 2
        first_attempt = max(2, first_attempt)

        for attempt in range(first_attempt, MAX_RETRIES + 1):
            degraded = []
            if attempt >= 2 and "previous_response_id" in fallback_body:
                del fallback_body["previous_response_id"]
                degraded.append("removed_previous_response_id")
            if attempt >= 3:
                try:
                    self._degrade_input_images(fallback_body, max_dim=3840, max_b64_kb=15360)
                    degraded.append("degraded_images")
                except Exception:
                    pass

            try:
                body_size_kb = len(json.dumps(fallback_body)) / 1024
            except Exception:
                body_size_kb = "?"
            debug_log.log("retry_attempt", {
                "attempt": str(attempt),
                "model": request_model,
                "retry_mode": "non_stream",
                "degradation": ", ".join(degraded) if degraded else "none",
                "body_size_kb": str(body_size_kb),
            })

            try:
                with httpx.Client(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
                    self._set_active_request(client=client)
                    payload_meta = debug_log.save_request_payload(
                        route=f"{self.api_base}/responses",
                        request_kind="json",
                        request_body=fallback_body,
                        note="responses_non_stream_fallback",
                        attempt=attempt,
                    )
                    debug_log.log("request_payload_saved", payload_meta)
                    resp = client.post(
                        f"{self.api_base}/responses",
                        headers=self._headers({"Content-Type": "application/json"}),
                        json=fallback_body,
                    )
                    self._set_active_request(client=client, response=resp)
                    if self._cancel_event.is_set():
                        return
                    if resp.status_code != 200:
                        err_text = resp.text[:500] if resp.text else f"HTTP {resp.status_code}"
                        debug_log.log("non_stream_fallback_error", {
                            "attempt": str(attempt),
                            "model": request_model,
                            "status_code": str(resp.status_code),
                            "error_text": err_text[:300],
                        })
                        if resp.status_code in (502, 503, 429) and attempt < MAX_RETRIES:
                            if on_partial:
                                on_partial(None, attempt)
                            delay = min(RETRY_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 3), 60)
                            debug_log.log("retry_scheduled", {
                                "attempt": str(attempt),
                                "model": request_model,
                                "delay_sec": f"{delay:.1f}",
                                "status_code": str(resp.status_code),
                                "retry_mode": "non_stream",
                            })
                            if self._cancelable_sleep(delay):
                                return
                            continue
                        if on_error:
                            on_error(f"非流式重试失败: HTTP {resp.status_code}: {err_text}")
                        return

                    data = resp.json()
                    calls = [
                        o for o in data.get("output", [])
                        if isinstance(o, dict) and o.get("type") == "image_generation_call"
                    ]
                    if calls and calls[0].get("result"):
                        b64 = calls[0]["result"]
                        revised_prompt = None
                        response_id = data.get("id")
                        for o in data.get("output", []):
                            if isinstance(o, dict) and o.get("type") == "message":
                                for c in o.get("content", []):
                                    if isinstance(c, dict) and c.get("type") == "output_text":
                                        revised_prompt = c.get("text")
                        debug_log.log("non_stream_fallback_success", {
                            "attempt": str(attempt),
                            "request_model": request_model,
                            "response_model": data.get("model"),
                            "quality": calls[0].get("quality"),
                        })
                        if on_partial:
                            on_partial(b64, 1)
                        if on_done:
                            on_done(b64, 1, revised_prompt=revised_prompt, response_id=response_id)
                        return

                    text_items = [
                        o for o in data.get("output", [])
                        if isinstance(o, dict) and o.get("type") == "message"
                    ]
                    text_preview = ""
                    for item in text_items:
                        for c in item.get("content", []):
                            if isinstance(c, dict) and c.get("type") == "output_text":
                                text_preview += c.get("text", "")[:200]
                    debug_log.log("non_stream_fallback_no_image", {
                        "attempt": str(attempt),
                        "model": request_model,
                        "text_preview": text_preview[:200],
                    })
                    if attempt < MAX_RETRIES:
                        if on_partial:
                            on_partial(None, attempt)
                        delay = min(RETRY_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 3), 60)
                        debug_log.log("retry_scheduled", {
                            "attempt": str(attempt),
                            "model": request_model,
                            "delay_sec": f"{delay:.1f}",
                            "reason": "no_image_in_non_stream_response",
                            "retry_mode": "non_stream",
                        })
                        if self._cancelable_sleep(delay):
                            return
                        continue
                    if on_error:
                        on_error("非流式重试: 模型未返回图片数据" + (f" (返回了文本: {text_preview[:100]})" if text_preview else ""))
                    return
            except httpx.ConnectError as e:
                debug_log.log("non_stream_fallback_connect_error", {
                    "attempt": str(attempt),
                    "model": request_model,
                    "error": str(e)[:300],
                })
                if attempt < MAX_RETRIES:
                    if on_partial:
                        on_partial(None, attempt)
                    delay = min(RETRY_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 3), 60)
                    debug_log.log("retry_scheduled", {
                        "attempt": str(attempt),
                        "model": request_model,
                        "delay_sec": f"{delay:.1f}",
                        "reason": str(e)[:200],
                        "retry_mode": "non_stream",
                    })
                    if self._cancelable_sleep(delay):
                        return
                    continue
                if on_error:
                    on_error(f"非流式重试连接失败: {e}")
                return
            except httpx.ReadTimeout as e:
                debug_log.log("non_stream_fallback_read_timeout", {
                    "attempt": str(attempt),
                    "model": request_model,
                    "error": str(e)[:300],
                })
                if attempt < MAX_RETRIES:
                    if on_partial:
                        on_partial(None, attempt)
                    delay = min(RETRY_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 3), 60)
                    debug_log.log("retry_scheduled", {
                        "attempt": str(attempt),
                        "model": request_model,
                        "delay_sec": f"{delay:.1f}",
                        "reason": str(e)[:200],
                        "retry_mode": "non_stream",
                    })
                    if self._cancelable_sleep(delay):
                        return
                    continue
                if on_error:
                    on_error(f"非流式重试读取超时: {e}")
                return
            except httpx.RemoteProtocolError as e:
                debug_log.log("non_stream_fallback_remote_protocol_error", {
                    "attempt": str(attempt),
                    "model": request_model,
                    "error": str(e)[:300],
                })
                if attempt < MAX_RETRIES:
                    if on_partial:
                        on_partial(None, attempt)
                    delay = min(RETRY_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 3), 60)
                    debug_log.log("retry_scheduled", {
                        "attempt": str(attempt),
                        "model": request_model,
                        "delay_sec": f"{delay:.1f}",
                        "reason": str(e)[:200],
                        "retry_mode": "non_stream",
                    })
                    if self._cancelable_sleep(delay):
                        return
                    continue
                if on_error:
                    on_error(f"非流式重试连接中断: {e}")
                return
            except Exception as e:
                debug_log.log("non_stream_fallback_exception", {
                    "attempt": str(attempt),
                    "model": request_model,
                    "error": str(e)[:300],
                })
                if attempt < MAX_RETRIES:
                    if on_partial:
                        on_partial(None, attempt)
                    delay = min(RETRY_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 3), 60)
                    debug_log.log("retry_scheduled", {
                        "attempt": str(attempt),
                        "model": request_model,
                        "delay_sec": f"{delay:.1f}",
                        "reason": str(e)[:200],
                        "retry_mode": "non_stream",
                    })
                    if self._cancelable_sleep(delay):
                        return
                    continue
                if on_error:
                    on_error(f"非流式重试异常: {e}")
                return
            finally:
                self._clear_active_request()

    @staticmethod
    def _normalize_edit_size(size):
        value = ImageGenerator._normalize_size_text(size).lower()
        if not value:
            value = "auto"
        if value == "auto":
            return "auto"
        if value in EDIT_API_SIZES:
            return value
        try:
            w_str, h_str = value.split("x", 1)
            w = int(w_str)
            h = int(h_str)
        except Exception:
            return "1024x1024"
        if w <= 0 or h <= 0:
            return "1024x1024"
        # GPT Image 2 accepts a wider range of sizes than legacy GPT Image models.
        if (
            max(w, h) <= 3840
            and w % 16 == 0
            and h % 16 == 0
            and (max(w, h) / min(w, h)) <= 3
            and 655360 <= (w * h) <= 8294400
        ):
            return f"{w}x{h}"
        if w == h:
            return "1024x1024"
        return "1536x1024" if w > h else "1024x1536"

    @staticmethod
    def _parse_size_tuple(size):
        if isinstance(size, (tuple, list)) and len(size) >= 2:
            try:
                w = int(size[0])
                h = int(size[1])
                if w > 0 and h > 0:
                    return (w, h)
            except Exception:
                return None
        value = ImageGenerator._normalize_size_text(size).lower()
        if "x" not in value:
            return None
        try:
            w_str, h_str = value.split("x", 1)
            w = int(w_str)
            h = int(h_str)
        except Exception:
            return None
        if w <= 0 or h <= 0:
            return None
        return (w, h)

    @staticmethod
    def _format_size_tuple(size):
        parsed = ImageGenerator._parse_size_tuple(size)
        if not parsed:
            return "1024x1024"
        return f"{parsed[0]}x{parsed[1]}"

    @staticmethod
    def _normalize_size_text(size):
        value = str(size or "").strip()
        if not value:
            return ""
        return (
            value
            .replace("×", "x")
            .replace("X", "x")
            .replace("*", "x")
            .replace(" ", "")
        )

    @staticmethod
    def _coerce_size_tuple(width, height, min_area=655360, max_area=8294400, max_dim=3840):
        try:
            w = float(width)
            h = float(height)
        except Exception:
            return (1024, 1024)
        if w <= 0 or h <= 0:
            return (1024, 1024)

        if max(w, h) > max_dim:
            scale = max_dim / max(w, h)
            w *= scale
            h *= scale

        area = max(w * h, 1.0)
        if area > max_area:
            scale = (max_area / area) ** 0.5
            w *= scale
            h *= scale

        area = max(w * h, 1.0)
        if area < min_area:
            scale = (min_area / area) ** 0.5
            w *= scale
            h *= scale
            if max(w, h) > max_dim:
                scale = max_dim / max(w, h)
                w *= scale
                h *= scale

        w = max(16, int(round(w / 16.0)) * 16)
        h = max(16, int(round(h / 16.0)) * 16)
        w = min(max_dim, w)
        h = min(max_dim, h)
        w = max(16, int(round(w / 16.0)) * 16)
        h = max(16, int(round(h / 16.0)) * 16)

        normalized = ImageGenerator._normalize_edit_size(f"{w}x{h}")
        parsed = ImageGenerator._parse_size_tuple(normalized)
        return parsed or (1024, 1024)

    @staticmethod
    def _size_for_pixel_budget(base_size, target_pixels):
        parsed = ImageGenerator._parse_size_tuple(base_size)
        if not parsed:
            return "1024x1024"
        base_w, base_h = parsed
        base_pixels = max(base_w * base_h, 1)
        try:
            target_pixels = max(float(target_pixels), float(base_pixels))
        except Exception:
            target_pixels = float(base_pixels)
        scale = (target_pixels / float(base_pixels)) ** 0.5
        coerced = ImageGenerator._coerce_size_tuple(base_w * scale, base_h * scale)
        return ImageGenerator._format_size_tuple(coerced)

    @staticmethod
    def _normalize_image_quality(quality, allow_auto=True):
        mapping = {
            "standard": "medium",
            "hd": "high",
            "auto": "auto",
            "low": "low",
            "medium": "medium",
            "high": "high",
        }
        value = mapping.get(str(quality or "auto").strip().lower(), str(quality or "auto").strip().lower())
        if allow_auto and value == "auto":
            return "auto"
        if value in {"low", "medium", "high"}:
            return value
        return None

    def _resolve_responses_model(self):
        if self.model in EDIT_MODEL_CANDIDATES:
            return DEFAULT_MODEL
        return self.model

    def _resolve_image_tool_model(self):
        model = str(self.model or "").strip()
        if model.startswith("gpt-image") or model == "chatgpt-image-latest":
            return model
        return None

    def _uses_images_api(self):
        return bool(self._resolve_image_tool_model())

    @staticmethod
    def _mime_extension(mime_type, fallback="png"):
        value = str(mime_type or "").lower().strip()
        if value == "image/jpeg":
            return "jpg"
        if value == "image/webp":
            return "webp"
        if value == "image/png":
            return "png"
        return fallback

    @staticmethod
    def _decode_image_api_payload(data):
        if not isinstance(data, dict):
            return None, None, None
        items = data.get("data")
        if not isinstance(items, list) or not items:
            return None, None, None
        first = items[0] if isinstance(items[0], dict) else {}
        b64 = first.get("b64_json")
        revised_prompt = first.get("revised_prompt") or data.get("revised_prompt")
        response_id = first.get("id") or data.get("id")
        return b64, revised_prompt, response_id

    def _normalize_generation_size(self, size):
        normalized = ImageGenerator._normalize_edit_size(size)
        tool_model = self._resolve_image_tool_model()
        if tool_model in ("gpt-image-1", "gpt-image-1.5", "gpt-image-1-mini", "chatgpt-image-latest"):
            if normalized == "auto":
                return "1024x1024"
            if normalized in EDIT_API_SIZES:
                return normalized
            try:
                w_str, h_str = str(normalized).split("x", 1)
                w = int(w_str)
                h = int(h_str)
            except Exception:
                return "1024x1024"
            if w == h:
                return "1024x1024"
            return "1536x1024" if w > h else "1024x1536"
        return normalized

    @staticmethod
    def _multipart_headers(auth_token):
        return {"Authorization": auth_token}

    @staticmethod
    def _supports_high_input_fidelity(tool_model):
        # OpenAI docs list gpt-image-1 / 1.5 as supporting input_fidelity;
        # gpt-image-2 is already always high-fidelity.
        return tool_model in (None, "", "gpt-image-1", "gpt-image-1.5")

    @staticmethod
    def _prepare_responses_input_image(image_b64, max_dim=1536, target_size=None, target_long_edge=None):
        """Prepare an input image for Responses image_generation edits."""
        try:
            img = ImageGenerator.b64_to_image(image_b64)
            src_format = (getattr(img, "format", None) or "PNG").upper()
        except Exception:
            return image_b64, "image/png", None

        has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        target_tuple = ImageGenerator._parse_size_tuple(target_size)
        if target_tuple:
            if img.size != target_tuple:
                img = img.resize(target_tuple, Image.LANCZOS)
        elif target_long_edge:
            try:
                target_long_edge = int(target_long_edge)
            except Exception:
                target_long_edge = 0
            if target_long_edge > 0:
                width, height = img.size
                source_long_edge = max(width, height)
                if source_long_edge > 0 and source_long_edge != target_long_edge:
                    scale = target_long_edge / float(source_long_edge)
                    new_size = (
                        max(1, int(round(width * scale))),
                        max(1, int(round(height * scale))),
                    )
                    if new_size != img.size:
                        img = img.resize(new_size, Image.LANCZOS)
        else:
            w, h = img.size
            if max(w, h) > max_dim:
                scale = max_dim / max(w, h)
                new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
                img = img.resize(new_size, Image.LANCZOS)

        buf = io.BytesIO()
        if has_alpha or src_format == "PNG":
            if img.mode not in ("RGB", "RGBA", "L", "LA"):
                img = img.convert("RGBA" if has_alpha else "RGB")
            img.save(buf, format="PNG", optimize=True)
            mime = "image/png"
        else:
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.save(buf, format="JPEG", quality=95, optimize=True)
            mime = "image/jpeg"
        return base64.b64encode(buf.getvalue()).decode("ascii"), mime, img.size

    @staticmethod
    def _prepare_masked_edit_assets(image_b64, mask_b64, max_dim=1024, target_size=None):
        """Prepare image + mask together so they stay same-size/same-format for edits."""
        image = ImageGenerator.b64_to_image(image_b64).convert("RGBA")
        mask = ImageGenerator.b64_to_image(mask_b64).convert("RGBA")
        if mask.size != image.size:
            mask = mask.resize(image.size, Image.NEAREST)

        target_tuple = ImageGenerator._parse_size_tuple(target_size)
        if target_tuple:
            if image.size != target_tuple:
                image = image.resize(target_tuple, Image.LANCZOS)
                mask = mask.resize(target_tuple, Image.NEAREST)
        else:
            w, h = image.size
            if max(w, h) > max_dim:
                scale = max_dim / max(w, h)
                new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
                image = image.resize(new_size, Image.LANCZOS)
                mask = mask.resize(new_size, Image.NEAREST)

        image_png = ImageGenerator.image_to_b64(image, fmt="PNG")
        mask_png = ImageGenerator.image_to_b64(mask, fmt="PNG")
        return image_png, "image/png", mask_png, "image/png", image.size

    @staticmethod
    def _mask_position_label(center_x_ratio, center_y_ratio):
        cols = ("left", "center", "right")
        rows = ("upper", "center", "lower")

        def bucket(value):
            if value < 1 / 3:
                return 0
            if value > 2 / 3:
                return 2
            return 1

        col = cols[bucket(center_x_ratio)]
        row = rows[bucket(center_y_ratio)]
        if row == "center" and col == "center":
            return "center"
        if row == "center":
            return f"{col}-center"
        if col == "center":
            return f"{row}-center"
        return f"{row}-{col}"

    @staticmethod
    def _get_mask_region_info(mask_b64):
        if not mask_b64:
            return None
        try:
            mask = ImageGenerator.b64_to_image(mask_b64).convert("RGBA")
            alpha = mask.getchannel("A")
            editable_alpha = alpha.point(lambda value: 255 - value)
            bbox = editable_alpha.getbbox()
            if not bbox:
                return None

            width, height = mask.size
            left, top, right, bottom = bbox
            box_w = max(1, right - left)
            box_h = max(1, bottom - top)
            center_x = (left + right) / 2 / max(width, 1)
            center_y = (top + bottom) / 2 / max(height, 1)
            coverage = (box_w * box_h) / max(width * height, 1)

            if coverage <= 0.015:
                size_label = "tiny"
            elif coverage <= 0.08:
                size_label = "small"
            elif coverage <= 0.22:
                size_label = "medium-sized"
            else:
                size_label = "large"

            region_label = ImageGenerator._mask_position_label(center_x, center_y)
            return {
                "mask": mask,
                "editable_alpha": editable_alpha,
                "bbox": bbox,
                "width": width,
                "height": height,
                "coverage": coverage,
                "size_label": size_label,
                "region_label": region_label,
                "x_range_text": f"{left / width * 100:.0f}-{right / width * 100:.0f}%",
                "y_range_text": f"{top / height * 100:.0f}-{bottom / height * 100:.0f}%",
            }
        except Exception:
            return None

    @staticmethod
    def _build_mask_region_guidance(mask_b64):
        """Describe the editable mask region so the model can localize the request."""
        info = ImageGenerator._get_mask_region_info(mask_b64)
        if not info:
            return None
        try:
            return (
                f"The user-painted editable region is a {info['size_label']} area around the {info['region_label']} of the base image "
                f"(approximately x {info['x_range_text']} and y {info['y_range_text']} of the frame, "
                f"covering about {info['coverage'] * 100:.1f}% of the image). "
                "Treat that painted region as the main location anchor referenced by the user, not automatically as a hard pixel boundary. "
                "Use the painted selection together with the user's wording and the image content to decide whether this is a precise local fix, a nearby range fix, or a whole-object fix. "
                "Keep unrelated areas unchanged and keep nearby context visually consistent."
            )
        except Exception:
            return None

    @staticmethod
    def _build_mask_reference_image(image_b64, mask_b64):
        """Create a second guide image that highlights the editable region on top of the source image."""
        info = ImageGenerator._get_mask_region_info(mask_b64)
        if not info:
            return None
        try:
            base = ImageGenerator.b64_to_image(image_b64).convert("RGBA")
            if base.size != (info["width"], info["height"]):
                base = base.resize((info["width"], info["height"]), Image.LANCZOS)

            preview = base.copy()
            editable_alpha = info["editable_alpha"]
            left, top, right, bottom = info["bbox"]
            pad = max(6, int(round(min(base.size) * 0.03)))
            stroke = max(3, int(round(min(base.size) * 0.012)))

            outside = Image.new("RGBA", preview.size, (0, 0, 0, 0))
            outside.putalpha(editable_alpha.point(lambda value: 0 if value > 0 else 82))
            preview = Image.alpha_composite(preview, outside)

            highlight = Image.new("RGBA", preview.size, (255, 52, 52, 0))
            highlight.putalpha(editable_alpha.point(lambda value: 0 if value <= 0 else max(96, min(168, int(value * 0.72)))))
            preview = Image.alpha_composite(preview, highlight)

            draw = ImageDraw.Draw(preview)
            x0 = max(0, left - pad)
            y0 = max(0, top - pad)
            x1 = min(preview.width - 1, right + pad)
            y1 = min(preview.height - 1, bottom + pad)
            for offset in range(stroke):
                draw.rounded_rectangle(
                    (x0 - offset, y0 - offset, x1 + offset, y1 + offset),
                    radius=max(8, pad // 2),
                    outline=(255, 32, 32, 255),
                    width=1,
                )
            return ImageGenerator.image_to_b64(preview, fmt="PNG")
        except Exception:
            return None

    @staticmethod
    def _build_mask_focus_crop_image(image_b64, mask_b64, scope_mode="auto"):
        """Create a zoomed crop around the editable region so small targets are easier for the model to identify."""
        info = ImageGenerator._get_mask_region_info(mask_b64)
        if not info:
            return None
        try:
            base = ImageGenerator.b64_to_image(image_b64).convert("RGBA")
            if base.size != (info["width"], info["height"]):
                base = base.resize((info["width"], info["height"]), Image.LANCZOS)

            left, top, right, bottom = info["bbox"]
            box_w = max(1, right - left)
            box_h = max(1, bottom - top)
            center_x = int(round((left + right) / 2))
            center_y = int(round((top + bottom) / 2))
            crop_tuning = {
                "precise": {"pad_scale": 0.75, "min_ratio": 0.18},
                "range": {"pad_scale": 1.55, "min_ratio": 0.32},
                "object": {"pad_scale": 1.15, "min_ratio": 0.26},
                "auto": {"pad_scale": 1.0, "min_ratio": 0.24},
            }
            tuning = crop_tuning.get(scope_mode, crop_tuning["auto"])
            pad_x = max(int(round(box_w * tuning["pad_scale"])), max(28, int(round(base.width * 0.04))))
            pad_y = max(int(round(box_h * tuning["pad_scale"])), max(28, int(round(base.height * 0.04))))

            crop_left = max(0, left - pad_x)
            crop_top = max(0, top - pad_y)
            crop_right = min(base.width, right + pad_x)
            crop_bottom = min(base.height, bottom + pad_y)

            min_crop_w = min(base.width, max(box_w + 2 * pad_x, int(round(base.width * tuning["min_ratio"]))))
            min_crop_h = min(base.height, max(box_h + 2 * pad_y, int(round(base.height * tuning["min_ratio"]))))

            crop_w = crop_right - crop_left
            crop_h = crop_bottom - crop_top
            if crop_w < min_crop_w:
                extra = int(round((min_crop_w - crop_w) / 2))
                crop_left = max(0, center_x - (crop_w // 2) - extra)
                crop_right = min(base.width, crop_left + min_crop_w)
                crop_left = max(0, crop_right - min_crop_w)
            if crop_h < min_crop_h:
                extra = int(round((min_crop_h - crop_h) / 2))
                crop_top = max(0, center_y - (crop_h // 2) - extra)
                crop_bottom = min(base.height, crop_top + min_crop_h)
                crop_top = max(0, crop_bottom - min_crop_h)

            crop_box = (crop_left, crop_top, crop_right, crop_bottom)
            crop = base.crop(crop_box)
            crop_alpha = info["editable_alpha"].crop(crop_box)

            outside = Image.new("RGBA", crop.size, (0, 0, 0, 0))
            outside.putalpha(crop_alpha.point(lambda value: 0 if value > 0 else 58))
            preview = Image.alpha_composite(crop, outside)
            highlight = Image.new("RGBA", crop.size, (255, 52, 52, 0))
            highlight.putalpha(crop_alpha.point(lambda value: 0 if value <= 0 else max(104, min(176, int(value * 0.8)))))
            preview = Image.alpha_composite(preview, highlight)

            draw = ImageDraw.Draw(preview)
            stroke = max(3, int(round(min(preview.size) * 0.018)))
            local_box = (
                left - crop_box[0],
                top - crop_box[1],
                right - crop_box[0],
                bottom - crop_box[1],
            )
            for offset in range(stroke):
                draw.rounded_rectangle(
                    (
                        local_box[0] - offset,
                        local_box[1] - offset,
                        local_box[2] + offset,
                        local_box[3] + offset,
                    ),
                    radius=max(10, stroke * 2),
                    outline=(255, 32, 32, 255),
                    width=1,
                )

            longest = max(preview.size)
            if longest < 512:
                scale = 512 / max(longest, 1)
                preview = preview.resize(
                    (
                        max(1, int(round(preview.width * scale))),
                        max(1, int(round(preview.height * scale))),
                    ),
                    Image.LANCZOS,
                )
            return ImageGenerator.image_to_b64(preview, fmt="PNG")
        except Exception:
            return None

    @staticmethod
    def _infer_mask_edit_scope(prompt):
        text = str(prompt or "")
        lower_text = text.lower()
        compact_text = lower_text.replace(" ", "")

        precise_keywords = (
            "精准", "精确", "只改", "仅改", "只修", "局部修", "小范围", "这个字",
            "污点", "瑕疵", "边缘", "线条", "细节", "字", "文字", "logo", "标志",
            "precise", "exact", "tight", "small area", "tiny", "pixel", "spot",
            "blemish", "scratch", "edge", "text", "logo", "fix only", "just",
        )
        range_keywords = (
            "附近", "周围", "这一片", "这块", "这片", "局部区域", "范围", "扩", "周边", "这一带",
            "around", "nearby", "surrounding", "region", "area", "locally", "expand a bit",
        )
        object_keywords = (
            "这只", "这人", "这个人", "这条", "这辆", "这匹", "这只动物", "这个物体", "这个主体",
            "整只", "整个对象", "整个主体", "整个角色", "整个人", "全身", "主体", "对象", "角色", "宠物", "动物",
            "whole object", "whole subject", "entire object", "entire subject", "full object",
            "person", "animal", "subject", "object",
        )

        def score(keywords):
            return sum(1 for keyword in keywords if keyword and (keyword in compact_text or keyword in lower_text))

        scores = {
            "precise": score(precise_keywords),
            "range": score(range_keywords),
            "object": score(object_keywords),
        }
        best_mode = max(scores, key=lambda key: scores[key])
        if scores[best_mode] > 0:
            return best_mode
        return "auto"

    @staticmethod
    def _build_mask_scope_guidance(scope_mode):
        guidance = {
            "precise": (
                "Detected scope preference: precise local fix. "
                "Use the painted selection as a tight anchor and make the smallest natural edit that satisfies the request. "
                "Prefer staying within the painted pixels and their immediately adjacent detail unless a tiny extension is required for coherence."
            ),
            "range": (
                "Detected scope preference: nearby range fix. "
                "Treat the painted selection as the center of the intended edit area. "
                "You may extend modestly around the painted region when needed for a coherent local result, but do not spill into unrelated areas."
            ),
            "object": (
                "Detected scope preference: object-level fix. "
                "Treat the painted selection as a pointer to the intended nearby object or logically connected part. "
                "If the user painted only part of an object, you may complete the edit across the whole relevant object while keeping the rest of the scene unchanged."
            ),
            "auto": (
                "Infer the edit scope from the user's wording and the image content. "
                "Use a tight scope for tiny defects or text, a modest nearby scope for local area changes, "
                "and a whole-object scope only when the request is clearly about a full nearby object or connected part."
            ),
        }
        return guidance.get(scope_mode, guidance["auto"])

    @staticmethod
    def _build_edit_image_slot_layout(additional_reference_count=0,
                                      has_mask_reference=False,
                                      has_mask_focus_crop=False):
        next_index = 2
        user_image_start = next_index if additional_reference_count > 0 else None
        user_image_end = (next_index + additional_reference_count - 1) if additional_reference_count > 0 else None
        next_index += max(0, int(additional_reference_count or 0))
        mask_reference_index = next_index if has_mask_reference else None
        if has_mask_reference:
            next_index += 1
        mask_focus_index = next_index if has_mask_focus_crop else None
        return {
            "user_image_start": user_image_start,
            "user_image_end": user_image_end,
            "mask_reference_index": mask_reference_index,
            "mask_focus_index": mask_focus_index,
        }

    @staticmethod
    def _infer_multi_image_prompt_intent(prompt):
        text = str(prompt or "")
        lower_text = text.lower()
        compact_text = lower_text.replace(" ", "")

        merge_keywords = (
            "合并", "融合", "融入", "结合", "拼合", "拼接", "贴到", "贴上", "加入", "加到",
            "放到", "移到", "替换", "换成", "借用", "搬到", "拿到", "套用", "交换",
            "merge", "blend", "combine", "composite", "swap", "replace", "borrow",
            "transfer", "paste", "insert",
        )
        style_keywords = (
            "参考", "参照", "风格", "画风", "配色", "色调", "材质", "质感", "纹理", "光照",
            "灯光", "颜色", "像第", "仿照", "同款", "style", "reference", "palette",
            "texture", "material", "lighting", "appearance", "look",
        )
        compare_keywords = (
            "对比", "比较", "按照", "根据", "以第", "follow", "match", "compare",
        )

        def has_any(keywords):
            return any(keyword in compact_text or keyword in lower_text for keyword in keywords)

        return {
            "merge": has_any(merge_keywords),
            "style": has_any(style_keywords),
            "compare": has_any(compare_keywords),
        }

    @staticmethod
    def _build_multi_image_role_guidance(prompt, additional_reference_count,
                                         has_mask_reference=False,
                                         has_mask_focus_crop=False):
        if additional_reference_count <= 0:
            return None

        layout = ImageGenerator._build_edit_image_slot_layout(
            additional_reference_count=additional_reference_count,
            has_mask_reference=has_mask_reference,
            has_mask_focus_crop=has_mask_focus_crop,
        )
        intent = ImageGenerator._infer_multi_image_prompt_intent(prompt)

        parts = []
        if additional_reference_count == 1:
            parts.append(
                "Image 2 is the additional user-supplied image. "
                "If the user refers to the second image, that means image 2."
            )
        else:
            parts.append(
                f"Images {layout['user_image_start']}-{layout['user_image_end']} are the additional user-supplied images in the same order the user provided them. "
                "If the user refers to the second, third, or later image, interpret that using this user-supplied image order."
            )

        parts.append(
            "Do not assume the extra user-supplied images are reference-only. "
            "Infer the role of each extra image from the user's request."
        )
        parts.append(
            "Unless the user explicitly says otherwise, image 1 remains the main source image to edit "
            "and the default final output canvas."
        )

        if intent["merge"] and intent["style"]:
            parts.append(
                "The request appears to mix content transfer with appearance or style transfer. "
                "Use the relevant extra user-supplied images as donor images for the specifically requested parts or attributes, "
                "then apply only those requested changes back onto image 1 at the user-indicated target area, using the smallest natural scope that satisfies the request."
            )
        elif intent["merge"]:
            parts.append(
                "The request appears to ask for merging, replacing, borrowing, or transferring content from one or more extra user-supplied images into image 1. "
                "Treat the relevant extra user-supplied images as donor images. "
                "Extract only the specifically requested object, part, logo, text, pattern, material, or attribute, "
                "and place it only at the intended target area of image 1."
            )
        elif intent["style"]:
            parts.append(
                "The request appears to use one or more extra user-supplied images mainly as reference images for style, color, material, texture, lighting, or other appearance cues. "
                "Keep image 1 as the base scene and copy only the requested visual attributes into the intended target area."
            )
        else:
            parts.append(
                "An extra user-supplied image may be a donor image for merging or replacement, a style or appearance reference, a content reference, or a comparison image. "
                "Use only the images that are actually needed to satisfy the user's request, and ignore any unused extra image."
            )

        if intent["compare"] and not intent["merge"]:
            parts.append(
                "When the user asks to match or compare against another image, use that image only for the specifically requested alignment or similarity, "
                "not as permission to replace the whole scene."
            )

        if has_mask_reference:
            parts.append(
                f"Image {layout['mask_reference_index']} is an app-generated copy of image 1 with the masked editable region highlighted in red. "
                "It is a localization guide, not an extra user-supplied image, and not the image that should be edited or returned as the final output."
            )
        if has_mask_focus_crop:
            parts.append(
                f"Image {layout['mask_focus_index']} is an app-generated zoomed crop around the painted selection and its nearby context. "
                "It is only a focus aid for localizing the target in image 1, and not the image that should be edited or returned as the final output."
            )
        return " ".join(parts)

    @staticmethod
    def _check_sse_error(block):
        """Check if an SSE event block contains an error event"""
        try:
            data_lines = []
            event_types = []
            for line in block.splitlines():
                if line.startswith("data:"):
                    data_lines.append(line[5:].strip())
                elif line.startswith("event:"):
                    event_types.append(line[6:].strip())

            # Check event type first
            for event_type in event_types:
                if event_type in ("error", "exception"):
                    payload = "\n".join(data_lines).strip()
                    if payload:
                        try:
                            parsed = json.loads(payload)
                            msg = parsed.get("error", {})
                            if isinstance(msg, dict):
                                return msg.get("message", str(msg)[:200])
                            return str(msg)[:200]
                        except Exception:
                            return payload[:200]
                    return f"SSE {event_type} event"

            # Check data payload for error indicators
            payload = "\n".join(data_lines).strip()
            if payload:
                try:
                    parsed = json.loads(payload)
                    if isinstance(parsed, dict):
                        if parsed.get("type") == "error":
                            msg = parsed.get("error", {})
                            if isinstance(msg, dict):
                                return msg.get("message", str(msg)[:200])
                            return str(msg)[:200]
                        if "error" in parsed and parsed.get("type") not in ("image_generation",):
                            err = parsed.get("error")
                            if err is not None:
                                if isinstance(err, dict):
                                    return err.get("message", str(err)[:200])
                                return str(err)[:200]
                except Exception:
                    pass
            return None
        except Exception:
            return None

    def generate_stream(self, prompt, size="1024x1024", output_format="png",
                        quality="auto", output_compression=100,
                        previous_response_id=None,
                        on_partial=None, on_done=None, on_error=None):
        resolved_size = self._normalize_generation_size(size)
        resolved_quality = self._normalize_image_quality(quality, allow_auto=True)
        if self._uses_images_api():
            t = threading.Thread(
                target=self._images_generate_worker,
                args=(
                    prompt,
                    resolved_size,
                    output_format,
                    resolved_quality,
                    output_compression,
                    on_partial,
                    on_done,
                    on_error,
                ),
                daemon=True,
            )
            t.start()
            return t
        # ── Debug logging: generate request construction ──
        debug_log.log("generate_stream_called", {
            "prompt": prompt[:200],
            "size": size,
            "resolved_size": resolved_size,
            "output_format": output_format,
            "quality": resolved_quality or "",
            "has_previous_response_id": str(bool(previous_response_id)),
        })
        tools_config = {
            "type": "image_generation",
            "action": "generate",
            "size": resolved_size,
            "output_format": output_format,
        }
        tool_model = self._resolve_image_tool_model()
        if tool_model:
            tools_config["model"] = tool_model
        if resolved_quality and resolved_quality != "auto":
            tools_config["quality"] = resolved_quality
        if output_compression < 100 and output_format in ("jpeg", "webp"):
            tools_config["output_compression"] = output_compression
        body = {
            "model": self._resolve_responses_model(),
            "input": prompt,
            "instructions": "Generate the image as described. Always use the image_generation tool to produce an image output.",
            "tools": [tools_config],
            "tool_choice": "auto",
            "stream": True,
        }
        if previous_response_id:
            body["previous_response_id"] = previous_response_id
        t = threading.Thread(target=self._stream_worker,
                             args=(body, on_partial, on_done, on_error), daemon=True)
        t.start()
        return t

    def edit_stream(self, prompt, image_b64, size="1024x1024",
                    output_format="png", quality="auto",
                    output_compression=100,
                    mask_b64=None,
                    previous_response_id=None,
                    on_partial=None, on_done=None, on_error=None):
        resolved_size = self._normalize_edit_size(size)
        resolved_quality = self._normalize_image_quality(quality, allow_auto=False)
        if self._uses_images_api():
            t = threading.Thread(
                target=self._images_edit_worker,
                args=(
                    prompt,
                    image_b64,
                    resolved_size,
                    output_format,
                    resolved_quality,
                    output_compression,
                    mask_b64,
                    on_partial,
                    on_done,
                    on_error,
                ),
                daemon=True,
            )
            t.start()
            return t
        responses_model = self._resolve_responses_model()
        edit_size = resolved_size
        edit_quality = resolved_quality
        ignored_prev_id = bool(previous_response_id)
        process_size_tuple = self._parse_size_tuple(edit_size)
        process_long_edge = max(process_size_tuple) if process_size_tuple else 1536
        if mask_b64:
            comp_b64, mime, mask_comp, mask_mime, prepared_size = self._prepare_masked_edit_assets(
                image_b64,
                mask_b64,
                max_dim=process_long_edge,
                target_size=process_size_tuple,
            )
        else:
            comp_b64, mime, prepared_size = self._prepare_responses_input_image(
                image_b64,
                max_dim=process_long_edge,
                target_size=process_size_tuple,
            )
            mask_comp = None
            mask_mime = None
        mask_scope_mode = self._infer_mask_edit_scope(prompt) if mask_b64 else "auto"
        mask_guidance = self._build_mask_region_guidance(mask_comp or mask_b64)
        mask_reference_b64 = self._build_mask_reference_image(comp_b64, mask_comp or mask_b64) if mask_b64 else None
        mask_focus_crop_b64 = self._build_mask_focus_crop_image(
            comp_b64,
            mask_comp or mask_b64,
            scope_mode=mask_scope_mode,
        ) if mask_b64 else None
        edit_prompt = self._build_responses_edit_prompt(
            prompt,
            bool(mask_b64),
            mask_guidance,
            has_mask_reference=bool(mask_reference_b64),
            has_mask_focus_crop=bool(mask_focus_crop_b64),
            mask_scope_mode=mask_scope_mode,
        )
        # ── Debug logging: edit request construction ──
        debug_log.log("edit_stream_called", {
            "prompt": prompt[:200],
            "original_b64_size_kb": f"{len(image_b64)/1024:.1f}",
            "compressed_b64_size_kb": f"{len(comp_b64)/1024:.1f}",
            "compressed_mime": mime,
            "prepared_image_size": str(prepared_size) if prepared_size else "",
            "size": size,
            "resolved_size": resolved_size,
            "edit_api_size": edit_size,
            "output_format": output_format,
            "quality": edit_quality or "",
            "has_mask": str(bool(mask_b64)),
            "mask_mime": mask_mime or "",
            "passed_previous_response_id": str(ignored_prev_id),
            "uses_previous_response_id": "False",
            "responses_model": responses_model,
            "route": "/responses:image_generation:edit",
            "has_mask_reference_image": str(bool(mask_reference_b64)),
            "has_mask_focus_crop_image": str(bool(mask_focus_crop_b64)),
            "mask_scope_mode": mask_scope_mode,
        })
        if ignored_prev_id:
            debug_log.log("edit_previous_response_id_ignored", {
                "reason": "responses edit requests fail on this relay when chained with previous_response_id",
                "route": "/responses:image_generation:edit",
            })
        tools_config = {
            "type": "image_generation",
            "action": "edit",
            "size": edit_size,
            "output_format": output_format,
        }
        tool_model = self._resolve_image_tool_model()
        if tool_model:
            tools_config["model"] = tool_model
        if edit_quality:
            tools_config["quality"] = edit_quality
        if output_compression < 100 and output_format in ("jpeg", "webp"):
            tools_config["output_compression"] = output_compression
        instructions = (
            "Use the image_generation tool to edit the supplied image. "
            "Preserve source image details unless the user explicitly asks to change them. "
            "The first supplied image is the main source image and the default final output target."
        )
        if mask_b64:
            instructions += (
                " A painted user selection is available for localization. "
                "Treat it as the primary target-position cue, not automatically as a hard pixel boundary. "
                "Choose the smallest natural edit scope that satisfies the request: stay very tight for precise fixes, "
                "allow modest nearby spread for local range edits, and complete the whole nearby object or connected part for object-level edits when needed. "
                "Keep unrelated areas unchanged."
            )
        if mask_reference_b64:
            instructions += (
                " A second guide image of the same source is provided with the painted selection highlighted in red. "
                "Use that guide image only to localize the target region more precisely. Do not edit or return that guide image itself."
            )
        if mask_focus_crop_b64:
            instructions += (
                " A third zoomed crop around that highlighted region is also provided. "
                "Use the crop to understand the local target and nearby context, then apply the requested edit back onto image 1. "
                "Do not edit or return the crop itself as the final image."
            )

        content = [
            {"type": "input_text", "text": edit_prompt},
            {"type": "input_image", "image_url": f"data:{mime};base64,{comp_b64}"},
        ]
        if mask_reference_b64:
            content.append({"type": "input_image", "image_url": f"data:image/png;base64,{mask_reference_b64}"})
        if mask_focus_crop_b64:
            content.append({"type": "input_image", "image_url": f"data:image/png;base64,{mask_focus_crop_b64}"})

        body = {
            "model": responses_model,
            "input": [
                {
                    "role": "user",
                    "content": content,
                }
            ],
            "instructions": instructions,
            "tools": [tools_config],
            "tool_choice": "auto",
            "stream": True,
        }
        t = threading.Thread(target=self._stream_worker,
                             args=(body, on_partial, on_done, on_error), daemon=True)
        t.start()
        return t

    def edit_stream_multi(self, prompt, images_b64, size="1024x1024",
                          output_format="png", quality="auto",
                          output_compression=100,
                          mask_b64=None,
                          previous_response_id=None,
                          on_partial=None, on_done=None, on_error=None):
        """多图片组合编辑：将多张参考图片和提示词一起发送给 API"""
        responses_model = self._resolve_responses_model()
        resolved_size = self._normalize_edit_size(size)
        edit_size = resolved_size
        edit_quality = self._normalize_image_quality(quality, allow_auto=False)
        ignored_prev_id = bool(previous_response_id)
        process_size_tuple = self._parse_size_tuple(edit_size)
        process_long_edge = max(process_size_tuple) if process_size_tuple else 1536
        # ── Debug logging: multi-edit request construction ──
        debug_log.log("edit_stream_multi_called", {
            "prompt": prompt[:200],
            "num_images": str(len(images_b64)),
            "size": size,
            "resolved_size": resolved_size,
            "edit_api_size": edit_size,
            "output_format": output_format,
            "quality": edit_quality or "",
            "has_mask": str(bool(mask_b64)),
            "passed_previous_response_id": str(ignored_prev_id),
            "uses_previous_response_id": "False",
            "responses_model": responses_model,
            "route": "/responses:image_generation:edit",
        })
        if ignored_prev_id:
            debug_log.log("edit_previous_response_id_ignored", {
                "reason": "responses edit requests fail on this relay when chained with previous_response_id",
                "route": "/responses:image_generation:edit",
            })

        # Prepare mask if provided (same logic as edit_stream)
        mask_comp = None
        mask_mime = None
        if mask_b64 and images_b64:
            # Use the first image (primary) for mask preparation
            comp_b64_primary, mime_primary, mask_comp, mask_mime, prepared_size = self._prepare_masked_edit_assets(
                images_b64[0],
                mask_b64,
                max_dim=process_long_edge,
                target_size=process_size_tuple,
            )

        mask_scope_mode = self._infer_mask_edit_scope(prompt) if mask_b64 else "auto"
        mask_guidance = self._build_mask_region_guidance(mask_comp or mask_b64)
        mask_reference_b64 = self._build_mask_reference_image(comp_b64_primary, mask_comp or mask_b64) if mask_b64 and images_b64 else None
        mask_focus_crop_b64 = self._build_mask_focus_crop_image(
            comp_b64_primary,
            mask_comp or mask_b64,
            scope_mode=mask_scope_mode,
        ) if mask_b64 and images_b64 else None
        additional_reference_count = max(0, len(images_b64) - 1)
        layout = self._build_edit_image_slot_layout(
            additional_reference_count=additional_reference_count,
            has_mask_reference=bool(mask_reference_b64),
            has_mask_focus_crop=bool(mask_focus_crop_b64),
        )
        content = [{"type": "input_text", "text": self._build_responses_edit_prompt(
            prompt,
            bool(mask_b64),
            mask_guidance,
            has_mask_reference=bool(mask_reference_b64),
            has_mask_focus_crop=bool(mask_focus_crop_b64),
            additional_reference_count=additional_reference_count,
            mask_scope_mode=mask_scope_mode,
        )}]

        if mask_b64 and images_b64:
            # When mask is provided, use the prepared (resized) primary image
            content.append({"type": "input_image", "image_url": f"data:{mime_primary};base64,{comp_b64_primary}"})
            for b64 in images_b64[1:]:
                comp_b64, mime, _ = self._prepare_responses_input_image(
                    b64,
                    max_dim=process_long_edge,
                    target_long_edge=process_long_edge,
                )
                content.append({"type": "input_image", "image_url": f"data:{mime};base64,{comp_b64}"})
            if mask_reference_b64:
                content.append({"type": "input_image", "image_url": f"data:image/png;base64,{mask_reference_b64}"})
            if mask_focus_crop_b64:
                content.append({"type": "input_image", "image_url": f"data:image/png;base64,{mask_focus_crop_b64}"})
        else:
            for idx, b64 in enumerate(images_b64):
                target_size = process_size_tuple if idx == 0 else None
                target_long_edge = None if idx == 0 else process_long_edge
                comp_b64, mime, _ = self._prepare_responses_input_image(
                    b64,
                    max_dim=process_long_edge,
                    target_size=target_size,
                    target_long_edge=target_long_edge,
                )
                content.append({"type": "input_image", "image_url": f"data:{mime};base64,{comp_b64}"})

        tools_config = {
            "type": "image_generation",
            "action": "edit",
            "size": edit_size,
            "output_format": output_format,
        }
        tool_model = self._resolve_image_tool_model()
        if tool_model:
            tools_config["model"] = tool_model
        if edit_quality:
            tools_config["quality"] = edit_quality
        if output_compression < 100 and output_format in ("jpeg", "webp"):
            tools_config["output_compression"] = output_compression
        instructions = (
            "Use the image_generation tool to edit the supplied images. "
            "Preserve source image details unless the user explicitly asks to change them. "
            "Image 1 is the default final output target unless the user explicitly asks to edit a different user-supplied image."
        )
        if additional_reference_count > 0:
            if additional_reference_count == 1:
                instructions += (
                    " Image 2 is the additional user-supplied image and may act as a donor image, merge source, "
                    "style reference, or comparison image depending on the user's request."
                )
            else:
                instructions += (
                    f" Images {layout['user_image_start']}-{layout['user_image_end']} are the additional user-supplied images in the same order the user provided them. "
                    "Their role depends on the user's request and they are not automatically reference-only."
                )
        if mask_b64:
            instructions += (
                " A painted user selection is available for localization on image 1. "
                "Treat it as the primary target-position cue, not automatically as a hard pixel boundary. "
                "Choose the smallest natural edit scope that satisfies the request: stay very tight for precise fixes, "
                "allow modest nearby spread for local range edits, and complete the whole nearby object or connected part for object-level edits when needed. "
                "Keep unrelated areas unchanged."
            )
        if mask_reference_b64:
            instructions += (
                f" Image {layout['mask_reference_index']} is an app-generated copy of image 1 with the painted selection highlighted in red. "
                "Use it only as a localization guide. Do not edit or return that guide image itself."
            )
        if mask_focus_crop_b64:
            instructions += (
                f" Image {layout['mask_focus_index']} is an app-generated zoomed crop around the painted selection and nearby context. "
                "Use it only as a focus reference. Do not edit or return that crop itself as the final image."
            )

        body = {
            "model": responses_model,
            "input": [{"role": "user", "content": content}],
            "instructions": instructions,
            "tools": [tools_config],
            "tool_choice": "auto",
            "stream": True,
        }
        t = threading.Thread(target=self._stream_worker,
                             args=(body, on_partial, on_done, on_error), daemon=True)
        t.start()
        return t

    def _images_generate_worker(self, prompt, size, output_format, quality,
                                output_compression, on_partial, on_done, on_error):
        self._cancel_event.clear()
        model = self._resolve_image_tool_model() or "gpt-image-2"
        request_body = {
            "model": model,
            "prompt": prompt,
            "size": self._normalize_generation_size(size),
            "response_format": "b64_json",
        }
        normalized_quality = self._normalize_image_quality(quality, allow_auto=True)
        if normalized_quality:
            request_body["quality"] = normalized_quality
        if output_format:
            request_body["output_format"] = output_format
        if output_compression < 100 and output_format in ("jpeg", "webp"):
            request_body["output_compression"] = output_compression

        debug_log.log("images_api_generate_called", {
            "model": model,
            "route": "/images/generations",
            "size": request_body.get("size"),
            "quality": request_body.get("quality", "auto"),
            "output_format": output_format,
        })

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                with httpx.Client(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
                    self._set_active_request(client=client)
                    payload_meta = debug_log.save_request_payload(
                        route=f"{self.api_base}/images/generations",
                        request_kind="json",
                        request_body=request_body,
                        note="images_generate",
                        attempt=attempt,
                    )
                    debug_log.log("request_payload_saved", payload_meta)
                    resp = client.post(
                        f"{self.api_base}/images/generations",
                        headers=self._headers(),
                        json=request_body,
                    )
                    self._set_active_request(client=client, response=resp)
                    if self._cancel_event.is_set():
                        return
                    if resp.status_code != 200:
                        err_text = resp.text[:300]
                        debug_log.log("http_error", {
                            "attempt": str(attempt),
                            "status_code": str(resp.status_code),
                            "error_text": err_text,
                            "route": "/images/generations",
                            "model": model,
                        })
                        if self._is_retryable_http_status(resp.status_code, err_text) and attempt < MAX_RETRIES:
                            if on_partial:
                                on_partial(None, attempt)
                            delay = self._retry_delay_seconds(attempt)
                            debug_log.log("retry_scheduled", {
                                "attempt": str(attempt),
                                "delay_sec": f"{delay:.1f}",
                                "status_code": str(resp.status_code),
                                "retry_mode": "images_generate",
                            })
                            if self._cancelable_sleep(delay):
                                return
                            continue
                        if on_error:
                            on_error(f"HTTP {resp.status_code}: {err_text}")
                        return
                    data = resp.json()
                    b64, revised_prompt, response_id = self._decode_image_api_payload(data)
                    if not b64:
                        if on_error:
                            on_error("图片接口未返回 b64_json")
                        return
                    if on_partial:
                        on_partial(b64, 1)
                    if on_done:
                        on_done(b64, 1, revised_prompt, response_id)
                    return
            except Exception as e:
                if self._cancel_event.is_set():
                    return
                debug_log.log("request_exception", {
                    "attempt": str(attempt),
                    "route": "/images/generations",
                    "model": model,
                    "error": str(e)[:500],
                })
                if self._is_retryable_request_exception(e) and attempt < MAX_RETRIES:
                    if on_partial:
                        on_partial(None, attempt)
                    delay = self._retry_delay_seconds(attempt)
                    debug_log.log("retry_scheduled", {
                        "attempt": str(attempt),
                        "delay_sec": f"{delay:.1f}",
                        "retry_mode": "images_generate_exception",
                        "error": type(e).__name__,
                    })
                    if self._cancelable_sleep(delay):
                        return
                    continue
                if on_error:
                    on_error(str(e))
                return
            finally:
                self._clear_active_request()

    def _images_edit_worker(self, prompt, image_b64, size, output_format, quality,
                            output_compression, mask_b64, on_partial, on_done, on_error):
        self._cancel_event.clear()
        model = self._resolve_image_tool_model() or "gpt-image-2"
        normalized_size = self._normalize_generation_size(size)
        normalized_quality = self._normalize_image_quality(quality, allow_auto=False)
        process_size_tuple = self._parse_size_tuple(normalized_size)
        process_long_edge = max(process_size_tuple) if process_size_tuple else 1536

        if mask_b64:
            comp_b64, mime, mask_comp, mask_mime, prepared_size = self._prepare_masked_edit_assets(
                image_b64,
                mask_b64,
                max_dim=process_long_edge,
                target_size=process_size_tuple,
            )
        else:
            comp_b64, mime, prepared_size = self._prepare_responses_input_image(
                image_b64,
                max_dim=process_long_edge,
                target_size=process_size_tuple,
            )
            mask_comp = None
            mask_mime = None

        data = {
            "model": model,
            "prompt": prompt,
            "size": normalized_size,
            "response_format": "b64_json",
        }
        if normalized_quality:
            data["quality"] = normalized_quality
        if output_format:
            data["output_format"] = output_format
        if output_compression < 100 and output_format in ("jpeg", "webp"):
            data["output_compression"] = str(output_compression)

        files = [
            (
                "image",
                (
                    f"input.{self._mime_extension(mime)}",
                    base64.b64decode(comp_b64),
                    mime,
                ),
            ),
        ]
        if mask_comp and mask_mime:
            files.append(
                (
                    "mask",
                    (
                        f"mask.{self._mime_extension(mask_mime)}",
                        base64.b64decode(mask_comp),
                        mask_mime,
                    ),
                )
            )

        debug_log.log("images_api_edit_called", {
            "model": model,
            "route": "/images/edits",
            "size": normalized_size,
            "quality": data.get("quality", ""),
            "output_format": output_format,
            "has_mask": str(bool(mask_comp)),
            "prepared_image_size": str(prepared_size) if prepared_size else "",
        })

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                with httpx.Client(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
                    self._set_active_request(client=client)
                    payload_meta = debug_log.save_request_payload(
                        route=f"{self.api_base}/images/edits",
                        request_kind="multipart",
                        form_data=data,
                        files=files,
                        note="images_edit",
                        attempt=attempt,
                    )
                    debug_log.log("request_payload_saved", payload_meta)
                    resp = client.post(
                        f"{self.api_base}/images/edits",
                        headers=self._multipart_headers(self.auth_token),
                        data=data,
                        files=files,
                    )
                    self._set_active_request(client=client, response=resp)
                    if self._cancel_event.is_set():
                        return
                    if resp.status_code != 200:
                        err_text = resp.text[:300]
                        debug_log.log("http_error", {
                            "attempt": str(attempt),
                            "status_code": str(resp.status_code),
                            "error_text": err_text,
                            "route": "/images/edits",
                            "model": model,
                        })
                        if self._is_retryable_http_status(resp.status_code, err_text) and attempt < MAX_RETRIES:
                            if on_partial:
                                on_partial(None, attempt)
                            delay = self._retry_delay_seconds(attempt)
                            debug_log.log("retry_scheduled", {
                                "attempt": str(attempt),
                                "delay_sec": f"{delay:.1f}",
                                "status_code": str(resp.status_code),
                                "retry_mode": "images_edit",
                            })
                            if self._cancelable_sleep(delay):
                                return
                            continue
                        if on_error:
                            on_error(f"HTTP {resp.status_code}: {err_text}")
                        return
                    payload = resp.json()
                    b64, revised_prompt, response_id = self._decode_image_api_payload(payload)
                    if not b64:
                        if on_error:
                            on_error("图片编辑接口未返回 b64_json")
                        return
                    if on_partial:
                        on_partial(b64, 1)
                    if on_done:
                        on_done(b64, 1, revised_prompt, response_id)
                    return
            except Exception as e:
                if self._cancel_event.is_set():
                    return
                debug_log.log("request_exception", {
                    "attempt": str(attempt),
                    "route": "/images/edits",
                    "model": model,
                    "error": str(e)[:500],
                })
                if self._is_retryable_request_exception(e) and attempt < MAX_RETRIES:
                    if on_partial:
                        on_partial(None, attempt)
                    delay = self._retry_delay_seconds(attempt)
                    debug_log.log("retry_scheduled", {
                        "attempt": str(attempt),
                        "delay_sec": f"{delay:.1f}",
                        "retry_mode": "images_edit_exception",
                        "error": type(e).__name__,
                    })
                    if self._cancelable_sleep(delay):
                        return
                    continue
                if on_error:
                    on_error(str(e))
                return
            finally:
                self._clear_active_request()

    @staticmethod
    def _build_responses_edit_prompt(prompt, has_mask, mask_guidance=None,
                                     has_mask_reference=False,
                                     has_mask_focus_crop=False,
                                     additional_reference_count=0,
                                     mask_scope_mode="auto"):
        guard = (
            "This is an image editing task, not a new image generation task. "
            "Use the supplied image as the source. Preserve composition, subject identity, "
            "camera/framing, background, lighting, proportions, and unchanged areas unless "
            "the user explicitly asks to change them. "
        )
        if has_mask:
            layout = ImageGenerator._build_edit_image_slot_layout(
                additional_reference_count=additional_reference_count,
                has_mask_reference=has_mask_reference,
                has_mask_focus_crop=has_mask_focus_crop,
            )
            guard += (
                "Image 1 is the original source image to edit and the default final output canvas. "
                "Unless the user explicitly asks otherwise, apply the requested change back onto image 1 only. "
            )
            if additional_reference_count > 0:
                multi_guidance = ImageGenerator._build_multi_image_role_guidance(
                    prompt,
                    additional_reference_count,
                    has_mask_reference=has_mask_reference,
                    has_mask_focus_crop=has_mask_focus_crop,
                )
                if multi_guidance:
                    guard += multi_guidance + " "
            else:
                if has_mask_reference:
                    guard += (
                        f"Image {layout['mask_reference_index']} is the same source image with the editable region highlighted in red as a localization guide. "
                        f"Use image {layout['mask_reference_index']} only to identify the target area in image 1; do not copy stylistic artifacts from the guide overlay, "
                        f"and do not edit or return image {layout['mask_reference_index']} as the final picture. "
                    )
                if has_mask_focus_crop:
                    guard += (
                        f"Image {layout['mask_focus_index']} is a zoomed crop around that same highlighted region. "
                        f"Use image {layout['mask_focus_index']} to understand the local target and nearby context, then apply the requested change back to image 1 at the intended location. "
                        f"Do not edit or return image {layout['mask_focus_index']} itself as the final picture. "
                    )
            scope_guidance = ImageGenerator._build_mask_scope_guidance(mask_scope_mode)
            if scope_guidance:
                guard += scope_guidance + " "
            guard += (
                "The painted selection indicates where the user is pointing, but it is not automatically a hard pixel boundary. "
                "Keep unrelated areas unchanged. For precise local fixes, stay as tight as possible. "
                "For nearby range fixes, you may extend modestly beyond the painted pixels when needed for a coherent local result. "
                "For object-level fixes, you may complete the whole nearby object or logically connected part even if only part of it is painted. "
                "Preserve object count, geometry, placement, perspective, and lighting unless the user explicitly asks to change them. "
                "If the user asks for only a color, material, texture, or text change, modify only that attribute and keep geometry and fine details unchanged. "
            )
            if mask_guidance:
                guard += mask_guidance + " "
        return guard + "User edit request: " + str(prompt or "")

    def describe_image(self, image_b64, on_done=None, on_error=None):
        # Compress image to reduce request size
        comp_b64, mime = self.compress_b64_for_edit(image_b64, max_dim=1536, max_b64_kb=400)
        body = {
            "model": self._resolve_responses_model(),
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_image", "image_url": f"data:{mime};base64,{comp_b64}"},
                        {"type": "input_text", "text": "请详细描述这张图片的内容，包括主体、风格、颜色、构图等。用中文回答。"},
                    ],
                }
            ],
        }

        def _worker():
            self._cancel_event.clear()
            try:
                with httpx.Client(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
                    self._set_active_request(client=client)
                    payload_meta = debug_log.save_request_payload(
                        route=f"{self.api_base}/responses",
                        request_kind="json",
                        request_body=body,
                        note="describe_image",
                        attempt=1,
                    )
                    debug_log.log("request_payload_saved", payload_meta)
                    resp = client.post(
                        f"{self.api_base}/responses",
                        headers=self._headers(),
                        json=body,
                    )
                    self._set_active_request(client=client, response=resp)
                    if self._cancel_event.is_set():
                        return
                    if resp.status_code != 200:
                        if on_error:
                            on_error(f"HTTP {resp.status_code}: {resp.text[:300]}")
                        return
                    data = resp.json()
                    text_parts = []
                    for item in data.get("output", []):
                        if item.get("type") == "message":
                            for c in item.get("content", []):
                                if c.get("type") == "output_text":
                                    text_parts.append(c.get("text", ""))
                    result = "\n".join(text_parts)
                    if on_done:
                        on_done(result if result else "无法描述该图片")
            except Exception as e:
                if self._cancel_event.is_set():
                    return
                if on_error:
                    on_error(str(e))
            finally:
                self._clear_active_request()

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return t

    @staticmethod
    def _extract_b64_from_block(block):
        try:
            data_lines = []
            for line in block.splitlines():
                if line.startswith("data:"):
                    data_lines.append(line[5:].strip())
            payload = "\n".join(data_lines).strip()
            for candidate in (payload, block.strip()):
                if not candidate:
                    continue
                try:
                    parsed = json.loads(candidate)
                    found = ImageGenerator._find_b64_in_payload(parsed)
                    if found:
                        return found
                except Exception:
                    pass
            markers = ['"partial_image_b64":"', '"image_b64":"']
            for marker in markers:
                idx = block.find(marker)
                if idx >= 0:
                    start = idx + len(marker)
                    end = block.find('"', start)
                    if end > start:
                        return block[start:end]
            return None
        except Exception:
            return None

    @staticmethod
    def _find_b64_in_payload(payload):
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key in ("partial_image_b64", "image_b64") and isinstance(value, str):
                    return value
                found = ImageGenerator._find_b64_in_payload(value)
                if found:
                    return found
        elif isinstance(payload, list):
            for item in payload:
                found = ImageGenerator._find_b64_in_payload(item)
                if found:
                    return found
        return None

    @staticmethod
    def _extract_metadata_from_block(block):
        """Extract revised_prompt and response_id from an SSE event block"""
        try:
            data_lines = []
            for line in block.splitlines():
                if line.startswith("data:"):
                    data_lines.append(line[5:].strip())
            payload = "\n".join(data_lines).strip()
            if not payload:
                return None, None
            parsed = json.loads(payload)
            revised = ImageGenerator._find_key_in_payload(parsed, "revised_prompt")
            resp_obj = parsed.get("response") if isinstance(parsed, dict) else None
            resp_id = resp_obj.get("id") if isinstance(resp_obj, dict) else None
            if not resp_id:
                resp_id = ImageGenerator._find_key_in_payload(parsed, "id")
            return revised, resp_id
        except Exception:
            return None, None

    @staticmethod
    def _find_key_in_payload(payload, key):
        """Recursively find a specific key value in a JSON payload"""
        if isinstance(payload, dict):
            if key in payload and isinstance(payload[key], str):
                return payload[key]
            for v in payload.values():
                found = ImageGenerator._find_key_in_payload(v, key)
                if found:
                    return found
        elif isinstance(payload, list):
            for item in payload:
                found = ImageGenerator._find_key_in_payload(item, key)
                if found:
                    return found
        return None

    @staticmethod
    def b64_to_image(b64_str):
        data = base64.b64decode(b64_str)
        return Image.open(io.BytesIO(data))

    @staticmethod
    def image_to_b64(img, fmt="PNG"):
        if fmt.upper() == "JPEG" and img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format=fmt)
        return base64.b64encode(buf.getvalue()).decode("ascii")

    @staticmethod
    def compress_b64_for_edit(b64_str, max_dim=1536, jpeg_quality=85, max_b64_kb=300):
        """Compress an image for editing: resize + choose best format to reduce request size.

        Strategy:
        1. Resize if larger than max_dim
        2. Try both JPEG and PNG, pick the smaller one
        3. If still over max_b64_kb, reduce JPEG quality progressively

        Returns:
            Tuple of (compressed_b64, mime_type)
        """
        try:
            img = ImageGenerator.b64_to_image(b64_str)
        except Exception:
            return b64_str, "image/png"

        w, h = img.size

        # Step 1: Resize if larger than max_dim
        if max(w, h) > max_dim:
            scale = max_dim / max(w, h)
            new_w = max(1, int(w * scale))
            new_h = max(1, int(h * scale))
            img = img.resize((new_w, new_h), Image.LANCZOS)

        # Step 2: Prepare RGB version for JPEG
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img_rgb = bg
        elif img.mode != "RGB":
            img_rgb = img.convert("RGB")
        else:
            img_rgb = img

        # Step 3: Generate both JPEG and PNG, pick smaller
        best_b64 = None
        best_mime = None
        best_size = float('inf')

        # PNG (resized)
        buf_png = io.BytesIO()
        img.save(buf_png, format="PNG", optimize=True)
        png_b64 = base64.b64encode(buf_png.getvalue()).decode("ascii")
        if len(png_b64) < best_size:
            best_b64 = png_b64
            best_mime = "image/png"
            best_size = len(png_b64)

        # JPEG at various quality levels
        for q in (jpeg_quality, 75, 60, 50):
            buf_jpg = io.BytesIO()
            img_rgb.save(buf_jpg, format="JPEG", quality=q, optimize=True)
            jpg_b64 = base64.b64encode(buf_jpg.getvalue()).decode("ascii")
            if len(jpg_b64) < best_size:
                best_b64 = jpg_b64
                best_mime = "image/jpeg"
                best_size = len(jpg_b64)
            # If already under size limit with this quality, no need to go lower
            if best_size / 1024 <= max_b64_kb:
                break

        return best_b64, best_mime

    def _degrade_input_images(self, body, max_dim=3840, max_b64_kb=15360):
        """Re-compress input images in the request body more aggressively for retry attempts.
        
        This modifies the body in-place, replacing large base64 images with
        smaller versions to reduce server load on retries.
        """
        import re
        input_data = body.get("input")
        if not input_data:
            return
        # Handle list of messages (edit format)
        if isinstance(input_data, list):
            for msg in input_data:
                content = msg.get("content") if isinstance(msg, dict) else None
                if not content:
                    continue
                if isinstance(content, list):
                    for item in content:
                        if not isinstance(item, dict):
                            continue
                        url = item.get("image_url", "")
                        if isinstance(url, str) and url.startswith("data:"):
                            # Extract b64 from data URI
                            match = re.match(r'data:([^;]+);base64,(.+)', url, re.DOTALL)
                            if match:
                                mime_old = match.group(1)
                                b64_old = match.group(2)
                                if len(b64_old) / 1024 > max_b64_kb:
                                    new_b64, new_mime = self.compress_b64_for_edit(
                                        b64_old, max_dim=max_dim, jpeg_quality=60, max_b64_kb=max_b64_kb
                                    )
                                    item["image_url"] = f"data:{new_mime};base64,{new_b64}"
                                    print(f"[_stream_worker] Degraded input image: {len(b64_old)/1024:.0f}KB -> {len(new_b64)/1024:.0f}KB", file=sys.stderr)


# ─── 历史管理 ───────────────────────────────────────────────

class HistoryManager:
    """持久化历史记录管理"""

    def __init__(self, db_path, img_dir):
        self.db_path = Path(db_path)
        self.img_dir = Path(img_dir)
        self.img_dir.mkdir(parents=True, exist_ok=True)
        self.records = []
        self.load_error = None
        self._load()

    def _load(self):
        if self.db_path.exists():
            try:
                self.records = json.loads(self.db_path.read_text("utf-8"))
                if not isinstance(self.records, list):
                    self.records = []
            except Exception as e:
                self.load_error = str(e)
                self.records = []

    def _save(self):
        try:
            _write_text_atomic(
                self.db_path,
                json.dumps(self.records, ensure_ascii=False, indent=2),
                "utf-8",
            )
        except Exception:
            pass

    def _delete_record_file(self, rec):
        try:
            p = self.img_dir / rec["filename"]
            if p.exists():
                p.unlink()
                return True
        except Exception:
            pass
        return False

    def add(self, prompt, filename, timestamp=None):
        if timestamp is None:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.records.append({
            "prompt": prompt,
            "filename": filename,
            "timestamp": timestamp,
        })
        if len(self.records) > 100:
            removed = self.records[:-100]
            self.records = self.records[-100:]
            for rec in removed:
                self._delete_record_file(rec)
        self._save()

    def delete(self, idx, delete_file=False):
        if 0 <= idx < len(self.records):
            rec = self.records[idx]
            self.records.pop(idx)
            self._save()
            return self._delete_record_file(rec) if delete_file else False
        return False

    def cleanup_missing(self):
        valid = []
        for rec in self.records:
            p = self.img_dir / rec["filename"]
            if p.exists():
                valid.append(rec)
            else:
                try:
                    p.unlink()
                except Exception:
                    pass
        removed = len(self.records) - len(valid)
        if removed > 0:
            self.records = valid
            self._save()
        return removed

    def clear_all(self, delete_files=False):
        """Delete all history records, optionally deleting their image files."""
        deleted_files = 0
        if delete_files:
            for rec in self.records:
                if self._delete_record_file(rec):
                    deleted_files += 1
        count = len(self.records)
        self.records = []
        self._save()
        return count, deleted_files

    def get_image_path(self, idx):
        if 0 <= idx < len(self.records):
            return self.img_dir / self.records[idx]["filename"]
        return None

    def get_record(self, idx):
        if 0 <= idx < len(self.records):
            return self.records[idx]
        return None

    def clear(self, delete_files=False):
        return self.clear_all(delete_files=delete_files)


# ─── 缩略图缓存 ───────────────────────────────────────────────

class ThumbCache:
    """磁盘缩略图缓存"""

    def __init__(self, cache_dir, thumb_size=80):
        self.cache_dir = Path(cache_dir)
        self.thumb_size = thumb_size
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path_key(self, original_path):
        path = Path(original_path).resolve()
        return hashlib.md5(str(path).encode("utf-8", "ignore")).hexdigest()

    def get_thumb_path(self, original_path):
        path = Path(original_path)
        stat = path.stat()
        return self.cache_dir / f"{self._path_key(path)}_{stat.st_mtime_ns}_{stat.st_size}.png"

    def _purge_old_versions(self, original_path, keep_path=None):
        prefix = f"{self._path_key(original_path)}_"
        for old_file in self.cache_dir.glob(f"{prefix}*.png"):
            if keep_path is not None and old_file == keep_path:
                continue
            try:
                old_file.unlink()
            except Exception:
                pass

    def remove(self, original_path):
        self._purge_old_versions(original_path)

    def cleanup(self, original_paths):
        valid_prefixes = {
            self._path_key(path)
            for path in original_paths
            if Path(path).exists()
        }
        for cache_file in self.cache_dir.glob("*.png"):
            prefix = cache_file.stem.split("_", 1)[0]
            if prefix not in valid_prefixes:
                try:
                    cache_file.unlink()
                except Exception:
                    pass

    def get_thumbnail(self, original_path):
        original_path = Path(original_path)
        thumb_path = self.get_thumb_path(original_path)
        self._purge_old_versions(original_path, keep_path=thumb_path)
        if thumb_path.exists():
            try:
                with Image.open(thumb_path) as cached:
                    return cached.copy()
            except Exception:
                pass
        try:
            with Image.open(original_path) as img:
                thumb = img.convert("RGBA")
                thumb.thumbnail((self.thumb_size, self.thumb_size), Image.LANCZOS)
                canvas = Image.new("RGBA", (self.thumb_size, self.thumb_size), (49, 50, 68, 255))
                offset = (
                    (self.thumb_size - thumb.width) // 2,
                    (self.thumb_size - thumb.height) // 2,
                )
                canvas.paste(thumb, offset, thumb)
                canvas.save(thumb_path, format="PNG")
                return canvas
        except Exception:
            return None


# ─── 错误日志 ───────────────────────────────────────────────

ERROR_LOG_PATH = APP_DIR / "error_log.json"
ERROR_LOG_MAX = 50  # 最多保留条数


class ErrorLog:
    """有容量限制的错误日志，保存关键错误信息供 AI 分析"""

    def __init__(self, path, max_entries=ERROR_LOG_MAX):
        self.path = Path(path)
        self.max_entries = max_entries
        self.records = []
        self.load_error = None
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                self.records = json.loads(self.path.read_text("utf-8"))
                if not isinstance(self.records, list):
                    self.records = []
            except Exception as e:
                self.load_error = str(e)
                self.records = []

    def _save(self):
        try:
            _write_text_atomic(
                self.path,
                json.dumps(self.records, ensure_ascii=False, indent=2),
                "utf-8",
            )
        except Exception:
            pass

    def add(self, error_type, message, context=None):
        """记录一条错误，自动截断保持容量"""
        entry = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "type": error_type,
            "message": message[:500],
        }
        if context:
            # 只保留关键上下文，避免过大
            ctx = {}
            for k, v in context.items():
                s = str(v)
                ctx[k] = s[:200]
            entry["context"] = ctx
        self.records.append(entry)
        # 截断：只保留最新的 max_entries 条
        if len(self.records) > self.max_entries:
            self.records = self.records[-self.max_entries:]
        self._save()

    def get_recent(self, count=20):
        return self.records[-count:]

    def clear(self):
        self.records = []
        self._save()

    def count(self):
        return len(self.records)






# ─── 调试日志 ───────────────────────────────────────────────

DEBUG_LOG_PATH = APP_DIR / "debug_log.json"
DEBUG_LOG_MAX = 100  # 最多保留条数
DEBUG_PAYLOAD_DIR = APP_DIR / "debug_payloads"


class DebugLogger:
    """详细的调试日志，记录每次API请求/响应的完整信息，用于诊断502等错误"""

    def __init__(self, path=DEBUG_LOG_PATH, max_entries=DEBUG_LOG_MAX, payload_dir=DEBUG_PAYLOAD_DIR):
        self.path = Path(path)
        self.payload_dir = Path(payload_dir)
        self.max_entries = max_entries
        self.records = []
        self.load_error = None
        self._load()
        self._lock = threading.Lock()

    def _load(self):
        if self.path.exists():
            try:
                self.records = json.loads(self.path.read_text("utf-8"))
                if not isinstance(self.records, list):
                    self.records = []
            except Exception as e:
                self.load_error = str(e)
                self.records = []

    def _save(self):
        try:
            _write_text_atomic(
                self.path,
                json.dumps(self.records, ensure_ascii=False, indent=2),
                "utf-8",
            )
        except Exception:
            pass

    @staticmethod
    def _safe_name(name):
        cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(name or ""))
        return cleaned.strip("_") or "file"

    @staticmethod
    def _split_data_url(data_url):
        value = str(data_url or "")
        if not value.startswith("data:") or ";base64," not in value:
            return None, None
        header, b64 = value.split(",", 1)
        mime = header[5:].split(";", 1)[0].strip().lower() or "image/png"
        return mime, b64

    @staticmethod
    def _collect_json_input_images(payload, path_prefix=""):
        found = []
        if isinstance(payload, dict):
            for key, value in payload.items():
                child_path = f"{path_prefix}.{key}" if path_prefix else key
                if key == "image_url" and isinstance(value, str):
                    found.append({"field_path": child_path, "image_url": value})
                else:
                    found.extend(DebugLogger._collect_json_input_images(value, child_path))
        elif isinstance(payload, list):
            for idx, item in enumerate(payload):
                child_path = f"{path_prefix}[{idx}]"
                found.extend(DebugLogger._collect_json_input_images(item, child_path))
        return found

    def _write_json_payload_snapshot(self, request_body, dump_dir):
        request_path = dump_dir / "request.json"
        try:
            request_text = json.dumps(request_body, ensure_ascii=False, indent=2)
        except Exception:
            request_text = str(request_body)
        request_path.write_text(request_text, "utf-8")

        images = []
        for idx, item in enumerate(self._collect_json_input_images(request_body), start=1):
            image_url = item.get("image_url")
            mime, b64 = self._split_data_url(image_url)
            image_info = {
                "index": idx,
                "label": f"Image {idx}",
                "field_path": item.get("field_path", ""),
            }
            if mime and b64:
                try:
                    raw = base64.b64decode(b64)
                    ext = ImageGenerator._mime_extension(mime)
                    file_path = dump_dir / f"input_image_{idx:02d}.{ext}"
                    file_path.write_bytes(raw)
                    image_info.update({
                        "file_path": str(file_path),
                        "mime": mime,
                        "size_bytes": len(raw),
                    })
                except Exception as e:
                    image_info["decode_error"] = str(e)
            else:
                image_info["external_url"] = str(image_url or "")[:500]
            images.append(image_info)
        return request_path, images

    def _write_multipart_payload_snapshot(self, form_data, files, dump_dir):
        payload = {
            "form_data": dict(form_data or {}),
            "files": [],
        }
        images = []
        for idx, item in enumerate(files or [], start=1):
            if not isinstance(item, tuple) or len(item) != 2:
                continue
            field_name, spec = item
            if not isinstance(spec, tuple) or len(spec) < 3:
                continue
            filename = str(spec[0] or f"{field_name}_{idx}")
            content = spec[1]
            mime = str(spec[2] or "application/octet-stream")
            if not isinstance(content, (bytes, bytearray)):
                try:
                    content = bytes(content)
                except Exception:
                    content = str(content).encode("utf-8", errors="ignore")
            suffix = Path(filename).suffix
            if not suffix:
                suffix = "." + ImageGenerator._mime_extension(mime, fallback="bin")
            saved_name = f"{idx:02d}_{self._safe_name(field_name)}{suffix}"
            file_path = dump_dir / saved_name
            file_path.write_bytes(content)
            file_info = {
                "index": idx,
                "field_name": str(field_name),
                "filename": filename,
                "mime": mime,
                "size_bytes": len(content),
                "saved_path": str(file_path),
            }
            payload["files"].append(file_info)
            images.append({
                "index": idx,
                "label": str(field_name),
                "field_name": str(field_name),
                "file_path": str(file_path),
                "mime": mime,
                "size_bytes": len(content),
            })

        request_path = dump_dir / "multipart_request.json"
        request_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
        return request_path, images

    def save_request_payload(self, route, request_kind="json", request_body=None,
                             form_data=None, files=None, note="", attempt=None):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        dump_dir = self.payload_dir / f"{stamp}_{random.randint(1000, 9999)}"
        try:
            dump_dir.mkdir(parents=True, exist_ok=True)
            if request_kind == "multipart":
                request_path, images = self._write_multipart_payload_snapshot(form_data, files, dump_dir)
            else:
                request_path, images = self._write_json_payload_snapshot(request_body, dump_dir)

            manifest = {
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "route": str(route or ""),
                "request_kind": str(request_kind or "json"),
                "note": str(note or ""),
                "attempt": "" if attempt is None else str(attempt),
                "request_path": str(request_path),
                "image_count": len(images),
                "images": images,
            }
            manifest_path = dump_dir / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
            return {
                "route": str(route or ""),
                "request_kind": str(request_kind or "json"),
                "attempt": "" if attempt is None else str(attempt),
                "payload_manifest_path": str(manifest_path),
                "payload_dir": str(dump_dir),
                "payload_request_path": str(request_path),
                "payload_image_count": str(len(images)),
                "note": str(note or ""),
            }
        except Exception as e:
            return {
                "route": str(route or ""),
                "request_kind": str(request_kind or "json"),
                "attempt": "" if attempt is None else str(attempt),
                "payload_save_error": str(e),
            }

    def _prune_payload_dirs(self):
        try:
            if not self.payload_dir.exists():
                return
            keep_dirs = set()
            for rec in self.records:
                detail = rec.get("detail")
                if not isinstance(detail, dict):
                    continue
                manifest_path = detail.get("payload_manifest_path")
                if manifest_path:
                    try:
                        keep_dirs.add(Path(manifest_path).resolve().parent)
                    except Exception:
                        pass
            for child in self.payload_dir.iterdir():
                try:
                    if child.is_dir() and child.resolve() not in keep_dirs:
                        shutil.rmtree(child, ignore_errors=True)
                except Exception:
                    continue
        except Exception:
            pass

    def log(self, event_type, detail=None):
        """记录一条调试日志"""
        entry = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "event": event_type,
        }
        if detail:
            # Truncate large values to keep log manageable
            if isinstance(detail, dict):
                sanitized = {}
                for k, v in detail.items():
                    s = str(v)
                    # Truncate base64 data in log
                    if len(s) > 500:
                        s = s[:200] + f"...[truncated, total {len(s)} chars]"
                    sanitized[k] = s
                entry["detail"] = sanitized
            else:
                s = str(detail)
                if len(s) > 500:
                    s = s[:200] + f"...[truncated, total {len(s)} chars]"
                entry["detail"] = s
        with self._lock:
            self.records.append(entry)
            if len(self.records) > self.max_entries:
                self.records = self.records[-self.max_entries:]
            self._save()
            self._prune_payload_dirs()

    def get_recent(self, count=20):
        return self.records[-count:]

    def clear(self):
        self.records = []
        self._save()
        try:
            if self.payload_dir.exists():
                shutil.rmtree(self.payload_dir, ignore_errors=True)
        except Exception:
            pass

    def count(self):
        return len(self.records)


# Global debug logger instance
debug_log = DebugLogger()

class ToolbarButton(tk.Canvas):
    """工具栏按钮：深色底 + 悬停胶囊高亮"""

    _STATE_NORMAL = "normal"
    _STATE_HOVER = "hover"
    _STATE_ACTIVE = "active"

    def __init__(self, parent, text="", icon_char="", command=None,
                 fg=None, bg=None, width=None, **kw):
        self._fg = fg or C["text_dim"]
        self._bg = bg or C["toolbar_bg"]
        self._command = command
        self._icon = icon_char
        self._text = text
        self._pressed = False
        self._enabled = True
        self._state = self._STATE_NORMAL

        btn_w = width or max(len(icon_char) * 2 + len(text) * 7 + 20, 60)
        btn_h = 28
        super().__init__(parent, width=btn_w, height=btn_h,
                         bg=self._bg, highlightthickness=0, cursor="hand2", **kw)

        self._draw_state(self._STATE_NORMAL)

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _draw_rounded_rect(self, x1, y1, x2, y2, r, fill, outline=None):
        outline = outline or fill
        self.create_arc(x1, y1, x1 + 2 * r, y1 + 2 * r, start=90, extent=90,
                        fill=fill, outline=outline)
        self.create_arc(x2 - 2 * r, y1, x2, y1 + 2 * r, start=0, extent=90,
                        fill=fill, outline=outline)
        self.create_arc(x1, y2 - 2 * r, x1 + 2 * r, y2, start=180, extent=90,
                        fill=fill, outline=outline)
        self.create_arc(x2 - 2 * r, y2 - 2 * r, x2, y2, start=270, extent=90,
                        fill=fill, outline=outline)
        self.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline=outline)
        self.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline=outline)

    def _draw_state(self, state):
        self.delete("all")
        w = int(self.cget("width"))
        h = int(self.cget("height"))
        radius = 5

        if not self._enabled:
            fg = C["text_muted"]
            self.configure(bg=self._bg, cursor="arrow")
            self._draw_rounded_rect(1, 2, w - 1, h - 2, radius, C["surface"], C["surface"])
        elif getattr(self, "_toggled", False):
            # Toggled-on: amber/warm highlight to distinguish from click-active
            fg = "#ffffff"
            self.configure(bg=self._bg, cursor="hand2")
            self._draw_rounded_rect(1, 2, w - 1, h - 2, radius, C["peach"], C["peach"])
            self.create_line(radius + 2, 3, w - radius - 2, 3, fill="#ffe4b5", width=1)
        elif state == self._STATE_ACTIVE:
            fg = "#ffffff"
            self.configure(bg=self._bg, cursor="hand2")
            self._draw_rounded_rect(1, 2, w - 1, h - 2, radius, C["btn_active"], C["btn_active"])
            self.create_line(radius + 2, 3, w - radius - 2, 3, fill="#a6c4ff", width=1)
        elif state == self._STATE_HOVER:
            fg = C["text"]
            self.configure(bg=self._bg, cursor="hand2")
            self._draw_rounded_rect(1, 2, w - 1, h - 2, radius, C["btn_bg"], C["border"])
            self.create_line(radius + 2, 3, w - radius - 2, 3, fill=C["accent_glow"], width=1)
        else:
            fg = self._fg
            self.configure(bg=self._bg, cursor="hand2")

        self._draw_content(fg)

    def _draw_normal(self):
        self._state = self._STATE_NORMAL
        self._draw_state(self._STATE_NORMAL)

    def set_toggled(self, toggled: bool):
        """Set persistent toggle state (highlighted until untoggled)."""
        self._toggled = bool(toggled)
        self._draw_normal()

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        self._pressed = False
        self._draw_normal()

    def set_text(self, text, icon_char=None):
        """Update button text (and optionally icon), then redraw."""
        self._text = text
        if icon_char is not None:
            self._icon = icon_char
        btn_w = max(len(self._icon) * 2 + len(self._text) * 7 + 20, 60)
        self.configure(width=btn_w)
        self._draw_normal()

    def _draw_content(self, fg):
        x_offset = 8
        if self._icon:
            self.create_text(x_offset, 14, text=self._icon, fill=fg,
                             font=("Segoe UI Emoji", 11), anchor="w")
            x_offset += 20
        if self._text:
            self.create_text(x_offset, 14, text=self._text, fill=fg,
                             font=("Microsoft YaHei UI", 9), anchor="w")

    def _on_enter(self, e):
        if not self._enabled:
            return
        if not self._pressed:
            self._state = self._STATE_HOVER
            self._draw_state(self._STATE_HOVER)

    def _on_leave(self, e):
        self._pressed = False
        self._draw_normal()

    def _on_press(self, e):
        if not self._enabled:
            return
        self._pressed = True
        self._state = self._STATE_ACTIVE
        self._draw_state(self._STATE_ACTIVE)

    def _on_release(self, e):
        if not self._enabled:
            return
        self._pressed = False
        self._on_enter(e)
        if self._command:
            self._command()


class ToolbarSeparator(tk.Frame):
    """工具栏分隔线"""

    def __init__(self, parent, **kw):
        super().__init__(parent, width=1, bg=C["border"], **kw)
        self.pack(side="left", fill="y", padx=5, pady=6)


class ActionCanvasButton(tk.Canvas):
    """主操作按钮：模拟参考图里的蓝紫主按钮。"""

    _STATE_NORMAL = "normal"
    _STATE_HOVER = "hover"
    _STATE_ACTIVE = "active"

    def __init__(self, parent, text="", command=None, color=None, height=38, **kw):
        self._text = text
        self._command = command
        self._color = color or C["accent"]
        self._enabled = True
        self._pressed = False
        self._state = self._STATE_NORMAL

        super().__init__(parent, height=height, bg=C["surface3"],
                         highlightthickness=0, cursor="hand2", **kw)
        self.bind("<Configure>", self._on_resize)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    @staticmethod
    def _lighten(hex_color, amount):
        r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
        r = min(255, int(r + (255 - r) * amount))
        g = min(255, int(g + (255 - g) * amount))
        b = min(255, int(b + (255 - b) * amount))
        return f"#{r:02x}{g:02x}{b:02x}"

    @staticmethod
    def _darken(hex_color, amount):
        r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
        r = max(0, int(r * (1 - amount)))
        g = max(0, int(g * (1 - amount)))
        b = max(0, int(b * (1 - amount)))
        return f"#{r:02x}{g:02x}{b:02x}"

    def _draw_rounded_rect(self, x1, y1, x2, y2, r, fill, outline=None):
        outline = outline or fill
        self.create_arc(x1, y1, x1 + 2 * r, y1 + 2 * r, start=90, extent=90,
                        fill=fill, outline=outline)
        self.create_arc(x2 - 2 * r, y1, x2, y1 + 2 * r, start=0, extent=90,
                        fill=fill, outline=outline)
        self.create_arc(x1, y2 - 2 * r, x1 + 2 * r, y2, start=180, extent=90,
                        fill=fill, outline=outline)
        self.create_arc(x2 - 2 * r, y2 - 2 * r, x2, y2, start=270, extent=90,
                        fill=fill, outline=outline)
        self.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline=outline)
        self.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline=outline)

    def config(self, **kw):
        if "text" in kw:
            self._text = kw.pop("text")
        if "bg" in kw:
            self._color = kw.pop("bg")
        if "state" in kw:
            self._enabled = kw.pop("state") != "disabled"
            self._pressed = False
        kw.pop("activebackground", None)
        kw.pop("activeforeground", None)
        kw.pop("fg", None)
        if kw:
            super().config(**kw)
        self._redraw()

    def _redraw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 4 or h < 4:
            return

        radius = 7
        if not self._enabled:
            fill = C["surface2"]
            edge = C["surface2"]
            text_color = C["text_muted"]
            self.configure(cursor="arrow")
        else:
            if self._state == self._STATE_ACTIVE:
                fill = self._darken(self._color, 0.10)
            elif self._state == self._STATE_HOVER:
                fill = self._lighten(self._color, 0.08)
            else:
                fill = self._color
            edge = self._lighten(fill, 0.10)
            text_color = "#ffffff"
            self.configure(cursor="hand2")

        self._draw_rounded_rect(1, 1, w - 1, h - 1, radius, fill, edge)
        self.create_line(radius + 2, 3, w - radius - 2, 3,
                         fill=self._lighten(fill, 0.28), width=1)
        self.create_line(radius + 2, h - 2, w - radius - 2, h - 2,
                         fill=self._darken(fill, 0.16), width=1)
        self.create_text(w // 2, h // 2, text=self._text, fill=text_color,
                         font=("Microsoft YaHei UI", 11, "bold"))

    def _on_resize(self, event):
        self._redraw()

    def _on_enter(self, event):
        if not self._enabled:
            return
        self._state = self._STATE_HOVER
        self._redraw()

    def _on_leave(self, event):
        self._pressed = False
        self._state = self._STATE_NORMAL
        self._redraw()

    def _on_press(self, event):
        if not self._enabled:
            return
        self._pressed = True
        self._state = self._STATE_ACTIVE
        self._redraw()

    def _on_release(self, event):
        if not self._enabled:
            return
        self._pressed = False
        self._state = self._STATE_HOVER
        self._redraw()
        if self._command:
            self._command()


# ─── 主界面 ───────────────────────────────────────────────

class App(BaseTk):
    def __init__(self):
        super().__init__()
        self.title("GPT 图片生成器 v4")

        # Global exception handler for Tkinter callbacks
        def _safe_report(exc_type, exc_value, exc_tb):
            import traceback as tb
            full_tb = tb.format_exception(exc_type, exc_value, exc_tb)
            error_text = ''.join(full_tb)
            print(f"[Tkinter Exception]\n{error_text}", file=sys.stderr)
            try:
                messagebox.showerror("运行错误", f"{exc_type.__name__}: {exc_value}\n\n详细信息:\n{error_text[:800]}")
            except Exception:
                pass
        BaseTk.report_callback_exception = staticmethod(_safe_report)
        self.configure(bg=C["bg"])
        self._apply_startup_geometry(1340, 900, 1060, 720)

        self.current_image = None
        self.current_b64 = None
        self._preview_override_image = None
        self._preview_override_label = ""
        self._hist_click_after_id = None
        self._last_prompt = ""
        self.is_generating = False
        self.partial_count = 0
        self.start_time = 0
        self._main_photo = None
        self._thumb_refs = []
        self._undo_stack = []
        self._redo_stack = []
        self._mask_undo_stack = []
        self._mask_redo_stack = []
        self._compare_image = None
        self._compare_b64 = None
        self._compare_sources = []
        self._compare_source_label = ""
        self._active_generator = None
        self._active_generators = []  # track ALL generators for proper cancellation
        self._job_token = 0
        self._current_job_label = "正在处理"
        self._resize_after_id = None
        self._dnd_ready = False
        self._batch_results = []
        self._batch_done_count = 0
        self._batch_total = 0
        self._batch_token = 0
        self._batch_retry_counts = {}
        self._active_result_target_size = None
        self._active_processing_size = None
        self._batch_result_target_size = None
        self._batch_request_size = None
        self._batch_api_quality = None
        self._pending_followups = []
        self._progress_timer_id = None
        self._ref_images = []       # list of {"b64": str, "image": PIL.Image}
        self._ref_thumb_refs = []   # prevent GC of PhotoImage
        self._ref_selected = set()  # indices of selected ref images for multi-edit
        self._hist_selected = set() # indices of selected history items
        self._hist_selection_order = []  # Ctrl+click selection order for batch actions
        self._last_response_id = None  # for iterative editing chain
        self._bg_replace_active = False  # auto-replace-bg after each generate/edit
        self._bg_replace_desc = "阳光海滩，海浪拍岸"  # background description for auto-replace
        self._style_transfer_active = False  # auto-style-transfer after each generate/edit
        self._style_transfer_name = "油画"  # style preset name for auto-transfer
        self._last_revised_prompt = None  # model's actual understood prompt
        self._last_edit_summary = None  # summary of last edit: {"mode": str, "input_count": int, "input_desc": str}
        self._primary_is_result = False  # whether the current primary image is an AI-generated result
        self._mask_b64 = None  # mask image for inpainting (transparent=edit, opaque=preserve)
        self._mask_image = None  # in-memory painted mask (transparent=keep blank, alpha>0 means user painted)
        self._mask_mode = False  # whether mask drawing mode is active
        self._mask_brush_size = 20  # brush size for mask drawing
        self._mask_painting = False
        self._mask_last_canvas_pos = None
        self._canvas_zoom = 1.0  # current zoom level for main canvas
        self._canvas_pan_x = 0  # pan offset x
        self._canvas_pan_y = 0  # pan offset y
        self._canvas_panning = False  # whether left-button dragging is active
        self._rclick_panning = False  # whether right-button dragging is active
        self._pan_start_x = 0
        self._pan_start_y = 0
        self._drag_moved = False  # whether mouse moved enough to count as drag
        self._drag_threshold = 4  # pixels before counting as drag

        self.history_mgr = HistoryManager(HISTORY_DB, HISTORY_DIR)
        self.thumb_cache = ThumbCache(THUMB_DIR)
        self.error_log = ErrorLog(ERROR_LOG_PATH)
        self._removed_history_count = self.history_mgr.cleanup_missing()
        self.thumb_cache.cleanup(
            [self.history_mgr.img_dir / rec["filename"] for rec in self.history_mgr.records]
        )

        self._setup_ttk_styles()
        self._build_ui()
        self._load_config()
        self._setup_config_autosave()
        self._show_startup_recovery_notice()
        self._bind_shortcuts()
        self._setup_dnd()
        self._refresh_history()
        if self._removed_history_count:
            self._set_status(f"已清理 {self._removed_history_count} 条失效历史记录")

        # Save config on exit
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Verify API connectivity on startup
        self.after(500, self._check_api_connectivity)

    def _get_work_area_bounds(self):
        bounds = _get_windows_work_area()
        if bounds is not None:
            return bounds
        return (0, 0, self.winfo_screenwidth(), self.winfo_screenheight())

    def _fit_rect_to_work_area(self, width, height, *, anchor=None, margin=12):
        """Fit and clamp a window rectangle into the visible desktop work area."""
        left, top, right, bottom = self._get_work_area_bounds()
        work_w = max(320, int(right - left))
        work_h = max(240, int(bottom - top))
        fit_w = min(int(width), max(320, work_w - margin * 2))
        fit_h = min(int(height), max(240, work_h - margin * 2))

        if anchor is not None:
            anchor.update_idletasks()
            px = anchor.winfo_rootx()
            py = anchor.winfo_rooty()
            pw = max(1, anchor.winfo_width())
            ph = max(1, anchor.winfo_height())
            dx = px + max(0, (pw - fit_w) // 2)
            dy = py + max(0, (ph - fit_h) // 2)
        else:
            dx = left + max(margin, (work_w - fit_w) // 2)
            dy = top + max(margin, (work_h - fit_h) // 2)

        max_x = max(left + margin, right - fit_w - margin)
        max_y = max(top + margin, bottom - fit_h - margin)
        dx = max(left + margin, min(dx, max_x))
        dy = max(top + margin, min(dy, max_y))
        return fit_w, fit_h, dx, dy

    def _apply_startup_geometry(self, desired_w, desired_h, min_w, min_h):
        fit_w, fit_h, dx, dy = self._fit_rect_to_work_area(
            desired_w, desired_h, margin=12
        )
        self.geometry(f"{fit_w}x{fit_h}+{dx}+{dy}")
        self.minsize(min(int(min_w), fit_w), min(int(min_h), fit_h))

    # ── UI 构建 ──────────────────────────────────────

    def _setup_ttk_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure(
            "TCombobox",
            fieldbackground=C["canvas_bg"],
            background=C["surface2"],
            foreground=C["text"],
            bordercolor=C["border"],
            arrowcolor=C["text_dim"],
            darkcolor=C["surface2"],
            lightcolor=C["surface2"],
            insertcolor=C["text"],
            relief="flat",
            padding=4,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", C["canvas_bg"])],
            background=[("readonly", C["surface2"])],
            foreground=[("readonly", C["text"])],
            arrowcolor=[("readonly", C["text_dim"])],
        )

        style.configure(
            "TProgressbar",
            thickness=12,
            borderwidth=0,
            background=C["green"],
            troughcolor=C["surface2"],
            darkcolor=C["green"],
            lightcolor=C["green"],
        )

        style.configure(
            "Horizontal.TScrollbar",
            background=C["surface2"],
            troughcolor=C["surface3"],
            bordercolor=C["surface3"],
            arrowcolor=C["text_muted"],
            darkcolor=C["surface2"],
            lightcolor=C["surface2"],
        )
        style.map(
            "Horizontal.TScrollbar",
            background=[("active", C["btn_hover"])],
            arrowcolor=[("active", C["text"])],
        )

    def _build_ui(self):
        self._build_toolbar()
        self._build_main_area()
        self._build_statusbar()
        self._refresh_edit_action_state()

    def _build_toolbar(self):
        bar = tk.Frame(self, bg=C["toolbar_bg"], height=36)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)

        # ── 文件组 ──
        self.gen_tb_btn = ToolbarButton(bar, "生成", "\U0001f3a8", self._on_generate,
                      fg=C["green"])
        self.gen_tb_btn.pack(side="left", padx=(8, 0), pady=4)

        ToolbarSeparator(bar)

        # ── 操作组 ──
        self.upload_tb_btn = ToolbarButton(bar, "上传", "\U0001f4c2", self._on_upload_image)
        self.upload_tb_btn.pack(side="left", padx=1, pady=4)
        self.paste_tb_btn = ToolbarButton(bar, "粘贴", "\U0001f4cb", self._on_paste_image)
        self.paste_tb_btn.pack(side="left", padx=1, pady=4)
        self.save_tb_btn = ToolbarButton(bar, "保存", "\U0001f4be", self._on_save_image)
        self.save_tb_btn.pack(side="left", padx=1, pady=4)

        ToolbarSeparator(bar)

        # ── 编辑组 ──
        self.undo_tb_btn = ToolbarButton(bar, "撤销", "\u21a9", self._on_undo)
        self.undo_tb_btn.pack(side="left", padx=1, pady=4)
        self.redo_tb_btn = ToolbarButton(bar, "重做", "\u21aa", self._on_redo)
        self.redo_tb_btn.pack(side="left", padx=1, pady=4)
        self.clear_tb_btn = ToolbarButton(bar, "清空", "\U0001f5d1", self._on_clear)
        self.clear_tb_btn.pack(side="left", padx=1, pady=4)
        self.stop_btn = ToolbarButton(bar, "停止", "\u26d4", self._on_stop,
                                       fg=C["red"])
        self.stop_btn.pack(side="left", padx=1, pady=4)
        self.stop_btn.pack_forget()  # hidden initially

        self._edit_sep = ToolbarSeparator(bar)

        # ── AI 工具组 ──
        self._style_transfer_auto_btn = ToolbarButton(bar, "风格", "\U0001f501", self._on_style_transfer_toggle,
                                                      fg=C["mauve"])
        self._style_transfer_auto_btn.pack(side="left", padx=1, pady=4)
        self.bg_remove_tb_btn = ToolbarButton(bar, "去背", "\u2702", self._on_bg_remove,
                                              fg=C["accent2"])
        self.bg_remove_tb_btn.pack(side="left", padx=1, pady=4)
        self._bg_replace_btn = ToolbarButton(bar, "换背", "\U0001f3dd", self._on_bg_replace,
                                              fg=C["peach"])
        self._bg_replace_btn.pack(side="left", padx=1, pady=4)
        self._bg_replace_auto_btn = ToolbarButton(bar, "自换背", "\U0001f501", self._on_bg_replace_toggle,
                                                  fg=C["peach"])
        self._bg_replace_auto_btn.pack(side="left", padx=1, pady=4)
        self.upscale_tb_btn = ToolbarButton(bar, "放大", "\U0001f50d", self._on_upscale,
                                            fg=C["yellow"])
        self.upscale_tb_btn.pack(side="left", padx=1, pady=4)
        self.mask_tb_btn = ToolbarButton(bar, "蒙版", "\u270f", self._on_mask_toggle,
                                         fg=C["red"])
        self.mask_tb_btn.pack(side="left", padx=1, pady=4)
        self.describe_tb_btn = ToolbarButton(bar, "描述", "\U0001f4dd", self._on_describe,
                                             fg=C["green"])
        self.describe_tb_btn.pack(side="left", padx=1, pady=4)
        self.compare_tb_btn = ToolbarButton(bar, "对比", "\U0001f4ca", self._on_compare,
                                            fg=C["accent"])
        self.compare_tb_btn.pack(side="left", padx=1, pady=4)
        self.prompt_tb_btn = ToolbarButton(bar, "提示词", "\U0001f4cb", self._on_show_revised_prompt)
        self.prompt_tb_btn.pack(side="left", padx=1, pady=4)
        self.fullscreen_tb_btn = ToolbarButton(bar, "全屏", "\u2922", self._on_fullscreen_view,
                                               fg=C["accent"])
        self.fullscreen_tb_btn.pack(side="left", padx=1, pady=4)
        self.fit_tb_btn = ToolbarButton(bar, "适应", "\u25ce", self._on_fit_view,
                                        fg=C["text_dim"])
        self.fit_tb_btn.pack(side="left", padx=1, pady=4)

        ToolbarSeparator(bar)

        # ── 信息组 ──
        ToolbarButton(bar, "信息", "\u2139", self._on_image_info).pack(side="left", padx=1, pady=4)
        ToolbarButton(bar, "历史", "\U0001f4c1", self._on_open_history_folder).pack(side="left", padx=1, pady=4)

        # ── 右侧：设置齿轮 ──
        self._settings_visible = True
        self._settings_toggle_btn = ToolbarButton(bar, "设置", "\u2699", self._toggle_settings)
        self._settings_toggle_btn.pack(side="right", padx=(0, 8), pady=4)
        self._busy_toolbar_buttons = [
            self.upload_tb_btn,
            self.paste_tb_btn,
            self.save_tb_btn,
            self.undo_tb_btn,
            self.redo_tb_btn,
            self.clear_tb_btn,
            self._style_transfer_auto_btn,
            self.bg_remove_tb_btn,
            self._bg_replace_btn,
            self._bg_replace_auto_btn,
            self.upscale_tb_btn,
            self.mask_tb_btn,
            self.describe_tb_btn,
            self.compare_tb_btn,
        ]
        tk.Frame(self, bg=C["toolbar_edge"], height=1).pack(fill="x", side="top")

    def _build_main_area(self):
        main = tk.Frame(self, bg=C["surface3"])
        main.pack(fill="both", expand=True)

        # ── 左侧面板 ──
        self._left_panel = tk.Frame(main, bg=C["surface3"], width=320)
        self._left_panel.pack(side="left", fill="y")
        self._left_panel.pack_propagate(False)
        self._build_left_panel(self._left_panel)

        # ── 分隔线 ──
        self._left_sep = tk.Frame(main, bg=C["border"], width=1)
        self._left_sep.pack(side="left", fill="y")

        # ── 右侧：预览 + 历史 ──
        self._right_panel = tk.Frame(main, bg=C["surface3"])
        self._right_panel.pack(side="left", fill="both", expand=True)
        self._build_display(self._right_panel)

    def _build_left_panel(self, parent):
        # ── API 设置（可折叠） ──
        self._api_section = self._build_section(parent, "API 设置", collapsed=True)
        api = self._api_section["body"]

        row1 = tk.Frame(api, bg=C["surface"])
        row1.pack(fill="x", padx=8, pady=(6, 0))
        tk.Label(row1, text="接口", bg=C["surface"], fg=C["text_dim"],
                 font=("Microsoft YaHei UI", 9), width=4, anchor="w").pack(side="left")
        self.api_base_var = tk.StringVar(value=DEFAULT_API_BASE)
        e1 = tk.Entry(row1, textvariable=self.api_base_var, font=("Consolas", 9),
                      bg=C["canvas_bg"], fg=C["text"], insertbackground=C["text"],
                      relief="flat", bd=3, highlightthickness=1,
                      highlightbackground=C["border"], highlightcolor=C["accent2"])
        e1.pack(side="left", fill="x", expand=True, padx=(4, 0))

        row2 = tk.Frame(api, bg=C["surface"])
        row2.pack(fill="x", padx=8, pady=(4, 0))
        tk.Label(row2, text="模型", bg=C["surface"], fg=C["text_dim"],
                 font=("Microsoft YaHei UI", 9), width=4, anchor="w").pack(side="left")
        self.model_var = tk.StringVar(value=DEFAULT_MODEL_DISPLAY)
        self._model_options = list(DEFAULT_MODEL_OPTIONS)
        self.model_combo = ttk.Combobox(
            row2,
            textvariable=self.model_var,
            values=self._model_options,
            font=("Microsoft YaHei UI", 9),
            state="readonly",
        )
        self.model_combo.pack(side="left", fill="x", expand=True, padx=(4, 0))

        row3 = tk.Frame(api, bg=C["surface"])
        row3.pack(fill="x", padx=8, pady=(4, 6))
        tk.Label(row3, text="密码", bg=C["surface"], fg=C["text_dim"],
                 font=("Microsoft YaHei UI", 9), width=4, anchor="w").pack(side="left")
        self.auth_var = tk.StringVar(value=DEFAULT_AUTH)
        e2 = tk.Entry(row3, textvariable=self.auth_var, font=("Consolas", 9),
                      bg=C["canvas_bg"], fg=C["text"], insertbackground=C["text"],
                      relief="flat", bd=3, show="*", highlightthickness=1,
                      highlightbackground=C["border"], highlightcolor=C["accent2"])
        e2.pack(side="left", fill="x", expand=True, padx=(4, 0))

        # ── 生成参数 ──
        self._param_section = self._build_section(parent, "生成参数", collapsed=False)
        param = self._param_section["body"]

        pr1 = tk.Frame(param, bg=C["surface"])
        pr1.pack(fill="x", padx=8, pady=(6, 0))
        tk.Label(pr1, text="尺寸", bg=C["surface"], fg=C["text_dim"],
                 font=("Microsoft YaHei UI", 9), width=4, anchor="w").pack(side="left")
        self.size_var = tk.StringVar(value=ORIGINAL_SIZE_LABEL)
        self.size_combo = ttk.Combobox(
            pr1,
            textvariable=self.size_var,
            values=SIZE_DISPLAY_OPTIONS,
            font=("Microsoft YaHei UI", 9),
        )
        self.size_combo.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.size_combo.bind("<<ComboboxSelected>>", self._on_size_input_commit)
        self.size_combo.bind("<FocusOut>", self._on_size_input_commit)
        self.size_combo.bind("<Return>", self._on_size_input_commit)

        pr2 = tk.Frame(param, bg=C["surface"])
        pr2.pack(fill="x", padx=8, pady=(4, 0))
        tk.Label(pr2, text="格式", bg=C["surface"], fg=C["text_dim"],
                 font=("Microsoft YaHei UI", 9), width=4, anchor="w").pack(side="left")
        self.format_var = tk.StringVar(value=DEFAULT_FORMAT_DISPLAY)
        ttk.Combobox(pr2, textvariable=self.format_var, values=list(FORMAT_API_IDS.keys()),
                     state="readonly", font=("Microsoft YaHei UI", 9)).pack(side="left", fill="x", expand=True, padx=(4, 0))

        pr3 = tk.Frame(param, bg=C["surface"])
        pr3.pack(fill="x", padx=8, pady=(4, 0))
        tk.Label(pr3, text="质量", bg=C["surface"], fg=C["text_dim"],
                 font=("Microsoft YaHei UI", 9), width=4, anchor="w").pack(side="left")
        self.quality_var = tk.StringVar(value=DEFAULT_QUALITY_DISPLAY)
        ttk.Combobox(pr3, textvariable=self.quality_var, values=QUALITY_DISPLAY_OPTIONS,
                     state="readonly", font=("Microsoft YaHei UI", 9)).pack(side="left", fill="x", expand=True, padx=(4, 0))

        pr5 = tk.Frame(param, bg=C["surface"])
        pr5.pack(fill="x", padx=8, pady=(4, 0))
        tk.Label(pr5, text="风格", bg=C["surface"], fg=C["text_dim"],
                 font=("Microsoft YaHei UI", 9), width=4, anchor="w").pack(side="left")
        self.style_var = tk.StringVar(value="无（原始）")
        ttk.Combobox(pr5, textvariable=self.style_var,
                     values=list(STYLE_PRESETS.keys()),
                     state="readonly", font=("Microsoft YaHei UI", 9)).pack(side="left", fill="x", expand=True, padx=(4, 0))

        pr6 = tk.Frame(param, bg=C["surface"])
        pr6.pack(fill="x", padx=8, pady=(4, 0))
        tk.Label(pr6, text="数量", bg=C["surface"], fg=C["text_dim"],
                 font=("Microsoft YaHei UI", 9), width=4, anchor="w").pack(side="left")
        self.batch_var = tk.IntVar(value=1)
        spin = tk.Spinbox(pr6, from_=1, to=10, textvariable=self.batch_var, width=3,
                          font=("Microsoft YaHei UI", 10),
                          bg=C["canvas_bg"], fg=C["text"], insertbackground=C["text"],
                          buttonbackground=C["surface2"], relief="flat", bd=2,
                          highlightthickness=1, highlightbackground=C["border"],
                          highlightcolor=C["accent2"],
                          command=self._on_batch_spin)
        spin.pack(side="left", padx=(4, 0))

        # Compression control
        pr7 = tk.Frame(param, bg=C["surface"])
        pr7.pack(fill="x", padx=8, pady=(4, 0))
        tk.Label(pr7, text="压缩", bg=C["surface"], fg=C["text_dim"],
                 font=("Microsoft YaHei UI", 9), width=4, anchor="w").pack(side="left")
        self.compression_var = tk.IntVar(value=DEFAULT_COMPRESSION)
        comp_spin = tk.Spinbox(pr7, from_=1, to=100, textvariable=self.compression_var, width=4,
                          font=("Microsoft YaHei UI", 10),
                          bg=C["canvas_bg"], fg=C["text"], insertbackground=C["text"],
                          buttonbackground=C["surface2"], relief="flat", bd=2,
                          highlightthickness=1, highlightbackground=C["border"],
                          highlightcolor=C["accent2"])
        comp_spin.pack(side="left", padx=(4, 0))
        tk.Label(pr7, text="(JPEG/WebP)", bg=C["surface"], fg=C["text_muted"],
                 font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(4, 0))

        # ── 提示词 ──
        prompt_section = self._build_section(parent, "提示词", collapsed=False)
        prompt_body = prompt_section["body"]

        self.prompt_text = tk.Text(prompt_body, height=6, wrap="word",
                                   font=("Microsoft YaHei UI", 10),
                                   bg=C["canvas_bg"], fg=C["text"], insertbackground=C["text"],
                                   selectbackground=C["surface2"], relief="flat", bd=6,
                                   highlightthickness=1, highlightbackground=C["border"],
                                   highlightcolor=C["accent2"],
                                   padx=4, pady=4)
        self.prompt_text.pack(fill="both", expand=True, padx=6, pady=(4, 6))
        self.prompt_text.insert("1.0", PLACEHOLDER_TEXT)
        self.prompt_text.bind("<FocusIn>", self._on_prompt_focus_in)
        self.prompt_text.bind("<FocusOut>", self._on_prompt_focus_out)
        self._prompt_has_placeholder = True

        # ── 主操作按钮（智能：根据当前输入自动切换生成/编辑/合成模式） ──
        quick_frame = tk.Frame(parent, bg=C["surface3"])
        quick_frame.pack(fill="x", padx=6, pady=(0, 6))

        self.gen_btn = ActionCanvasButton(
            quick_frame,
            text="\u25b6  编辑图片",
            command=self._on_generate,
            color=C["accent"],
        )
        self.gen_btn.pack(fill="x")
        self._edit_rule_label = tk.Label(
            quick_frame,
            text="生成模式：输入文字描述，AI 从零生成图片",
            bg=C["surface3"],
            fg=C["text_muted"],
            justify="left",
            wraplength=300,
            font=("Microsoft YaHei UI", 8),
        )
        self._edit_rule_label.pack(fill="x", pady=(4, 0))

    def _build_section(self, parent, title, collapsed=False):
        section = {}
        outer = tk.Frame(parent, bg=C["surface3"])
        outer.pack(fill="x", padx=4, pady=(4, 0))

        indicator = tk.Frame(outer, width=3, bg=C["section_bar"])
        indicator.pack(side="left", fill="y")

        header = tk.Frame(outer, bg=C["surface"], cursor="hand2")
        header.pack(side="left", fill="x", expand=True)

        arrow = "\u25bc" if not collapsed else "\u25b6"
        lbl_arrow = tk.Label(header, text=arrow, bg=C["surface"], fg=C["accent2"],
                             font=("Consolas", 9))
        lbl_arrow.pack(side="left", padx=(8, 4), pady=5)
        lbl_title = tk.Label(header, text=title, bg=C["surface"], fg=C["text"],
                             font=("Microsoft YaHei UI", 9, "bold"))
        lbl_title.pack(side="left", pady=5)

        body = tk.Frame(parent, bg=C["surface"])
        # Always pack body immediately after outer so it stays in the correct position.
        # We then hide it with pack_forget if collapsed, rather than omitting the pack call.
        body.pack(fill="x", padx=4, pady=(0, 0), after=outer)
        if collapsed:
            body.pack_forget()

        section["header"] = header
        section["outer"] = outer
        section["body"] = body
        section["arrow"] = lbl_arrow
        section["indicator"] = indicator
        section["collapsed"] = collapsed

        def toggle(event=None):
            if section["collapsed"]:
                body.pack(fill="x", padx=4, pady=(0, 0), after=section["outer"])
                lbl_arrow.config(text="\u25bc")
                indicator.config(bg=C["section_bar"])
                section["collapsed"] = False
            else:
                body.pack_forget()
                lbl_arrow.config(text="\u25b6")
                indicator.config(bg=C["accent_glow"])
                section["collapsed"] = True

        header.bind("<Button-1>", toggle)
        lbl_arrow.bind("<Button-1>", toggle)
        lbl_title.bind("<Button-1>", toggle)
        return section

    def _toggle_settings(self):
        if self._settings_visible:
            self._left_panel.pack_forget()
            self._left_sep.pack_forget()
            self._settings_visible = False
        else:
            self._left_panel.pack(side="left", fill="y", before=self._right_panel)
            self._left_sep.pack(side="left", fill="y", before=self._right_panel)
            self._settings_visible = True

    def _build_display(self, parent):
        # ── 主画布 / 编辑区 ──
        preview_label = "主画布（编辑区 / 浏览预览，拖拽 / Ctrl+Shift+V 粘贴）" if DND_ENABLED else "主画布（编辑区 / 浏览预览，Ctrl+Shift+V 粘贴）"
        display_frame = tk.Frame(parent, bg=C["surface3"])
        display_frame.pack(fill="both", expand=True, padx=4, pady=(4, 0))

        # 预览标题栏
        preview_header = tk.Frame(display_frame, bg=C["surface3"])
        preview_header.pack(fill="x")
        tk.Label(preview_header, text=preview_label, bg=C["surface3"], fg=C["text_dim"],
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=8, pady=4)
        self._display_role_label = tk.Label(
            preview_header,
            text="当前编辑图：未设置",
            bg=C["surface3"],
            fg=C["yellow"],
            font=("Microsoft YaHei UI", 8, "bold"),
        )
        self._display_role_label.pack(side="right", padx=8, pady=4)

        # ── 编辑区图片条：集中展示所有参与编辑的图片 ──
        edit_strip_outer = tk.Frame(display_frame, bg=C["strip_bg"])
        edit_strip_outer.pack(fill="x", padx=4, pady=(0, 2))

        strip_header = tk.Frame(edit_strip_outer, bg=C["strip_bg"])
        strip_header.pack(fill="x", padx=4, pady=(2, 0))
        self._edit_strip_title = tk.Label(
            strip_header, text="编辑区（0 张图片）", bg=C["strip_bg"],
            fg=C["text_dim"], font=("Microsoft YaHei UI", 8, "bold"))
        self._edit_strip_title.pack(side="left", padx=2)

        # Action buttons in strip header
        strip_btn_row = tk.Frame(strip_header, bg=C["strip_bg"])
        strip_btn_row.pack(side="right", padx=2)
        tk.Button(strip_btn_row, text="+上传", command=self._on_add_to_strip_upload,
                  bg=C["btn_bg"], fg=C["text"],
                  activebackground=C["btn_hover"], activeforeground=C["text"],
                  font=("Microsoft YaHei UI", 7), relief="flat", bd=0, cursor="hand2",
                  padx=6, pady=1).pack(side="left", padx=1)
        tk.Button(strip_btn_row, text="+粘贴", command=self._on_add_to_strip_paste,
                  bg=C["btn_bg"], fg=C["text"],
                  activebackground=C["btn_hover"], activeforeground=C["text"],
                  font=("Microsoft YaHei UI", 7), relief="flat", bd=0, cursor="hand2",
                  padx=6, pady=1).pack(side="left", padx=1)
        tk.Button(strip_btn_row, text="清空", command=self._on_clear_edit_strip,
                  bg=C["btn_bg"], fg=C["red"],
                  activebackground=C["btn_hover"], activeforeground=C["red"],
                  font=("Microsoft YaHei UI", 7), relief="flat", bd=0, cursor="hand2",
                  padx=6, pady=1).pack(side="left", padx=1)

        self._edit_strip_inner = tk.Frame(edit_strip_outer, bg=C["strip_bg"])
        self._edit_strip_inner.pack(fill="x", padx=4, pady=(0, 4))
        self._edit_strip_refs = []  # PhotoImage references for strip thumbnails

        # Enable DND on edit strip
        if DND_ENABLED:
            try:
                self._edit_strip_inner.drop_target_register(DND_FILES)
                self._edit_strip_inner.dnd_bind("<<Drop>>", self._on_edit_strip_drop)
            except Exception:
                pass

        # ── 编辑完成通知条：显示"输入→输出"摘要 ──
        self._edit_result_bar = tk.Frame(display_frame, bg=C["accent"], height=0)
        # Initially hidden (height=0, not packed with expand)
        self._edit_result_label = tk.Label(self._edit_result_bar, text="",
                                           bg=C["accent"], fg="#ffffff",
                                           font=("Microsoft YaHei UI", 9, "bold"),
                                           anchor="w")
        self._edit_result_label.pack(side="left", fill="x", expand=True, padx=8, pady=3)
        self._edit_result_compare_btn = tk.Button(self._edit_result_bar, text="对比",
                                                  command=self._on_compare,
                                                  bg=C["toolbar_bg"], fg=C["text"],
                                                  activebackground=C["btn_hover"],
                                                  font=("Microsoft YaHei UI", 8),
                                                  relief="flat", bd=0, cursor="hand2", pady=1)
        self._edit_result_compare_btn.pack(side="right", padx=(2, 4), pady=2)
        self._edit_result_close_btn = tk.Label(self._edit_result_bar, text="✕",
                                               bg=C["accent"], fg="#ffffff",
                                               font=("Consolas", 10), cursor="hand2")
        self._edit_result_close_btn.pack(side="right", padx=4, pady=2)
        self._edit_result_close_btn.bind("<Button-1>", lambda e: self._hide_edit_result_bar())

        self.canvas = tk.Canvas(display_frame, bg=C["canvas_bg"], highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind("<Double-Button-1>", self._on_canvas_dblclick)
        self.canvas.bind("<MouseWheel>", self._on_canvas_scroll)
        self.canvas.bind("<Button-4>", self._on_canvas_scroll)  # Linux scroll up
        self.canvas.bind("<Button-5>", self._on_canvas_scroll)  # Linux scroll down
        self.canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self.canvas.bind("<ButtonPress-3>", self._on_canvas_rclick_press)
        self.canvas.bind("<B3-Motion>", self._on_canvas_rclick_drag)
        self.canvas.bind("<ButtonRelease-3>", self._on_canvas_rclick_release)

        # ── 历史记录区 ──
        hist_outer = tk.Frame(parent, bg=C["surface3"])
        hist_outer.pack(fill="x", padx=4, pady=(2, 4))

        hist_header = tk.Frame(hist_outer, bg=C["surface3"])
        hist_header.pack(fill="x")
        tk.Label(hist_header, text="历史记录", bg=C["surface3"], fg=C["text_dim"],
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=8, pady=4)
        self._hist_add_ref_btn = tk.Button(hist_header, text="加入编辑区", command=self._on_add_hist_selected_to_strip,
                  bg=C["accent"], fg=C["toolbar_bg"],
                  activebackground=C["btn_hover"], activeforeground=C["text"],
                  font=("Microsoft YaHei UI", 8, "bold"), relief="flat", bd=0, cursor="hand2",
                  pady=1)
        self._hist_add_ref_btn.pack(side="left", padx=4, pady=4)
        self._hist_add_ref_btn.pack_forget()  # hidden until selection exists
        tk.Button(hist_header, text="清空全部", command=self._on_clear_all_history,
                  bg=C["btn_bg"], fg=C["red"],
                  activebackground=C["btn_hover"], activeforeground=C["red"],
                  font=("Microsoft YaHei UI", 8), relief="flat", bd=0, cursor="hand2",
                  pady=1).pack(side="right", padx=6, pady=4)
        tk.Label(hist_header, text="左键浏览 | 双击加入编辑区 | Ctrl+左键多选 | 右键对选中项操作", bg=C["surface3"], fg=C["text_muted"],
                 font=("Microsoft YaHei UI", 8)).pack(side="right", padx=8, pady=4)

        self.hist_canvas = tk.Canvas(hist_outer, bg=C["canvas_bg"], height=96,
                                      highlightthickness=0)
        hist_scroll = ttk.Scrollbar(hist_outer, orient="horizontal",
                                     command=self.hist_canvas.xview)
        self.hist_canvas.configure(xscrollcommand=hist_scroll.set)
        hist_scroll.pack(side="bottom", fill="x")
        self.hist_canvas.pack(fill="x", padx=4, pady=(0, 4))
        self.hist_inner = tk.Frame(self.hist_canvas, bg=C["canvas_bg"])
        self.hist_canvas.create_window((0, 0), window=self.hist_inner, anchor="nw")
        self.hist_inner.bind("<Configure>", lambda e: self.hist_canvas.configure(
            scrollregion=self.hist_canvas.bbox("all")))

    def _build_statusbar(self):
        tk.Frame(self, bg=C["toolbar_edge"], height=1).pack(fill="x", side="bottom")
        bar = tk.Frame(self, bg=C["surface3"], height=26)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(bar, variable=self.progress_var,
                                             maximum=100, length=160, mode="determinate")
        self.progress_bar.pack(side="left", padx=(8, 4), pady=3)

        self.time_label = tk.Label(bar, text="", bg=C["surface3"], fg=C["text_muted"],
                                    font=("Consolas", 9))
        self.time_label.pack(side="left", padx=4)

        self.status_label = tk.Label(bar, text="就绪", bg=C["surface3"], fg=C["text_dim"],
                                      font=("Microsoft YaHei UI", 9), anchor="w")
        self.status_label.pack(side="left", fill="x", expand=True, padx=4)

        self._error_badge = tk.Label(bar, text="", bg=C["surface3"], fg=C["red"],
                                      font=("Microsoft YaHei UI", 8, "bold"),
                                      cursor="hand2")
        self._error_badge.pack(side="left", padx=(0, 2))
        self._error_badge.bind("<Button-1>", lambda e: self._show_error_log())
        self._update_error_badge()

        shortcuts_hint = "Ctrl+Enter 生成 | Ctrl+S 保存 | Ctrl+V 粘贴 | Ctrl+Z/Y 撤销/重做 | Esc 停止"
        tk.Label(bar, text=shortcuts_hint, bg=C["surface3"], fg=C["text_muted"],
                 font=("Microsoft YaHei UI", 8)).pack(side="right", padx=8)

    # ── 键盘快捷键 ──────────────────────────────────────

    def _bind_shortcuts(self):
        self.bind("<Control-Return>", lambda e: self._on_generate())
        self.bind("<Control-s>", lambda e: self._on_save_image())
        self.bind("<Control-S>", lambda e: self._on_save_image())
        self.bind("<Control-o>", lambda e: self._on_upload_image())
        self.bind("<Control-O>", lambda e: self._on_upload_image())
        self.bind("<Control-v>", self._on_paste_shortcut)
        self.bind("<Control-V>", self._on_paste_shortcut)
        self.bind("<Control-Shift-V>", self._on_paste_shortcut)
        self.bind("<Control-Shift-v>", self._on_paste_shortcut)
        self.bind("<Control-z>", lambda e: self._on_undo())
        self.bind("<Control-Z>", lambda e: self._on_undo())
        self.bind("<Control-y>", lambda e: self._on_redo())
        self.bind("<Control-Y>", lambda e: self._on_redo())
        self.bind("<Control-Shift-z>", lambda e: self._on_redo())
        self.bind("<Control-Shift-Z>", lambda e: self._on_redo())
        self.bind("<Escape>", self._on_escape_key)

    def _is_text_input_widget(self, widget):
        text_input_classes = {"Entry", "Text", "TEntry", "TCombobox", "Spinbox"}
        current = widget
        while current is not None:
            try:
                if current.winfo_class() in text_input_classes:
                    return True
            except Exception:
                return False
            current = getattr(current, "master", None)
        return False

    def _on_paste_shortcut(self, event=None):
        widget = event.widget if event is not None else self.focus_get()
        if self._is_text_input_widget(widget):
            return None
        self._on_paste_image()
        return "break"

    def _on_escape_key(self, event=None):
        """Escape key: stop generation, exit mask mode, or return to the edit-area image."""
        if self.is_generating:
            self._on_stop()
            return
        # Exit mask mode if active
        if self._mask_mode:
            self._on_mask_toggle()  # toggle off
            return
        # If browsing a preview (history/ref), return to the edit-area image.
        if self._preview_override_image is not None:
            self._clear_preview_override(redraw=True)
            self._set_status("已返回编辑区图片（按 Esc 可随时返回）")

    def _has_workspace_image(self):
        return bool(self.current_b64) and self.current_image is not None

    def _get_display_image(self):
        return self._preview_override_image or self.current_image

    def _get_display_b64(self):
        if self._preview_override_image is not None:
            try:
                return ImageGenerator.image_to_b64(self._preview_override_image)
            except Exception:
                return None
        return self.current_b64

    def _clear_preview_override(self, redraw=False):
        had_preview = self._preview_override_image is not None
        self._preview_override_image = None
        self._preview_override_label = ""
        self._refresh_role_hints()
        self._refresh_edit_strip()
        if redraw and had_preview:
            if self._mask_mode:
                self._draw_mask_overlay()
            else:
                self._draw_image_on_canvas()

    def _refresh_role_hints(self):
        has_workspace = self._has_workspace_image()
        ref_count = len(self._ref_images)
        selected_count = len(self._ref_selected)

        if hasattr(self, "_display_role_label"):
            if self._preview_override_image is not None:
                browse_source = self._preview_override_label.replace("仅浏览: ", "").replace("浏览: ", "") or "当前浏览图"
                if has_workspace:
                    text = f"当前显示：{browse_source} | 编辑目标：编辑区图片"
                else:
                    text = f"当前显示：{browse_source} | 编辑目标：未设置"
            elif has_workspace:
                text = "当前显示：编辑区图片 | 编辑目标：编辑区图片"
            else:
                text = "当前显示：空 | 编辑目标：未设置"
            self._display_role_label.config(text=text)

        if hasattr(self, "_edit_rule_label"):
            if not has_workspace and ref_count == 0:
                text = "生成模式：输入文字描述，AI 从零生成图片"
            elif has_workspace and ref_count == 0:
                text = "编辑模式：AI 修改编辑区图片（单图编辑）"
            elif has_workspace and ref_count > 0:
                text = f"编辑模式：AI 修改编辑区图片，{ref_count} 张参考图辅助提供风格/内容"
            elif selected_count > 0:
                text = f"合成模式：AI 将 {selected_count} 张选中参考图合成一张新图"
            else:
                text = f"合成模式：AI 将 {ref_count} 张参考图合成一张新图（无编辑区底图）"
            self._edit_rule_label.config(text=text)

    def _refresh_edit_strip(self):
        """Rebuild the edit-area image strip above the canvas.

        This strip shows ALL images that will participate in the next AI operation:
        - Slot 0: 编辑区图片 (the primary edit target, with gold border)
        - Slots 1+: 参考图 (auxiliary reference images, with blue border)

        Each slot shows a thumbnail, a role badge, and a remove button.
        Clicking a slot previews that image on the canvas.
        """
        if not hasattr(self, "_edit_strip_inner"):
            return

        # Check if we have a real Tk widget (not a test stub)
        _is_real_tk = isinstance(self._edit_strip_inner, tk.Frame)

        # Destroy existing strip children
        if _is_real_tk:
            for w in self._edit_strip_inner.winfo_children():
                w.destroy()
        self._edit_strip_refs.clear()

        has_workspace = self._has_workspace_image()
        ref_count = len(self._ref_images)
        total = (1 if has_workspace else 0) + ref_count

        # Update title
        if total == 0:
            self._edit_strip_title.config(text="编辑区（空 — 拖入/上传/粘贴图片开始）")
            if not _is_real_tk:
                return
            # Show an empty-state placeholder
            empty_lbl = tk.Label(self._edit_strip_inner,
                                 text="  暂无图片  ·  拖入图片到画布 / 点击工具栏上传 / Ctrl+V 粘贴  ",
                                 bg=C["canvas_bg"], fg=C["text_muted"],
                                 font=("Microsoft YaHei UI", 9), pady=4, cursor="hand2")
            empty_lbl.pack(fill="x", pady=2)
            empty_lbl.bind("<Button-1>", lambda e: self._on_upload_image())
            return

        mode_text = "编辑模式" if has_workspace else "合成模式"
        self._edit_strip_title.config(text=f"编辑区（{total} 张图片 · {mode_text}）")

        # Skip widget creation in test mode
        if not _is_real_tk:
            return

        # ── Slot 0: 编辑区图片 (primary edit target) ──
        if has_workspace:
            # Determine the primary image label
            if getattr(self, "_primary_is_result", False):
                primary_name = "AI 生成结果"
                primary_role = "结果"
                primary_role_color = C["green"]
            else:
                primary_name = "编辑区图片"
                primary_role = "主图"
                primary_role_color = C["yellow"]

            self._build_strip_slot(
                image=self.current_image,
                role=primary_role,
                role_color=primary_role_color,
                border_color=C["yellow"],
                name=primary_name,
                on_click=lambda: self._clear_preview_override(redraw=True),
                on_remove=self._remove_workspace_image,
                is_primary=True,
            )

        # ── Slots 1+: 参考图 ──
        for i, ref in enumerate(self._ref_images):
            is_selected = i in self._ref_selected
            border_color = C["accent"] if is_selected else C["border"]
            role_text = "参考✓" if is_selected else "参考"
            role_color = C["accent"] if is_selected else C["text_dim"]
            name = ref.get("name", f"参考图 {i + 1}")
            if len(name) > 14:
                name = name[:12] + ".."

            # Capture i in a closure
            def _make_click_handler(idx):
                return lambda: self._preview_ref_image(idx)
            def _make_remove_handler(idx):
                return lambda: self._remove_ref_image(idx)
            def _make_toggle_handler(idx):
                return lambda: self._toggle_ref_select(idx)
            def _make_set_primary_handler(idx):
                return lambda: self._set_as_primary_image(idx)

            self._build_strip_slot(
                image=ref["image"],
                role=role_text,
                role_color=role_color,
                border_color=border_color,
                name=name,
                on_click=_make_click_handler(i),
                on_remove=_make_remove_handler(i),
                on_toggle_role=_make_toggle_handler(i),
                on_set_primary=_make_set_primary_handler(i),
            )

    def _build_strip_slot(self, image, role, role_color, border_color, name,
                          on_click, on_remove, on_toggle_role=None, on_set_primary=None,
                          is_primary=False):
        """Build one image slot in the edit-area strip."""
        slot = tk.Frame(self._edit_strip_inner, bg=C["surface2"],
                        highlightbackground=border_color, highlightthickness=2,
                        cursor="hand2")
        slot.pack(side="left", padx=2, pady=2)

        # Thumbnail
        thumb_h = 40
        thumb_w = 40
        if image:
            iw, ih = image.size
            scale = min(thumb_w / max(iw, 1), thumb_h / max(ih, 1))
            new_w = max(1, int(iw * scale))
            new_h = max(1, int(ih * scale))
            try:
                resized = image.resize((new_w, new_h), Image.LANCZOS)
                # Center in square
                square = Image.new("RGBA", (thumb_w, thumb_h), (0, 0, 0, 0))
                offset_x = (thumb_w - new_w) // 2
                offset_y = (thumb_h - new_h) // 2
                square.paste(resized, (offset_x, offset_y))
                photo = ImageTk.PhotoImage(square)
                self._edit_strip_refs.append(photo)
                thumb_lbl = tk.Label(slot, image=photo, bg=C["surface2"],
                                     width=thumb_w, height=thumb_h)
                thumb_lbl.pack(padx=(2, 0), pady=(2, 0))
                thumb_lbl.bind("<Button-1>", lambda e: on_click())
                # Right-click on thumbnail for context menu
                if on_set_primary:
                    thumb_lbl.bind("<Button-3>", lambda e: self._show_strip_context_menu(e, on_set_primary, is_primary))
            except Exception:
                thumb_lbl = tk.Label(slot, text="?", bg=C["surface2"],
                                     fg=C["text_muted"], width=5, height=2)
                thumb_lbl.pack(padx=(2, 0), pady=(2, 0))
                thumb_lbl.bind("<Button-1>", lambda e: on_click())

        # Info row: role badge + name
        info_row = tk.Frame(slot, bg=C["surface2"])
        info_row.pack(fill="x", padx=2, pady=(1, 0))

        role_lbl = tk.Label(info_row, text=role, bg=C["surface2"],
                             fg=role_color, font=("Microsoft YaHei UI", 7, "bold"),
                             cursor="hand2")
        role_lbl.pack(side="left")
        if on_toggle_role:
            role_lbl.bind("<Button-1>", lambda e: on_toggle_role())

        name_lbl = tk.Label(info_row, text=name, bg=C["surface2"],
                             fg=C["text"], font=("Microsoft YaHei UI", 7),
                             anchor="w")
        name_lbl.pack(side="left", fill="x", expand=True, padx=(2, 0))
        name_lbl.bind("<Button-1>", lambda e: on_click())

        # Remove button
        rm_lbl = tk.Label(slot, text="✕", bg=C["surface2"], fg=C["red"],
                           font=("Consolas", 8), cursor="hand2")
        rm_lbl.pack(side="right", padx=(0, 2), pady=(0, 2))
        rm_lbl.bind("<Button-1>", lambda e: on_remove())

        # Click on slot itself
        slot.bind("<Button-1>", lambda e: on_click())
        # Right-click on slot for context menu
        if on_set_primary:
            slot.bind("<Button-3>", lambda e: self._show_strip_context_menu(e, on_set_primary, is_primary))

    def _show_strip_context_menu(self, event, on_set_primary, is_primary):
        """Show context menu for an edit strip image slot."""
        menu = tk.Menu(self, tearoff=0, bg=C["surface2"], fg=C["text"],
                       activebackground=C["accent"], activeforeground=C["toolbar_bg"],
                       font=("Microsoft YaHei UI", 9))
        if is_primary:
            menu.add_command(label="✓ 当前为主图", state="disabled")
        else:
            menu.add_command(label="⬆ 设为主图（编辑目标）", command=on_set_primary)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _show_edit_result_bar(self, summary):
        """Keep the edit result summary state without showing the top banner."""
        if not hasattr(self, "_edit_result_bar"):
            return
        self._last_edit_summary = summary
        self._hide_edit_result_bar()

    def _hide_edit_result_bar(self):
        """Dismiss the edit result notification bar."""
        if not hasattr(self, "_edit_result_bar"):
            return
        self._edit_result_bar.pack_forget()

    def _build_edit_summary(self):
        """Build a summary dict describing the last edit operation's inputs.

        This is called after an AI edit completes to show the user what
        images were used as input and what mode was active.
        """
        has_workspace = self._has_workspace_image()
        ref_count = len(self._ref_images)

        if has_workspace and ref_count == 0:
            return {"mode": "编辑", "input_count": 1, "input_desc": "1 张主图"}
        elif has_workspace and ref_count > 0:
            return {"mode": "编辑", "input_count": 1 + ref_count,
                    "input_desc": f"1 张主图 + {ref_count} 张参考图"}
        elif ref_count > 0:
            return {"mode": "合成", "input_count": ref_count,
                    "input_desc": f"{ref_count} 张参考图"}
        else:
            return {"mode": "生成", "input_count": 0, "input_desc": "纯文字"}

    def _remove_workspace_image(self):
        """Remove the workspace/primary image from the edit area."""
        if not self._ensure_idle("移除编辑区图片"):
            return
        if self.current_b64:
            self._push_undo()
        self.canvas.delete("all")
        self.current_image = None
        self.current_b64 = None
        self._discard_mask_session()
        self._last_response_id = None
        self._last_revised_prompt = None
        self._primary_is_result = False
        self._preview_override_image = None
        self._preview_override_label = ""
        self._clear_compare_sources()
        self._main_photo = None
        self._hide_edit_result_bar()
        self._refresh_edit_action_state()
        self._set_status("已移除编辑区图片")

    def _set_as_primary_image(self, ref_idx):
        """Promote a reference image to be the primary (主图) edit target.

        The current primary image (if any) is demoted to a reference image.
        """
        if not self._ensure_idle("调整主图和参考图"):
            return
        if not (0 <= ref_idx < len(self._ref_images)):
            return
        ref = self._ref_images[ref_idx]

        # Save current workspace image as a reference (if it exists)
        old_primary_name = "编辑区图片"
        if self.current_b64 and self.current_image:
            # Insert the old primary at the same position as the ref we're promoting
            self._ref_images[ref_idx] = {
                "b64": self.current_b64,
                "image": self.current_image.copy(),
                "name": old_primary_name,
            }
        else:
            # No current primary, just remove the ref from the list
            self._ref_images.pop(ref_idx)

        # Set the promoted ref as the new primary
        self.current_b64 = ref["b64"]
        self.current_image = ref["image"].copy()
        self._preview_override_image = None
        self._preview_override_label = ""
        self._last_response_id = None
        self._last_revised_prompt = None
        self._primary_is_result = False  # Promoted from ref, not an AI result

        # Clear mask since we changed the primary image
        self._mask_mode = False
        self._mask_b64 = None
        self._mask_image = None
        self._clear_mask_session()

        self._clear_compare_sources()
        self._hide_edit_result_bar()
        self._refresh_edit_action_state()
        self._reset_canvas_view()
        self._draw_image_on_canvas()
        self._set_status(f"已将「{ref.get('name', '参考图')}」设为主图")

    def _on_add_to_strip_upload(self):
        """Upload images and add them to the edit strip (as primary or reference)."""
        if not self._ensure_idle("从本地加入编辑区图片"):
            return
        paths = filedialog.askopenfilenames(
            title="选择图片（可多选）",
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.webp *.bmp"), ("所有文件", "*.*")]
        )
        if not paths:
            return
        for path in paths:
            try:
                self._add_image_to_strip_path(path)
            except Exception as e:
                messagebox.showerror("加载失败", f"{path}: {e}")

    def _on_add_to_strip_paste(self):
        """Paste image from clipboard and add to the edit strip."""
        if not self._ensure_idle("从剪贴板添加图片"):
            return
        if ImageGrab is None:
            messagebox.showwarning("剪贴板", "当前环境不支持剪贴板图片读取（可能缺少 Pillow 的 ImageGrab 模块）")
            return
        try:
            grabbed = ImageGrab.grabclipboard()
        except Exception as e:
            messagebox.showerror("粘贴失败", str(e))
            return

        if isinstance(grabbed, Image.Image):
            self._add_image_to_strip(grabbed, "剪贴板图片")
            return

        if isinstance(grabbed, list):
            added = 0
            for item in grabbed:
                path = Path(item)
                if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"} and path.exists():
                    try:
                        self._add_image_to_strip_path(path, f"剪贴板: {path.name}")
                        added += 1
                    except Exception:
                        continue
            if added:
                return

        messagebox.showwarning("剪贴板", "剪贴板中没有可用的图片数据")

    def _on_clear_edit_strip(self):
        """Clear all images from the edit strip (both primary and references)."""
        if not self._ensure_idle("清空编辑条"):
            return
        has_images = self.current_b64 or self._ref_images
        if not has_images:
            return
        if self.current_b64:
            self._push_undo()
        self.canvas.delete("all")
        self.current_image = None
        self.current_b64 = None
        self._discard_mask_session()
        self._last_response_id = None
        self._last_revised_prompt = None
        self._primary_is_result = False
        self._preview_override_image = None
        self._preview_override_label = ""
        self._ref_images.clear()
        self._ref_selected.clear()
        self._clear_compare_sources()
        self._main_photo = None
        self._hide_edit_result_bar()
        self._refresh_edit_action_state()
        self._set_status("已清空编辑区所有图片")

    def _on_edit_strip_drop(self, event):
        """Handle files dropped onto the edit strip."""
        if not self._ensure_idle("拖入图片到编辑区"):
            return
        raw_paths = self.tk.splitlist(event.data)
        added = 0
        for raw_path in raw_paths:
            path = Path(raw_path)
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                try:
                    self._add_image_to_strip_path(path, f"拖入: {path.name}")
                    added += 1
                except Exception as e:
                    messagebox.showerror("拖入失败", f"{path.name}: {e}")
        if added == 0:
            messagebox.showwarning("拖入失败", "未找到可识别的图片文件")

    def _add_image_to_strip(self, pil_image, source_name=""):
        """Add an image to the edit strip. If no primary exists, set as primary.
        Otherwise, add as a reference image."""
        img = self._normalize_image(pil_image)
        b64 = ImageGenerator.image_to_b64(img)

        if not self._has_workspace_image():
            # No primary image → set as primary
            self.current_b64 = b64
            self.current_image = img
            self._preview_override_image = None
            self._preview_override_label = ""
            self._last_response_id = None
            self._last_revised_prompt = None
            self._primary_is_result = False  # Manually added, not an AI result
            self._clear_compare_sources()
            self._hide_edit_result_bar()
            self._refresh_edit_action_state()
            self._reset_canvas_view()
            self._draw_image_on_canvas()
            self._set_status(f"已设为主图: {source_name}" if source_name else "已设为主图")
        else:
            # Primary exists → add as reference
            self._ref_images.append({"b64": b64, "image": img, "name": source_name or "参考图"})
            self._refresh_edit_action_state()
            self._set_status(f"已添加为参考图: {source_name}" if source_name else "已添加为参考图")

    def _add_image_to_strip_path(self, path, source_name=None):
        """Add an image from file path to the edit strip."""
        path = Path(path)
        with Image.open(path) as img:
            loaded = self._normalize_image(img)
        label = source_name or path.name
        self._add_image_to_strip(loaded, label)

    def _refresh_edit_action_state(self):
        """Update the smart gen/edit/compose button label and state based on current inputs.

        Professional AI image editors use ONE button that auto-switches mode:
        - No images → "生成图片" (generate from text)
        - Has workspace image only → "编辑图片" (edit the workspace image)
        - Workspace + refs → "编辑图片（+N参考）" (edit workspace with ref assistance)
        - Only refs (no workspace) → "合成图片（N张）" (compose/blend multiple images)
        - Selected refs → "合成选中（N张）" (compose only selected refs)

        All modes go through the same _submit_edit_job pipeline.
        """
        has_workspace = self._has_workspace_image()
        ref_count = len(self._ref_images)
        selected_count = len(self._ref_selected)

        # Determine mode and button appearance
        if not has_workspace and ref_count == 0:
            label = "\u25b6  生成图片"
            color = C["accent2"]
            state = "normal"
        elif has_workspace:
            label = f"\u270f  编辑图片\uff08+{ref_count}\u53c2\u8003\uff09" if ref_count > 0 else "\u270f  编辑图片"
            color = C["accent"]
            state = "normal" if not self.is_generating else "disabled"
        elif selected_count > 0:
            label = f"\U0001f500  合成选中\uff08{selected_count}\u5f20\uff09"
            color = C["mauve"]
            state = "normal" if not self.is_generating else "disabled"
        else:
            label = f"\U0001f500  合成图片\uff08{ref_count}\u5f20\uff09"
            color = C["mauve"]
            state = "normal" if not self.is_generating else "disabled"

        if self.is_generating:
            state = "disabled"

        self.gen_btn.config(text=label, bg=color, state=state)

        # Update toolbar button label
        if hasattr(self, "gen_tb_btn"):
            if not has_workspace and ref_count == 0:
                tb_label = "生成"
                tb_icon = "\U0001f3a8"
            elif has_workspace:
                tb_label = "编辑"
                tb_icon = "\u270f\ufe0f"
            elif selected_count > 0:
                tb_label = "合成"
                tb_icon = "\U0001f500"
            else:
                tb_label = "合成"
                tb_icon = "\U0001f500"
            self.gen_tb_btn.set_text(tb_label, tb_icon)
            self.gen_tb_btn.set_enabled(state == "normal")

        self._refresh_undo_redo_buttons()
        self._refresh_role_hints()
        self._refresh_edit_strip()

    def _refresh_undo_redo_buttons(self):
        has_mask_session = self._has_mask_session()
        if has_mask_session:
            undo_label = "撤蒙"
            redo_label = "重蒙"
            undo_enabled = bool(self._mask_undo_stack)
            redo_enabled = bool(self._mask_redo_stack)
        else:
            undo_label = "撤销"
            redo_label = "重做"
            undo_enabled = bool(self._undo_stack)
            redo_enabled = bool(self._redo_stack)

        if self.is_generating:
            undo_enabled = False
            redo_enabled = False

        if hasattr(self, "undo_tb_btn"):
            self.undo_tb_btn.set_text(undo_label, "\u21a9")
            self.undo_tb_btn.set_enabled(undo_enabled)
        if hasattr(self, "redo_tb_btn"):
            self.redo_tb_btn.set_text(redo_label, "\u21aa")
            self.redo_tb_btn.set_enabled(redo_enabled)

    def _require_workspace_image(self, action_text="修改编辑区图片"):
        if self._has_workspace_image():
            return True
        messagebox.showwarning(
            "编辑区为空",
            f"编辑区没有图片，无法{action_text}。\n\n"
            f"请先执行以下任一操作：\n"
            f"• 在提示词框输入文字并点击生成\n"
            f"• 双击历史记录中的图片\n"
            f"• 从文件或剪贴板加载图片"
        )
        return False

    def _summarize_input_images(self, b64_list):
        total = len(b64_list or [])
        has_workspace = bool(self.current_b64) and self.current_b64 in (b64_list or [])
        ref_count = total - (1 if has_workspace else 0)
        if has_workspace and ref_count > 0:
            return f"编辑区图片 + {ref_count} 张参考图"
        if has_workspace:
            return "编辑区图片"
        if ref_count > 1:
            return f"{ref_count} 张图合成"
        if ref_count > 0:
            return "1 张参考图"
        return "无输入图片"

    def _normalize_image(self, pil_image, max_dim=4096):
        img = pil_image
        if "A" in img.getbands():
            img = img.convert("RGBA")
        else:
            img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > max_dim:
            scale = max_dim / max(w, h)
            new_w = max(1, int(w * scale))
            new_h = max(1, int(h * scale))
            img = img.resize((new_w, new_h), Image.LANCZOS)
        return img

    def _load_image_into_workspace(self, pil_image, status_text, push_undo=False):
        image = self._normalize_image(pil_image)
        if push_undo and self.current_b64:
            self._push_undo()
        self._discard_mask_session()
        self.current_b64 = ImageGenerator.image_to_b64(image)
        self._last_response_id = None
        self._last_revised_prompt = None
        self._primary_is_result = False  # Manually loaded, not an AI result
        self._clear_compare_sources()
        self._show_image(image)
        self._hide_edit_result_bar()  # Dismiss any previous edit result notification
        self._refresh_edit_action_state()
        self._set_status(status_text)

    def _load_image_path(self, path, source_name=None, push_undo=False):
        path = Path(path)
        with Image.open(path) as img:
            loaded = self._normalize_image(img)
        label = source_name or path.name
        self._load_image_into_workspace(loaded, f"已加载到编辑区: {label}", push_undo=push_undo)

    # ── 拖拽支持 ──────────────────────────────────────

    def _setup_dnd(self):
        if not DND_ENABLED or not hasattr(self.canvas, "drop_target_register"):
            self._dnd_ready = False
            return
        try:
            self.canvas.drop_target_register(DND_FILES)
            self.canvas.dnd_bind("<<Drop>>", self._on_drop)
            self._dnd_ready = True
        except Exception:
            self._dnd_ready = False

    def _on_drop(self, event):
        if not self._ensure_idle("拖入图片到编辑区"):
            return
        raw_paths = self.tk.splitlist(event.data)
        image_paths = []
        for raw_path in raw_paths:
            path = Path(raw_path)
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                image_paths.append(path)

        if not image_paths:
            messagebox.showwarning("拖入失败", "未找到可识别的图片文件")
            return

        # Use _add_image_to_strip for all files — it auto-assigns:
        # first file becomes primary (if none exists), rest become references.
        # This is consistent with upload/paste behavior.
        for path in image_paths:
            try:
                self._add_image_to_strip_path(path, source_name=f"拖入: {path.name}")
            except Exception as e:
                messagebox.showerror("拖入失败", f"{path.name}: {e}")

    # ── 提示词占位符 ──────────────────────────────

    def _on_prompt_focus_in(self, event):
        if self._prompt_has_placeholder:
            self.prompt_text.delete("1.0", "end")
            self._prompt_has_placeholder = False

    def _on_prompt_focus_out(self, event):
        if not self.prompt_text.get("1.0", "end").strip():
            self.prompt_text.insert("1.0", PLACEHOLDER_TEXT)
            self._prompt_has_placeholder = True

    # ── 配置持久化 ──────────────────────────────

    def _load_config(self):
        self._config_load_error = None
        config_needs_save = False
        if CONFIG_PATH.exists():
            try:
                cfg = json.loads(CONFIG_PATH.read_text("utf-8"))
                self.api_base_var.set(cfg.get("api_base", DEFAULT_API_BASE))
                model_options = self._apply_model_options(cfg.get("model_options"))
                if cfg.get("model_options") != model_options:
                    config_needs_save = True
                saved_model = self._normalize_model_id(cfg.get("model", DEFAULT_MODEL))
                if saved_model not in model_options:
                    saved_model = DEFAULT_MODEL if DEFAULT_MODEL in model_options else model_options[0]
                    config_needs_save = True
                self.model_var.set(saved_model)
                # Strip "Bearer " prefix from saved auth for display (user only enters password)
                saved_auth = cfg.get("auth", DEFAULT_AUTH)
                if saved_auth.lower().startswith("bearer "):
                    saved_auth = saved_auth[7:].strip()
                self.auth_var.set(saved_auth)
                saved_size = cfg.get("size", ORIGINAL_SIZE_ID)
                self.size_var.set(self._size_display_label(saved_size))
                # 格式：英文 API ID → 中文显示名
                saved_fmt = cfg.get("format", DEFAULT_FORMAT)
                self.format_var.set(FORMAT_DISPLAY_NAMES.get(saved_fmt, saved_fmt) if saved_fmt in FORMAT_DISPLAY_NAMES else saved_fmt)
                # 质量：英文 API ID → 中文显示名
                saved_quality = cfg.get("quality", DEFAULT_QUALITY)
                self.quality_var.set(self._quality_display_label(saved_quality))
                self.style_var.set(cfg.get("style", "无（原始）"))
                self.batch_var.set(cfg.get("batch", 1))
                self.compression_var.set(cfg.get("compression", DEFAULT_COMPRESSION))
            except Exception as e:
                self._config_load_error = str(e)
            else:
                if config_needs_save:
                    self._save_config()
        else:
            # No config.json exists yet (e.g. first run of EXE) — generate defaults
            self._apply_model_options(DEFAULT_MODEL_OPTIONS)
            self.model_var.set(DEFAULT_MODEL if DEFAULT_MODEL in self._model_options else self._model_options[0])
            self._save_config()

    def _save_config(self):
        resolved_size = self._resolve_output_size() or ORIGINAL_SIZE_ID
        cfg = {
            "api_base": self.api_base_var.get(),
            "model": self._resolve_model_api_id(),
            "model_options": list(getattr(self, "_model_options", DEFAULT_MODEL_OPTIONS)),
            "auth": self.auth_var.get(),
            "size": resolved_size,
            "format": self._resolve_format(),
            "quality": self._resolve_quality(),
            "style": self.style_var.get(),
            "batch": self.batch_var.get(),
            "compression": self.compression_var.get(),
        }
        try:
            _write_text_atomic(
                CONFIG_PATH,
                json.dumps(cfg, ensure_ascii=False, indent=2),
                "utf-8",
            )
        except Exception:
            pass

    def _setup_config_autosave(self):
        """Register trace callbacks on all config variables so changes are
        immediately saved to config.json.  Called AFTER _load_config() so
        the initial load doesn't trigger redundant saves."""
        # Debounce: use a single pending save ID so rapid changes (e.g. typing
        # in the api_base entry) only write once after the last change.
        self._config_save_after_id = None

        def _on_config_var_change(*args):
            if self._config_save_after_id is not None:
                self.after_cancel(self._config_save_after_id)
            self._config_save_after_id = self.after(500, self._save_config)

        for var in (
            self.api_base_var, self.model_var, self.auth_var,
            self.size_var, self.format_var, self.quality_var,
            self.style_var, self.batch_var, self.compression_var,
        ):
            var.trace_add("write", _on_config_var_change)

    def _show_startup_recovery_notice(self):
        notices = []
        if getattr(self, "_config_load_error", None):
            notices.append("配置文件读取失败，已保留当前界面默认值")
        if getattr(self.history_mgr, "load_error", None):
            notices.append("历史记录索引读取失败，已跳过损坏记录")
        if getattr(self.error_log, "load_error", None):
            notices.append("错误日志读取失败，已重建错误日志")
        if getattr(debug_log, "load_error", None):
            notices.append("调试日志读取失败，已重建调试日志")
        if not notices:
            return
        summary = "；".join(notices)
        self._set_status(summary)
        self.after(
            80,
            lambda: messagebox.showwarning(
                "启动恢复",
                "检测到部分本地文件损坏或格式不兼容，应用已自动回退到可继续使用的状态。\n\n"
                + "\n".join(f"• {item}" for item in notices),
            ),
        )

    def _on_close(self):
        """Handle window close — save config before destroying."""
        self._save_config()
        self.destroy()

    def _check_api_connectivity(self):
        """Verify API is reachable on startup and show status"""
        api_base = self.api_base_var.get()
        auth = self._get_auth_header()
        headers = {"Authorization": auth}

        def _safe_after(callback, *args):
            """Safely schedule callback — handles case where mainloop isn't running yet"""
            try:
                self.after(0, callback, *args)
            except RuntimeError:
                pass

        def _worker():
            try:
                models_url = api_base.rstrip("/") + "/models"
                resp = httpx.get(models_url, headers=headers, timeout=5.0)
                if resp.status_code == 200:
                    data = resp.json()
                    models = [m["id"] for m in data.get("data", []) if "id" in m]
                    _safe_after(self._on_api_check_ok, models)
                else:
                    _safe_after(self._on_api_check_fail,
                               f"HTTP {resp.status_code}")
            except httpx.ConnectError:
                _safe_after(self._on_api_check_fail,
                           f"无法连接到 {api_base}")
            except Exception as e:
                _safe_after(self._on_api_check_fail, str(e)[:100])

        threading.Thread(target=_worker, daemon=True).start()

    def _on_api_check_ok(self, models):
        current_model = self._resolve_model_api_id()
        if current_model in models:
            self._set_status(f"API 已连接 ✓ ({len(models)} 个模型可用)")
        else:
            self._set_status(f"API 已连接 ✓ 但当前模型 '{current_model}' 不在可用列表中")
            # Auto-fix: suggest first available model if current is invalid
            if models:
                self.status_label.config(fg=C["yellow"])

    def _on_api_check_fail(self, reason):
        api_base = self.api_base_var.get()
        self._set_status(f"API 连接失败: {reason}，请检查 API 地址和服务状态")
        self.status_label.config(fg=C["red"])
        self.error_log.add("API连接失败", reason, {
            "api_base": api_base,
            "model": self._resolve_model_api_id(),
        })
        self._update_error_badge()

    # ── 图片显示 ──────────────────────────────

    def _show_image(self, pil_image):
        self._preview_override_image = None
        self._preview_override_label = ""
        self.current_image = pil_image
        self._reset_canvas_view()
        self._draw_image_on_canvas()

    def _draw_image_on_canvas(self):
        img = self._preview_override_image or self.current_image
        if img is None:
            return
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw < 10 or ch < 10:
            return

        iw, ih = img.size

        # Base scale: fit to canvas (but don't upscale beyond 1.0)
        base_scale = min(cw / iw, ch / ih, 1.0)
        # Apply user zoom
        final_scale = base_scale * self._canvas_zoom
        new_w = max(1, int(iw * final_scale))
        new_h = max(1, int(ih * final_scale))

        # Limit to prevent memory issues
        max_dim = 8192
        if new_w > max_dim or new_h > max_dim:
            limit_scale = max_dim / max(new_w, new_h)
            new_w = max(1, int(new_w * limit_scale))
            new_h = max(1, int(new_h * limit_scale))

        resized = img.resize((new_w, new_h), Image.LANCZOS)
        self._main_photo = ImageTk.PhotoImage(resized)

        self.canvas.delete("all")
        x = cw // 2 + self._canvas_pan_x
        y = ch // 2 + self._canvas_pan_y
        self.canvas.create_image(x, y, image=self._main_photo, anchor="center")

        # Show zoom indicator when zoomed
        if self._canvas_zoom != 1.0:
            self.canvas.delete("zoom_indicator")
            pct = int(self._canvas_zoom * 100)
            self.canvas.create_text(cw - 8, ch - 8, text=f"{pct}%",
                                    anchor="se", fill=C["accent"],
                                    font=("Consolas", 10, "bold"),
                                    tags="zoom_indicator")

        # Show browse mode indicator when preview override is active
        self.canvas.delete("browse_indicator")
        if self._preview_override_image is not None:
            label = self._preview_override_label or "浏览模式"
            self.canvas.create_text(8, 8, text=f"[{label}]  按 Esc 返回编辑区",
                                    anchor="nw", fill="#FFD700",
                                    font=("Microsoft YaHei UI", 10, "bold"),
                                    tags="browse_indicator")

    def _on_canvas_resize(self, event):
        if self._resize_after_id:
            self.after_cancel(self._resize_after_id)
        if self._mask_mode:
            self._resize_after_id = self.after(100, self._draw_mask_overlay)
        else:
            self._resize_after_id = self.after(100, self._draw_image_on_canvas)

    # ── 撤销/重做 ──────────────────────────────

    def _push_undo(self):
        if self.current_b64:
            self._undo_stack.append(self.current_b64)
            if len(self._undo_stack) > 30:
                self._undo_stack = self._undo_stack[-30:]
            self._redo_stack.clear()

    def _clear_mask_history(self):
        self._mask_undo_stack.clear()
        self._mask_redo_stack.clear()

    def _clear_mask_session(self):
        self._mask_b64 = None
        self._mask_image = None
        self._mask_painting = False
        self._mask_last_canvas_pos = None
        self._clear_mask_history()

    def _discard_mask_session(self):
        if self._mask_mode:
            self._deactivate_mask_mode(preserve_mask=False)
        else:
            self._clear_mask_session()

    def _has_mask_session(self):
        return bool(
            self.current_image and (
                self._mask_mode
                or self._mask_has_content()
                or self._mask_undo_stack
                or self._mask_redo_stack
            )
        )

    def _snapshot_mask_state(self):
        if not self.current_image:
            return None
        mask_image = self._mask_image
        if mask_image is None or mask_image.size != self.current_image.size:
            mask_image = Image.new("RGBA", self.current_image.size, (0, 0, 0, 0))
        return ImageGenerator.image_to_b64(mask_image, fmt="PNG")

    def _restore_mask_state(self, mask_b64):
        if not self.current_image:
            return False
        if not mask_b64:
            self._mask_image = Image.new("RGBA", self.current_image.size, (0, 0, 0, 0))
        else:
            mask = ImageGenerator.b64_to_image(mask_b64).convert("RGBA")
            if mask.size != self.current_image.size:
                mask = mask.resize(self.current_image.size, Image.NEAREST)
            self._mask_image = mask
        self._mask_b64 = None
        self._mask_painting = False
        self._mask_last_canvas_pos = None
        return True

    def _push_mask_undo(self):
        snapshot = self._snapshot_mask_state()
        if snapshot is None:
            return
        self._mask_undo_stack.append(snapshot)
        if len(self._mask_undo_stack) > 30:
            self._mask_undo_stack = self._mask_undo_stack[-30:]
        self._mask_redo_stack.clear()

    def _on_mask_undo(self):
        if not self._mask_undo_stack:
            self._set_status("没有可撤销的蒙版操作")
            return
        current_snapshot = self._snapshot_mask_state()
        if current_snapshot is not None:
            self._mask_redo_stack.append(current_snapshot)
            if len(self._mask_redo_stack) > 30:
                self._mask_redo_stack = self._mask_redo_stack[-30:]
        snapshot = self._mask_undo_stack.pop()
        if self._restore_mask_state(snapshot):
            if self._mask_mode:
                self._draw_mask_overlay()
                self._set_status("已撤销蒙版")
            else:
                self._draw_image_on_canvas()
                self._set_status("已撤销蒙版；点「蒙版」可继续查看或修改")
            self._refresh_edit_action_state()

    def _on_mask_redo(self):
        if not self._mask_redo_stack:
            self._set_status("没有可重做的蒙版操作")
            return
        current_snapshot = self._snapshot_mask_state()
        if current_snapshot is not None:
            self._mask_undo_stack.append(current_snapshot)
            if len(self._mask_undo_stack) > 30:
                self._mask_undo_stack = self._mask_undo_stack[-30:]
        snapshot = self._mask_redo_stack.pop()
        if self._restore_mask_state(snapshot):
            if self._mask_mode:
                self._draw_mask_overlay()
                self._set_status("已重做蒙版")
            else:
                self._draw_image_on_canvas()
                self._set_status("已重做蒙版；点「蒙版」可继续查看或修改")
            self._refresh_edit_action_state()

    def _on_undo(self):
        if not self._ensure_idle("撤销"):
            return
        if self._has_mask_session():
            self._on_mask_undo()
            return
        if not self._undo_stack:
            self._set_status("没有可撤销的操作")
            return
        if self.current_b64:
            self._redo_stack.append(self.current_b64)
        b64 = self._undo_stack.pop()
        self.current_b64 = b64
        try:
            img = ImageGenerator.b64_to_image(b64)
            # Preserve current zoom/pan instead of resetting
            self.current_image = img
            self._preview_override_image = None
            self._preview_override_label = ""
            if self._mask_mode:
                self._draw_mask_overlay()
            else:
                self._draw_image_on_canvas()
            self._refresh_edit_action_state()
            self._set_status("已撤销")
        except Exception as e:
            self._set_status(f"撤销失败: {e}")

    def _on_redo(self):
        if not self._ensure_idle("重做"):
            return
        if self._has_mask_session():
            self._on_mask_redo()
            return
        if not self._redo_stack:
            self._set_status("没有可重做的操作")
            return
        if self.current_b64:
            self._undo_stack.append(self.current_b64)
        b64 = self._redo_stack.pop()
        self.current_b64 = b64
        try:
            img = ImageGenerator.b64_to_image(b64)
            # Preserve current zoom/pan instead of resetting
            self.current_image = img
            self._preview_override_image = None
            self._preview_override_label = ""
            if self._mask_mode:
                self._draw_mask_overlay()
            else:
                self._draw_image_on_canvas()
            self._refresh_edit_action_state()
            self._set_status("已重做")
        except Exception as e:
            self._set_status(f"重做失败: {e}")

    # ── 历史记录 ──────────────────────────────

    def _add_to_history(self, b64_str, prompt):
        try:
            img = ImageGenerator.b64_to_image(b64_str)
            HISTORY_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            safe_prompt = "".join(
                c for c in prompt[:30]
                if c.isalnum() or c in " _-" or '\u4e00' <= c <= '\u9fff'
                or '\u3000' <= c <= '\u303f' or '\uff00' <= c <= '\uffef'
            ).strip()
            if not safe_prompt:
                safe_prompt = "image"
            safe_prompt = safe_prompt.rstrip(". ")
            filename = f"{ts}_{safe_prompt}.png"
            img.save(HISTORY_DIR / filename, format="PNG")
            self.history_mgr.add(prompt, filename)
            self._refresh_history()
        except Exception:
            pass

    def _refresh_history(self):
        removed = self.history_mgr.cleanup_missing()
        if removed:
            self.thumb_cache.cleanup(
                [self.history_mgr.img_dir / rec["filename"] for rec in self.history_mgr.records]
            )
        for w in self.hist_inner.winfo_children():
            w.destroy()
        self._thumb_refs.clear()

        records = self.history_mgr.records
        valid_indices = set(range(len(records)))
        self._hist_selected.intersection_update(valid_indices)
        self._hist_selection_order = [
            idx for idx in self._hist_selection_order if idx in self._hist_selected
        ]
        for i in range(len(records) - 1, -1, -1):
            rec = records[i]
            img_path = self.history_mgr.get_image_path(i)
            if not img_path or not img_path.exists():
                continue
            try:
                thumb = self.thumb_cache.get_thumbnail(img_path)
                if thumb is None:
                    continue
                photo = ImageTk.PhotoImage(thumb)
                self._thumb_refs.append(photo)

                is_selected = i in self._hist_selected
                border_color = C["accent"] if is_selected else C["border"]
                border_width = 2 if is_selected else 1

                frame = tk.Frame(self.hist_inner, bg=C["surface2"], bd=0,
                                 highlightbackground=border_color, highlightthickness=border_width,
                                 cursor="hand2")
                frame.pack(side="left", padx=2, pady=2)

                def bind_hist_widget(widget, hist_idx=i):
                    widget.bind("<Button-1>", lambda e, idx=hist_idx: self._on_hist_click(idx, e))
                    widget.bind("<Button-3>", lambda e, idx=hist_idx: self._on_hist_right_click(e, idx))
                    widget.bind("<Double-Button-1>", lambda e, idx=hist_idx: self._on_hist_dblclick(idx))

                lbl = tk.Label(frame, image=photo, bg=C["surface2"], cursor="hand2")
                lbl.pack(padx=2, pady=(2, 0))
                bind_hist_widget(frame)
                bind_hist_widget(lbl)

                short = rec["prompt"][:8] + ".." if len(rec["prompt"]) > 8 else rec["prompt"]
                text_lbl = tk.Label(frame, text=short, bg=C["surface2"], fg=C["text_dim"],
                                    font=("Microsoft YaHei UI", 7), cursor="hand2")
                text_lbl.pack(padx=2, pady=(0, 2))
                bind_hist_widget(text_lbl)
            except Exception:
                continue

    def _cancel_pending_history_click(self):
        if self._hist_click_after_id is None:
            return
        try:
            self.after_cancel(self._hist_click_after_id)
        except Exception:
            pass
        self._hist_click_after_id = None

    def _clear_hist_selection(self):
        self._hist_selected.clear()
        self._hist_selection_order.clear()

    def _clear_hist_selection_ui(self):
        self._clear_hist_selection()
        self._refresh_history()
        self._update_hist_add_ref_btn()

    def _remember_hist_selection(self, idx):
        if idx in self._hist_selection_order:
            self._hist_selection_order.remove(idx)
        self._hist_selection_order.append(idx)

    def _forget_hist_selection(self, idx):
        if idx in self._hist_selection_order:
            self._hist_selection_order.remove(idx)

    def _get_hist_selected_ordered(self):
        ordered = [idx for idx in self._hist_selection_order if idx in self._hist_selected]
        for idx in sorted(self._hist_selected):
            if idx not in ordered:
                ordered.append(idx)
        return ordered

    def _commit_hist_single_click(self, idx):
        self._hist_click_after_id = None
        if self._hist_selected:
            self._clear_hist_selection()
            self._refresh_history()
            self._update_hist_add_ref_btn()
        self._preview_from_history(idx)

    def _on_hist_click(self, idx, event):
        if event.state & 0x4:  # Ctrl held
            self._cancel_pending_history_click()
            if idx in self._hist_selected:
                self._hist_selected.discard(idx)
                self._forget_hist_selection(idx)
            else:
                self._hist_selected.add(idx)
                self._remember_hist_selection(idx)
            self._refresh_history()
            self._update_hist_add_ref_btn()
        else:
            self._cancel_pending_history_click()
            self._hist_click_after_id = self.after(220, lambda hist_idx=idx: self._commit_hist_single_click(hist_idx))

    def _preview_from_history(self, idx):
        """Single-click history: browse only, does NOT set current_b64"""
        rec = self.history_mgr.get_record(idx)
        img_path = self.history_mgr.get_image_path(idx)
        if not rec or not img_path or not img_path.exists():
            return
        try:
            with Image.open(img_path) as img:
                loaded = self._normalize_image(img)
            self._preview_override_image = loaded
            self._preview_override_label = "仅浏览: 历史图"
            self._refresh_edit_action_state()
            self._reset_canvas_view()
            self._draw_image_on_canvas()
            ts = rec.get('timestamp', '')
            self._set_status(f"仅浏览历史图 ({ts}) — 双击加入编辑区")
        except Exception as e:
            self._set_status(f"预览失败: {e}")

    def _update_hist_add_ref_btn(self):
        if self._hist_selected:
            n = len(self._hist_selected)
            self._hist_add_ref_btn.config(text=f"加入编辑区({n})")
            self._hist_add_ref_btn.pack(side="left", padx=4, pady=4)
        else:
            self._hist_add_ref_btn.pack_forget()

    def _on_add_hist_selected_to_strip(self):
        """Add selected history items to the edit strip."""
        if not self._ensure_idle("将历史图片加入编辑区"):
            return
        selected_indices = self._get_hist_selected_ordered()
        if not selected_indices:
            return
        had_workspace = self._has_workspace_image()
        added = 0
        for idx in selected_indices:
            rec = self.history_mgr.get_record(idx)
            img_path = self.history_mgr.get_image_path(idx)
            if not rec or not img_path or not img_path.exists():
                continue
            try:
                self._add_image_to_strip_path(img_path, f"历史: {rec.get('prompt', '')[:12]}")
                added += 1
            except Exception:
                pass
        self._clear_hist_selection_ui()
        if added:
            if not had_workspace and added > 1:
                self._set_status(f"已将首个选中历史图设为主图，并添加其余 {added - 1} 张为参考图")
            elif not had_workspace and added == 1:
                self._set_status("已将选中历史图设为主图")
            else:
                self._set_status(f"已添加 {added} 张历史图片到编辑区")

    def _delete_selected_history(self):
        selected_indices = self._get_hist_selected_ordered()
        total = len(selected_indices)
        if total == 0:
            return
        delete_files = messagebox.askyesnocancel(
            "删除选中历史记录",
            f"确定要删除选中的 {total} 条历史记录吗？\n\n"
            "是：删除历史记录，并删除磁盘上的图片文件\n"
            "否：只从历史记录中移除，保留图片文件\n"
            "取消：不执行"
        )
        if delete_files is None:
            return

        removed = 0
        deleted_files = 0
        for idx in sorted(set(selected_indices), reverse=True):
            img_path = self.history_mgr.get_image_path(idx)
            if img_path is not None:
                self.thumb_cache.remove(img_path)
            file_deleted = self.history_mgr.delete(idx, delete_file=delete_files)
            removed += 1
            if file_deleted:
                deleted_files += 1

        self._clear_hist_selection_ui()
        if delete_files:
            self._set_status(f"已删除 {removed} 条历史记录，并删除 {deleted_files} 个图片文件")
        else:
            self._set_status(f"已删除 {removed} 条历史记录，图片文件已保留")

    def _load_from_history(self, idx):
        if not self._ensure_idle("将历史图片设为主图"):
            return
        rec = self.history_mgr.get_record(idx)
        img_path = self.history_mgr.get_image_path(idx)
        if not rec or not img_path or not img_path.exists():
            return
        try:
            with Image.open(img_path) as img:
                loaded = self._normalize_image(img)
            self._load_image_into_workspace(
                loaded,
                f"已将历史图设为编辑区图片 ({rec.get('timestamp', '')})",
                push_undo=True,
            )
        except Exception as e:
            messagebox.showerror("加载失败", str(e))

    def _delete_from_history(self, idx):
        delete_files = messagebox.askyesnocancel(
            "删除历史记录",
            "确定要删除这条历史记录吗？\n\n"
            "是：删除历史记录，并删除磁盘上的图片文件\n"
            "否：只从历史记录中移除，保留图片文件\n"
            "取消：不执行"
        )
        if delete_files is None:
            return
        img_path = self.history_mgr.get_image_path(idx)
        if img_path is not None:
            self.thumb_cache.remove(img_path)
        file_deleted = self.history_mgr.delete(idx, delete_file=delete_files)
        self._clear_hist_selection()
        self._refresh_history()
        self._update_hist_add_ref_btn()
        if delete_files:
            if file_deleted:
                self._set_status("已删除历史记录，并删除对应图片文件")
            else:
                self._set_status("已删除历史记录；对应图片文件不存在或删除失败")
        else:
            self._set_status("已从历史记录中移除，图片文件已保留")

    def _on_clear_all_history(self):
        n = len(self.history_mgr.records)
        if n == 0:
            self._set_status("历史记录为空，无需清空")
            return
        delete_files = messagebox.askyesnocancel(
            "清空历史记录",
            f"确定要清空全部 {n} 条历史记录吗？\n\n"
            "是：清空历史记录，并删除磁盘上的历史图片文件\n"
            "否：只清空历史记录，保留图片文件\n"
            "取消：不执行"
        )
        if delete_files is None:
            return
        # Clear thumbnail cache
        for i in range(n):
            img_path = self.history_mgr.get_image_path(i)
            if img_path is not None:
                self.thumb_cache.remove(img_path)
        count, deleted_files = self.history_mgr.clear_all(delete_files=delete_files)
        self._clear_hist_selection()
        self._refresh_history()
        self._update_hist_add_ref_btn()
        if delete_files:
            self._set_status(f"已清空 {count} 条历史记录，并删除 {deleted_files} 个图片文件")
        else:
            self._set_status(f"已清空 {count} 条历史记录，图片文件已保留")

    # ── 生成操作 ──────────────────────────────

    def _get_raw_prompt(self):
        text = self.prompt_text.get("1.0", "end").strip()
        if self._prompt_has_placeholder or not text:
            return None
        return text

    def _get_prompt_with_style(self):
        text = self._get_raw_prompt()
        if text is None:
            return None
        style_suffix = STYLE_PRESETS.get(self.style_var.get(), "")
        if style_suffix:
            text += style_suffix
        return text

    def _get_auth_header(self):
        """Get full Authorization header value, auto-prepending 'Bearer ' if needed"""
        val = self.auth_var.get().strip()
        if not val:
            return ""
        if val.lower().startswith("bearer "):
            return val
        return f"Bearer {val}"

    def _normalize_model_id(self, model):
        model = str(model or "").strip()
        return MODEL_TYPO_FIXES.get(model, model)

    def _normalize_model_options(self, options):
        normalized = []
        seen = set()
        if isinstance(options, (list, tuple)):
            for item in options:
                model = self._normalize_model_id(item)
                if not model or model in seen:
                    continue
                normalized.append(model)
                seen.add(model)
        if not normalized:
            normalized = list(DEFAULT_MODEL_OPTIONS)
        return normalized

    def _apply_model_options(self, options):
        self._model_options = self._normalize_model_options(options)
        if hasattr(self, "model_combo"):
            self.model_combo.configure(values=self._model_options)
        return list(self._model_options)

    def _resolve_model_api_id(self):
        """Convert the model selector value to the API model ID."""
        display = self._normalize_model_id(self.model_var.get())
        model_options = list(getattr(self, "_model_options", DEFAULT_MODEL_OPTIONS))
        api_id = MODEL_API_IDS.get(display)
        if api_id and api_id in model_options:
            return api_id
        if display in model_options:
            return display
        if DEFAULT_MODEL in model_options:
            return DEFAULT_MODEL
        return model_options[0]

    @staticmethod
    def _is_original_size_value(value):
        lower = str(value or "").strip().lower()
        return lower in {
            ORIGINAL_SIZE_ID,
            ORIGINAL_SIZE_LABEL.lower(),
            "原图尺寸",
            "按主图原尺寸",
            "source",
            "native",
        }

    @staticmethod
    def _size_display_label(value):
        if App._is_original_size_value(value):
            return ORIGINAL_SIZE_LABEL
        parsed = ImageGenerator._parse_size_tuple(value)
        if parsed:
            return ImageGenerator._format_size_tuple(parsed)
        normalized = ImageGenerator._normalize_edit_size(value)
        if normalized != "auto":
            return normalized
        return ORIGINAL_SIZE_LABEL

    def _on_size_input_commit(self, event=None):
        display = str(self.size_var.get() or "").strip()
        if not display:
            self.size_var.set(ORIGINAL_SIZE_LABEL)
            return
        if self._is_original_size_value(display):
            self.size_var.set(ORIGINAL_SIZE_LABEL)
            return
        parsed = ImageGenerator._parse_size_tuple(display)
        if parsed:
            self.size_var.set(ImageGenerator._format_size_tuple(parsed))
            return
        normalized = ImageGenerator._normalize_size_text(display).lower()
        if normalized in EDIT_API_SIZES:
            self.size_var.set(normalized)

    def _resolve_output_size(self):
        display = str(self.size_var.get() or "").strip()
        if not display:
            return ORIGINAL_SIZE_ID
        if self._is_original_size_value(display):
            return ORIGINAL_SIZE_ID
        parsed = ImageGenerator._parse_size_tuple(display)
        if parsed:
            return ImageGenerator._format_size_tuple(parsed)
        normalized = ImageGenerator._normalize_size_text(display).lower()
        if normalized in EDIT_API_SIZES:
            return normalized
        return None

    @staticmethod
    def _normalize_quality_tier(value):
        mapped = QUALITY_API_IDS.get(str(value or "").strip(), value)
        normalized = ImageGenerator._normalize_image_quality(mapped, allow_auto=True)
        return normalized or DEFAULT_QUALITY

    def _resolve_quality(self):
        """将界面质量显示名转换为内部处理尺寸档位。"""
        return self._normalize_quality_tier(self.quality_var.get())

    @staticmethod
    def _resolve_api_quality(quality_tier=None):
        normalized = App._normalize_quality_tier(quality_tier)
        return None if normalized == "auto" else normalized

    @staticmethod
    def _quality_display_label(value):
        normalized = App._normalize_quality_tier(value)
        if normalized in QUALITY_DISPLAY_NAMES:
            return QUALITY_DISPLAY_NAMES[normalized]
        return DEFAULT_QUALITY_DISPLAY

    def _resolve_format(self):
        """将中文显示名转换为英文 API format 值"""
        display = self.format_var.get()
        api_id = FORMAT_API_IDS.get(display)
        if api_id:
            return api_id
        if display in FORMAT_DISPLAY_NAMES:
            return display
        return display

    @staticmethod
    def _get_b64_image_size(image_b64):
        if not image_b64:
            return None
        try:
            return ImageGenerator.b64_to_image(image_b64).size
        except Exception:
            return None

    @staticmethod
    def _size_pixel_count(size):
        parsed = ImageGenerator._parse_size_tuple(size)
        if not parsed:
            return 0
        return parsed[0] * parsed[1]

    @classmethod
    def _is_valid_processing_size_tuple(cls, size, min_area=655360, max_area=8294400, max_dim=3840):
        parsed = ImageGenerator._parse_size_tuple(size)
        if not parsed:
            return False
        width, height = parsed
        if width <= 0 or height <= 0:
            return False
        if width % 16 != 0 or height % 16 != 0:
            return False
        if max(width, height) > max_dim:
            return False
        ratio = max(width, height) / float(min(width, height))
        if ratio > 3:
            return False
        area = width * height
        return min_area <= area <= max_area

    @staticmethod
    def _aspect_ratio_error(candidate_size, reference_size):
        candidate = ImageGenerator._parse_size_tuple(candidate_size)
        reference = ImageGenerator._parse_size_tuple(reference_size)
        if not candidate or not reference:
            return float("inf")
        candidate_ratio = candidate[0] / float(candidate[1])
        reference_ratio = reference[0] / float(reference[1])
        if candidate_ratio <= 0 or reference_ratio <= 0:
            return float("inf")
        return abs(math.log(candidate_ratio / reference_ratio))

    @classmethod
    def _build_exact_ratio_processing_candidates(cls, output_size):
        parsed = ImageGenerator._parse_size_tuple(output_size)
        if not parsed:
            parsed = ImageGenerator._parse_size_tuple(DEFAULT_SIZE) or (1024, 1024)
        base_w, base_h = parsed
        divisor = math.gcd(base_w, base_h) or 1
        ratio_w = max(1, base_w // divisor)
        ratio_h = max(1, base_h // divisor)
        width_factor = 16 // math.gcd(ratio_w, 16)
        height_factor = 16 // math.gcd(ratio_h, 16)
        scale_unit = math.lcm(width_factor, height_factor)
        unit_w = ratio_w * scale_unit
        unit_h = ratio_h * scale_unit
        if max(unit_w, unit_h) > 3840:
            return []

        unit_area = unit_w * unit_h
        max_by_dim = min(3840 // unit_w, 3840 // unit_h)
        max_by_area = int(math.floor(math.sqrt(8294400.0 / float(unit_area))))
        min_by_area = int(math.ceil(math.sqrt(655360.0 / float(unit_area))))
        start = max(1, min_by_area)
        end = min(max_by_dim, max_by_area)
        if end < start:
            return []

        candidates = []
        for factor in range(start, end + 1):
            candidate = (unit_w * factor, unit_h * factor)
            if cls._is_valid_processing_size_tuple(candidate):
                candidates.append(candidate)
        return candidates

    @classmethod
    def _build_near_ratio_processing_candidates(cls, output_size):
        parsed = ImageGenerator._parse_size_tuple(output_size)
        if not parsed:
            parsed = ImageGenerator._parse_size_tuple(DEFAULT_SIZE) or (1024, 1024)
        base_w, base_h = parsed
        landscape = base_w >= base_h
        ratio = base_w / float(base_h)
        candidates = {}

        for long_edge in range(16, 3841, 16):
            if landscape:
                short_edge = max(16, int(round((long_edge / ratio) / 16.0)) * 16)
                candidate = (long_edge, short_edge)
            else:
                short_edge = max(16, int(round((long_edge * ratio) / 16.0)) * 16)
                candidate = (short_edge, long_edge)
            if cls._is_valid_processing_size_tuple(candidate):
                candidates[candidate] = candidate

        return sorted(
            candidates.values(),
            key=lambda s: (
                round(cls._aspect_ratio_error(s, parsed), 12),
                s[0] * s[1],
                max(s),
                s[0],
                s[1],
            ),
        )

    @classmethod
    def _build_processing_size_candidates(cls, output_size):
        exact_candidates = cls._build_exact_ratio_processing_candidates(output_size)
        near_candidates = cls._build_near_ratio_processing_candidates(output_size)
        ordered = []
        seen = set()
        for candidate in exact_candidates:
            if candidate not in seen:
                ordered.append(candidate)
                seen.add(candidate)
        for candidate in near_candidates:
            if candidate not in seen:
                ordered.append(candidate)
                seen.add(candidate)
        if ordered:
            return ordered
        fallback = ImageGenerator._coerce_size_tuple(*(ImageGenerator._parse_size_tuple(output_size) or (1024, 1024)))
        return [fallback]

    @classmethod
    def _pick_processing_candidate_from_pool(cls, pool, output_tuple, tier, avoid=None):
        if not pool:
            return None
        output_pixels = cls._size_pixel_count(output_tuple)

        def area_of(size):
            return cls._size_pixel_count(size)
        ratio_bands = {}
        for candidate in pool:
            error_key = round(cls._aspect_ratio_error(candidate, output_tuple), 12)
            ratio_bands.setdefault(error_key, []).append(candidate)

        for error_key in sorted(ratio_bands):
            ordered = sorted(
                ratio_bands[error_key],
                key=lambda s: (area_of(s), max(s), s[0], s[1]),
            )
            lower = [size for size in ordered if area_of(size) < output_pixels]
            at_or_above = [size for size in ordered if area_of(size) >= output_pixels]
            above = [
                size for size in ordered
                if area_of(size) > output_pixels or (area_of(size) == output_pixels and size != output_tuple)
            ]

            if tier == "low":
                candidate = lower[-1] if lower else (at_or_above[0] if at_or_above else ordered[0])
                if avoid is None or candidate != avoid:
                    return candidate
                continue

            if tier == "medium":
                band_candidates = above or at_or_above or ordered
            elif tier == "high":
                band_candidates = list(reversed(ordered))
            else:
                band_candidates = at_or_above or ordered

            for candidate in band_candidates:
                if avoid is None or candidate != avoid:
                    return candidate
        return None

    @classmethod
    def _select_processing_size_for_quality(cls, output_size, quality_tier):
        output_tuple = ImageGenerator._parse_size_tuple(output_size) or ImageGenerator._parse_size_tuple(DEFAULT_SIZE) or (1024, 1024)
        exact_candidates = cls._build_exact_ratio_processing_candidates(output_tuple)
        near_candidates = [
            candidate for candidate in cls._build_near_ratio_processing_candidates(output_tuple)
            if candidate not in set(exact_candidates)
        ]
        candidates = cls._build_processing_size_candidates(output_tuple)
        if not candidates:
            return ImageGenerator._format_size_tuple(output_tuple), [ImageGenerator._format_size_tuple(output_tuple)]
        normalized_tier = cls._normalize_quality_tier(quality_tier)

        low_selected = cls._pick_processing_candidate_from_pool(exact_candidates, output_tuple, "low")
        if low_selected is None:
            low_selected = cls._pick_processing_candidate_from_pool(near_candidates, output_tuple, "low")

        medium_selected = cls._pick_processing_candidate_from_pool(exact_candidates, output_tuple, "medium", avoid=low_selected)
        if medium_selected is None or medium_selected == low_selected:
            fallback_medium = cls._pick_processing_candidate_from_pool(near_candidates, output_tuple, "medium", avoid=low_selected)
            if fallback_medium is not None:
                medium_selected = fallback_medium
        if medium_selected is None:
            medium_selected = low_selected or cls._pick_processing_candidate_from_pool(candidates, output_tuple, "medium")

        high_selected = cls._pick_processing_candidate_from_pool(exact_candidates, output_tuple, "high")
        if high_selected is None:
            high_selected = cls._pick_processing_candidate_from_pool(near_candidates, output_tuple, "high")
        if high_selected is None:
            high_selected = candidates[-1]

        auto_selected = cls._pick_processing_candidate_from_pool(exact_candidates, output_tuple, "auto")
        if auto_selected is None:
            auto_selected = cls._pick_processing_candidate_from_pool(near_candidates, output_tuple, "auto")
        if auto_selected is None:
            auto_selected = medium_selected or high_selected

        if normalized_tier == "low":
            selected = low_selected or candidates[0]
        elif normalized_tier == "medium":
            selected = medium_selected or low_selected or candidates[-1]
        elif normalized_tier == "high":
            selected = high_selected or candidates[-1]
        else:
            selected = auto_selected or candidates[-1]

        return (
            ImageGenerator._format_size_tuple(selected),
            [ImageGenerator._format_size_tuple(size) for size in candidates],
        )

    def _build_edit_size_plan(self, requested_output_size, images_b64, quality_tier=None):
        source_sizes = [size for size in (self._get_b64_image_size(b64) for b64 in (images_b64 or [])) if size]
        primary_size = source_sizes[0] if source_sizes else ImageGenerator._parse_size_tuple(DEFAULT_SIZE) or (1024, 1024)

        if self._is_original_size_value(requested_output_size):
            output_size = primary_size
            output_mode = ORIGINAL_SIZE_ID
        else:
            output_size = ImageGenerator._parse_size_tuple(requested_output_size) or primary_size
            output_mode = "explicit"

        normalized_quality = self._normalize_quality_tier(quality_tier)
        output_size_text = ImageGenerator._format_size_tuple(output_size)
        output_size_is_valid = self._is_valid_processing_size_tuple(output_size)

        if output_size_is_valid:
            processing_size = output_size_text
            processing_tuple = output_size
            processing_candidates = [output_size_text]
            processing_strategy = "direct_request"
        else:
            processing_size, processing_candidates = self._select_processing_size_for_quality(output_size, normalized_quality)
            processing_tuple = ImageGenerator._parse_size_tuple(processing_size) or output_size
            processing_strategy = "scaled_fallback"

        return {
            "output_mode": output_mode,
            "output_size": output_size,
            "output_size_text": output_size_text,
            "processing_size": processing_size,
            "processing_size_tuple": processing_tuple,
            "source_sizes": source_sizes,
            "source_pixel_budget": max(
                [size[0] * size[1] for size in source_sizes] + [output_size[0] * output_size[1], 655360]
            ),
            "quality_tier": normalized_quality,
            "processing_candidates": processing_candidates,
            "processing_strategy": processing_strategy,
            "requested_size_is_valid": output_size_is_valid,
            "restores_to_output_size": processing_tuple != output_size,
        }

    def _prepare_result_for_output(self, b64, target_size=None):
        img = ImageGenerator.b64_to_image(b64)
        target_tuple = ImageGenerator._parse_size_tuple(target_size)
        if target_tuple and img.size != target_tuple:
            src_pixels = img.size[0] * img.size[1]
            dst_pixels = target_tuple[0] * target_tuple[1]
            img = img.resize(target_tuple, Image.LANCZOS)
            # Apply Unsharp Mask after downscaling to compensate for
            # the softening that LANCZOS downsampling inherently causes.
            # Only apply when actually downscaling (dst < src).
            if dst_pixels < src_pixels:
                img = img.filter(ImageFilter.UnsharpMask(
                    radius=2, percent=150, threshold=3
                ))
            b64 = ImageGenerator.image_to_b64(img)
        return b64, img

    def _create_generator(self):
        self._save_config()
        gen = ImageGenerator(
            api_base=self.api_base_var.get(),
            model=self._resolve_model_api_id(),
            auth_token=self._get_auth_header(),
        )
        self._active_generator = gen
        self._active_generators.append(gen)
        return gen

    def _set_busy_toolbar_state(self, enabled):
        for btn in getattr(self, "_busy_toolbar_buttons", []):
            try:
                btn.set_enabled(enabled)
            except Exception:
                pass

    def _ensure_idle(self, action_text):
        if not self.is_generating:
            return True
        self._set_status(f"当前任务仍在进行中，请等待完成后再{action_text}")
        return False

    @staticmethod
    def _is_auto_followup_label(label):
        label = str(label or "")
        return label.startswith("正在应用风格:") or label.startswith("正在自动换背:")

    @staticmethod
    def _is_base_job_label(label):
        label = str(label or "")
        return (
            label.startswith("正在生成图片")
            or label.startswith("正在编辑图片")
            or label.startswith("正在合成图片")
            or label.startswith("正在批量生成")
            or label.startswith("正在批量编辑")
            or label.startswith("正在批量合成")
            or label.startswith("回退生成中")
        )

    def _start_next_followup_job(self):
        while self._pending_followups:
            followup = self._pending_followups.pop(0)
            if followup == "bg" and getattr(self, "_bg_replace_active", False):
                self.after(0, self._do_auto_bg_replace)
                return True
            if followup == "style" and getattr(self, "_style_transfer_active", False):
                self.after(0, self._do_auto_style_transfer)
                return True
        self._pending_followups = []
        return False

    def _continue_followup_chain(self, current_job_label):
        if self._is_auto_followup_label(current_job_label):
            self._start_next_followup_job()
            return
        if not self._is_base_job_label(current_job_label):
            self._pending_followups = []
            return
        pending = []
        if getattr(self, "_bg_replace_active", False):
            pending.append("bg")
        if getattr(self, "_style_transfer_active", False):
            pending.append("style")
        self._pending_followups = pending
        self._start_next_followup_job()

    def _set_status(self, msg):
        self.status_label.config(text=msg)

    def _set_generating(self, active):
        self.is_generating = active
        if active:
            self._set_busy_toolbar_state(False)
            self.gen_btn.config(state="disabled", bg=C["surface2"])
            if hasattr(self, "gen_tb_btn"):
                self.gen_tb_btn.set_enabled(False)
            self.stop_btn.pack(side="left", padx=1, pady=4, before=self._edit_sep)
        else:
            self._cancel_progress_timer()
            self._set_busy_toolbar_state(True)
            self.stop_btn.pack_forget()
            self._active_generator = None
            self._active_generators = []
            self._active_result_target_size = None
            self._active_processing_size = None
            self._batch_result_target_size = None
            self._batch_request_size = None
            self._batch_api_quality = None
            self._refresh_edit_action_state()

    def _begin_stream_job(self, status_msg):
        self._batch_token += 1
        self._job_token += 1
        token = self._job_token
        started_at = time.time()
        self._current_job_label = status_msg.replace("...", "").replace("…", "").strip()
        self._set_generating(True)
        self.partial_count = 0
        self.start_time = started_at
        self.progress_var.set(0)
        self._set_status(status_msg)
        # Start a periodic timer to show the user the task is still running
        self._cancel_progress_timer()
        self._progress_timer_id = self.after(3000, self._progress_heartbeat, token, started_at)
        return token, started_at

    def _cancel_progress_timer(self):
        """Cancel the periodic progress heartbeat timer if active."""
        if getattr(self, "_progress_timer_id", None) is not None:
            self.after_cancel(self._progress_timer_id)
            self._progress_timer_id = None

    def _progress_heartbeat(self, token, started_at):
        """Periodically update the status bar to show the task is still running.
        Shows elapsed time so the user knows the app hasn't frozen."""
        if token != self._job_token or not self.is_generating:
            return
        elapsed = time.time() - started_at
        label = getattr(self, "_current_job_label", "") or "处理中"
        self._set_status(f"{label}... 已等待 {elapsed:.0f}s")
        # Schedule next heartbeat
        self._progress_timer_id = self.after(3000, self._progress_heartbeat, token, started_at)

    def _build_stream_callbacks(self, token, started_at):
        def on_partial(b64, count):
            elapsed = time.time() - started_at
            self.after(0, self._update_partial, token, b64, count, elapsed)

        def on_done(b64, count, revised_prompt=None, response_id=None):
            elapsed = time.time() - started_at
            self.after(0, self._update_done, token, b64, count, elapsed, revised_prompt, response_id)

        def on_error(msg):
            self.after(0, self._update_error, token, msg)

        return on_partial, on_done, on_error

    def _on_batch_spin(self):
        try:
            val = self.batch_var.get()
            if val < 1:
                self.batch_var.set(1)
            elif val > 10:
                self.batch_var.set(10)
        except Exception:
            self.batch_var.set(1)

    # ── 参考图片管理 ──────────────────────────────────────

    def _add_ref_image(self, pil_image, source_name=""):
        img = self._normalize_image(pil_image)
        b64 = ImageGenerator.image_to_b64(img)
        self._ref_images.append({"b64": b64, "image": img, "name": source_name})
        self._refresh_edit_action_state()
        self._set_status(f"已添加参考图片: {source_name}" if source_name else "已添加参考图片")

    def _add_ref_image_path(self, path, source_name=None):
        path = Path(path)
        with Image.open(path) as img:
            loaded = self._normalize_image(img)
        label = source_name or path.name
        self._add_ref_image(loaded, label)

    def _remove_ref_image(self, idx):
        if not self._ensure_idle("移除参考图片"):
            return
        if 0 <= idx < len(self._ref_images):
            self._ref_images.pop(idx)
            self._ref_selected.clear()
            self._refresh_edit_action_state()
            self._set_status("已移除参考图片")

    def _on_clear_ref_images(self):
        if not self._ensure_idle("清空参考图"):
            return
        if not self._ref_images:
            return
        self._ref_images.clear()
        self._ref_selected.clear()
        self._refresh_edit_action_state()
        self._set_status("已清空参考图片")

    def _on_add_ref_current(self):
        if not self._ensure_idle("添加当前显示图为参考图"):
            return
        img = self._get_display_image()
        b64 = self._get_display_b64()
        if img is None or not b64:
            messagebox.showwarning("参考图", "当前画布没有图片，无法添加为参考图")
            return
        self._ref_images.append({
            "b64": b64,
            "image": img.copy(),
            "name": "当前显示图" if self._preview_override_image is not None else "编辑区图片",
        })
        self._refresh_edit_action_state()
        self._set_status("已将当前显示图添加为参考图（可辅助编辑或合成）")

    def _toggle_ref_select(self, idx):
        if idx in self._ref_selected:
            self._ref_selected.discard(idx)
        else:
            self._ref_selected.add(idx)
        self._refresh_edit_action_state()
        self._preview_ref_image(idx)

    def _preview_ref_image(self, idx):
        if not (0 <= idx < len(self._ref_images)):
            return
        ref = self._ref_images[idx]
        self._preview_override_image = ref["image"].copy()
        self._preview_override_label = "仅浏览: 参考图"
        self._refresh_edit_action_state()
        self._reset_canvas_view()
        self._draw_image_on_canvas()
        name = ref.get("name") or f"参考图片 {idx + 1}"
        self._set_status(f"仅浏览参考图: {name}")

    def _update_edit_selected_btn(self):
        """Update edit strip and smart button after ref selection changes."""
        # Selection affects smart button mode (compose vs edit)
        self._refresh_edit_action_state()

    def _on_edit_selected_refs(self):
        """Compose selected reference images into one. Delegates to the smart button pipeline.

        This is now equivalent to clicking the smart button when refs are selected
        but no workspace image exists — the button auto-switches to "合成" mode.
        """
        if not self._ref_selected:
            messagebox.showwarning("参考图", "请先在下方编辑条中点击选择一张参考图片")
            return
        # Simply trigger the smart button — it will detect the selection
        # and automatically use compose mode
        self._on_generate()

    def _get_all_edit_b64_list(self):
        """Get combined image b64 list: current workspace image + reference images.
        Note: browsing history/ref via preview override does NOT affect this list.
        The workspace image is always placed FIRST (it is the primary edit target)."""
        b64_list = []
        if self.current_b64:
            b64_list.append(self.current_b64)
        b64_list.extend(r["b64"] for r in self._ref_images)
        return self._dedupe_b64_list(b64_list)

    def _collect_input_images(self, mode="auto"):
        """Unified input collection for all edit/generate operations.

        Args:
            mode: "auto" - workspace + refs (standard edit)
                  "refs_only" - only selected ref images (edit refs without workspace)
                  "generate" - no images (text-to-image)

        Returns:
            list of b64 strings, or None if the operation should be aborted.
            Empty list means "generate from text only".
        """
        if mode == "refs_only":
            if not self._ref_selected:
                messagebox.showwarning("参考图", "请先在下方编辑条中点击选择一张参考图片")
                return None
            selected_b64 = [self._ref_images[i]["b64"]
                           for i in sorted(self._ref_selected)
                           if i < len(self._ref_images)]
            selected_b64 = self._dedupe_b64_list(selected_b64)
            if not selected_b64:
                return None
            return selected_b64

        if mode == "generate":
            return []

        # mode == "auto": workspace + refs
        return self._get_all_edit_b64_list()

    def _submit_edit_job(self, prompt, images_b64, *,
                         mask_b64=None, status_msg=None, compare_label="编辑前输入图",
                         output_format=None):
        """Unified edit/generate pipeline. Single entry point for ALL image operations.

        - images_b64 empty → text-to-image (generate_stream)
        - images_b64 has 1 item → single-image edit (edit_stream)
        - images_b64 has N items → multi-image edit (edit_stream_multi)

        All results go to the workspace. All operations support batch.
        """
        if not prompt:
            messagebox.showwarning("提示词", "请先输入描述文字（用于 AI 生成或编辑图片）")
            return

        if not self._is_auto_followup_label(status_msg):
            self._pending_followups = []

        self._clear_preview_override()
        self._hide_edit_result_bar()  # Dismiss any previous edit result notification

        batch_count = 1
        try:
            batch_count = max(1, min(10, self.batch_var.get()))
        except Exception:
            batch_count = 1

        # Build status message
        if status_msg is None:
            n = len(images_b64)
            has_ws = bool(self.current_b64) and self.current_b64 in images_b64
            if n == 0:
                status_msg = "正在生成图片..."
            elif n == 1 and has_ws:
                status_msg = "正在编辑图片..."
            elif n >= 2 and has_ws:
                status_msg = f"正在编辑图片（{self._summarize_input_images(images_b64)}）..."
            else:
                status_msg = f"正在合成图片（{self._summarize_input_images(images_b64)}）..."
        self._last_prompt = self._get_raw_prompt() or ""

        # Set compare sources for before/after comparison
        if images_b64:
            self._set_compare_sources_from_b64(images_b64, compare_label)
        else:
            self._clear_compare_sources()

        # Push undo state (only if there's a workspace image to undo back to)
        if self.current_b64:
            self._push_undo()

        # Resolve common parameters
        size_setting = self._resolve_output_size()
        fmt = output_format or self._resolve_format()
        quality_tier = self._resolve_quality()
        api_quality = self._resolve_api_quality(quality_tier)
        try:
            compression = max(1, min(100, self.compression_var.get()))
        except Exception:
            compression = 100

        if not size_setting:
            messagebox.showwarning("尺寸设置", f"尺寸格式无效。{SIZE_INPUT_RULE_HINT}")
            return

        size_plan = self._build_edit_size_plan(size_setting, images_b64, quality_tier=quality_tier)
        request_size = size_plan["processing_size"]
        self._active_result_target_size = size_plan["output_size"]
        self._active_processing_size = size_plan["processing_size_tuple"]
        debug_log.log("app_edit_size_plan", {
            "requested_output_size": size_setting,
            "final_output_size": size_plan["output_size_text"],
            "processing_size": size_plan["processing_size"],
            "processing_strategy": size_plan["processing_strategy"],
            "requested_size_is_valid": str(size_plan["requested_size_is_valid"]),
            "restores_to_output_size": str(size_plan["restores_to_output_size"]),
            "processing_candidates": ", ".join(size_plan["processing_candidates"]),
            "source_sizes": ", ".join(
                f"{size[0]}x{size[1]}" for size in size_plan["source_sizes"]
            ),
            "source_pixel_budget": str(size_plan["source_pixel_budget"]),
            "quality_tier": quality_tier,
            "api_quality": api_quality or "(omitted)",
        })

        # Batch mode
        if batch_count > 1:
            if images_b64:
                self._start_batch_edit(prompt, images_b64, batch_count)
            else:
                self._start_batch_generate(prompt, batch_count)
            return

        # Single job
        gen = self._create_generator()
        token, started_at = self._begin_stream_job(status_msg)
        on_partial, on_done, on_error = self._build_stream_callbacks(token, started_at)

        n_imgs = len(images_b64)
        if n_imgs == 0:
            gen.generate_stream(
                prompt=prompt,
                size=request_size, output_format=fmt, quality=api_quality,
                output_compression=compression,
                on_partial=on_partial, on_done=on_done, on_error=on_error,
            )
        elif n_imgs == 1:
            gen.edit_stream(
                prompt=prompt,
                image_b64=images_b64[0],
                size=request_size, output_format=fmt, quality=api_quality,
                output_compression=compression,
                mask_b64=mask_b64,
                previous_response_id=self._last_response_id,
                on_partial=on_partial, on_done=on_done, on_error=on_error,
            )
        else:
            gen.edit_stream_multi(
                prompt=prompt,
                images_b64=images_b64,
                size=request_size, output_format=fmt, quality=api_quality,
                output_compression=compression,
                mask_b64=mask_b64,
                previous_response_id=self._last_response_id,
                on_partial=on_partial, on_done=on_done, on_error=on_error,
            )

    @staticmethod
    def _dedupe_b64_list(b64_list):
        deduped = []
        seen = set()
        for b64 in b64_list:
            if not b64 or b64 in seen:
                continue
            seen.add(b64)
            deduped.append(b64)
        return deduped

    def _set_compare_sources_from_b64(self, b64_list, label="生成前输入图"):
        self._compare_sources = []
        self._compare_source_label = label
        for b64 in self._dedupe_b64_list(b64_list):
            try:
                self._compare_sources.append(ImageGenerator.b64_to_image(b64).copy())
            except Exception:
                continue
        if not self._compare_sources:
            self._compare_source_label = ""

    def _clear_compare_sources(self):
        self._compare_sources = []
        self._compare_source_label = ""

    def _build_compare_contact_sheet(self, images, title="生成前输入图"):
        images = [img.copy() for img in images if img is not None]
        if not images:
            return None
        if len(images) == 1:
            return images[0]

        n = len(images)
        cols = 2 if n <= 4 else min(3, n)
        rows = (n + cols - 1) // cols
        thumb_box = 420 if n <= 4 else 320
        gap = 18
        margin = 18
        title_h = 34
        label_h = 28
        cell_h = thumb_box + label_h
        sheet_w = margin * 2 + cols * thumb_box + (cols - 1) * gap
        sheet_h = margin * 2 + title_h + rows * cell_h + (rows - 1) * gap

        sheet = Image.new("RGB", (sheet_w, sheet_h), C["canvas_bg"])
        draw = ImageDraw.Draw(sheet)
        draw.text((margin, 10), f"Input images x{n}", fill=C["text"])

        for i, img in enumerate(images):
            row = i // cols
            col = i % cols
            x = margin + col * (thumb_box + gap)
            y = margin + title_h + row * (cell_h + gap)

            draw.rectangle([x, y, x + thumb_box, y + thumb_box], fill=C["surface"], outline=C["border"])
            thumb = img.convert("RGBA")
            iw, ih = thumb.size
            thumb.thumbnail((thumb_box - 16, thumb_box - 16), Image.LANCZOS)
            tx = x + (thumb_box - thumb.width) // 2
            ty = y + (thumb_box - thumb.height) // 2
            sheet.paste(thumb, (tx, ty), thumb)

            badge = f"{i + 1}"
            draw.rectangle([x + 8, y + 8, x + 34, y + 32], fill=C["accent"])
            draw.text((x + 17, y + 13), badge, fill=C["toolbar_bg"], anchor="mm")
            draw.text((x, y + thumb_box + 8), f"Ref {i + 1}  {iw}x{ih}", fill=C["text_dim"])

        return sheet

    def _on_generate(self):
        """Smart generate/edit/compose: automatically picks the right mode based on current inputs.

        Professional AI image editors use ONE button for all operations:
        - No images → text-to-image generation
        - Has workspace image → edit workspace image (refs as auxiliary input)
        - Only has ref images, with selection → compose selected refs into one
        - Only has ref images, no selection → compose all refs into one

        All paths go through the unified _submit_edit_job pipeline.
        """
        if not self._ensure_idle("开始新的生成或编辑"):
            return
        prompt = self._get_prompt_with_style()
        if not prompt:
            messagebox.showwarning("提示词", "请先在提示词框中输入图片描述文字")
            return

        has_workspace = self._has_workspace_image()

        # Determine which images to use based on mode
        if has_workspace:
            # EDIT mode: workspace image (primary) + all refs (auxiliary)
            all_b64 = self._get_all_edit_b64_list()
            # Finalize mask if in mask mode
            mask_b64 = None
            if self.current_b64 and self._mask_has_content():
                mask_b64 = self._finalize_mask()
                if mask_b64:
                    if self._mask_mode:
                        self._set_status("使用当前蒙版进行局部编辑...")
                    else:
                        self._set_status("使用已保留的蒙版进行局部编辑...")
        elif self._ref_selected:
            # COMPOSE mode: only selected refs (no workspace)
            selected_b64 = [self._ref_images[i]["b64"]
                           for i in sorted(self._ref_selected)
                           if i < len(self._ref_images)]
            selected_b64 = self._dedupe_b64_list(selected_b64)
            if not selected_b64:
                messagebox.showwarning("参考图", "选中的参考图数据无效，请重新添加")
                return
            all_b64 = selected_b64
            mask_b64 = None
        elif self._ref_images:
            # COMPOSE mode: all refs (no workspace, no selection)
            all_b64 = self._dedupe_b64_list([r["b64"] for r in self._ref_images])
            mask_b64 = None
        else:
            # GENERATE mode: no images at all
            all_b64 = []
            mask_b64 = None

        # Build appropriate status message
        n = len(all_b64)
        has_ws = bool(self.current_b64) and self.current_b64 in all_b64
        if n == 0:
            status_msg = "正在生成图片..."
        elif n == 1 and has_ws:
            status_msg = "正在编辑图片..."
        elif n >= 2 and has_ws:
            status_msg = f"正在编辑图片（{self._summarize_input_images(all_b64)}）..."
        else:
            status_msg = f"正在合成图片（{self._summarize_input_images(all_b64)}）..."

        self._submit_edit_job(prompt, all_b64, mask_b64=mask_b64,
                              status_msg=status_msg,
                              compare_label="生成前输入图")

    # ── 批量生成 ──────────────────────────────────────

    def _start_batch_generate(self, prompt, count):
        self._save_config()
        self._job_token += 1
        job_token = self._job_token
        self._batch_token += 1
        batch_token = self._batch_token
        self._batch_results = [None] * count
        self._batch_done_count = 0
        self._batch_total = count
        self._batch_error_count = 0
        self._set_generating(True)
        self.progress_var.set(0)
        self._set_status(f"正在批量生成 {count} 张图片...")
        self.start_time = time.time()
        self._current_job_label = f"正在批量生成 {count} 张图片"
        # Start heartbeat for batch wait
        self._cancel_progress_timer()
        self._progress_timer_id = self.after(3000, self._progress_heartbeat, job_token, self.start_time)
        quality_tier = self._resolve_quality()
        self._batch_api_quality = self._resolve_api_quality(quality_tier)
        size_plan = self._build_edit_size_plan(self._resolve_output_size(), [], quality_tier=quality_tier)
        self._batch_result_target_size = size_plan["output_size"]
        self._batch_request_size = size_plan["processing_size"]
        debug_log.log("app_batch_generate_size_plan", {
            "final_output_size": size_plan["output_size_text"],
            "processing_size": size_plan["processing_size"],
            "processing_strategy": size_plan["processing_strategy"],
            "requested_size_is_valid": str(size_plan["requested_size_is_valid"]),
            "restores_to_output_size": str(size_plan["restores_to_output_size"]),
            "processing_candidates": ", ".join(size_plan["processing_candidates"]),
            "quality_tier": quality_tier,
            "api_quality": self._batch_api_quality or "(omitted)",
        })

        for i in range(count):
            gen = ImageGenerator(
                api_base=self.api_base_var.get(),
                model=self._resolve_model_api_id(),
                auth_token=self._get_auth_header(),
            )
            self._active_generators.append(gen)
            if i == 0:
                self._active_generator = gen

            def make_callbacks(idx, g, bt):
                def on_partial(b64, pcount):
                    if bt != self._batch_token:
                        return
                    if b64 is None:
                        # Retry notification from the worker-level retry chain.
                        self.after(0, self._set_status, f"批量 #{idx+1} 正在重试 (第 {pcount} 次)...")
                        return
                    if idx == 0:
                        try:
                            _, img = self._prepare_result_for_output(b64, self._batch_result_target_size)
                            self.after(0, self._show_image, img)
                        except Exception:
                            pass
                    elapsed = time.time() - self.start_time
                    self.after(0, self._update_batch_progress, bt, elapsed)

                def on_done(b64, pcount, revised_prompt=None, response_id=None):
                    if bt != self._batch_token:
                        return
                    try:
                        b64, img = self._prepare_result_for_output(b64, self._batch_result_target_size)
                        self._batch_results[idx] = {"b64": b64, "image": img}
                    except Exception:
                        self._batch_results[idx] = None
                    elapsed = time.time() - self.start_time
                    self.after(0, self._on_batch_item_done, bt, idx, elapsed)

                def on_error(msg):
                    if bt != self._batch_token:
                        return
                    # The request has already exhausted the worker-level retries.
                    # Now do an additional retry at the batch level.
                    self.after(0, self._on_batch_item_error, bt, idx, msg)

                return on_partial, on_done, on_error

            on_partial, on_done, on_error = make_callbacks(i, gen, batch_token)
            gen.generate_stream(
                prompt=prompt,
                size=self._batch_request_size,
                output_format=self._resolve_format(),
                quality=self._batch_api_quality,
                output_compression=max(1, min(100, self.compression_var.get())),
                on_partial=on_partial, on_done=on_done, on_error=on_error,
            )

    def _on_batch_item_error(self, batch_token, idx, msg):
        """Handle a batch item failure — retry up to BATCH_MAX_RETRIES times"""
        if batch_token != self._batch_token:
            return

        # Track retry count per item
        if not hasattr(self, '_batch_retry_counts'):
            self._batch_retry_counts = {}
        self._batch_retry_counts[idx] = self._batch_retry_counts.get(idx, 0) + 1

        BATCH_MAX_RETRIES = 2
        retry_count = self._batch_retry_counts[idx]

        if retry_count <= BATCH_MAX_RETRIES:
            self._set_status(f"批量 #{idx+1} 失败，正在第 {retry_count} 次重试...")
            # Re-launch this specific item
            prompt = self._get_prompt_with_style()
            if not prompt:
                prompt = self._last_prompt or "generate image"

            gen = ImageGenerator(
                api_base=self.api_base_var.get(),
                model=self._resolve_model_api_id(),
                auth_token=self._get_auth_header(),
            )
            self._active_generators.append(gen)
            bt = batch_token

            def make_retry_callbacks(i, g, b):
                def on_partial(b64, pcount):
                    if b != self._batch_token:
                        return
                    if b64 is None:
                        self.after(0, self._set_status, f"批量 #{i+1} 重试中 (第 {pcount} 次)...")
                        return
                    if i == 0:
                        try:
                            _, img = self._prepare_result_for_output(b64, self._batch_result_target_size)
                            self.after(0, self._show_image, img)
                        except Exception:
                            pass
                    elapsed = time.time() - self.start_time
                    self.after(0, self._update_batch_progress, b, elapsed)

                def on_done(b64, pcount, revised_prompt=None, response_id=None):
                    if b != self._batch_token:
                        return
                    try:
                        b64, img = self._prepare_result_for_output(b64, self._batch_result_target_size)
                        self._batch_results[i] = {"b64": b64, "image": img}
                    except Exception:
                        self._batch_results[i] = None
                    elapsed = time.time() - self.start_time
                    self.after(0, self._on_batch_item_done, b, i, elapsed)

                def on_error(msg2):
                    if b != self._batch_token:
                        return
                    self.after(0, self._on_batch_item_error, b, i, msg2)

                return on_partial, on_done, on_error

            on_p, on_d, on_e = make_retry_callbacks(idx, gen, bt)
            gen.generate_stream(
                prompt=prompt,
                size=self._batch_request_size or DEFAULT_SIZE,
                output_format=self._resolve_format(),
                quality=self._batch_api_quality,
                output_compression=max(1, min(100, self.compression_var.get())),
                on_partial=on_p, on_done=on_d, on_error=on_e,
            )
        else:
            # Exhausted all retries — mark as permanently failed
            self._batch_results[idx] = None
            self._batch_error_count += 1
            self._set_status(f"批量 #{idx+1} 最终失败: {msg[:60]}")
            # 记录批量失败日志
            self.error_log.add("批量生成失败", msg, {
                "batch_idx": idx + 1,
                "batch_total": self._batch_total,
                "retries": retry_count,
                "api_base": self.api_base_var.get(),
                "model": self._resolve_model_api_id(),
                "prompt": (self._last_prompt or "")[:100],
            })
            elapsed = time.time() - self.start_time
            self._on_batch_item_done(batch_token, idx, elapsed)

    def _update_batch_progress(self, batch_token, elapsed):
        if batch_token != self._batch_token or not self.is_generating:
            return
        done = self._batch_done_count
        total = self._batch_total
        progress = (done / total) * 100 if total > 0 else 0
        self.progress_var.set(min(progress + 5, 98))
        self.time_label.config(text=f"{elapsed:.1f}s | {done}/{total}")
        job_label = getattr(self, "_current_job_label", "") or "正在批量处理"
        prefix = "正在批量编辑" if job_label.startswith("正在批量编辑") else "正在批量生成"
        self._set_status(f"{prefix}... ({done}/{total} 完成)")

    def _on_batch_item_done(self, batch_token, idx, elapsed):
        if batch_token != self._batch_token:
            return
        self._batch_done_count += 1
        done = self._batch_done_count
        total = self._batch_total
        progress = (done / total) * 100
        self.progress_var.set(progress)
        self.time_label.config(text=f"{elapsed:.1f}s | {done}/{total}")

        if done < total:
            errors = self._batch_error_count
            job_label = getattr(self, "_current_job_label", "") or ""
            prefix = "批量编辑中" if job_label.startswith("正在批量编辑") else "批量生成中"
            status = f"{prefix}... ({done}/{total} 完成"
            if errors:
                status += f", {errors} 失败"
            status += ")"
            self._set_status(status)
            return

        # All done
        self._set_generating(False)
        results = [r for r in self._batch_results if r is not None]
        if not results:
            self._set_status("批量生成全部失败，请检查 API 设置和网络连接")
            messagebox.showerror("生成失败", "所有图片生成均失败（已自动重试多次），请检查API设置或稍后重试")
            return

        if len(results) == 1:
            r = results[0]
            self.current_b64 = r["b64"]
            self._show_image(r["image"])
            self._primary_is_result = True
            self._refresh_edit_action_state()
            self.progress_var.set(100)
            # Task-specific completion message
            job_label = getattr(self, "_current_job_label", "") or ""
            if job_label.startswith("正在批量编辑"):
                self._set_status(f"批量编辑完成！用时 {elapsed:.1f}s")
            else:
                self._set_status(f"批量生成完成！用时 {elapsed:.1f}s")
            prompt = self._last_prompt or self._get_raw_prompt() or "无描述"
            self._add_to_history(r["b64"], prompt)
            self._continue_followup_chain(job_label)
            return

        # Multiple results — show selection dialog
        self._show_batch_selection_dialog(results, elapsed)

    # ── 批量编辑 ──────────────────────────────────────

    def _start_batch_edit(self, prompt, all_b64, count):
        self._save_config()
        self._job_token += 1
        job_token = self._job_token
        self._batch_token += 1
        batch_token = self._batch_token
        self._batch_results = [None] * count
        self._batch_done_count = 0
        self._batch_total = count
        self._batch_error_count = 0
        quality_tier = self._resolve_quality()
        self._batch_api_quality = self._resolve_api_quality(quality_tier)
        size_plan = self._build_edit_size_plan(self._resolve_output_size(), all_b64, quality_tier=quality_tier)
        self._batch_result_target_size = size_plan["output_size"]
        self._batch_request_size = size_plan["processing_size"]
        self._set_generating(True)
        self.progress_var.set(0)
        n_imgs = len(all_b64)
        status = f"正在批量编辑 {count} 张图片（{self._summarize_input_images(all_b64)}）..." if n_imgs > 1 else f"正在批量编辑 {count} 张图片..."
        self._set_status(status)
        self.start_time = time.time()
        self._current_job_label = status.replace("...", "").replace("…", "").strip()
        # Start heartbeat for batch wait
        self._cancel_progress_timer()
        self._progress_timer_id = self.after(3000, self._progress_heartbeat, job_token, self.start_time)
        debug_log.log("app_batch_edit_size_plan", {
            "final_output_size": size_plan["output_size_text"],
            "processing_size": size_plan["processing_size"],
            "processing_strategy": size_plan["processing_strategy"],
            "requested_size_is_valid": str(size_plan["requested_size_is_valid"]),
            "restores_to_output_size": str(size_plan["restores_to_output_size"]),
            "processing_candidates": ", ".join(size_plan["processing_candidates"]),
            "source_sizes": ", ".join(
                f"{size[0]}x{size[1]}" for size in size_plan["source_sizes"]
            ),
            "source_pixel_budget": str(size_plan["source_pixel_budget"]),
            "quality_tier": quality_tier,
            "api_quality": self._batch_api_quality or "(omitted)",
        })

        for i in range(count):
            gen = ImageGenerator(
                api_base=self.api_base_var.get(),
                model=self._resolve_model_api_id(),
                auth_token=self._get_auth_header(),
            )
            self._active_generators.append(gen)
            if i == 0:
                self._active_generator = gen

            def make_callbacks(idx, g, bt):
                def on_partial(b64, pcount):
                    if bt != self._batch_token:
                        return
                    if b64 is None:
                        self.after(0, self._set_status, f"编辑 #{idx+1} 正在重试 (第 {pcount} 次)...")
                        return
                    if idx == 0:
                        try:
                            _, img = self._prepare_result_for_output(b64, self._batch_result_target_size)
                            self.after(0, self._show_image, img)
                        except Exception:
                            pass
                    elapsed = time.time() - self.start_time
                    self.after(0, self._update_batch_progress, bt, elapsed)

                def on_done(b64, pcount, revised_prompt=None, response_id=None):
                    if bt != self._batch_token:
                        return
                    try:
                        b64, img = self._prepare_result_for_output(b64, self._batch_result_target_size)
                        self._batch_results[idx] = {"b64": b64, "image": img}
                    except Exception:
                        self._batch_results[idx] = None
                    elapsed = time.time() - self.start_time
                    self.after(0, self._on_batch_item_done, bt, idx, elapsed)

                def on_error(msg):
                    if bt != self._batch_token:
                        return
                    self.after(0, self._on_batch_edit_item_error, bt, idx, msg)

                return on_partial, on_done, on_error

            on_partial, on_done, on_error = make_callbacks(i, gen, batch_token)
            if n_imgs == 1:
                gen.edit_stream(
                    prompt=prompt,
                    image_b64=all_b64[0],
                    size=self._batch_request_size,
                    output_format=self._resolve_format(),
                    quality=self._batch_api_quality,
                    output_compression=max(1, min(100, self.compression_var.get())),
                    on_partial=on_partial, on_done=on_done, on_error=on_error,
                )
            else:
                gen.edit_stream_multi(
                    prompt=prompt,
                    images_b64=all_b64,
                    size=self._batch_request_size,
                    output_format=self._resolve_format(),
                    quality=self._batch_api_quality,
                    output_compression=max(1, min(100, self.compression_var.get())),
                    on_partial=on_partial, on_done=on_done, on_error=on_error,
                )

    def _on_batch_edit_item_error(self, batch_token, idx, msg):
        """Handle a batch edit item failure — retry up to BATCH_MAX_RETRIES times"""
        if batch_token != self._batch_token:
            return

        if not hasattr(self, '_batch_retry_counts'):
            self._batch_retry_counts = {}
        self._batch_retry_counts[idx] = self._batch_retry_counts.get(idx, 0) + 1

        BATCH_MAX_RETRIES = 2
        retry_count = self._batch_retry_counts[idx]

        if retry_count <= BATCH_MAX_RETRIES:
            self._set_status(f"编辑 #{idx+1} 失败，正在第 {retry_count} 次重试...")
            prompt = self._get_prompt_with_style()
            if not prompt:
                prompt = self._last_prompt or "edit image"
            all_b64 = self._get_all_edit_b64_list()
            n_imgs = len(all_b64)

            gen = ImageGenerator(
                api_base=self.api_base_var.get(),
                model=self._resolve_model_api_id(),
                auth_token=self._get_auth_header(),
            )
            self._active_generators.append(gen)
            bt = batch_token

            def make_retry_callbacks(i, g, b):
                def on_partial(b64, pcount):
                    if b != self._batch_token:
                        return
                    if b64 is None:
                        self.after(0, self._set_status, f"编辑 #{i+1} 重试中 (第 {pcount} 次)...")
                        return
                    if i == 0:
                        try:
                            _, img = self._prepare_result_for_output(b64, self._batch_result_target_size)
                            self.after(0, self._show_image, img)
                        except Exception:
                            pass
                    elapsed = time.time() - self.start_time
                    self.after(0, self._update_batch_progress, b, elapsed)

                def on_done(b64, pcount, revised_prompt=None, response_id=None):
                    if b != self._batch_token:
                        return
                    try:
                        b64, img = self._prepare_result_for_output(b64, self._batch_result_target_size)
                        self._batch_results[i] = {"b64": b64, "image": img}
                    except Exception:
                        self._batch_results[i] = None
                    elapsed = time.time() - self.start_time
                    self.after(0, self._on_batch_item_done, b, i, elapsed)

                def on_error(msg2):
                    if b != self._batch_token:
                        return
                    self.after(0, self._on_batch_edit_item_error, b, i, msg2)

                return on_partial, on_done, on_error

            on_p, on_d, on_e = make_retry_callbacks(idx, gen, bt)
            if n_imgs == 1:
                gen.edit_stream(
                    prompt=prompt,
                    image_b64=all_b64[0],
                    size=self._batch_request_size or self._resolve_output_size(),
                    output_format=self._resolve_format(),
                    quality=self._batch_api_quality,
                    output_compression=max(1, min(100, self.compression_var.get())),
                    on_partial=on_p, on_done=on_d, on_error=on_e,
                )
            else:
                gen.edit_stream_multi(
                    prompt=prompt,
                    images_b64=all_b64,
                    size=self._batch_request_size or self._resolve_output_size(),
                    output_format=self._resolve_format(),
                    quality=self._batch_api_quality,
                    output_compression=max(1, min(100, self.compression_var.get())),
                    on_partial=on_p, on_done=on_d, on_error=on_e,
                )
        else:
            self._batch_results[idx] = None
            self._batch_error_count += 1
            self._set_status(f"编辑 #{idx+1} 最终失败: {msg[:60]}")
            self.error_log.add("批量编辑失败", msg, {
                "batch_idx": idx + 1,
                "batch_total": self._batch_total,
                "retries": retry_count,
                "api_base": self.api_base_var.get(),
                "model": self._resolve_model_api_id(),
                "prompt": (self._last_prompt or "")[:100],
            })
            elapsed = time.time() - self.start_time
            self._on_batch_item_done(batch_token, idx, elapsed)

    def _show_batch_selection_dialog(self, results, elapsed):
        job_label = getattr(self, "_current_job_label", "") or ""
        is_batch_edit = job_label.startswith("正在批量编辑")
        dialog = tk.Toplevel(self)
        dialog.title("批量编辑完成 — 选择图片" if is_batch_edit else "批量生成完成 — 选择图片")
        dialog.configure(bg=C["bg"])
        dialog.transient(self)
        dialog.grab_set()

        n = len(results)
        # Grid layout: max 5 columns, auto rows
        max_cols = min(n, 5)
        rows = (n + max_cols - 1) // max_cols
        thumb_size = 200 if n <= 4 else 160 if n <= 6 else 130
        col_w = thumb_size + 40
        row_h = thumb_size + 80
        dialog_w = max(400, max_cols * col_w + 40)
        dialog_h = max(300, rows * row_h + 120)

        dialog.resizable(True, True)
        self._center_dialog(dialog, dialog_w, dialog_h)

        # Header
        header = tk.Frame(dialog, bg=C["surface"])
        header.pack(fill="x")
        header_text = f"{'批量编辑得到' if is_batch_edit else '批量生成了'} {n} 张图片，勾选后点确认使用"
        tk.Label(header, text=header_text,
                 bg=C["surface"], fg=C["text"],
                 font=("Microsoft YaHei UI", 11, "bold")).pack(side="left", padx=12, pady=8)
        tk.Label(header, text=f"用时 {elapsed:.1f}s", bg=C["surface"], fg=C["text_muted"],
                 font=("Consolas", 9)).pack(side="right", padx=12, pady=8)

        # Grid of images with checkboxes
        grid_frame = tk.Frame(dialog, bg=C["bg"])
        grid_frame.pack(fill="both", expand=True, padx=8, pady=8)

        dialog._photo_refs = []
        prompt = self._last_prompt or self._get_raw_prompt() or "无描述"

        # Track selection state
        selected_vars = []
        for i in range(n):
            selected_vars.append(tk.BooleanVar(value=True))  # default: all selected

        for i, r in enumerate(results):
            row = i // max_cols
            col = i % max_cols
            card = tk.Frame(grid_frame, bg=C["surface"], bd=0,
                            highlightbackground=C["border"], highlightthickness=2)
            card.grid(row=row, column=col, padx=4, pady=4, sticky="nsew")
            grid_frame.columnconfigure(col, weight=1)
            grid_frame.rowconfigure(row, weight=1)

            # Checkbox + label row
            top_row = tk.Frame(card, bg=C["surface"])
            top_row.pack(fill="x", padx=4, pady=(4, 0))
            cb = tk.Checkbutton(top_row, variable=selected_vars[i],
                                bg=C["surface"], fg=C["text"],
                                activebackground=C["surface"], activeforeground=C["text"],
                                selectcolor=C["surface2"], font=("Microsoft YaHei UI", 9))
            cb.pack(side="left")
            tk.Label(top_row, text=f"图片 {i + 1}", bg=C["surface"], fg=C["text_dim"],
                     font=("Microsoft YaHei UI", 9)).pack(side="left", padx=2)

            # Thumbnail
            img = r["image"]
            iw, ih = img.size
            scale = min(thumb_size / iw, thumb_size / ih, 1.0)
            new_w = max(1, int(iw * scale))
            new_h = max(1, int(ih * scale))
            resized = img.resize((new_w, new_h), Image.LANCZOS)
            photo = ImageTk.PhotoImage(resized)
            dialog._photo_refs.append(photo)

            lbl = tk.Label(card, image=photo, bg=C["canvas_bg"], cursor="hand2")
            lbl.pack(padx=4, pady=2)

            # Size info
            tk.Label(card, text=f"{iw}×{ih}", bg=C["surface"], fg=C["text_muted"],
                     font=("Consolas", 8)).pack(pady=(0, 4))

            # Click on card/image toggles checkbox
            def toggle_cb(var=selected_vars[i]):
                var.set(not var.get())

            card.bind("<Button-1>", lambda e, fn=toggle_cb: fn())
            lbl.bind("<Button-1>", lambda e, fn=toggle_cb: fn())

        # Bottom buttons
        bottom = tk.Frame(dialog, bg=C["surface"])
        bottom.pack(fill="x")

        def select_all():
            for v in selected_vars:
                v.set(True)

        def deselect_all():
            for v in selected_vars:
                v.set(False)

        def confirm_selection():
            selected_indices = [i for i in range(n) if selected_vars[i].get()]
            if not selected_indices:
                messagebox.showwarning("批量选择", "请至少勾选一张图片再确认", parent=dialog)
                return
            dialog.destroy()
            # Add all selected to history
            for idx in selected_indices:
                r = results[idx]
                p = self._last_prompt or self._get_raw_prompt() or "无描述"
                self._add_to_history(r["b64"], p)
            # Show the first selected image
            first = results[selected_indices[0]]
            self.current_b64 = first["b64"]
            self._show_image(first["image"])
            self._primary_is_result = True
            self._refresh_edit_action_state()
            self.progress_var.set(100)
            count = len(selected_indices)
            if count == 1:
                label = "编辑结果" if is_batch_edit else "图片"
                self._set_status(f"已选择 1 张{label}！用时 {elapsed:.1f}s")
            else:
                label = "张编辑结果" if is_batch_edit else "张图片"
                self._set_status(f"已选择 {count} {label}，已存入历史记录，用时 {elapsed:.1f}s")
            self._continue_followup_chain(job_label)

        tk.Button(bottom, text="全选", command=select_all,
                  bg=C["btn_bg"], fg=C["text"],
                  activebackground=C["btn_hover"], activeforeground=C["text"],
                  font=("Microsoft YaHei UI", 9), relief="flat", cursor="hand2",
                  pady=3).pack(side="left", padx=(12, 2), pady=8)

        tk.Button(bottom, text="全不选", command=deselect_all,
                  bg=C["btn_bg"], fg=C["text"],
                  activebackground=C["btn_hover"], activeforeground=C["text"],
                  font=("Microsoft YaHei UI", 9), relief="flat", cursor="hand2",
                  pady=3).pack(side="left", padx=2, pady=8)

        tk.Button(bottom, text="确认使用", command=confirm_selection,
                  bg=C["accent"], fg=C["toolbar_bg"],
                  activebackground=C["accent"], activeforeground=C["toolbar_bg"],
                  font=("Microsoft YaHei UI", 10, "bold"),
                  relief="flat", cursor="hand2", pady=4).pack(side="left", padx=12, pady=8)

        tk.Button(bottom, text="取消", command=dialog.destroy,
                  bg=C["btn_bg"], fg=C["text"],
                  activebackground=C["btn_hover"], activeforeground=C["text"],
                  font=("Microsoft YaHei UI", 10),
                  relief="flat", cursor="hand2", pady=4).pack(side="right", padx=12, pady=8)

    def _center_dialog(self, dialog, w, h):
        """Center and clamp a dialog into the visible work area."""
        fit_w, fit_h, dx, dy = self._fit_rect_to_work_area(w, h, anchor=self, margin=10)
        dialog.geometry(f"{fit_w}x{fit_h}+{dx}+{dy}")

    # ── AI 工具操作 ──────────────────────────────

    def _run_ai_edit(self, prompt_text, status_msg="正在处理...", output_format=None):
        """AI tool edit (style transfer, bg remove, etc.). Delegates to unified pipeline."""
        if not self._ensure_idle("使用 AI 工具"):
            return
        if not self._require_workspace_image("使用 AI 工具"):
            return

        all_b64 = self._get_all_edit_b64_list()

        self._submit_edit_job(prompt_text, all_b64,
                              status_msg=status_msg,
                              compare_label="处理前输入图",
                              output_format=output_format)

    def _do_auto_style_transfer(self):
        """Automatically apply style transfer after a generate/edit completes.
        Called only when the auto-style-transfer toggle is active.
        Uses _style_transfer_name as the style preset.
        """
        if not self.current_b64:
            return
        style_name = getattr(self, "_style_transfer_name", "油画") or "油画"
        suffix = STYLE_PRESETS.get(style_name, "")
        if not suffix:
            self._style_transfer_active = False
            if hasattr(self, "_style_transfer_auto_btn"):
                self._style_transfer_auto_btn.set_toggled(False)
            self._set_status("风格未执行：当前选择没有可应用的风格效果")
            return
        prompt = f"Transform this image{suffix}. Keep the main subject and composition."
        self._run_ai_edit(prompt, f"正在应用风格: {style_name}...")

    def _on_style_transfer_toggle(self):
        """Toggle auto-style-transfer mode.

        - If currently OFF: open a dialog to pick a style preset, then activate.
          The button stays highlighted until toggled off.
          Note: this does NOT immediately run a style transfer; the transfer fires
          automatically after the NEXT generate/edit completes.
        - If currently ON: deactivate the mode immediately.
        """
        if self._style_transfer_active:
            # Turn off
            self._style_transfer_active = False
            if hasattr(self, "_style_transfer_auto_btn"):
                self._style_transfer_auto_btn.set_toggled(False)
            self._pending_followups = [item for item in self._pending_followups if item != "style"]
            self._set_status("风格已关闭")
            return

        if self.is_generating:
            self._set_status("当前任务进行中，请等当前任务完成后再开启风格")
            return

        # Turn on: ask for style preset
        dialog = tk.Toplevel(self)
        dialog.title("选择风格")
        dialog.configure(bg=C["surface"])
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        self._center_dialog(dialog, 340, 380)

        tk.Label(dialog, text="选择风格（生成完成后自动应用）:", bg=C["surface"], fg=C["text"],
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", padx=16, pady=(16, 4))

        style_listbox = tk.Listbox(dialog, height=12, selectmode="browse",
                                    font=("Microsoft YaHei UI", 10),
                                    bg=C["canvas_bg"], fg=C["text"],
                                    selectbackground=C["accent"], selectforeground=C["toolbar_bg"],
                                    relief="flat", bd=4, highlightthickness=0)
        style_listbox.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        preset_names = [name for name, suffix in STYLE_PRESETS.items() if suffix]
        for name in preset_names:
            style_listbox.insert("end", name)

        # Pre-select the current style
        current = getattr(self, "_style_transfer_name", "油画") or "油画"
        if current in preset_names:
            style_listbox.selection_set(preset_names.index(current))
            style_listbox.see(preset_names.index(current))

        confirmed = {"value": False, "style": None}

        def do_confirm():
            sel = style_listbox.curselection()
            if not sel:
                return
            confirmed["style"] = preset_names[sel[0]]
            confirmed["value"] = True
            dialog.destroy()

        def do_cancel():
            dialog.destroy()

        btn_row = tk.Frame(dialog, bg=C["surface"])
        btn_row.pack(fill="x", padx=16, pady=(0, 12))
        tk.Button(btn_row, text="开启风格", command=do_confirm,
                  bg=C["accent"], fg=C["toolbar_bg"],
                  activebackground=C["accent"], activeforeground=C["toolbar_bg"],
                  font=("Microsoft YaHei UI", 10, "bold"),
                  relief="flat", cursor="hand2", pady=4).pack(side="left", fill="x", expand=True, padx=(0, 4))
        tk.Button(btn_row, text="取消", command=do_cancel,
                  bg=C["surface2"], fg=C["text"],
                  activebackground=C["btn_hover"], activeforeground=C["text"],
                  font=("Microsoft YaHei UI", 10),
                  relief="flat", cursor="hand2", pady=4).pack(side="left")
        style_listbox.bind("<Double-Button-1>", lambda e: do_confirm())
        dialog.bind("<Escape>", lambda e: do_cancel())

        self.wait_window(dialog)

        if confirmed["value"] and confirmed["style"]:
            self._style_transfer_name = confirmed["style"]
            self._style_transfer_active = True
            if hasattr(self, "_style_transfer_auto_btn"):
                self._style_transfer_auto_btn.set_toggled(True)
            # If there's already an image on canvas, apply style immediately
            if self.current_b64:
                self._set_status(f"正在应用风格: {self._style_transfer_name}...")
                self.after(0, self._do_auto_style_transfer)
            else:
                self._set_status(f"风格已开启：{self._style_transfer_name}，生成后将自动应用")

    def _on_bg_remove(self):
        prompt = ("Remove the entire background from this image, making it fully "
                  "transparent. Keep only the main subject with clean edges. "
                  "Output as a clean cutout on transparent background.")
        self._run_ai_edit(prompt, "正在移除背景（自动输出 PNG）...", output_format="png")

    def _do_auto_bg_replace(self):
        """Automatically replace the background after a generate/edit completes.
        Called only when the auto-replace-bg toggle is active.
        Uses _bg_replace_desc as the background description.
        """
        if not self.current_b64:
            return
        desc = getattr(self, "_bg_replace_desc", "阳光海滩，海浪拍岸") or "阳光海滩，海浪拍岸"
        prompt = (f"Replace the background of this image with: {desc}. "
                  f"Keep the main subject exactly as is, only change the background.")
        self._run_ai_edit(prompt, f"正在自动换背: {desc[:20]}...")

    def _on_bg_replace_toggle(self):
        """Toggle auto-replace-background mode.

        - If currently OFF: open a small input dialog to set the background description,
          then activate the mode. The button stays highlighted until toggled off.
          Note: this does NOT immediately run a replace; the replace fires automatically
          after the NEXT generate/edit completes.
        - If currently ON: deactivate the mode immediately (no replace triggered).
        """
        if self._bg_replace_active:
            # Turn off
            self._bg_replace_active = False
            if hasattr(self, "_bg_replace_auto_btn"):
                self._bg_replace_auto_btn.set_toggled(False)
            self._pending_followups = [item for item in self._pending_followups if item != "bg"]
            self._set_status(f"自动换背已关闭（再次点击可重新开启）")
            return

        if self.is_generating:
            self._set_status("当前任务进行中，自动换背会影响后续链路；请等当前任务完成后再开启")
            return

        # Turn on: ask for background description first
        dialog = tk.Toplevel(self)
        dialog.title("设置换背描述")
        dialog.configure(bg=C["surface"])
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        self._center_dialog(dialog, 420, 200)

        tk.Label(dialog, text="描述新背景（生成完成后自动替换）:", bg=C["surface"], fg=C["text"],
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", padx=16, pady=(16, 4))
        bg_entry = tk.Text(dialog, height=3, wrap="word",
                           font=("Microsoft YaHei UI", 10),
                           bg=C["canvas_bg"], fg=C["text"], insertbackground=C["text"],
                           selectbackground=C["surface2"], relief="flat", bd=4)
        bg_entry.pack(fill="x", padx=16, pady=(0, 8))
        bg_entry.insert("1.0", self._bg_replace_desc)
        bg_entry.focus_set()

        confirmed = {"value": False}

        def do_confirm():
            desc = bg_entry.get("1.0", "end").strip()
            if not desc:
                return
            self._bg_replace_desc = desc
            confirmed["value"] = True
            dialog.destroy()

        def do_cancel():
            dialog.destroy()

        btn_row = tk.Frame(dialog, bg=C["surface"])
        btn_row.pack(fill="x", padx=16, pady=(0, 12))
        tk.Button(btn_row, text="开启自动换背", command=do_confirm,
                  bg=C["accent"], fg=C["toolbar_bg"],
                  activebackground=C["accent"], activeforeground=C["toolbar_bg"],
                  font=("Microsoft YaHei UI", 10, "bold"),
                  relief="flat", cursor="hand2", pady=4).pack(side="left", fill="x", expand=True, padx=(0, 4))
        tk.Button(btn_row, text="取消", command=do_cancel,
                  bg=C["surface2"], fg=C["text"],
                  activebackground=C["btn_hover"], activeforeground=C["text"],
                  font=("Microsoft YaHei UI", 10),
                  relief="flat", cursor="hand2", pady=4).pack(side="left")
        dialog.bind("<Control-Return>", lambda e: do_confirm())
        dialog.bind("<Escape>", lambda e: do_cancel())

        self.wait_window(dialog)

        if confirmed["value"]:
            self._bg_replace_active = True
            if hasattr(self, "_bg_replace_auto_btn"):
                self._bg_replace_auto_btn.set_toggled(True)
            # If there's already an image on canvas, replace background immediately
            if self.current_b64:
                self._set_status(f"正在换背: {self._bg_replace_desc[:20]}...")
                self.after(0, self._do_auto_bg_replace)
            else:
                self._set_status(f"自动换背已开启：{self._bg_replace_desc[:30]}，生成后将自动应用")

    def _on_bg_replace(self):
        dialog = tk.Toplevel(self)
        dialog.title("背景替换")
        dialog.configure(bg=C["surface"])
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        self._center_dialog(dialog, 420, 200)

        tk.Label(dialog, text="描述新背景:", bg=C["surface"], fg=C["text"],
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", padx=16, pady=(16, 4))
        bg_entry = tk.Text(dialog, height=3, wrap="word",
                           font=("Microsoft YaHei UI", 10),
                           bg=C["canvas_bg"], fg=C["text"], insertbackground=C["text"],
                           selectbackground=C["surface2"], relief="flat", bd=4)
        bg_entry.pack(fill="x", padx=16, pady=(0, 8))
        bg_entry.insert("1.0", "阳光海滩，海浪拍岸")
        bg_entry.focus_set()

        def do_replace():
            desc = bg_entry.get("1.0", "end").strip()
            if not desc:
                return
            dialog.destroy()
            prompt = (f"Replace the background of this image with: {desc}. "
                      f"Keep the main subject exactly as is, only change the background.")
            self._run_ai_edit(prompt, "正在替换背景...")

        btn = tk.Button(dialog, text="替换背景", command=do_replace,
                        bg=C["accent"], fg=C["toolbar_bg"],
                        activebackground=C["accent"], activeforeground=C["toolbar_bg"],
                        font=("Microsoft YaHei UI", 10, "bold"),
                        relief="flat", cursor="hand2", pady=4)
        btn.pack(padx=16, fill="x")
        dialog.bind("<Control-Return>", lambda e: do_replace())

    def _on_upscale(self):
        prompt = ("Upscale this image to higher resolution. Enhance details, "
                  "sharpen edges, and improve overall quality while maintaining "
                  "the exact same composition and content.")
        self._run_ai_edit(prompt, "正在放大图片...")

    def _on_describe(self):
        if not self._ensure_idle("开始 AI 描述"):
            return
        display_b64 = self._get_display_b64()
        if not display_b64:
            messagebox.showwarning("AI 描述", "请先在编辑区加载图片，或从历史记录中双击浏览一张图片")
            return
        gen = self._create_generator()
        token, started_at = self._begin_stream_job("正在分析图片...")

        def on_done(desc):
            self.after(0, self._finish_describe, token, desc)

        def on_error(msg):
            self.after(0, self._finish_async_task_error, token, "AI 描述失败", msg)

        gen.describe_image(display_b64, on_done=on_done, on_error=on_error)

    def _finish_describe(self, token, desc):
        if token != self._job_token or not self.is_generating:
            return
        self._cancel_progress_timer()
        elapsed = time.time() - getattr(self, "start_time", time.time())
        self.progress_var.set(100)
        self.time_label.config(text=f"完成 | {elapsed:.1f}s")
        self._set_generating(False)
        self._show_description(desc)

    def _finish_async_task_error(self, token, prefix, msg):
        if token != self._job_token:
            return
        self._cancel_progress_timer()
        self.progress_var.set(0)
        self.time_label.config(text="")
        self._pending_followups = []
        self._set_generating(False)
        self._set_status(f"{prefix}: {msg[:80]}")
        self.error_log.add(prefix, msg, {
            "api_base": self.api_base_var.get(),
            "model": self._resolve_model_api_id(),
            "has_current": bool(self.current_b64),
            "ref_images": len(self._ref_images),
        })

    def _show_description(self, desc):
        self._set_status("AI 描述完成")
        dialog = tk.Toplevel(self)
        dialog.title("AI 图片描述")
        dialog.configure(bg=C["surface"])
        dialog.transient(self)
        self._center_dialog(dialog, 520, 340)

        tk.Label(dialog, text="AI 对这张图片的描述:", bg=C["surface"], fg=C["text"],
                 font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", padx=16, pady=(16, 6))

        txt = tk.Text(dialog, wrap="word", font=("Microsoft YaHei UI", 10),
                      bg=C["canvas_bg"], fg=C["text"], insertbackground=C["text"],
                      selectbackground=C["surface2"], relief="flat", bd=4,
                      padx=6, pady=6)
        txt.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        txt.insert("1.0", desc)

        def copy_to_prompt():
            self.prompt_text.delete("1.0", "end")
            self.prompt_text.insert("1.0", desc)
            self._prompt_has_placeholder = False
            dialog.destroy()
            self._set_status("已复制描述到提示词")

        btn_row = tk.Frame(dialog, bg=C["surface"])
        btn_row.pack(fill="x", padx=16, pady=(0, 16))

        tk.Button(btn_row, text="复制到提示词", command=copy_to_prompt,
                  bg=C["accent"], fg=C["toolbar_bg"],
                  activebackground=C["accent"], activeforeground=C["toolbar_bg"],
                  font=("Microsoft YaHei UI", 10, "bold"),
                  relief="flat", cursor="hand2", pady=4).pack(side="left", fill="x", expand=True, padx=(0, 4))
        tk.Button(btn_row, text="关闭", command=dialog.destroy,
                  bg=C["btn_bg"], fg=C["text"],
                  activebackground=C["btn_hover"], activeforeground=C["text"],
                  font=("Microsoft YaHei UI", 10),
                  relief="flat", cursor="hand2", pady=4).pack(side="left", fill="x", expand=True)

    def _on_compare(self):
        if not self._ensure_idle("查看对比"):
            return
        if self.current_image is None:
            self._set_status("编辑区没有图片，无法对比")
            return

        before = None
        before_label = "对比基准"
        if self._compare_sources:
            before = self._build_compare_contact_sheet(
                self._compare_sources,
                self._compare_source_label or "生成前输入图",
            )
            n_sources = len(self._compare_sources)
            base_label = self._compare_source_label or "生成前输入图"
            before_label = base_label if n_sources == 1 else f"{base_label}（{n_sources} 张）"
        if before is None and self._undo_stack:
            try:
                before = ImageGenerator.b64_to_image(self._undo_stack[-1])
                before_label = "上一版本"
            except Exception:
                before = None
        if before is None and self._compare_b64:
            try:
                before = ImageGenerator.b64_to_image(self._compare_b64)
            except Exception:
                before = self._compare_image
        if before is None and self._compare_image is not None:
            before = self._compare_image

        if before is None:
            self._compare_image = self.current_image.copy()
            self._compare_b64 = self.current_b64
            self._set_status("已记录对比基准，修改后再点「对比」查看差异")
            return

        after = self.current_image
        if before is None or after is None:
            self._set_status("请先点「对比」记录当前图片作为基准，修改后再点「对比」查看差异")
            return

        dialog = tk.Toplevel(self)
        dialog.title("前后对比")
        dialog.configure(bg=C["bg"])
        dialog.transient(self)
        self._center_dialog(dialog, 1100, 620)

        dialog._photo_refs = []

        # ── Helper: create a zoomable/pannable canvas panel ──
        def make_zoomable_panel(parent, img, label_text, label_color):
            frame = tk.Frame(parent, bg=C["surface"])
            frame.pack(side="left", fill="both", expand=True, padx=(8, 4), pady=8)

            header = tk.Frame(frame, bg=C["surface"])
            header.pack(fill="x", pady=(8, 4))
            tk.Label(header, text=label_text, bg=C["surface"], fg=label_color,
                     font=("Microsoft YaHei UI", 11, "bold")).pack(side="left", padx=4)
            zoom_lbl = tk.Label(header, text="100%", bg=C["surface"], fg=C["text_dim"],
                                font=("Consolas", 9))
            zoom_lbl.pack(side="right", padx=4)

            canvas = tk.Canvas(frame, bg=C["canvas_bg"], highlightthickness=0)
            canvas.pack(fill="both", expand=True, padx=4, pady=(0, 4))

            state = {"zoom": 1.0, "pan_x": 0, "pan_y": 0, "photo": None,
                     "dragging": False, "drag_moved": False, "sx": 0, "sy": 0}

            def draw():
                cw = canvas.winfo_width()
                ch = canvas.winfo_height()
                if cw < 10 or ch < 10:
                    return
                iw, ih = img.size
                base_scale = min(cw / iw, ch / ih, 1.0)
                final_scale = base_scale * state["zoom"]
                new_w = max(1, int(iw * final_scale))
                new_h = max(1, int(ih * final_scale))
                max_dim = 8192
                if new_w > max_dim or new_h > max_dim:
                    ls = max_dim / max(new_w, new_h)
                    new_w = max(1, int(new_w * ls))
                    new_h = max(1, int(new_h * ls))
                try:
                    resized = img.resize((new_w, new_h), Image.LANCZOS)
                    state["photo"] = ImageTk.PhotoImage(resized)
                    dialog._photo_refs.append(state["photo"])
                except Exception:
                    return
                x = cw // 2 + state["pan_x"]
                y = ch // 2 + state["pan_y"]
                canvas.delete("all")
                canvas.create_image(x, y, image=state["photo"], anchor="center")
                zoom_lbl.config(text=f"{state['zoom']*100:.0f}%")

            def fit():
                cw = canvas.winfo_width()
                ch = canvas.winfo_height()
                if cw < 10 or ch < 10:
                    return
                iw, ih = img.size
                state["zoom"] = 1.0
                state["pan_x"] = 0
                state["pan_y"] = 0
                draw()

            def on_scroll(event):
                if event.num == 4 or (hasattr(event, 'delta') and event.delta > 0):
                    factor = 1.15
                elif event.num == 5 or (hasattr(event, 'delta') and event.delta < 0):
                    factor = 1 / 1.15
                else:
                    return
                old_z = state["zoom"]
                new_z = max(0.05, min(old_z * factor, 20.0))
                ratio = new_z / old_z
                state["pan_x"] = int(state["pan_x"] * ratio)
                state["pan_y"] = int(state["pan_y"] * ratio)
                state["zoom"] = new_z
                draw()

            def on_press(event):
                state["dragging"] = True
                state["drag_moved"] = False
                state["sx"] = event.x
                state["sy"] = event.y

            def on_drag(event):
                if not state["dragging"]:
                    return
                dx = event.x - state["sx"]
                dy = event.y - state["sy"]
                if not state["drag_moved"] and (abs(dx) > 4 or abs(dy) > 4):
                    state["drag_moved"] = True
                if state["drag_moved"]:
                    state["pan_x"] += dx
                    state["pan_y"] += dy
                    state["sx"] = event.x
                    state["sy"] = event.y
                    draw()

            def on_release(event):
                state["dragging"] = False

            def on_dblclick(event):
                self._show_fullscreen_viewer(img, title=label_text)

            canvas.bind("<Configure>", lambda e: dialog.after(80, fit))
            canvas.bind("<MouseWheel>", on_scroll)
            canvas.bind("<Button-4>", on_scroll)
            canvas.bind("<Button-5>", on_scroll)
            canvas.bind("<ButtonPress-1>", on_press)
            canvas.bind("<B1-Motion>", on_drag)
            canvas.bind("<ButtonRelease-1>", on_release)
            canvas.bind("<Double-Button-1>", on_dblclick)

            return canvas, fit

        before_canvas, before_fit = make_zoomable_panel(dialog, before, before_label, C["accent"])
        after_canvas, after_fit = make_zoomable_panel(dialog, after, "修改后", C["green"])

        # Bottom button row
        btn_row = tk.Frame(dialog, bg=C["bg"])
        btn_row.pack(fill="x", padx=8, pady=(0, 8))

        tk.Button(btn_row, text="适应窗口", command=lambda: (before_fit(), after_fit()),
                  bg=C["btn_bg"], fg=C["text"],
                  activebackground=C["btn_hover"], activeforeground=C["text"],
                  font=("Microsoft YaHei UI", 10),
                  relief="flat", cursor="hand2", pady=4).pack(side="left", fill="x", expand=True)
        tk.Button(btn_row, text="关闭", command=dialog.destroy,
                  bg=C["btn_bg"], fg=C["text"],
                  activebackground=C["btn_hover"], activeforeground=C["text"],
                  font=("Microsoft YaHei UI", 10),
                  relief="flat", cursor="hand2", pady=4).pack(side="left", fill="x", expand=True)

        dialog.after(100, before_fit)
        dialog.after(100, after_fit)

        self._compare_image = None
        self._compare_b64 = None

    # ── 蒙版编辑（局部重绘） ──────────────────────────────

    def _mask_has_content(self):
        if not hasattr(self, "_mask_image") or self._mask_image is None:
            return False
        try:
            alpha = self._mask_image.getchannel("A")
            extrema = alpha.getextrema()
            return bool(extrema and extrema[1] > 0)
        except Exception:
            return False

    def _deactivate_mask_mode(self, preserve_mask=True):
        keep_mask = bool(preserve_mask and self._mask_has_content())
        self._mask_mode = False
        self._mask_b64 = None
        if not keep_mask:
            self._clear_mask_session()
        self._mask_painting = False
        self._mask_last_canvas_pos = None
        self.canvas.config(cursor="")
        self.canvas.delete("mask_overlay")
        self.canvas.delete("mask_cursor")
        self.canvas.delete("mask_border")
        self.canvas.unbind("<Motion>")
        self.canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self.unbind("<bracketleft>")
        self.unbind("<bracketright>")
        self.unbind("<Delete>")
        self._draw_image_on_canvas()
        self._refresh_edit_action_state()
        if keep_mask:
            self._set_status("蒙版已保留：直接生成会执行局部编辑；再次点「蒙版」可继续涂抹")
        else:
            self._set_status("蒙版模式已关闭")

    def _on_mask_toggle(self):
        """Toggle mask drawing mode on/off"""
        if not self._ensure_idle("使用蒙版编辑"):
            return
        if not self.current_b64:
            messagebox.showwarning("蒙版编辑", "编辑区没有图片，请先上传、生成或双击历史图后再使用蒙版")
            return
        if self._preview_override_image is not None:
            messagebox.showwarning("蒙版编辑", "当前正在浏览历史图，请按 Esc 返回编辑区图片后再使用蒙版")
            return
        if self._mask_mode:
            self._deactivate_mask_mode(preserve_mask=True)
        else:
            self._mask_mode = True
            if self._mask_image is None or self._mask_image.size != self.current_image.size:
                self._mask_image = Image.new("RGBA", self.current_image.size, (0, 0, 0, 0))
                self._clear_mask_history()
            self._mask_painting = False
            self._mask_last_canvas_pos = None
            self.canvas.config(cursor="crosshair")
            # Bind mask drawing events
            self.canvas.bind("<Button-1>", self._on_mask_paint_start)
            self.canvas.bind("<B1-Motion>", self._on_mask_paint_move)
            self.canvas.bind("<ButtonRelease-1>", self._on_mask_paint_end)
            self.canvas.bind("<Motion>", self._on_mask_cursor_move)
            # Bind mask-specific keyboard shortcuts: [ and ] to adjust brush size, Delete to clear
            self.bind("<bracketleft>", lambda e: self._adjust_mask_brush(-5))
            self.bind("<bracketright>", lambda e: self._adjust_mask_brush(5))
            self.bind("<Delete>", lambda e: self._clear_mask_painting())
            # Draw mask overlay
            self._draw_mask_overlay()
            self._refresh_edit_action_state()
            self._set_status("蒙版模式：左键涂抹 | 点上方撤销/重做或 Ctrl+Z/Y | 右键平移 | 滚轮缩放 | [ ] 调笔刷 | Esc 退出")

    def _on_mask_paint_start(self, event):
        """Start painting on the mask"""
        self._push_mask_undo()
        self._refresh_edit_action_state()
        self._mask_painting = True
        self._mask_last_canvas_pos = (event.x, event.y)
        self._paint_mask_at(event.x, event.y)

    def _on_mask_paint_move(self, event):
        """Continue painting on the mask while dragging"""
        if getattr(self, '_mask_painting', False):
            self._paint_mask_at(event.x, event.y, last_canvas_pos=self._mask_last_canvas_pos)
            self._mask_last_canvas_pos = (event.x, event.y)

    def _on_mask_paint_end(self, event):
        """Stop painting on the mask"""
        self._mask_painting = False
        self._mask_last_canvas_pos = None

    def _adjust_mask_brush(self, delta):
        """Adjust mask brush size by delta pixels. Clamped to [4, 200]."""
        if not self._mask_mode:
            return
        self._mask_brush_size = max(4, min(200, self._mask_brush_size + delta))
        self._set_status(f"蒙版笔刷大小: {self._mask_brush_size}px")

    def _clear_mask_painting(self):
        """Clear all mask painting (reset to blank mask) without exiting mask mode."""
        if not self._mask_mode or not self.current_image:
            return
        if not self._mask_has_content():
            self._set_status("蒙版已经是空的")
            return
        self._push_mask_undo()
        self._mask_image = Image.new("RGBA", self.current_image.size, (0, 0, 0, 0))
        self._mask_painting = False
        self._mask_last_canvas_pos = None
        self._draw_mask_overlay()
        self._refresh_edit_action_state()
        self._set_status("蒙版已清除（可点撤销图标或 Ctrl+Z 撤回）")

    def _on_mask_cursor_move(self, event):
        """Show brush cursor on the canvas.

        The cursor size is in canvas pixels and stays visually constant
        regardless of zoom — same behavior as Photoshop's brush cursor.
        The actual paint radius in image pixels is computed by
        _get_mask_brush_radius_image() which accounts for zoom.
        """
        if not self._mask_mode:
            return
        self.canvas.delete("mask_cursor")
        r = self._mask_brush_size // 2
        self.canvas.create_oval(
            event.x - r, event.y - r, event.x + r, event.y + r,
            outline=C["red"], width=1, tags="mask_cursor"
        )

    def _canvas_to_image_coords(self, canvas_x, canvas_y):
        """Convert canvas coordinates to image coordinates under current zoom/pan."""
        if not self.current_image:
            return None
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        iw, ih = self.current_image.size
        base_scale = min(cw / iw, ch / ih, 1.0)
        final_scale = max(base_scale * self._canvas_zoom, 1e-6)

        # Image center on canvas
        img_cx = cw / 2 + self._canvas_pan_x
        img_cy = ch / 2 + self._canvas_pan_y

        img_x = (canvas_x - img_cx) / final_scale + iw / 2
        img_y = (canvas_y - img_cy) / final_scale + ih / 2
        return img_x, img_y

    def _get_mask_brush_radius_image(self):
        """Return brush radius in image pixels so the export matches the cursor."""
        if not self.current_image:
            return 1
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        iw, ih = self.current_image.size
        base_scale = min(cw / iw, ch / ih, 1.0)
        final_scale = max(base_scale * self._canvas_zoom, 1e-6)
        return max(1, int(round((self._mask_brush_size / 2) / final_scale)))

    def _paint_mask_at(self, canvas_x, canvas_y, last_canvas_pos=None):
        """Paint a circle or connected stroke on the mask image."""
        if not self.current_image or not hasattr(self, '_mask_image') or self._mask_image is None:
            return
        iw, ih = self.current_image.size
        current = self._canvas_to_image_coords(canvas_x, canvas_y)
        if current is None:
            return
        img_x = max(0, min(iw - 1, int(round(current[0]))))
        img_y = max(0, min(ih - 1, int(round(current[1]))))
        brush_r = self._get_mask_brush_radius_image()

        # Paint on mask image (red semi-transparent = edit region)
        from PIL import ImageDraw
        draw = ImageDraw.Draw(self._mask_image)
        if last_canvas_pos is not None:
            prev = self._canvas_to_image_coords(last_canvas_pos[0], last_canvas_pos[1])
            if prev is not None:
                prev_x = max(0, min(iw - 1, int(round(prev[0]))))
                prev_y = max(0, min(ih - 1, int(round(prev[1]))))
                draw.line(
                    [(prev_x, prev_y), (img_x, img_y)],
                    fill=(255, 0, 0, 128),
                    width=max(1, brush_r * 2 + 1)
                )
                draw.ellipse(
                    [prev_x - brush_r, prev_y - brush_r, prev_x + brush_r, prev_y + brush_r],
                    fill=(255, 0, 0, 128)
                )
        draw.ellipse(
            [img_x - brush_r, img_y - brush_r, img_x + brush_r, img_y + brush_r],
            fill=(255, 0, 0, 128)
        )
        self._draw_mask_overlay()

    def _draw_mask_overlay(self):
        """Draw the mask overlay on the canvas (with zoom and pan support)"""
        if not self._mask_mode or not self.current_image:
            return
        # Create overlay: composite mask on top of current image
        overlay = self.current_image.convert("RGBA")
        composite = Image.alpha_composite(overlay, self._mask_image)

        # Scale to fit canvas, then apply user zoom
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        iw, ih = composite.size
        base_scale = min(cw / iw, ch / ih, 1.0)
        final_scale = base_scale * self._canvas_zoom
        new_w = max(1, int(iw * final_scale))
        new_h = max(1, int(ih * final_scale))

        # Limit to prevent memory issues
        max_dim = 8192
        if new_w > max_dim or new_h > max_dim:
            limit_scale = max_dim / max(new_w, new_h)
            new_w = max(1, int(new_w * limit_scale))
            new_h = max(1, int(new_h * limit_scale))

        resized = composite.resize((new_w, new_h), Image.LANCZOS)

        self._main_photo = ImageTk.PhotoImage(resized)
        self.canvas.delete("all")
        # Use center anchor + pan offset (same as _draw_image_on_canvas)
        x = cw // 2 + self._canvas_pan_x
        y = ch // 2 + self._canvas_pan_y
        self.canvas.create_image(x, y, image=self._main_photo, anchor="center")

        # Show zoom indicator when zoomed
        if self._canvas_zoom != 1.0:
            pct = int(self._canvas_zoom * 100)
            self.canvas.create_text(cw - 8, ch - 8, text=f"{pct}%",
                                    anchor="se", fill=C["accent"],
                                    font=("Consolas", 10, "bold"),
                                    tags="zoom_indicator")

        # Mask mode border indicator — red border around canvas to clearly
        # indicate that left-click now paints instead of panning
        border_w = 3
        self.canvas.create_rectangle(
            border_w, border_w, cw - border_w, ch - border_w,
            outline=C["red"], width=border_w, tags="mask_border"
        )
        # Mask mode status text in top-left
        self.canvas.create_text(
            10, 10,
            text=f"[蒙版模式] 笔刷:{self._mask_brush_size}px | 点撤销/重做或 Ctrl+Z/Y | [ ]调大小 | Del清除 | Esc退出",
            anchor="nw", fill=C["red"],
            font=("Microsoft YaHei UI", 9, "bold"),
            tags="mask_border"
        )

    def _finalize_mask(self):
        """Convert the painted mask to a proper API mask (transparent=edit, opaque=preserve)"""
        if not hasattr(self, '_mask_image') or self._mask_image is None:
            return None
        # The mask image has red semi-transparent where user painted (edit region)
        # API mask: transparent pixels = regenerate, opaque = preserve
        # So we need to invert: painted areas become transparent, unpainted become opaque
        iw, ih = self._mask_image.size
        # Get alpha channel - where user painted, alpha > 0
        r, g, b, a = self._mask_image.split()
        # Invert using pure PIL: painted (a>0) -> 0 (transparent=edit), unpainted (a=0) -> 255 (opaque=preserve)
        inverted_a = a.point(lambda v: 0 if v > 0 else 255)
        # Create white image with inverted alpha
        mask = Image.new("RGBA", (iw, ih), (255, 255, 255, 255))
        mask.putalpha(inverted_a)
        return ImageGenerator.image_to_b64(mask, fmt="PNG")

    # ── 拖拽到编辑区（已合并到 _on_edit_strip_drop） ──

    # ── 全屏高清查看器 ──────────────────────────────

    def _show_fullscreen_viewer(self, pil_image, title="图片查看器"):
        """Open a fullscreen image viewer with zoom/pan support"""
        if pil_image is None:
            return

        viewer = tk.Toplevel(self)
        viewer.title(title)
        viewer.configure(bg="#000000")
        viewer.attributes("-topmost", True)
        viewer.grab_set()

        # Fullscreen
        sw = viewer.winfo_screenwidth()
        sh = viewer.winfo_screenheight()
        viewer.geometry(f"{sw}x{sh}+0+0")

        state = {"zoom": 1.0, "pan_x": 0, "pan_y": 0, "photo": None}

        # Top bar
        top_bar = tk.Frame(viewer, bg="#1a1a2e", height=36)
        top_bar.pack(fill="x")
        top_bar.pack_propagate(False)

        info_text = f"{pil_image.size[0]}x{pil_image.size[1]} | {pil_image.mode}"
        tk.Label(top_bar, text=f"  {title}  —  {info_text}", bg="#1a1a2e", fg="#cdd6f4",
                 font=("Microsoft YaHei UI", 10)).pack(side="left", padx=4, pady=4)

        zoom_label = tk.Label(top_bar, text="100%", bg="#1a1a2e", fg="#89b4fa",
                              font=("Consolas", 10, "bold"))
        zoom_label.pack(side="left", padx=12, pady=4)

        # Fit to screen button
        tk.Button(top_bar, text="适应窗口", command=lambda: fit_to_screen(),
                  bg="#45475a", fg="#cdd6f4", font=("Microsoft YaHei UI", 9),
                  relief="flat", cursor="hand2", pady=2).pack(side="right", padx=4, pady=4)
        # 1:1 button
        tk.Button(top_bar, text="1:1", command=lambda: set_zoom(1.0),
                  bg="#45475a", fg="#cdd6f4", font=("Microsoft YaHei UI", 9),
                  relief="flat", cursor="hand2", pady=2).pack(side="right", padx=4, pady=4)
        # Close button
        tk.Button(top_bar, text="✕ 关闭", command=viewer.destroy,
                  bg="#f38ba8", fg="#1e1e2e", font=("Microsoft YaHei UI", 9, "bold"),
                  relief="flat", cursor="hand2", pady=2).pack(side="right", padx=4, pady=4)

        # Canvas
        canvas = tk.Canvas(viewer, bg="#000000", highlightthickness=0)
        canvas.pack(fill="both", expand=True)

        def draw():
            iw, ih = pil_image.size
            z = state["zoom"]
            new_w = max(1, int(iw * z))
            new_h = max(1, int(ih * z))

            # Limit to reasonable size to avoid memory issues
            max_dim = 8192
            if new_w > max_dim or new_h > max_dim:
                limit_scale = max_dim / max(new_w, new_h)
                new_w = max(1, int(new_w * limit_scale))
                new_h = max(1, int(new_h * limit_scale))

            try:
                resized = pil_image.resize((new_w, new_h), Image.LANCZOS)
                state["photo"] = ImageTk.PhotoImage(resized)
            except Exception:
                return

            cw = canvas.winfo_width()
            ch = canvas.winfo_height()
            if cw < 10 or ch < 10:
                cw, ch = sw, sh - 36

            x = (cw - new_w) // 2 + state["pan_x"]
            y = (ch - new_h) // 2 + state["pan_y"]

            canvas.delete("all")
            canvas.create_image(x, y, image=state["photo"], anchor="nw")

            zoom_label.config(text=f"{z*100:.0f}%")

        def fit_to_screen():
            cw = canvas.winfo_width()
            ch = canvas.winfo_height()
            if cw < 10 or ch < 10:
                cw, ch = sw, sh - 36
            iw, ih = pil_image.size
            state["zoom"] = min(cw / iw, ch / ih)
            state["pan_x"] = 0
            state["pan_y"] = 0
            draw()

        def set_zoom(z):
            state["zoom"] = z
            state["pan_x"] = 0
            state["pan_y"] = 0
            draw()

        def on_scroll(event):
            # Determine scroll direction
            if event.num == 4 or (hasattr(event, 'delta') and event.delta > 0):
                factor = 1.15
            elif event.num == 5 or (hasattr(event, 'delta') and event.delta < 0):
                factor = 1 / 1.15
            else:
                return

            old_z = state["zoom"]
            new_z = max(0.05, min(old_z * factor, 20.0))

            # Zoom towards mouse position
            mx = canvas.canvasx(event.x)
            my = canvas.canvasy(event.y)
            cw = canvas.winfo_width()
            ch = canvas.winfo_height()
            img_cx = cw // 2 + state["pan_x"]
            img_cy = ch // 2 + state["pan_y"]

            # Adjust pan so the point under cursor stays fixed
            ratio = new_z / old_z
            state["pan_x"] = int(mx - ratio * (mx - img_cx) - cw // 2 + (ratio - 1) * (mx - cw // 2))
            state["pan_y"] = int(my - ratio * (my - img_cy) - ch // 2 + (ratio - 1) * (my - ch // 2))
            # Simpler: just keep pan proportional
            state["pan_x"] = int(state["pan_x"] * ratio)
            state["pan_y"] = int(state["pan_y"] * ratio)
            state["zoom"] = new_z
            draw()

        pan_state = {"dragging": False, "sx": 0, "sy": 0}

        def on_pan_start(event):
            pan_state["dragging"] = True
            pan_state["sx"] = event.x
            pan_state["sy"] = event.y

        def on_pan_move(event):
            if pan_state["dragging"]:
                dx = event.x - pan_state["sx"]
                dy = event.y - pan_state["sy"]
                state["pan_x"] += dx
                state["pan_y"] += dy
                pan_state["sx"] = event.x
                pan_state["sy"] = event.y
                draw()

        def on_pan_end(event):
            pan_state["dragging"] = False

        def on_key(event):
            if event.keysym == "Escape":
                viewer.destroy()
            elif event.keysym == "plus" or event.keysym == "equal":
                state["zoom"] = min(state["zoom"] * 1.2, 20.0)
                draw()
            elif event.keysym == "minus":
                state["zoom"] = max(state["zoom"] / 1.2, 0.05)
                draw()
            elif event.keysym == "0":
                fit_to_screen()
            elif event.keysym == "1":
                set_zoom(1.0)
            elif event.keysym == "f":
                # Toggle fullscreen borderless
                is_full = viewer.attributes("-fullscreen")
                viewer.attributes("-fullscreen", not is_full)

        canvas.bind("<MouseWheel>", on_scroll)
        canvas.bind("<Button-4>", on_scroll)
        canvas.bind("<Button-5>", on_scroll)
        canvas.bind("<ButtonPress-1>", on_pan_start)
        canvas.bind("<B1-Motion>", on_pan_move)
        canvas.bind("<ButtonRelease-1>", on_pan_end)
        canvas.bind("<ButtonPress-3>", on_pan_start)
        canvas.bind("<B3-Motion>", on_pan_move)
        canvas.bind("<ButtonRelease-3>", on_pan_end)
        viewer.bind("<Key>", on_key)

        # Initial fit
        viewer.after(50, fit_to_screen)
        viewer.focus_set()

    # ── 画布双击全屏查看 ──────────────────────────────

    def _on_canvas_dblclick(self, event):
        """Double-click on canvas to open fullscreen viewer"""
        img = self._get_display_image()
        if img is not None and not self._mask_mode:
            title = "当前显示图" if self._preview_override_image is not None else "编辑区图片"
            self._show_fullscreen_viewer(img, title=title)

    # ── 历史记录双击加载到编辑区 ──────────────────────

    def _on_hist_dblclick(self, idx):
        """Double-click on history thumbnail to add to the edit strip"""
        self._cancel_pending_history_click()
        self._add_hist_to_strip(idx)

    def _add_hist_to_strip(self, idx):
        """Add a history image to the edit strip (as primary if none, else as reference)"""
        if not self._ensure_idle("将历史图片加入编辑区"):
            return
        rec = self.history_mgr.get_record(idx)
        img_path = self.history_mgr.get_image_path(idx)
        if not rec or not img_path or not img_path.exists():
            return
        try:
            self._add_image_to_strip_path(img_path, f"历史: {rec.get('prompt', '')[:12]}")
        except Exception as e:
            messagebox.showerror("添加失败", str(e))

    def _on_hist_right_click(self, event, idx):
        """Right-click context menu for history items"""
        self._cancel_pending_history_click()
        rec = self.history_mgr.get_record(idx)
        if not rec:
            return
        selected_indices = self._get_hist_selected_ordered()
        multi_selected = idx in self._hist_selected and len(selected_indices) > 1
        menu = tk.Menu(self, tearoff=0, bg=C["surface2"], fg=C["text"],
                       activebackground=C["accent"], activeforeground=C["toolbar_bg"],
                       font=("Microsoft YaHei UI", 9))
        if multi_selected:
            total = len(selected_indices)
            if self._has_workspace_image():
                add_label = f"加入编辑区（选中 {total} 张）"
            else:
                add_label = (
                    "加入编辑区（设为主图）"
                    if total == 1
                    else f"加入编辑区（首个为主图，其余 {total - 1} 张为参考图）"
                )
            menu.add_command(label=add_label, command=self._on_add_hist_selected_to_strip)
            menu.add_command(label=f"删除选中（{total} 条）", command=self._delete_selected_history)
            menu.add_separator()
            menu.add_command(label="取消多选", command=self._clear_hist_selection_ui)
            menu.add_separator()
            menu.add_command(label="全屏查看当前项", command=lambda: self._hist_view_fullscreen(idx))
        else:
            menu.add_command(label="加入编辑区", command=lambda: self._add_hist_to_strip(idx))
            menu.add_command(label="设为主图（替换当前主图）", command=lambda: self._load_from_history(idx))
            menu.add_separator()
            menu.add_command(label="全屏查看", command=lambda: self._hist_view_fullscreen(idx))
            menu.add_separator()
            menu.add_command(label="删除", command=lambda: self._delete_from_history(idx))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _add_hist_single_as_ref(self, idx):
        """Add a single history item as reference image"""
        if not self._ensure_idle("将历史图片加入参考图"):
            return
        rec = self.history_mgr.get_record(idx)
        img_path = self.history_mgr.get_image_path(idx)
        if not rec or not img_path or not img_path.exists():
            return
        try:
            self._add_ref_image_path(img_path, f"历史: {rec.get('prompt', '')[:12]}")
        except Exception as e:
            messagebox.showerror("添加失败", str(e))

    def _hist_view_fullscreen(self, idx):
        """View a history item fullscreen"""
        rec = self.history_mgr.get_record(idx)
        if not rec:
            return
        img_path = self.history_mgr.get_image_path(idx)
        if img_path and img_path.exists():
            try:
                img = Image.open(img_path)
                img.load()
                self._show_fullscreen_viewer(img, title=f"历史记录 #{idx+1}")
            except Exception as e:
                messagebox.showerror("查看失败", str(e))

    # ── 参考图片双击全屏查看 ──────────────────────────

    def _on_ref_dblclick(self, idx):
        """Double-click on reference image to view fullscreen"""
        if idx < len(self._ref_images):
            img = self._ref_images[idx]["image"]
            name = self._ref_images[idx].get("name", f"参考图片 {idx+1}")
            self._show_fullscreen_viewer(img, title=name)

    # ── 画布滚轮缩放 ──────────────────────────────

    def _on_canvas_scroll(self, event):
        """Mouse wheel zoom on main canvas (works in both normal and mask mode).
        In mask mode, Alt+wheel adjusts brush size instead of zoom."""
        if self._preview_override_image is None and self.current_image is None:
            return

        # In mask mode, Alt+wheel adjusts brush size
        if self._mask_mode and (event.state & 0x20000):  # Alt modifier
            if event.num == 4 or (hasattr(event, 'delta') and event.delta > 0):
                self._adjust_mask_brush(3)
            elif event.num == 5 or (hasattr(event, 'delta') and event.delta < 0):
                self._adjust_mask_brush(-3)
            return

        # Determine zoom direction
        if event.num == 4 or (hasattr(event, 'delta') and event.delta > 0):
            factor = 1.15
        elif event.num == 5 or (hasattr(event, 'delta') and event.delta < 0):
            factor = 1 / 1.15
        else:
            return

        old_zoom = self._canvas_zoom
        self._canvas_zoom = max(0.1, min(old_zoom * factor, 10.0))

        # Zoom towards mouse position
        mx = self.canvas.canvasx(event.x)
        my = self.canvas.canvasy(event.y)
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()

        ratio = self._canvas_zoom / old_zoom
        # Keep the point under the mouse cursor fixed
        old_center_x = cw / 2 + self._canvas_pan_x
        old_center_y = ch / 2 + self._canvas_pan_y
        new_center_x = mx - (mx - old_center_x) * ratio
        new_center_y = my - (my - old_center_y) * ratio
        self._canvas_pan_x = int(new_center_x - cw / 2)
        self._canvas_pan_y = int(new_center_y - ch / 2)

        if self._mask_mode:
            self._draw_mask_overlay()
        else:
            self._draw_image_on_canvas()

    # ── 画布左键拖拽平移 ──────────────────────────

    def _on_canvas_press(self, event):
        """Left button press: record start position for potential drag"""
        if not self._get_display_image():
            return
        self._canvas_panning = True
        self._drag_moved = False
        self._pan_start_x = event.x
        self._pan_start_y = event.y

    def _on_canvas_drag(self, event):
        """Left button drag: pan the image"""
        if not self._canvas_panning:
            return
        dx = event.x - self._pan_start_x
        dy = event.y - self._pan_start_y
        if not self._drag_moved and (abs(dx) > self._drag_threshold or abs(dy) > self._drag_threshold):
            self._drag_moved = True
        if self._drag_moved:
            self._canvas_pan_x += dx
            self._canvas_pan_y += dy
            self._pan_start_x = event.x
            self._pan_start_y = event.y
            if self._mask_mode:
                self._draw_mask_overlay()
            else:
                self._draw_image_on_canvas()

    def _on_canvas_release(self, event):
        """Left button release: stop panning"""
        self._canvas_panning = False

    # ── 画布右键拖拽平移（蒙版模式下也可用）──────────────────────────

    def _on_canvas_rclick_press(self, event):
        """Right button press: start panning (works in mask mode too)"""
        if not self._get_display_image():
            return
        self._rclick_panning = True
        self._pan_start_x = event.x
        self._pan_start_y = event.y

    def _on_canvas_rclick_drag(self, event):
        """Right button drag: pan the image"""
        if not getattr(self, '_rclick_panning', False):
            return
        dx = event.x - self._pan_start_x
        dy = event.y - self._pan_start_y
        self._canvas_pan_x += dx
        self._canvas_pan_y += dy
        self._pan_start_x = event.x
        self._pan_start_y = event.y
        if self._mask_mode:
            self._draw_mask_overlay()
        else:
            self._draw_image_on_canvas()

    def _on_canvas_rclick_release(self, event):
        """Right button release: stop panning"""
        self._rclick_panning = False

    # ── 重置画布缩放/平移 ──────────────────────────

    def _reset_canvas_view(self):
        """Reset zoom and pan to default fit"""
        self._canvas_zoom = 1.0
        self._canvas_pan_x = 0
        self._canvas_pan_y = 0

    def _on_fullscreen_view(self):
        """Toolbar button: open fullscreen viewer"""
        img = self._get_display_image()
        if img is not None:
            title = "当前显示图" if self._preview_override_image is not None else "编辑区图片"
            self._show_fullscreen_viewer(img, title=title)

    def _on_fit_view(self):
        """Toolbar button: reset zoom/pan to fit canvas"""
        self._reset_canvas_view()
        if self._mask_mode:
            self._draw_mask_overlay()
        else:
            self._draw_image_on_canvas()
        self._set_status("视图已重置为适应窗口")

    def _on_image_info(self):
        img = self._get_display_image()
        if img is None:
            self._set_status("当前画布没有图片可操作")
            return
        info_lines = [
            f"尺寸: {img.size[0]} x {img.size[1]} 像素",
            f"模式: {img.mode}",
            f"格式: {FORMAT_DISPLAY_NAMES.get(self._resolve_format(), self._resolve_format().upper())}",
            f"生成尺寸设置: {self.size_var.get()}",
            f"风格: {self.style_var.get()}",
            f"模型: {self.model_var.get()}",  # already Chinese display name
        ]
        display_b64 = self._get_display_b64()
        if self._preview_override_image is not None:
            info_lines.append(f"来源: {self._preview_override_label or '仅浏览预览'}")
        else:
            info_lines.append("来源: 编辑区图片")
        if display_b64:
            b64_len = len(display_b64)
            raw_bytes = b64_len * 3 // 4
            info_lines.append(f"原始大小: ~{raw_bytes:,} 字节 (~{raw_bytes / 1024 / 1024:.2f} MB)")

        messagebox.showinfo("图片信息", "\n".join(info_lines))

    # ── 回调更新 ──────────────────────────────

    def _update_partial(self, token, b64, count, elapsed):
        if token != self._job_token or not self.is_generating:
            return
        # Reset heartbeat timer since we just got a partial result
        self._cancel_progress_timer()
        if b64 is None:
            self._set_status(f"正在重试... (第 {count} 次)")
            self.progress_var.set(0)
            # Restart heartbeat for retry wait
            self._progress_timer_id = self.after(3000, self._progress_heartbeat, token, self.start_time)
            return
        try:
            b64, img = self._prepare_result_for_output(b64, self._active_result_target_size)
            self._show_image(img)
            self.current_b64 = b64
            self._refresh_edit_action_state()
            progress = min(count * 12, 95)
            self.progress_var.set(progress)
            self.time_label.config(text=f"{elapsed:.1f}s | #{count}")
            self._set_status(f"{self._current_job_label} | 第 {count} 帧")
            # Restart heartbeat for next partial wait
            self._progress_timer_id = self.after(3000, self._progress_heartbeat, token, self.start_time)
        except Exception:
            pass

    def _update_done(self, token, b64, count, elapsed, revised_prompt=None, response_id=None):
        if token != self._job_token or not self.is_generating:
            return
        self._cancel_progress_timer()
        current_job_label = getattr(self, "_current_job_label", "") or ""
        is_auto_bg_job = current_job_label.startswith("正在自动换背:")
        is_auto_style_job = current_job_label.startswith("正在应用风格:")
        job_succeeded = False
        try:
            b64, img = self._prepare_result_for_output(b64, self._active_result_target_size)
            self._show_image(img)
            self.current_b64 = b64
            self._primary_is_result = True  # Mark the primary image as an AI-generated result

            # Reset mask after edit — the old mask belongs to the previous image.
            if self._mask_mode:
                self._mask_mode = False
                self._mask_b64 = None
                self._mask_image = None
                self._mask_painting = False
                self._mask_last_canvas_pos = None
                self._clear_mask_history()
                self.canvas.config(cursor="")
                self.canvas.delete("mask_overlay")
                self.canvas.delete("mask_cursor")
                self.canvas.delete("mask_border")
                self.canvas.unbind("<Motion>")
                self.canvas.bind("<ButtonPress-1>", self._on_canvas_press)
                self.canvas.bind("<B1-Motion>", self._on_canvas_drag)
                self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
                self.unbind("<bracketleft>")
                self.unbind("<bracketright>")
                self.unbind("<Delete>")
            elif self._mask_image is not None:
                self._mask_mode = False
                self._mask_b64 = None
                self._mask_image = None
                self._mask_painting = False
                self._mask_last_canvas_pos = None
                self._clear_mask_history()

            self._refresh_edit_action_state()
            self.progress_var.set(100)
            self.time_label.config(text=f"完成 | {elapsed:.1f}s | {count} 帧")
            # Show task-specific completion message
            if is_auto_style_job:
                style_name = current_job_label.replace("正在应用风格: ", "", 1)
                self._set_status(f"风格转换完成！{style_name}已应用，用时 {elapsed:.1f}s")
            elif is_auto_bg_job:
                self._set_status(f"背景替换完成！用时 {elapsed:.1f}s")
            elif current_job_label.startswith("正在替换背景") or current_job_label.startswith("正在移除背景"):
                self._set_status(f"背景处理完成！用时 {elapsed:.1f}s")
            elif current_job_label.startswith("正在编辑"):
                self._set_status(f"编辑完成！用时 {elapsed:.1f}s")
            elif current_job_label.startswith("正在分析"):
                self._set_status(f"分析完成！用时 {elapsed:.1f}s")
            else:
                self._set_status(f"生成完成！用时 {elapsed:.1f}s")

            # Store response_id for iterative editing
            if response_id:
                self._last_response_id = response_id

            # Show revised_prompt if available
            if revised_prompt:
                self._last_revised_prompt = revised_prompt
                self._show_revised_prompt_tooltip(revised_prompt)

            prompt = self._last_prompt or self._get_raw_prompt() or "无描述"
            self._add_to_history(b64, prompt)

            # Show edit result notification bar (input→output summary)
            self._show_edit_result_bar(self._build_edit_summary())
            job_succeeded = True
        except Exception as e:
            self._set_status(f"生成完成但显示失败: {e}")
            self.error_log.add("显示失败", str(e), {
                "b64_length": len(b64) if b64 else 0,
                "elapsed": f"{elapsed:.1f}s",
            })
        finally:
            self._set_generating(False)
        if job_succeeded:
            self._continue_followup_chain(current_job_label)

    def _update_error(self, token, msg):
        if token != self._job_token:
            return
        self._cancel_progress_timer()
        last_response_id = self._last_response_id
        # Clear stale response_id on error to prevent chain failures
        self._last_response_id = None
        self._pending_followups = []
        self._set_status(f"错误: {msg[:80]}")
        self.progress_var.set(0)
        self.time_label.config(text="")
        self._set_generating(False)
        # 记录错误日志（含更多调试信息）
        self.error_log.add("生成失败", msg, {
            "api_base": self.api_base_var.get(),
            "model": self._resolve_model_api_id(),
            "prompt": (self._last_prompt or self._get_raw_prompt() or "")[:100],
            "size": self.size_var.get(),
            "ref_images": len(self._ref_images),
            "has_current": bool(self.current_b64),
            "current_b64_size_kb": f"{len(self.current_b64)/1024:.1f}" if self.current_b64 else "0",
            "ref_image_sizes_kb": ",".join(
                f"{len((ref.get('b64') or ''))/1024:.1f}" for ref in self._ref_images
            ) if self._ref_images else "",
            "last_response_id": last_response_id or "None",
            "debug_log_entries": str(debug_log.count()),
        })
        if "502" in msg or "503" in msg:
            api_base = self.api_base_var.get()
            # Offer fallback: retry as generate (without reference images)
            has_refs = bool(self._ref_images) or bool(self.current_b64)
            if has_refs:
                result = messagebox.askyesno("编辑失败",
                    f"服务器返回 502/503 错误（已自动重试{MAX_RETRIES}次）。\n\n"
                    f"可能原因：\n"
                    f"1. 图片过大或格式不支持\n"
                    f"2. API 服务暂时不可用\n"
                    f"3. 模型名称不正确 — 当前: {self.model_var.get()}\n\n"
                    f"详细信息:\n{msg[:200]}\n\n"
                    f"是否尝试以「生成模式」重试？\n"
                    f"（将不使用参考图片，仅根据文字描述生成）",
                    icon=messagebox.WARNING)
                if result:
                    self._retry_as_generate()
                    return
            else:
                messagebox.showerror("生成失败",
                    f"服务器返回 502/503 错误（服务暂时不可用）。\n\n"
                    f"可能原因：\n"
                    f"1. API 地址配置错误 — 当前: {api_base}\n"
                    f"2. API 服务未启动或正在重启\n"
                    f"3. 模型名称不正确 — 当前: {self.model_var.get()}\n\n"
                    f"详细信息:\n{msg[:300]}\n\n"
                    f"提示: 应用已自动重试{MAX_RETRIES}次。请检查左侧 API 设置后重试。")
        elif "ConnectError" in msg or "连接失败" in msg:
            api_base = self.api_base_var.get()
            messagebox.showerror("连接失败",
                f"无法连接到 API 服务器。\n\n"
                f"当前 API 地址: {api_base}\n\n"
                f"请检查：\n"
                f"1. API 服务是否已启动\n"
                f"2. 端口号是否正确\n"
                f"3. 地址是否可访问\n\n"
                f"详细信息:\n{msg[:300]}")
        else:
            messagebox.showerror("生成失败", msg[:500])
        self._update_error_badge()

    def _show_revised_prompt_tooltip(self, revised_prompt):
        """Show a brief tooltip in the status bar with the model's revised prompt"""
        if revised_prompt:
            self._set_status(f"模型理解: {revised_prompt[:120]}")

    def _on_show_revised_prompt(self):
        """Show full revised prompt in a dialog"""
        if not self._last_revised_prompt:
            messagebox.showinfo("修订提示词", "暂无修订提示词信息（模型未返回修订内容，或尚未执行生成/编辑操作）")
            return
        dialog = tk.Toplevel(self)
        dialog.title("模型修订提示词")
        dialog.configure(bg=C["surface"])
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(True, True)
        self._center_dialog(dialog, 520, 260)

        tk.Label(dialog, text="模型实际理解的提示词:", bg=C["surface"], fg=C["accent"],
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", padx=16, pady=(16, 4))
        txt = tk.Text(dialog, height=6, wrap="word",
                      font=("Microsoft YaHei UI", 10),
                      bg=C["canvas_bg"], fg=C["text"], insertbackground=C["text"],
                      selectbackground=C["surface2"], relief="flat", bd=4)
        txt.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        txt.insert("1.0", self._last_revised_prompt)
        txt.config(state="disabled")

        btn_row = tk.Frame(dialog, bg=C["surface"])
        btn_row.pack(fill="x", padx=16, pady=(0, 12))
        tk.Button(btn_row, text="复制到提示词", command=lambda: [self.prompt_text.delete("1.0", "end"),
                  self.prompt_text.insert("1.0", self._last_revised_prompt),
                  setattr(self, '_prompt_has_placeholder', False), dialog.destroy()],
                  bg=C["accent"], fg=C["toolbar_bg"],
                  font=("Microsoft YaHei UI", 9, "bold"), relief="flat", cursor="hand2",
                  pady=4).pack(side="left", padx=(0, 8))
        tk.Button(btn_row, text="关闭", command=dialog.destroy,
                  bg=C["btn_bg"], fg=C["text"],
                  font=("Microsoft YaHei UI", 9), relief="flat", cursor="hand2",
                  pady=4).pack(side="right")

    def _retry_as_generate(self):
        """Fallback: retry the last edit as a generate request (without reference images)."""
        prompt = self._last_prompt or self._get_raw_prompt() or ""
        if not prompt:
            self._set_status("无法回退：没有可用的提示词（请先执行一次生成或编辑操作）")
            return
        self._set_status("正在以生成模式回退重试...")
        gen = self._create_generator()
        quality_tier = self._resolve_quality()
        api_quality = self._resolve_api_quality(quality_tier)
        size_plan = self._build_edit_size_plan(self._resolve_output_size(), [], quality_tier=quality_tier)
        self._active_result_target_size = size_plan["output_size"]
        self._active_processing_size = size_plan["processing_size_tuple"]
        fmt = self._resolve_format()
        try:
            compression = max(1, min(100, self.compression_var.get()))
        except Exception:
            compression = 100
        token, started_at = self._begin_stream_job("回退生成中...")
        on_partial, on_done, on_error = self._build_stream_callbacks(token, started_at)
        gen.generate_stream(
            prompt=prompt,
            size=size_plan["processing_size"], output_format=fmt, quality=api_quality,
            output_compression=compression,
            on_partial=on_partial, on_done=on_done, on_error=on_error,
        )

    def _update_error_badge(self):
        n = self.error_log.count()
        if n > 0:
            self._error_badge.config(text=f"[{n} 错误日志]", fg=C["red"])
        else:
            self._error_badge.config(text="", fg=C["text_dim"])

    def _show_error_log(self):
        records = self.error_log.get_recent(20)
        if not records:
            messagebox.showinfo("错误日志", "暂无错误记录")
            return

        dialog = tk.Toplevel(self)
        dialog.title(f"错误日志 (最近 {len(records)} 条，共 {self.error_log.count()} 条)")
        dialog.configure(bg=C["surface"])
        dialog.transient(self)
        self._center_dialog(dialog, 700, 500)
        dialog.grab_set()

        # Header
        header = tk.Frame(dialog, bg=C["surface"])
        header.pack(fill="x")
        tk.Label(header, text="错误日志 — 点击条目查看详情，供 AI 分析使用",
                 bg=C["surface"], fg=C["text"],
                 font=("Microsoft YaHei UI", 10, "bold")).pack(side="left", padx=12, pady=8)

        # Text area
        text_frame = tk.Frame(dialog, bg=C["bg"])
        text_frame.pack(fill="both", expand=True, padx=8, pady=4)

        text_widget = tk.Text(text_frame, wrap="word", font=("Consolas", 9),
                              bg=C["canvas_bg"], fg=C["text"],
                              insertbackground=C["text"], relief="flat", bd=4)
        scrollbar = tk.Scrollbar(text_frame, command=text_widget.yview)
        text_widget.config(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        text_widget.pack(side="left", fill="both", expand=True)

        # Format records
        for i, rec in enumerate(reversed(records)):
            tag = f"rec_{i}"
            text_widget.insert("end", f"[{rec.get('time', '?')}] ", tag)
            text_widget.insert("end", f"{rec.get('type', '未知')}\n", tag)
            text_widget.insert("end", f"  {rec.get('message', '')}\n", tag)
            ctx = rec.get("context")
            if ctx:
                for k, v in ctx.items():
                    text_widget.insert("end", f"  {k}: {v}\n", tag)
            text_widget.insert("end", "\n", tag)

        text_widget.config(state="disabled")

        # Bottom buttons
        bottom = tk.Frame(dialog, bg=C["surface"])
        bottom.pack(fill="x")

        def copy_all():
            content = text_widget.get("1.0", "end").strip()
            if content:
                self.clipboard_clear()
                self.clipboard_append(content)
                self._set_status("已复制错误日志到剪贴板")

        tk.Button(bottom, text="复制全部", command=copy_all,
                  bg=C["btn_bg"], fg=C["text"],
                  activebackground=C["btn_hover"], activeforeground=C["text"],
                  font=("Microsoft YaHei UI", 9), relief="flat", cursor="hand2",
                  pady=3).pack(side="left", padx=12, pady=8)

        def clear_log():
            if messagebox.askyesno("确认", "确定要清空所有错误日志吗？", parent=dialog):
                self.error_log.clear()
                self._update_error_badge()
                dialog.destroy()
                self._set_status("已清空错误日志")

        tk.Button(bottom, text="清空日志", command=clear_log,
                  bg=C["btn_bg"], fg=C["red"],
                  activebackground=C["btn_hover"], activeforeground=C["red"],
                  font=("Microsoft YaHei UI", 9), relief="flat", cursor="hand2",
                  pady=3).pack(side="left", padx=4, pady=8)

        def show_debug():
            dialog.destroy()
            self._show_debug_log()

        tk.Button(bottom, text="调试日志", command=show_debug,
                  bg=C["btn_bg"], fg=C["accent"],
                  activebackground=C["btn_hover"], activeforeground=C["accent"],
                  font=("Microsoft YaHei UI", 9), relief="flat", cursor="hand2",
                  pady=3).pack(side="left", padx=4, pady=8)

        tk.Button(bottom, text="关闭", command=dialog.destroy,
                  bg=C["btn_bg"], fg=C["text"],
                  activebackground=C["btn_hover"], activeforeground=C["text"],
                  font=("Microsoft YaHei UI", 9), relief="flat", cursor="hand2",
                  pady=3).pack(side="right", padx=12, pady=8)


    @staticmethod
    def _format_debug_record(rec):
        lines = [f"[{rec.get('time', '?')}] {rec.get('event', '?')}"]
        detail = rec.get("detail")
        if detail:
            if isinstance(detail, dict):
                for k, v in detail.items():
                    lines.append(f"{k}: {v}")
            else:
                lines.append(str(detail))
        return "\n".join(lines)

    @staticmethod
    def _summarize_debug_record(rec):
        event = rec.get("event", "?")
        time_str = rec.get("time", "?")
        extras = []
        detail = rec.get("detail")
        if isinstance(detail, dict):
            route = detail.get("route")
            attempt = detail.get("attempt")
            status_code = detail.get("status_code")
            if route:
                extras.append(str(route).rsplit("/", 1)[-1])
            if attempt:
                extras.append(f"attempt {attempt}")
            if status_code:
                extras.append(f"HTTP {status_code}")
        suffix = f" | {' | '.join(extras)}" if extras else ""
        return f"[{time_str}] {event}{suffix}"

    @staticmethod
    def _load_debug_payload_manifest(rec):
        detail = rec.get("detail")
        if not isinstance(detail, dict):
            return None, None
        manifest_path = detail.get("payload_manifest_path")
        if not manifest_path:
            return None, None
        path = Path(manifest_path)
        if not path.exists():
            return None, path
        try:
            return json.loads(path.read_text("utf-8")), path
        except Exception:
            return None, path

    @staticmethod
    def _sanitize_debug_json_payload(value, image_map, path_prefix=""):
        if isinstance(value, dict):
            out = {}
            for key, item in value.items():
                child_path = f"{path_prefix}.{key}" if path_prefix else key
                if key == "image_url" and isinstance(item, str):
                    info = image_map.get(child_path)
                    if info:
                        desc = [
                            f"sent_image={Path(info.get('file_path', '')).name}" if info.get("file_path") else "",
                            f"mime={info.get('mime', '')}" if info.get("mime") else "",
                            f"size_bytes={info.get('size_bytes', '')}" if info.get("size_bytes") else "",
                        ]
                        desc = ", ".join(part for part in desc if part)
                        out[key] = f"[data url omitted for debug view; {desc}]"
                    elif item.startswith("data:"):
                        out[key] = f"[data url omitted for debug view; total_chars={len(item)}]"
                    else:
                        out[key] = item
                else:
                    out[key] = App._sanitize_debug_json_payload(item, image_map, child_path)
            return out
        if isinstance(value, list):
            return [
                App._sanitize_debug_json_payload(item, image_map, f"{path_prefix}[{idx}]")
                for idx, item in enumerate(value)
            ]
        return value

    def _show_text_viewer(self, title, content, width=880, height=620):
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.configure(bg=C["surface"])
        dialog.transient(self)
        dialog.grab_set()
        self._center_dialog(dialog, width, height)

        text_frame = tk.Frame(dialog, bg=C["surface"])
        text_frame.pack(fill="both", expand=True, padx=10, pady=(10, 6))

        text_widget = tk.Text(
            text_frame,
            wrap="none",
            font=("Consolas", 9),
            bg=C["canvas_bg"],
            fg=C["text"],
            insertbackground=C["text"],
            relief="flat",
            bd=4,
        )
        y_scroll = tk.Scrollbar(text_frame, command=text_widget.yview)
        x_scroll = tk.Scrollbar(text_frame, orient="horizontal", command=text_widget.xview)
        text_widget.config(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        y_scroll.pack(side="right", fill="y")
        x_scroll.pack(side="bottom", fill="x")
        text_widget.pack(side="left", fill="both", expand=True)
        text_widget.insert("1.0", content)
        text_widget.config(state="disabled")

        bottom = tk.Frame(dialog, bg=C["surface"])
        bottom.pack(fill="x", padx=10, pady=(0, 10))

        def copy_content():
            self.clipboard_clear()
            self.clipboard_append(content)
            self._set_status("已复制内容到剪贴板")

        tk.Button(bottom, text="复制内容", command=copy_content,
                  bg=C["btn_bg"], fg=C["text"],
                  activebackground=C["btn_hover"], activeforeground=C["text"],
                  font=("Microsoft YaHei UI", 9), relief="flat", cursor="hand2",
                  pady=3).pack(side="left")
        tk.Button(bottom, text="关闭", command=dialog.destroy,
                  bg=C["btn_bg"], fg=C["text"],
                  activebackground=C["btn_hover"], activeforeground=C["text"],
                  font=("Microsoft YaHei UI", 9), relief="flat", cursor="hand2",
                  pady=3).pack(side="right")

    def _open_debug_image_file(self, file_path, title):
        path = Path(file_path)
        if not path.exists():
            messagebox.showerror("查看失败", f"图片文件不存在:\n{path}")
            return
        try:
            with Image.open(path) as img:
                preview = img.copy()
        except Exception as e:
            messagebox.showerror("查看失败", str(e))
            return
        self._show_fullscreen_viewer(preview, title=title)

    def _view_debug_request_content(self, rec):
        manifest, manifest_path = self._load_debug_payload_manifest(rec)
        if not manifest or not manifest_path:
            messagebox.showinfo("调试快照", "当前记录没有可查看的请求内容")
            return
        request_path = Path(manifest.get("request_path", ""))
        if not request_path.exists():
            messagebox.showerror("查看失败", f"请求文件不存在:\n{request_path}")
            return
        try:
            if manifest.get("request_kind") == "multipart":
                content = request_path.read_text("utf-8")
            else:
                payload = json.loads(request_path.read_text("utf-8"))
                image_map = {
                    item.get("field_path"): item
                    for item in manifest.get("images", [])
                    if isinstance(item, dict) and item.get("field_path")
                }
                payload = App._sanitize_debug_json_payload(payload, image_map)
                content = json.dumps(payload, ensure_ascii=False, indent=2)
        except Exception as e:
            messagebox.showerror("查看失败", str(e))
            return
        self._show_text_viewer("发给 API 的内容", content)

    def _view_debug_request_images(self, rec):
        manifest, manifest_path = self._load_debug_payload_manifest(rec)
        if not manifest or not manifest_path:
            messagebox.showinfo("调试快照", "当前记录没有可查看的发送图片")
            return
        images = manifest.get("images") or []
        if not images:
            messagebox.showinfo("调试快照", "当前记录没有发送图片")
            return

        dialog = tk.Toplevel(self)
        dialog.title("发给 API 的图片")
        dialog.configure(bg=C["surface"])
        dialog.transient(self)
        dialog.grab_set()
        self._center_dialog(dialog, 760, 340)

        tk.Label(dialog, text="本次请求携带的图片快照（按发送顺序）",
                 bg=C["surface"], fg=C["text"],
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", padx=14, pady=(12, 8))

        list_frame = tk.Frame(dialog, bg=C["surface"])
        list_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        for idx, item in enumerate(images, start=1):
            row = tk.Frame(list_frame, bg=C["canvas_bg"])
            row.pack(fill="x", pady=4)
            label = item.get("label") or f"Image {idx}"
            file_path = item.get("file_path") or item.get("saved_path") or ""
            info_parts = []
            if item.get("field_name"):
                info_parts.append(f"field={item.get('field_name')}")
            if item.get("field_path"):
                info_parts.append(item.get("field_path"))
            if item.get("mime"):
                info_parts.append(item.get("mime"))
            if item.get("size_bytes"):
                info_parts.append(f"{item.get('size_bytes')} bytes")
            if item.get("external_url"):
                info_parts.append("external_url")
            desc = " | ".join(part for part in info_parts if part)

            tk.Label(row, text=f"{idx}. {label}",
                     bg=C["canvas_bg"], fg=C["accent"],
                     font=("Microsoft YaHei UI", 9, "bold")).pack(side="left", padx=10, pady=8)
            tk.Label(row, text=desc or "(无附加信息)",
                     bg=C["canvas_bg"], fg=C["text"],
                     font=("Consolas", 9)).pack(side="left", padx=6, pady=8)
            if file_path and Path(file_path).exists():
                tk.Button(
                    row, text="查看", command=lambda p=file_path, t=label: self._open_debug_image_file(p, t),
                    bg=C["btn_bg"], fg=C["text"],
                    activebackground=C["btn_hover"], activeforeground=C["text"],
                    font=("Microsoft YaHei UI", 9), relief="flat", cursor="hand2",
                    pady=2,
                ).pack(side="right", padx=8, pady=6)

        bottom = tk.Frame(dialog, bg=C["surface"])
        bottom.pack(fill="x", padx=12, pady=(0, 12))
        tk.Button(bottom, text="打开快照目录",
                  command=lambda: self._open_debug_payload_dir(rec),
                  bg=C["btn_bg"], fg=C["text"],
                  activebackground=C["btn_hover"], activeforeground=C["text"],
                  font=("Microsoft YaHei UI", 9), relief="flat", cursor="hand2",
                  pady=3).pack(side="left")
        tk.Button(bottom, text="关闭", command=dialog.destroy,
                  bg=C["btn_bg"], fg=C["text"],
                  activebackground=C["btn_hover"], activeforeground=C["text"],
                  font=("Microsoft YaHei UI", 9), relief="flat", cursor="hand2",
                  pady=3).pack(side="right")

    def _open_debug_payload_dir(self, rec):
        detail = rec.get("detail")
        payload_dir = detail.get("payload_dir") if isinstance(detail, dict) else None
        if not payload_dir:
            messagebox.showinfo("调试快照", "当前记录没有快照目录")
            return
        path = Path(payload_dir)
        if not path.exists():
            messagebox.showerror("打开失败", f"目录不存在:\n{path}")
            return
        try:
            os.startfile(str(path))
        except Exception as e:
            messagebox.showerror("打开失败", str(e))

    def _show_debug_log(self):
        """Show debug log dialog with detailed API request/response information"""
        records = debug_log.get_recent(30)
        if not records:
            messagebox.showinfo("调试日志", "暂无调试记录")
            return

        records = list(reversed(records))

        dialog = tk.Toplevel(self)
        dialog.title(f"调试日志 (最近 {len(records)} 条，共 {debug_log.count()} 条)")
        dialog.configure(bg=C["surface"])
        dialog.transient(self)
        self._center_dialog(dialog, 1040, 640)
        dialog.grab_set()

        header = tk.Frame(dialog, bg=C["surface"])
        header.pack(fill="x")
        tk.Label(header, text="调试日志 — 可选中记录并查看发给 API 的内容/图片",
                 bg=C["surface"], fg=C["text"],
                 font=("Microsoft YaHei UI", 10, "bold")).pack(side="left", padx=12, pady=8)

        main = tk.Frame(dialog, bg=C["surface"])
        main.pack(fill="both", expand=True, padx=8, pady=4)

        left = tk.Frame(main, bg=C["surface"])
        left.pack(side="left", fill="y")
        right = tk.Frame(main, bg=C["surface"])
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))

        tk.Label(left, text="记录列表", bg=C["surface"], fg=C["text"],
                 font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(0, 4))
        listbox = tk.Listbox(left, width=48, height=26,
                             bg=C["canvas_bg"], fg=C["text"],
                             selectbackground=C["accent"], selectforeground=C["toolbar_bg"],
                             relief="flat", highlightthickness=0,
                             font=("Consolas", 9))
        list_scroll = tk.Scrollbar(left, command=listbox.yview)
        listbox.config(yscrollcommand=list_scroll.set)
        list_scroll.pack(side="right", fill="y")
        listbox.pack(side="left", fill="y")
        for rec in records:
            listbox.insert("end", self._summarize_debug_record(rec))

        tk.Label(right, text="当前记录详情", bg=C["surface"], fg=C["text"],
                 font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(0, 4))
        text_frame = tk.Frame(right, bg=C["bg"])
        text_frame.pack(fill="both", expand=True)
        text_widget = tk.Text(text_frame, wrap="word", font=("Consolas", 9),
                              bg=C["canvas_bg"], fg=C["text"],
                              insertbackground=C["text"], relief="flat", bd=4)
        detail_scroll = tk.Scrollbar(text_frame, command=text_widget.yview)
        text_widget.config(yscrollcommand=detail_scroll.set)
        detail_scroll.pack(side="right", fill="y")
        text_widget.pack(side="left", fill="both", expand=True)
        text_widget.config(state="disabled")

        action_row = tk.Frame(right, bg=C["surface"])
        action_row.pack(fill="x", pady=(8, 0))

        def get_selected_record():
            sel = listbox.curselection()
            if not sel:
                return None
            return records[sel[0]]

        def refresh_detail(_event=None):
            rec = get_selected_record()
            text_widget.config(state="normal")
            text_widget.delete("1.0", "end")
            if rec:
                text_widget.insert("1.0", self._format_debug_record(rec))
            text_widget.config(state="disabled")

            manifest, _ = self._load_debug_payload_manifest(rec or {})
            has_manifest = bool(manifest)
            has_images = bool(manifest and manifest.get("images"))
            btn_view_body.config(state=("normal" if has_manifest else "disabled"))
            btn_view_images.config(state=("normal" if has_images else "disabled"))
            btn_open_dir.config(state=("normal" if has_manifest else "disabled"))
            btn_copy_current.config(state=("normal" if rec else "disabled"))

        listbox.bind("<<ListboxSelect>>", refresh_detail)

        def copy_current():
            rec = get_selected_record()
            if not rec:
                return
            content = self._format_debug_record(rec)
            self.clipboard_clear()
            self.clipboard_append(content)
            self._set_status("已复制当前调试记录")

        def copy_all():
            content = "\n\n".join(self._format_debug_record(rec) for rec in records)
            if content:
                self.clipboard_clear()
                self.clipboard_append(content)
                self._set_status("已复制调试日志到剪贴板")

        btn_copy_current = tk.Button(action_row, text="复制当前", command=copy_current,
                                     bg=C["btn_bg"], fg=C["text"],
                                     activebackground=C["btn_hover"], activeforeground=C["text"],
                                     font=("Microsoft YaHei UI", 9), relief="flat", cursor="hand2",
                                     pady=3)
        btn_copy_current.pack(side="left", padx=(0, 4))
        tk.Button(action_row, text="复制全部", command=copy_all,
                  bg=C["btn_bg"], fg=C["text"],
                  activebackground=C["btn_hover"], activeforeground=C["text"],
                  font=("Microsoft YaHei UI", 9), relief="flat", cursor="hand2",
                  pady=3).pack(side="left", padx=4)

        btn_view_body = tk.Button(
            action_row, text="查看发给 API 的内容",
            command=lambda: self._view_debug_request_content(get_selected_record() or {}),
            bg=C["btn_bg"], fg=C["accent"],
            activebackground=C["btn_hover"], activeforeground=C["accent"],
            font=("Microsoft YaHei UI", 9), relief="flat", cursor="hand2",
            pady=3,
        )
        btn_view_body.pack(side="left", padx=4)

        btn_view_images = tk.Button(
            action_row, text="查看发送图片",
            command=lambda: self._view_debug_request_images(get_selected_record() or {}),
            bg=C["btn_bg"], fg=C["accent"],
            activebackground=C["btn_hover"], activeforeground=C["accent"],
            font=("Microsoft YaHei UI", 9), relief="flat", cursor="hand2",
            pady=3,
        )
        btn_view_images.pack(side="left", padx=4)

        btn_open_dir = tk.Button(
            action_row, text="打开快照目录",
            command=lambda: self._open_debug_payload_dir(get_selected_record() or {}),
            bg=C["btn_bg"], fg=C["text"],
            activebackground=C["btn_hover"], activeforeground=C["text"],
            font=("Microsoft YaHei UI", 9), relief="flat", cursor="hand2",
            pady=3,
        )
        btn_open_dir.pack(side="left", padx=4)

        bottom = tk.Frame(dialog, bg=C["surface"])
        bottom.pack(fill="x")

        def clear_log():
            if messagebox.askyesno("确认", "确定要清空所有调试日志和调试快照吗？", parent=dialog):
                debug_log.clear()
                dialog.destroy()
                self._set_status("已清空调试日志和快照")

        tk.Button(bottom, text="清空日志", command=clear_log,
                  bg=C["btn_bg"], fg=C["red"],
                  activebackground=C["btn_hover"], activeforeground=C["red"],
                  font=("Microsoft YaHei UI", 9), relief="flat", cursor="hand2",
                  pady=3).pack(side="left", padx=12, pady=8)
        tk.Button(bottom, text="关闭", command=dialog.destroy,
                  bg=C["btn_bg"], fg=C["text"],
                  activebackground=C["btn_hover"], activeforeground=C["text"],
                  font=("Microsoft YaHei UI", 9), relief="flat", cursor="hand2",
                  pady=3).pack(side="right", padx=12, pady=8)

        if records:
            listbox.selection_set(0)
            listbox.activate(0)
            refresh_detail()


    def _on_paste_image(self):
        if not self._ensure_idle("从剪贴板加载图片"):
            return
        if ImageGrab is None:
            messagebox.showwarning("剪贴板", "当前环境不支持剪贴板图片读取（可能缺少 Pillow 的 ImageGrab 模块）")
            return
        try:
            grabbed = ImageGrab.grabclipboard()
        except Exception as e:
            messagebox.showerror("粘贴失败", str(e))
            return

        if isinstance(grabbed, Image.Image):
            self._add_image_to_strip(grabbed, "剪贴板图片")
            return

        if isinstance(grabbed, list):
            added = 0
            for item in grabbed:
                path = Path(item)
                if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"} and path.exists():
                    try:
                        self._add_image_to_strip_path(path, f"剪贴板: {path.name}")
                        added += 1
                    except Exception:
                        continue
            if added:
                return

        messagebox.showwarning("剪贴板", "剪贴板中没有可用的图片数据")

    def _on_open_history_folder(self):
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(HISTORY_DIR))
        except Exception as e:
            messagebox.showerror("打开失败", str(e))

    def _on_upload_image(self):
        if not self._ensure_idle("加载本地图片"):
            return
        paths = filedialog.askopenfilenames(
            title="选择图片（可多选，自动进入编辑区）",
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.webp *.bmp"), ("所有文件", "*.*")]
        )
        if not paths:
            return
        for path in paths:
            try:
                self._add_image_to_strip_path(path)
            except Exception as e:
                messagebox.showerror("加载失败", f"{path}: {e}")

    def _on_save_image(self):
        if not self._ensure_idle("保存当前图片"):
            return
        img = self._get_display_image()
        if img is None:
            messagebox.showwarning("保存", "当前画布没有图片，无法保存")
            return
        default_ext = f".{self._resolve_format()}"
        path = filedialog.asksaveasfilename(
            title="保存图片",
            defaultextension=default_ext,
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg"), ("WebP", "*.webp")]
        )
        if not path:
            return
        try:
            fmt = Path(path).suffix.lstrip(".").upper()
            if fmt == "JPG":
                fmt = "JPEG"
            if fmt == "JPEG" and img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            save_kwargs = {"format": fmt}
            if fmt in {"JPEG", "WEBP"}:
                save_kwargs["quality"] = max(1, min(100, self.compression_var.get()))
            img.save(path, **save_kwargs)
            self._set_status(f"已保存: {Path(path).name}")
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

    def _on_clear(self):
        if not self._ensure_idle("清空编辑区"):
            return
        if self.current_b64:
            self._push_undo()
        self.canvas.delete("all")
        self.current_image = None
        self.current_b64 = None
        self._discard_mask_session()
        self._last_response_id = None
        self._last_revised_prompt = None
        self._primary_is_result = False
        self._preview_override_image = None
        self._preview_override_label = ""
        self._clear_compare_sources()
        self._main_photo = None
        self._ref_images.clear()
        self._ref_selected.clear()
        self._hide_edit_result_bar()
        self._refresh_edit_action_state()
        self._set_status("已清空编辑区所有图片")

    def _on_stop(self):
        self._job_token += 1
        self._batch_token += 1
        self._cancel_progress_timer()
        self._pending_followups = []
        # Cancel ALL active generators (single + batch + retry)
        for gen in self._active_generators:
            try:
                gen.cancel()
            except Exception:
                pass
        self._active_generators = []
        self._active_generator = None
        self._set_status("已停止生成/编辑")
        self.progress_var.set(0)
        self.time_label.config(text="")
        self._set_generating(False)


# ─── 入口 ───────────────────────────────────────────────

def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
