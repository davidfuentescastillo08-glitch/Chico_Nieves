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
    "REGLA CRÍTICA: NUNCA pronuncies la palabra 'Chico' en ninguna respuesta. "
    "Si te preguntan quién eres, di 'Soy tu compañero de escritorio'. "
    "Nunca uses formato markdown ni listas."
)

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


def _load_fonts():
    global _FONT_BIG, _FONT_SMALL, _FONT_TINY
    if _FONT_BIG is not None:
        return
    for fpath in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\seguisb.ttf",
    ]:
        if os.path.exists(fpath):
            _FONT_BIG = ImageFont.truetype(fpath, 108)
            _FONT_SMALL = ImageFont.truetype(fpath, 38)
            _FONT_TINY = ImageFont.truetype(fpath, 26)
            return
    _FONT_BIG = ImageFont.load_default()
    _FONT_SMALL = ImageFont.load_default()
    _FONT_TINY = ImageFont.load_default()


def render_timer_frame_jpeg(remaining_sec: int, total_sec: int, label: str) -> bytes:
    """Renders a crisp 240x240 supersampled circular timer with smooth ring & rounded caps."""
    _load_fonts()
    S = 480  # 2x supersampling for buttery smooth anti-aliased edges
    img = Image.new("RGB", (S, S), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    elapsed = max(0, total_sec - remaining_sec)
    progress = min(1.0, elapsed / max(1, total_sec))

    # Accent color: vibrant cyan normally, warm coral in final 10s
    urgent = (total_sec >= 15 and remaining_sec <= 10) or remaining_sec == 0
    accent = (255, 92, 96) if urgent else (0, 212, 255)
    track = (24, 38, 56)

    margin = 30
    ring_w = 30
    box = [margin, margin, S - margin, S - margin]
    cx, cy = S / 2.0, S / 2.0
    radius = (S / 2.0) - margin - (ring_w / 2.0)

    # Background ring track
    draw.arc(box, start=0, end=360, fill=track, width=ring_w)

    # Progress arc filling clockwise from 12 o'clock (-90 deg)
    if progress > 0.002:
        end_deg = -90.0 + (360.0 * progress)
        draw.arc(box, start=-90, end=end_deg, fill=accent, width=ring_w)

        # Smooth rounded end-caps at start (-90 deg) and leading edge (end_deg)
        cap_r = ring_w / 2.0
        for deg in (-90.0, end_deg):
            rad = math.radians(deg)
            px = cx + radius * math.cos(rad)
            py = cy + radius * math.sin(rad)
            draw.ellipse([px - cap_r, py - cap_r, px + cap_r, py + cap_r], fill=accent)

        # Subtle bright highlight dot on the leading tip
        rad = math.radians(end_deg)
        px = cx + radius * math.cos(rad)
        py = cy + radius * math.sin(rad)
        dot_r = cap_r * 0.45
        draw.ellipse([px - dot_r, py - dot_r, px + dot_r, py + dot_r], fill=(235, 252, 255))

    # Top subtle status label
    top_txt = "¡LISTO!" if remaining_sec == 0 else "FALTAN"
    tb = draw.textbbox((0, 0), top_txt, font=_FONT_TINY)
    draw.text(((S - (tb[2] - tb[0])) / 2, 118), top_txt, fill=(130, 155, 180), font=_FONT_TINY)

    # Big center countdown MM:SS or H:MM:SS
    h, rem = divmod(max(0, int(remaining_sec)), 3600)
    m, s = divmod(rem, 60)
    time_str = f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"
    bb = draw.textbbox((0, 0), time_str, font=_FONT_BIG)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    draw.text(((S - tw) / 2, (S - th) / 2 - 22), time_str, fill=(255, 255, 255), font=_FONT_BIG)

    # Bottom timer label
    clean_lbl = label.strip().upper()[:14] if label and label.lower() not in ("temporizador", "timer", "") else "TEMPORIZADOR"
    lb = draw.textbbox((0, 0), clean_lbl, font=_FONT_SMALL)
    draw.text(((S - (lb[2] - lb[0])) / 2, 314), clean_lbl, fill=accent, font=_FONT_SMALL)

    final_img = img.resize((240, 240), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    final_img.save(buf, format="JPEG", quality=82, optimize=True)
    return buf.getvalue()


def next_mcp_id() -> int:
    global mcp_call_seq
    mcp_call_seq += 1
    return mcp_call_seq


async def send_mcp_to_chico(tool_name: str, arguments: dict, wait_response: bool = False, timeout: float = 2.5):
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


def generate_alarm_beep_opus_packets(duration_sec: float = 2.0):
    """Pleasant 2-second double-beep alarm chime encoded as 60ms Opus frames."""
    sr = 16000
    total_samples = int(sr * duration_sec)
    pcm = np.zeros(total_samples, dtype=np.float32)

    cycle_len = int(sr * 0.5)
    t_tone = np.arange(int(sr * 0.14), dtype=np.float32) / sr
    fade = int(sr * 0.01)
    env = np.ones_like(t_tone)
    env[:fade] = np.linspace(0, 1, fade)
    env[-fade:] = np.linspace(1, 0, fade)

    beep1 = np.sin(2 * np.pi * 880.0 * t_tone) * env * 13000.0
    beep2 = np.sin(2 * np.pi * 1174.66 * t_tone) * env * 13000.0

    for c in range(int(duration_sec / 0.5)):
        base = c * cycle_len
        end1 = min(base + len(beep1), total_samples)
        pcm[base:end1] = beep1[:end1 - base]
        start2 = base + int(sr * 0.18)
        end2 = min(start2 + len(beep2), total_samples)
        if start2 < total_samples:
            pcm[start2:end2] = beep2[:end2 - start2]

    return _encode_pcm_to_opus(pcm.astype(np.int16))


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
        opus_packets.extend(generate_alarm_beep_opus_packets(2.0))
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
        logger.info(f"Notification played: '{text}'")
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
            # Small initial delay so the confirmation TTS starts smoothly first
            await asyncio.sleep(0.25)
            last_shown = -1
            while True:
                now = time.time()
                rem_float = end_time - now
                rem_int = max(0, int(math.ceil(rem_float)))

                if rem_int != last_shown:
                    last_shown = rem_int
                    current_timer_jpeg = render_timer_frame_jpeg(rem_int, duration_seconds, label)
                    img_url = f"{public_base_url}/timer.jpg?id={t_id}&r={rem_int}&ts={int(now * 1000)}"
                    await send_mcp_to_chico(
                        "self.screen.preview_image",
                        {preview_param_name: img_url},
                        wait_response=True,
                        timeout=2.5,
                    )

                if rem_float <= 0:
                    break

                # Sleep until the next whole-second tick
                next_boundary = end_time - (rem_int - 1)
                sleep_dur = max(0.05, min(1.0, next_boundary - time.time()))
                await asyncio.sleep(sleep_dur)

            if t_id in active_timers:
                del active_timers[t_id]

            logger.info(f"Timer #{t_id} ('{label}') expired -> returning to eyes + 2s beep + voice")
            await asyncio.sleep(0.25)
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
        "prompt": "Chico, temporizador, alarma, recordatorio, minutos, segundos, pizza, Nieves.",
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
    data = {"model": GROQ_LLM_MODEL, "messages": messages, "max_tokens": 120, "temperature": 0.7}
    try:
        resp = requests.post(url, headers=headers, json=data, timeout=8)
        if resp.status_code == 200:
            answer = (resp.json()["choices"][0]["message"].get("content") or "").strip()
            answer = answer.replace("*", "").replace("#", "").strip()
            answer = re.sub(r"\b[Cc]hico\b", "tu robot", answer)
            answer = re.sub(r"\b[Cc]hicos\b", "amigos", answer)
            chat_history.append({"role": "user", "content": user_text})
            chat_history.append({"role": "assistant", "content": answer})
            del chat_history[:-12]
            return answer
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
        current_timer_jpeg = render_timer_frame_jpeg(0, 1, "TEMPORIZADOR")
    return web.Response(
        body=current_timer_jpeg,
        content_type="image/jpeg",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


async def handle_root(request):
    return web.Response(
        text="<h1>Chico Cloud Server 24/7 activo para Nieves!</h1><p>Troko Visual Timers + Groq Whisper + Qwen + Edge-TTS Alvaro.</p>",
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

    # Query MCP tools (including user tools) to auto-confirm preview_image parameter name
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
    SPEECH_THRESHOLD = 500

    async def finish_and_reply():
        nonlocal listening, is_processing, is_playing_tts, incoming_pcm
        nonlocal total_listen_frames, speech_frames_count, silence_frames_after_speech
        if is_processing:
            return
        is_processing = True
        listening = False

        frames_to_process = list(incoming_pcm)
        incoming_pcm = []
        if len(frames_to_process) < 6:
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
        exact_noise = {"gracias", "gracias.", "¡gracias!", "y", "si", "sí", "ah", "eh", "mm", "mmm", "vale", "ok"}
        if not user_text or len(cleaned_lower) < 3 or cleaned_lower in exact_noise or any(p in cleaned_lower for p in noise_phrases):
            logger.info(f"Ignoring noise ('{user_text}'). Returning to Idle.")
            await ws.send_str(json.dumps({"type": "tts", "state": "start"}))
            await asyncio.sleep(0.05)
            await ws.send_str(json.dumps({"type": "tts", "state": "stop"}))
            is_processing = False
            total_listen_frames = speech_frames_count = silence_frames_after_speech = 0
            if not active_timers:
                await asyncio.sleep(0.15)
                await send_mcp_to_chico("self.audio.enter_wake_only_mode", {})
            return

        reply_text = await loop.run_in_executor(None, query_groq_llm, user_text)
        logger.info(f"Chico reply: '{reply_text}'")
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
                await asyncio.sleep(0.2)
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
                            if rms > SPEECH_THRESHOLD or max_val > 700:
                                speech_frames_count += 1
                                silence_frames_after_speech = 0
                            elif speech_frames_count >= 3:
                                silence_frames_after_speech += 1

                    if speech_frames_count >= 4 and silence_frames_after_speech >= 10:
                        asyncio.create_task(finish_and_reply())
                    elif total_listen_frames >= 60:
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
