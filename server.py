import asyncio
import io
import json
import logging
import math
import os
import re
import time
import wave
import aiohttp
from aiohttp import web
import av
import edge_tts
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chico_server")

# Configuration (set GROQ_API_KEY in Render Environment Variables)
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_STT_MODEL = "whisper-large-v3-turbo"
GROQ_LLM_MODEL = "qwen/qwen3.8-27b"
TTS_VOICE = "es-ES-AlvaroNeural"
PORT = int(os.environ.get("PORT", "8000"))

SYSTEM_PROMPT = (
    "Eres un pequeño robot de escritorio inteligente, cariñoso, simpático y leal para Nieves. "
    "Hablas en español de España de forma concisa, divertida y natural. "
    "Da respuestas breves (máximo 1 o 2 frases cortas) para que suenen ágiles al hablar. "
    "REGLA CRÍTICA: NUNCA pronuncies la palabra 'Chico' ni 'Troko' en ninguna respuesta. "
    "Si te preguntan quién eres, di 'Soy tu compañero de escritorio'. "
    "Nunca uses formato markdown ni listas."
)

# --- Paleta exacta de temporizador.pyw ---
BG_RGB = (11, 13, 18)          # #0B0D12
TRACK_RGB = (26, 32, 48)       # #1A2030
TICK_IDLE_RGB = (37, 45, 64)   # #252D40
TEXT_PRIMARY = (243, 246, 252) # #F3F6FC
TEXT_SECONDARY = (122, 132, 155)
COLOR_FULL = (94, 234, 212)    # Turquesa menta (#5EEAD4)
COLOR_MID = (129, 140, 248)    # Índigo suave (#818CF8)
COLOR_LOW = (251, 191, 36)     # Ámbar (#FBBF24)
COLOR_CRIT = (251, 113, 133)   # Coral (#FB7185)
COLOR_DONE = (52, 211, 153)    # Esmeralda (#34D399)

chat_history = []
active_chico_ws = None
main_loop = None
active_timers = {}
timer_counter = 0
mcp_call_seq = 100
pending_mcp_calls = {}
preview_param_name = "url"
public_base_url = "https://chico-nieves.onrender.com"
current_timer_jpeg = b""
recent_debug = []


def dbg(msg: str):
    logger.info(msg)
    recent_debug.append(f"{time.strftime('%H:%M:%S')} {msg}")
    del recent_debug[:-30]

_FONT_BIG = None
_FONT_SMALL = None
_FONT_TINY = None

_NUM_WORDS = {
    "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13,
    "catorce": 14, "quince": 15, "dieciséis": 16, "dieciseis": 16, "diecisiete": 17,
    "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veinticinco": 25, "treinta": 30,
    "cuarenta": 40, "cuarenta y cinco": 45, "cincuenta": 50, "sesenta": 60, "noventa": 90,
}


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _lerp_rgb(c1, c2, t: float):
    t = _clamp(t, 0.0, 1.0)
    return (
        int(round(c1[0] + (c2[0] - c1[0]) * t)),
        int(round(c1[1] + (c2[1] - c1[1]) * t)),
        int(round(c1[2] + (c2[2] - c1[2]) * t)),
    )


def _blend_bg(rgb, alpha: float):
    return _lerp_rgb(BG_RGB, rgb, alpha)


def _accent_for_progress(progress: float, remaining_sec: float):
    if remaining_sec <= 0:
        return COLOR_DONE
    if remaining_sec <= 10.0 and progress < 0.5:
        return _lerp_rgb(COLOR_CRIT, COLOR_LOW, _clamp(remaining_sec / 10.0, 0.0, 1.0))
    if progress >= 0.5:
        return _lerp_rgb(COLOR_MID, COLOR_FULL, (progress - 0.5) / 0.5)
    return _lerp_rgb(COLOR_LOW, COLOR_MID, progress / 0.5)


def _load_fonts():
    global _FONT_BIG, _FONT_SMALL, _FONT_TINY
    if _FONT_BIG is not None:
        return
    for fpath in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        r"C:\Windows\Fonts\segoeuil.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
    ]:
        if os.path.exists(fpath):
            _FONT_BIG = ImageFont.truetype(fpath, 98)
            _FONT_SMALL = ImageFont.truetype(fpath, 32)
            _FONT_TINY = ImageFont.truetype(fpath, 22)
            return
    _FONT_BIG = ImageFont.load_default()
    _FONT_SMALL = ImageFont.load_default()
    _FONT_TINY = ImageFont.load_default()


def render_timer_frame_jpeg(remaining_sec: int, total_sec: int, label: str) -> bytes:
    """Clones the exact minimalist animated timer from temporizador.pyw at 240x240."""
    _load_fonts()
    S = 480  # 2x supersampling for crisp anti-aliased lines
    img = Image.new("RGB", (S, S), BG_RGB)
    draw = ImageDraw.Draw(img)

    # Progress = remaining fraction (1.0 -> 0.0), matching temporizador.pyw
    progress = _clamp(remaining_sec / max(1, total_sec), 0.0, 1.0)
    accent = _accent_for_progress(progress, float(remaining_sec))

    cx, cy = S / 2.0, S / 2.0
    radius = 178.0

    # 1. 60 radial watch-like ticks around the ring
    active_ticks = int(round(progress * 60))
    for i in range(60):
        ang = math.radians(-90 + i * 6)
        is_major = (i % 5 == 0)
        r_outer = radius + 28.0
        r_inner = radius + (14.0 if is_major else 20.0)
        x1 = cx + r_inner * math.cos(ang)
        y1 = cy + r_inner * math.sin(ang)
        x2 = cx + r_outer * math.cos(ang)
        y2 = cy + r_outer * math.sin(ang)
        if i < active_ticks:
            tick_col = _blend_bg(accent, 0.65 if is_major else 0.35)
        else:
            tick_col = TICK_IDLE_RGB
        draw.line([(x1, y1), (x2, y2)], fill=tick_col, width=4 if is_major else 2)

    # 2. Background track ring
    box = [cx - radius, cy - radius, cx + radius, cy + radius]
    draw.arc(box, start=0, end=360, fill=TRACK_RGB, width=16)

    # 3. Soft glow halo + main progress arc
    if progress > 0.002:
        sweep = 360.0 * min(progress, 0.9999)
        end_deg = -90.0 + sweep
        for glow_w, glow_alpha in ((40, 0.09), (28, 0.18), (20, 0.30)):
            gbox = [cx - radius - (glow_w - 14) / 2, cy - radius - (glow_w - 14) / 2,
                    cx + radius + (glow_w - 14) / 2, cy + radius + (glow_w - 14) / 2]
            draw.arc(gbox, start=-90, end=end_deg, fill=_blend_bg(accent, glow_alpha), width=glow_w)

        draw.arc(box, start=-90, end=end_deg, fill=accent, width=14)

        # Rounded start cap
        cap_r = 7.0
        sx, sy = cx, cy - radius
        draw.ellipse([sx - cap_r, sy - cap_r, sx + cap_r, sy + cap_r], fill=accent)

        # Luminous tip dot (matching temporizador.pyw)
        rad = math.radians(end_deg)
        tx = cx + radius * math.cos(rad)
        ty = cy + radius * math.sin(rad)
        draw.ellipse([tx - 18, ty - 18, tx + 18, ty + 18], fill=_blend_bg(accent, 0.28))
        draw.ellipse([tx - 10, ty - 10, tx + 10, ty + 10], fill=TEXT_PRIMARY, outline=accent, width=3)

    # 4. Center countdown MM:SS or H:MM:SS
    h, rem = divmod(max(0, int(remaining_sec)), 3600)
    m, s = divmod(rem, 60)
    time_str = f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"
    bb = draw.textbbox((0, 0), time_str, font=_FONT_BIG)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    draw.text(((S - tw) / 2, cy - th / 2 - 22), time_str, fill=TEXT_PRIMARY, font=_FONT_BIG)

    # 5. Subtitle / label below time
    if remaining_sec <= 0:
        sub_txt = "COMPLETADO"
    elif label and label.lower() not in ("temporizador", "timer", ""):
        sub_txt = label.strip().upper()[:14]
    else:
        sub_txt = "EN CURSO"
    lb = draw.textbbox((0, 0), sub_txt, font=_FONT_SMALL)
    draw.text(((S - (lb[2] - lb[0])) / 2, cy + 54), sub_txt, fill=accent, font=_FONT_SMALL)

    final_img = img.resize((240, 240), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    # Baseline non-progressive JPEG (optimize=False, subsampling=0) for 100% ESP32 LVGL tjpgd compatibility
    final_img.save(buf, format="JPEG", quality=80, optimize=False, progressive=False, subsampling=0)
    return buf.getvalue()


def next_mcp_id() -> int:
    global mcp_call_seq
    mcp_call_seq += 1
    return mcp_call_seq


async def send_mcp_to_chico(tool_name: str, arguments: dict, wait_response: bool = False, timeout: float = 3.0):
    global active_chico_ws
    if not active_chico_ws or active_chico_ws.closed:
        return None
    call_id = next_mcp_id()
    fut = None
    if wait_response:
        fut = asyncio.get_running_loop().create_future()
        pending_mcp_calls[call_id] = fut

    mcp_msg = {
        "type": "mcp",
        "payload": {
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        },
    }
    try:
        await active_chico_ws.send_str(json.dumps(mcp_msg))
        logger.info(f"Sent MCP: {tool_name}({arguments}) [id={call_id}]")
        if fut is not None:
            return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(f"MCP timeout for {tool_name} [id={call_id}]")
    except Exception as e:
        logger.warning(f"Failed MCP {tool_name}: {e}")
    finally:
        pending_mcp_calls.pop(call_id, None)
    return None


def parse_spanish_duration(text: str):
    t = text.lower()
    if "media hora" in t:
        return 1800
    if "cuarto de hora" in t:
        return 900
    total = 0
    found = False
    num_re = r"(\d+(?:[.,]\d+)?|" + "|".join(sorted(map(re.escape, _NUM_WORDS), key=len, reverse=True)) + r")"
    for m in re.finditer(num_re + r"\s*(horas?|h\b|minutos?|min\b|segundos?|seg\b|s\b)(\s+y\s+medi[oa])?", t):
        raw, unit, half = m.group(1), m.group(2), m.group(3)
        val = float(raw.replace(",", ".")) if raw[0].isdigit() else _NUM_WORDS[raw]
        if half:
            val += 0.5
        mult = 3600 if unit.startswith("h") else 60 if unit.startswith("m") else 1
        total += val * mult
        found = True
    if not found and re.search(r"\bun minuto\b|\bminuto\b", t):
        return 60
    return int(total) if found and total > 0 else None


def parse_timer_label(text: str) -> str:
    m = re.search(r"\bpara\s+(?:el|la|los|las|mi|un|una)?\s*([a-záéíóúñ ]{2,30})", text.lower())
    if m:
        label = m.group(1).strip(" .?!¿¡")
        return label if label else "temporizador"
    return "temporizador"


def _fmt_duration(sec: int) -> str:
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append("1 hora" if h == 1 else f"{h} horas")
    if m:
        parts.append("1 minuto" if m == 1 else f"{m} minutos")
    if s or not parts:
        parts.append("1 segundo" if s == 1 else f"{s} segundos")
    return " y ".join(parts)


def generate_alarm_beep_opus_packets():
    """Exact 3-note chime from temporizador.pyw (C5-E5-G5: 523/659/784 Hz) played twice smoothly."""
    sr = 16000
    segments = []
    for _repeat in range(2):
        for freq, dur_ms in ((523.25, 130), (659.25, 130), (783.99, 260)):
            n = int(sr * dur_ms / 1000.0)
            t = np.arange(n, dtype=np.float32) / sr
            fade = min(int(sr * 0.015), n // 3)
            env = np.ones(n, dtype=np.float32)
            env[:fade] = np.linspace(0.0, 1.0, fade)
            env[-fade:] = np.linspace(1.0, 0.0, fade)
            tone = (np.sin(2 * np.pi * freq * t) + 0.25 * np.sin(2 * np.pi * freq * 2 * t)) * env * 11000.0
            segments.append(tone)
            segments.append(np.zeros(int(sr * 0.045), dtype=np.float32))
        segments.append(np.zeros(int(sr * 0.20), dtype=np.float32))
    full_pcm = np.concatenate(segments).astype(np.int16)
    return _encode_pcm_to_opus(full_pcm)


def _encode_pcm_to_opus(full_pcm: np.ndarray):
    encoder = av.CodecContext.create("libopus", "w")
    encoder.sample_rate = 16000
    encoder.format = "s16"
    encoder.layout = "mono"
    encoder.bit_rate = 32000
    encoder.options = {"frame_duration": "60"}
    encoder.open()

    frame_size = 960
    opus_packets = []
    for i in range(0, len(full_pcm), frame_size):
        chunk = full_pcm[i : i + frame_size]
        if len(chunk) < frame_size:
            chunk = np.pad(chunk, (0, frame_size - len(chunk)), "constant")
        frame = av.AudioFrame.from_ndarray(chunk.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = 16000
        for packet in encoder.encode(frame):
            opus_packets.append(bytes(packet))
    for packet in encoder.encode(None):
        opus_packets.append(bytes(packet))
    return opus_packets


async def generate_speech_opus_packets(text: str):
    logger.info(f"Generating TTS for: '{text}' ({TTS_VOICE})")
    communicate = edge_tts.Communicate(text, TTS_VOICE)
    mp3_data = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            mp3_data.extend(chunk["data"])
    if not mp3_data:
        return []

    input_container = av.open(io.BytesIO(mp3_data), mode="r", format="mp3")
    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    pcm_samples = []
    for frame in input_container.decode(audio=0):
        for resampled in resampler.resample(frame):
            pcm_samples.append(resampled.to_ndarray()[0])
    if not pcm_samples:
        return []
    return _encode_pcm_to_opus(np.concatenate(pcm_samples))


async def notify_chico(text: str, with_beep: bool = False):
    global active_chico_ws
    if not active_chico_ws or active_chico_ws.closed:
        logger.warning(f"Chico not connected, cannot speak: '{text}'")
        return
    opus_packets = []
    if with_beep:
        opus_packets.extend(generate_alarm_beep_opus_packets())
    speech_packets = await generate_speech_opus_packets(text)
    if not speech_packets:
        return
    opus_packets.extend(speech_packets)
    try:
        await active_chico_ws.send_str(json.dumps({"type": "tts", "state": "start"}))
        await active_chico_ws.send_str(json.dumps({"type": "tts", "state": "sentence_start", "text": text}))
        for i, pkt in enumerate(opus_packets):
            await active_chico_ws.send_bytes(pkt)
            if i < 4:
                continue
            await asyncio.sleep(0.055)
        await active_chico_ws.send_str(json.dumps({"type": "tts", "state": "sentence_end"}))
        await active_chico_ws.send_str(json.dumps({"type": "tts", "state": "stop"}))
        dbg(f"Notification played: '{text}'")
        if not active_timers:
            await asyncio.sleep(0.25)
            await send_mcp_to_chico("self.audio.enter_wake_only_mode", {})
    except Exception as e:
        logger.error(f"Error streaming notification: {e}")


def add_timer(duration_seconds: int, label: str) -> int:
    global timer_counter
    timer_counter += 1
    t_id = timer_counter
    end_time = time.time() + duration_seconds

    async def timer_worker():
        global current_timer_jpeg
        try:
            # Wait briefly so confirmation TTS finishes streaming cleanly without competing for bandwidth
            await asyncio.sleep(0.3)
            last_shown = -1
            while True:
                now = time.time()
                rem_float = end_time - now
                rem_int = max(0, int(math.ceil(rem_float)))

                if rem_int != last_shown:
                    last_shown = rem_int
                    current_timer_jpeg = render_timer_frame_jpeg(rem_int, duration_seconds, label)
                    img_url = f"{public_base_url}/timer.jpg?id={t_id}&r={rem_int}&ts={int(now * 1000)}"
                    resp = await send_mcp_to_chico(
                        "self.screen.preview_image",
                        {preview_param_name: img_url},
                        wait_response=True,
                        timeout=3.5,
                    )
                    if resp is not None:
                        logger.info(f"preview_image resp (r={rem_int}): {resp}")

                if rem_float <= 0:
                    break

                next_boundary = end_time - (rem_int - 1)
                sleep_dur = max(0.05, min(1.0, next_boundary - time.time()))
                await asyncio.sleep(sleep_dur)

            if t_id in active_timers:
                del active_timers[t_id]

            logger.info(f"Timer #{t_id} ('{label}') expired -> eyes + chime + voice")
            await asyncio.sleep(0.35)
            await send_mcp_to_chico("self.screen.clear_preview", {}, wait_response=True, timeout=1.5)
            await asyncio.sleep(0.15)
            para = "" if label.lower() in ("temporizador", "timer", "") else f" de {label}"
            await notify_chico(f"¡Nieves! Se ha acabado el temporizador{para}.", with_beep=True)
        except asyncio.CancelledError:
            logger.info(f"Timer #{t_id} cancelled -> clearing preview.")
            await send_mcp_to_chico("self.screen.clear_preview", {}, wait_response=False)

    future = asyncio.run_coroutine_threadsafe(timer_worker(), main_loop)
    active_timers[t_id] = {"label": label, "duration": duration_seconds, "end_time": end_time, "task": future}
    return t_id


def execute_timer_tool(fn_name: str, args: dict) -> str:
    if fn_name == "set_timer":
        dur = max(1, min(86400, int(args.get("duration_seconds", 60))))
        label = str(args.get("label", "temporizador")).strip() or "temporizador"
        add_timer(dur, label)
        para = "" if label.lower() in ("temporizador", "timer") else f" para {label}"
        return f"¡Marchando! Temporizador de {_fmt_duration(dur)}{para}. Te aviso cuando termine."

    if fn_name == "get_timers":
        if not active_timers:
            return "No tienes ningún temporizador activo."
        now = time.time()
        parts = []
        for info in active_timers.values():
            left = max(0, int(info["end_time"] - now))
            name = "" if info["label"].lower() in ("temporizador", "timer") else f" de {info['label']}"
            parts.append(f"al temporizador{name} le quedan {_fmt_duration(left)}")
        txt = ", y ".join(parts) + "."
        return txt[0].upper() + txt[1:]

    if fn_name == "cancel_timer":
        if not active_timers:
            return "No hay ningún temporizador activo."
        cancelled = len(active_timers)
        for info in list(active_timers.values()):
            info["task"].cancel()
        active_timers.clear()
        return "Temporizador cancelado." if cancelled == 1 else f"He cancelado {cancelled} temporizadores."

    return "Entendido."


def detect_timer_intent(text: str):
    low = text.lower()
    has_timer_word = bool(re.search(r"porizador|temporiz|timer|cuenta atr[aá]s|cron[oó]metro|alarma|av[ií]sa", low))
    dur = parse_spanish_duration(low)
    if not has_timer_word and not (dur and re.search(r"\b(pon|para|en|de)\b", low)):
        return None
    if re.search(r"cancel|quita|para el|p[aá]ralo|det[eé]n|borra|anula|apaga", low):
        return ("cancel_timer", {"label": "todos"})
    if re.search(r"cu[aá]nto|queda|falta", low):
        return ("get_timers", {})
    if dur:
        return ("set_timer", {"duration_seconds": dur, "label": parse_timer_label(low)})
    return None


def transcribe_audio_groq(wav_bytes: bytes) -> str:
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "User-Agent": "ChicoClient/1.0"}
    files = {"file": ("speech.wav", io.BytesIO(wav_bytes), "audio/wav")}
    data = {
        "model": GROQ_STT_MODEL,
        "language": "es",
        "prompt": "Troko, Chico, temporizador, alarma, recordatorio, minutos, segundos, pizza, Nieves.",
    }
    try:
        resp = requests.post(url, headers=headers, files=files, data=data, timeout=8)
        if resp.status_code == 200:
            return resp.json().get("text", "").strip()
        logger.error(f"Groq Whisper error {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"Groq Whisper exception: {e}")
    return ""


def query_groq_llm(user_text: str) -> str:
    intent = detect_timer_intent(user_text)
    if intent:
        logger.info(f"Timer intent: {intent}")
        answer = execute_timer_tool(*intent)
        chat_history.append({"role": "user", "content": user_text})
        chat_history.append({"role": "assistant", "content": answer})
        del chat_history[:-12]
        return answer

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "ChicoClient/1.0",
    }
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + chat_history[-6:] + [{"role": "user", "content": user_text}]
    data = {
        "model": GROQ_LLM_MODEL,
        "messages": messages,
        "max_tokens": 120,
        "temperature": 0.7,
        "reasoning_effort": "none",
    }
    try:
        resp = requests.post(url, headers=headers, json=data, timeout=8)
        if resp.status_code == 200:
            answer = (resp.json()["choices"][0]["message"].get("content") or "").strip()
            answer = answer.replace("*", "").replace("#", "").strip()
            answer = re.sub(r"\b[Cc]hico\b", "tu robot", answer)
            answer = re.sub(r"\b[Tt]roko\b", "tu robot", answer)
            if answer:
                chat_history.append({"role": "user", "content": user_text})
                chat_history.append({"role": "assistant", "content": answer})
                del chat_history[:-12]
                return answer
        else:
            logger.error(f"Groq LLM error {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"Groq LLM exception: {e}")
    return "Perdona, no te he entendido bien. ¿Me lo repites?"


async def handle_ota(request):
    global public_base_url
    host = request.headers.get("Host", f"localhost:{PORT}")
    proto = request.headers.get("X-Forwarded-Proto", "http")
    public_base_url = f"{proto}://{host}"
    ws_scheme = "wss" if proto == "https" else "ws"
    return web.json_response({
        "websocket": {"url": f"{ws_scheme}://{host}/ws"},
        "theme": "dark",
    })


async def handle_timer_image(request):
    global current_timer_jpeg
    if not current_timer_jpeg:
        current_timer_jpeg = render_timer_frame_jpeg(15, 15, "TEMPORIZADOR")
    return web.Response(
        body=current_timer_jpeg,
        content_type="image/jpeg",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


async def handle_debug(request):
    return web.Response(text="\n".join(recent_debug) or "(no logs yet)", content_type="text/plain")


async def handle_test_preview(request):
    global current_timer_jpeg
    current_timer_jpeg = render_timer_frame_jpeg(42, 60, "pizza")
    img_url = f"{public_base_url}/timer.jpg?test={int(time.time() * 1000)}"
    resp = await send_mcp_to_chico("self.screen.preview_image", {preview_param_name: img_url}, wait_response=True, timeout=5.0)
    dbg(f"TEST preview_image({img_url}) -> {resp}")
    return web.json_response({"url": img_url, "resp": resp, "ws_connected": bool(active_chico_ws and not active_chico_ws.closed)})


async def handle_root(request):
    return web.Response(
        text="<h1>Chico Cloud Server 24/7 activo para Nieves!</h1><p>Temporizador minimalista v2 + Groq Instant + Edge-TTS Alvaro.</p>",
        content_type="text/html",
    )


async def handle_websocket(request):
    global active_chico_ws, main_loop, public_base_url, preview_param_name
    main_loop = asyncio.get_running_loop()
    host = request.headers.get("Host", "")
    proto = request.headers.get("X-Forwarded-Proto", "https" if "onrender.com" in host else "http")
    if host:
        public_base_url = f"{proto}://{host}"

    ws = web.WebSocketResponse(heartbeat=25.0)
    await ws.prepare(request)
    active_chico_ws = ws
    logger.info(f"Chico WebSocket connected from {request.remote}! Base URL: {public_base_url}")

    await ws.send_str(json.dumps({
        "type": "hello",
        "transport": "websocket",
        "audio_params": {"format": "opus", "sample_rate": 16000, "channels": 1, "frame_duration": 60},
    }))

    # Proper MCP handshake: initialize -> tools/list
    await ws.send_str(json.dumps({
        "type": "mcp",
        "payload": {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "chico-server", "version": "1.0.0"},
            },
        },
    }))
    await ws.send_str(json.dumps({
        "type": "mcp",
        "payload": {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {"withUserTools": True},
        },
    }))

    decoder = av.CodecContext.create("opus", "r")
    decoder.open()
    mic_resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)

    incoming_pcm = []
    listening = False
    is_processing = False
    is_playing_tts = False
    total_listen_frames = 0
    speech_frames_count = 0
    silence_frames_after_speech = 0
    SPEECH_THRESHOLD = 420

    async def finish_and_reply():
        nonlocal listening, is_processing, is_playing_tts, incoming_pcm
        nonlocal total_listen_frames, speech_frames_count, silence_frames_after_speech
        if is_processing:
            return
        is_processing = True
        listening = False

        frames_to_process = list(incoming_pcm)
        incoming_pcm = []
        if len(frames_to_process) < 5:
            is_processing = False
            return

        full_audio = np.concatenate(frames_to_process)
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(full_audio.tobytes())
        wav_bytes = wav_buffer.getvalue()

        loop = asyncio.get_running_loop()
        user_text = await loop.run_in_executor(None, transcribe_audio_groq, wav_bytes)
        logger.info(f"User asked: '{user_text}'")

        cleaned_lower = user_text.lower().strip()
        noise_phrases = ["suscríbete", "subtítulo", "subtitul", "amara.org", "gracias por ver", "canal"]
        exact_noise = {"gracias", "gracias.", "¡gracias!", "y", "si", "sí", "ah", "eh", "mm", "mmm", "vale", "ok", "troko", "troko."}
        if not user_text or len(cleaned_lower) < 3 or cleaned_lower in exact_noise or any(p in cleaned_lower for p in noise_phrases):
            dbg(f"Ignoring noise ('{user_text}'). Returning to Idle.")
            await ws.send_str(json.dumps({"type": "tts", "state": "start"}))
            await asyncio.sleep(0.05)
            await ws.send_str(json.dumps({"type": "tts", "state": "stop"}))
            is_processing = False
            total_listen_frames = speech_frames_count = silence_frames_after_speech = 0
            if not active_timers:
                await asyncio.sleep(0.2)
                await send_mcp_to_chico("self.audio.enter_wake_only_mode", {})
            return

        reply_text = await loop.run_in_executor(None, query_groq_llm, user_text)
        dbg(f"User: '{user_text}' -> Reply: '{reply_text}'")
        opus_packets = await generate_speech_opus_packets(reply_text)

        is_playing_tts = True
        try:
            await ws.send_str(json.dumps({"type": "tts", "state": "start"}))
            await ws.send_str(json.dumps({"type": "tts", "state": "sentence_start", "text": reply_text}))
            for i, pkt in enumerate(opus_packets):
                if not is_playing_tts:
                    break
                await ws.send_bytes(pkt)
                if i < 4:
                    continue
                await asyncio.sleep(0.055)
            await ws.send_str(json.dumps({"type": "tts", "state": "sentence_end"}))
            await ws.send_str(json.dumps({"type": "tts", "state": "stop"}))
            if not active_timers:
                await asyncio.sleep(0.25)
                await send_mcp_to_chico("self.audio.enter_wake_only_mode", {})
        except Exception as e:
            logger.warning(f"TTS stream error: {e}")
        finally:
            is_playing_tts = False
            is_processing = False
            total_listen_frames = speech_frames_count = silence_frames_after_speech = 0

    async for msg in ws:
        if msg.type == aiohttp.WSMsgType.TEXT:
            try:
                data = json.loads(msg.data)
                msg_type = data.get("type")
                state = data.get("state")

                if msg_type == "mcp":
                    payload = data.get("payload", {})
                    resp_id = payload.get("id")
                    dbg(f"MCP resp [id={resp_id}]: {str(payload)[:260]}")
                    if resp_id in pending_mcp_calls and not pending_mcp_calls[resp_id].done():
                        pending_mcp_calls[resp_id].set_result(payload)
                    elif resp_id == 1 and "result" in payload:
                        for t in payload["result"].get("tools", []):
                            if t.get("name") == "self.screen.preview_image":
                                props = list(t.get("inputSchema", {}).get("properties", {}).keys())
                                if props:
                                    preview_param_name = props[0]
                                    logger.info(f"Confirmed preview_image param: '{preview_param_name}'")
                    continue

                logger.info(f"Chico event: {data}")
                if msg_type == "listen" and state == "start":
                    if is_processing or is_playing_tts:
                        continue
                    incoming_pcm = []
                    total_listen_frames = speech_frames_count = silence_frames_after_speech = 0
                    listening = True
                elif msg_type == "listen" and state == "stop":
                    if listening and not is_processing:
                        asyncio.create_task(finish_and_reply())
            except Exception as e:
                logger.error(f"Message error: {e}")

        elif msg.type == aiohttp.WSMsgType.BINARY:
            if listening and not is_processing and not is_playing_tts:
                try:
                    for df in decoder.decode(av.Packet(msg.data)):
                        for rf in mic_resampler.resample(df):
                            arr = rf.to_ndarray()[0]
                            incoming_pcm.append(arr)
                            total_listen_frames += 1
                            rms = float(np.sqrt(np.mean(arr.astype(np.float32) ** 2)))
                            max_val = int(np.max(np.abs(arr)))
                            if rms > SPEECH_THRESHOLD or max_val > 650:
                                speech_frames_count += 1
                                silence_frames_after_speech = 0
                            elif speech_frames_count >= 3:
                                silence_frames_after_speech += 1

                    if speech_frames_count >= 4 and silence_frames_after_speech >= 7:
                        asyncio.create_task(finish_and_reply())
                    elif total_listen_frames >= 80:
                        asyncio.create_task(finish_and_reply())
                except Exception:
                    pass

        elif msg.type == aiohttp.WSMsgType.ERROR:
            break

    if active_chico_ws == ws:
        active_chico_ws = None
    logger.info("Chico disconnected")
    return ws


def main():
    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_get("/debug", handle_debug)
    app.router.add_get("/test_preview", handle_test_preview)
    app.router.add_get("/o", handle_ota)
    app.router.add_post("/o", handle_ota)
    app.router.add_get("/ota/", handle_ota)
    app.router.add_post("/ota/", handle_ota)
    app.router.add_get("/timer.jpg", handle_timer_image)
    app.router.add_get("/ws", handle_websocket)
    logger.info(f"CHICO CLOUD SERVER 24/7 INICIADO EN 0.0.0.0:{PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
