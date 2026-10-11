"""
Voice Engine for Prime AI.
Provides high-fidelity Voice Synthesis featuring Mark-LIV's signature Gemini "Charon" voice,
Microsoft Edge-TTS neural voices, and offline Windows SAPI5 fallback.
Includes speech recognition with ambient noise adjustment.
"""

from __future__ import annotations

import asyncio
import collections
import io
import logging
import os
import queue
import re
import tempfile
import threading
import wave
from typing import Callable, Dict, List, Optional

import edge_tts
import pygame
import pyttsx3
import speech_recognition as sr
from config import config

log = logging.getLogger("prime.voice")

# Mark-LIV Voice Directory
MARK_LIV_GEMINI_VOICES: Dict[str, str] = {
    "charon": "Charon",       # Mark-LIV signature JARVIS (deep, authoritative)
    "puck": "Puck",           # Upbeat, energetic
    "fenrir": "Fenrir",       # Excitable, dynamic
    "kore": "Kore",           # Firm, confident
    "aoede": "Aoede",         # Breezy, calm
}

OPENAI_TTS_VOICES: Dict[str, str] = {
    "nova": "nova",               # Sagar Tamang F.R.I.D.A.Y. signature voice
    "onyx": "onyx",               # Sagar Tamang U.L.T.R.O.N. deep baritone
    "echo": "echo",               # Warm resonant male
    "alloy": "alloy",             # Balanced neutral
    "fable": "fable",             # Expressive British
    "shimmer": "shimmer",         # Clear bright female
    "ultron": "onyx",             # Ultron alias
    "friday": "nova",             # F.R.I.D.A.Y. alias
}

EDGE_NEURAL_VOICES: Dict[str, str] = {
    "ultron": "en-US-ChristopherNeural",     # Ultron deep cinematic male (Sagar Tamang build)
    "friday": "en-US-AvaNeural",             # F.R.I.D.A.Y. / Nova clear natural female
    "nova": "en-US-AvaNeural",               # Nova (matches OpenAI nova)
    "onyx": "en-US-ChristopherNeural",       # Onyx (matches OpenAI onyx)
    "brian": "en-US-BrianMultilingualNeural",# Ultra-cool modern AI companion (fluent in English & Hindi)
    "andrew": "en-US-AndrewMultilingualNeural",# Suave & charismatic multilingual
    "christopher": "en-US-ChristopherNeural",# American deep baritone
    "ryan": "en-GB-RyanNeural",              # British JARVIS
    "madhur": "hi-IN-MadhurNeural",          # Native Hindi male
    "swara": "hi-IN-SwaraNeural",            # Native Hindi female
    "prabhat": "en-IN-PrabhatNeural",        # Indian English male
    "neerja": "en-IN-NeerjaExpressiveNeural",# Indian English expressive female
    "guy": "en-US-GuyNeural",                # Friendly American male
    "hamdan": "ar-AE-HamdanNeural",          # Native UAE Emirati Arabic male
    "fatima": "ar-AE-FatimaNeural",          # Native UAE Emirati Arabic female
    "arabic": "ar-AE-HamdanNeural",          # UAE Arabic alias
}


def is_arabic(text: str) -> bool:
    """Detect if text contains Arabic script or common Romanized Emirati/Gulf Arabic expressions."""
    if not text:
        return False
    if re.search(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]", text):
        return True
    arabic_markers = {
        "habibi", "yalla", "inshallah", "shukran", "marhaba", "hala", "wallah",
        "khalas", "mabrook", "salam", "alaykum", "assalamu", "ahlan", "mashallah",
        "hayyak", "tamam", "aywa", "naam", "intazir", "tawaqaf"
    }
    words = set(re.findall(r"\b[a-zA-Z]+\b", text.lower()))
    return bool(words & arabic_markers)


def is_hindi_or_hinglish(text: str) -> bool:
    """Detect if text contains Devanagari script or common Romanized Hindi/Hinglish vocabulary."""
    if not text:
        return False
    if re.search(r"[\u0900-\u097f]", text):
        return True
    hinglish_markers = {
        "kaisa", "kaise", "kaisi", "accha", "theek", "bhej", "diya", "karo", "karna", "hai", "hain", "hoon",
        "nhi", "nahi", "kya", "kyun", "kyu", "kaun", "kab", "kahan", "yahan", "wahan", "bolo", "bol", "batao",
        "namaste", "shukriya", "dhanyawad", "dekha", "suno", "samajh", "aaya", "gaya", "chalo", "shuru", "band",
        "kholo", "mera", "meri", "mere", "aap", "tum", "hum", "sab", "kar", "ho", "bhai", "yaar"
    }
    words = set(re.findall(r"\b[a-zA-Z]+\b", text.lower()))
    return bool(words & hinglish_markers)


is_hinglish = is_hindi_or_hinglish


def detect_language_mode(text: str) -> str:
    """Detect trilingual language code ('ar-AE', 'hi-IN', or 'en-US') for UAE/Global operations."""
    if is_arabic(text):
        return "ar-AE"
    if is_hindi_or_hinglish(text):
        return "hi-IN"
    return "en-US"


BARGE_IN_KEYWORDS = (
    "stop", "ruko", "chup", "pause", "wait", "hold on", "shant", "ek second",
    "bas", "quiet", "shut up", "mat bolo", "ruk jao", "cancel",
    "khalas", "tawaqaf", "intazir", "وقف", "خلاص", "انتظر"
)


def check_barge_in_phrase(text: str) -> bool:
    """Detect if an incoming voice utterance is a speech interruption command."""
    if not text:
        return False
    t = text.lower().strip()
    return any(
        k == t or t.startswith(k + " ") or t.endswith(" " + k) or f" {k} " in t
        for k in BARGE_IN_KEYWORDS
    )


def apply_stark_intercom_filter(
    audio_data: "np.ndarray",
    sample_rate: int = 24000,
    intensity: float = 0.65
) -> "np.ndarray":
    """
    Cinematic Stark Intercom / JARVIS Laboratory DSP Filter:
    1. Bandpass filter (180 Hz - 6800 Hz): removes muddy sub-bass & harsh digital fizz.
    2. Peaking presence EQ at 2800 Hz (+3.5 dB): gives crisp vocal intelligibility.
    3. Warm analog saturation (tanh): gives that authentic helmet intercom harmonics.
    4. Early reflection: subtle 12ms acoustic reflection simulating an open glass lab.
    """
    try:
        import numpy as np
        import scipy.signal as signal
    except ImportError:
        return audio_data

    if audio_data is None or len(audio_data) == 0:
        return audio_data

    try:
        is_stereo = (audio_data.ndim == 2)
        data = audio_data.astype(np.float32) / 32768.0
        nyquist = 0.5 * max(8000, sample_rate)

        # 1. Bandpass filter
        low_cut = max(20.0, min(180.0, nyquist - 100.0))
        high_cut = max(low_cut + 100.0, min(6800.0, nyquist - 50.0))
        b_bp, a_bp = signal.butter(2, [low_cut / nyquist, high_cut / nyquist], btype="bandpass")
        filtered = signal.lfilter(b_bp, a_bp, data, axis=0)

        # 2. Presence Peak at 2800 Hz (peaking biquad)
        f0 = 2800.0 / nyquist
        if f0 < 0.95:
            Q = 1.3
            A = 10.0 ** (3.5 / 40.0)
            w0 = np.pi * f0
            alpha = np.sin(w0) / (2.0 * Q)
            b_eq = np.array([1.0 + alpha * A, -2.0 * np.cos(w0), 1.0 - alpha * A]) / (1.0 + alpha / A)
            a_eq = np.array([1.0, -2.0 * np.cos(w0) / (1.0 + alpha / A), (1.0 - alpha / A) / (1.0 + alpha / A)])
            filtered = signal.lfilter(b_eq, a_eq, filtered, axis=0)

        # 3. Warm analog saturation
        drive = 1.15
        saturated = np.tanh(drive * filtered) / np.tanh(drive)

        # 4. Subtle early reflection (12ms delay)
        delay_samples = int(0.012 * sample_rate)
        if delay_samples > 0 and len(saturated) > delay_samples:
            refl = np.zeros_like(saturated)
            refl[delay_samples:] = saturated[:-delay_samples] * 0.16
            saturated = saturated * 0.84 + refl

        # Wet/dry mix
        out = (1.0 - intensity) * data + intensity * saturated
        return np.clip(out * 32767.0, -32768.0, 32767.0).astype(np.int16)
    except Exception:
        return audio_data


class VoiceEngine:
    def __init__(self):
        self.tts_queue: queue.Queue[Optional[str]] = queue.Queue()
        self.tts_thread: Optional[threading.Thread] = None
        self._shutdown = threading.Event()
        self._abort_utterance = threading.Event()
        self._is_speaking = False
        self._tts_enabled = config.voice_output
        self.current_voice = config.tts_voice or "ultron"
        self._genai_client = None
        self._genai_api_key = None

        # Cinematic Stark Intercom Audio Filter
        self.stark_filter_enabled = os.getenv("STARK_AUDIO_FILTER", "true").lower() in ("true", "1", "yes")
        default_intensity = "0.75" if "ultron" in str(self.current_voice).lower() or "onyx" in str(self.current_voice).lower() else "0.65"
        self.stark_filter_intensity = float(os.getenv("STARK_FILTER_INTENSITY", default_intensity))

        # Speech recognition
        self.recognizer = sr.Recognizer()
        self.recognizer.energy_threshold = 300
        self.recognizer.dynamic_energy_threshold = True

        # Initialize pygame mixer for audio playback
        try:
            pygame.mixer.init(frequency=24000, size=-16, channels=2, buffer=1024)
        except Exception as e:
            log.warning("Could not init pygame.mixer: %s", e)

        # Dispatch tracker for test inspection and race-free queue monitoring
        self._dispatched_history = collections.deque(maxlen=50)

        # Start TTS background worker
        self._start_tts_worker()

    def _dispatch_tts_item(self, text: str):
        """Append to dispatch history and enqueue into tts_queue."""
        s = str(text).strip()
        if s:
            self._dispatched_history.append(s)
            self.tts_queue.put(s)

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking

    @property
    def tts_enabled(self) -> bool:
        return self._tts_enabled

    @tts_enabled.setter
    def tts_enabled(self, val: bool):
        self._tts_enabled = bool(val)

    def set_voice(self, voice_name: str) -> str:
        """Set active voice (e.g. 'ultron', 'friday', 'nova', 'onyx', 'charon', 'ryan', etc.)."""
        clean = (voice_name or '').strip().lower()
        if not clean:
            return self.current_voice
        resolved = None

        if clean in ("ultron", "onyx"):
            resolved = "ultron"
            self.stark_filter_enabled = True
            self.stark_filter_intensity = 0.75
        elif clean in ("friday", "nova"):
            resolved = "friday"
        elif clean in OPENAI_TTS_VOICES:
            resolved = clean
        elif clean in MARK_LIV_GEMINI_VOICES:
            resolved = MARK_LIV_GEMINI_VOICES[clean]
        elif clean in [v.lower() for v in MARK_LIV_GEMINI_VOICES.values()]:
            resolved = next(v for v in MARK_LIV_GEMINI_VOICES.values() if v.lower() == clean)
        elif clean in EDGE_NEURAL_VOICES:
            resolved = EDGE_NEURAL_VOICES[clean]
        elif clean in [v.lower() for v in EDGE_NEURAL_VOICES.values()]:
            resolved = next(v for v in EDGE_NEURAL_VOICES.values() if v.lower() == clean)
        elif clean in ("david", "offline", "sapi5", "pyttsx3"):
            resolved = "david"
        else:
            # Direct match
            resolved = voice_name.strip()

        # Auto-enable Stark Intercom filter for Ultron and Onyx voices to produce that authentic metallic baritone
        if clean in ("ultron", "onyx") or (isinstance(resolved, str) and any(k in resolved.lower() for k in ("ultron", "onyx"))):
            self.stark_filter_enabled = True
            self.stark_filter_intensity = 0.75

        self.current_voice = resolved
        config.set_voice(resolved)
        return resolved

    def get_available_voices(self) -> Dict[str, List[str]]:
        return {
            "sagar_ultron_friday": ["ultron", "friday", "nova", "onyx"],
            "voicestudio": ["voicestudio", "omnivoice", "cloned_profiles"],
            "openai": list(OPENAI_TTS_VOICES.values()),
            "mark_liv_gemini": list(MARK_LIV_GEMINI_VOICES.values()),
            "edge_neural": list(EDGE_NEURAL_VOICES.values()),
            "offline": ["Microsoft David"],
        }

    def _start_tts_worker(self):
        self._shutdown.clear()
        self.tts_thread = threading.Thread(target=self._tts_worker, daemon=True)
        self.tts_thread.start()

    def _tts_worker(self):
        """Worker thread that executes TTS playback."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            while not self._shutdown.is_set():
                try:
                    text = self.tts_queue.get(timeout=0.2)
                except queue.Empty:
                    continue

                if text is None:
                    break

                text_str = str(text).strip()
                if not self._tts_enabled or not text_str:
                    self.tts_queue.task_done()
                    continue
                text = text_str

                self._abort_utterance.clear()
                self._is_speaking = True
                try:
                    played = False
                    curr = str(self.current_voice).strip()
                    curr_lower = curr.lower()

                    # 1. Check if configured for Mark-LIV Gemini voice (only if tts_engine is explicitly 'gemini')
                    use_gemini_engine = getattr(config, "tts_engine", "edge-tts") == "gemini"
                    is_gemini_voice = curr in MARK_LIV_GEMINI_VOICES.values() or curr_lower in MARK_LIV_GEMINI_VOICES
                    if use_gemini_engine and is_gemini_voice and config.gemini_api_key:
                        target_voice = MARK_LIV_GEMINI_VOICES.get(curr_lower, curr)
                        played = self._speak_gemini_tts(text, target_voice)

                    # 2. Check OpenAI TTS (Sagar Tamang Ultron/Friday or OpenAI voices) if OpenAI key available
                    if not played and (curr_lower in OPENAI_TTS_VOICES or getattr(config, "tts_engine", "") == "openai"):
                        openai_voice = OPENAI_TTS_VOICES.get(curr_lower, "onyx" if "ultron" in curr_lower else "nova")
                        if getattr(config, "openai_api_key", None) or os.getenv("OPENAI_API_KEY"):
                            played = self._speak_openai_tts(text, openai_voice)

                    # 3. Check VoiceStudio Local TTS (OmniVoice / Cloned voices / port 3900)
                    use_voicestudio = getattr(config, "tts_engine", "") == "voicestudio" or curr_lower.startswith(("cloned_", "designed_"))
                    if not played and use_voicestudio:
                        played = self._speak_voicestudio_tts(text, curr)

                    # 4. If not Gemini/OpenAI/VoiceStudio, try Edge-TTS Neural voice (Zero-credential fallback!)
                    if not played and curr_lower != "david":
                        is_female_profile = any(k in curr_lower for k in ("female", "swara", "neerja", "sonia", "friday", "nova", "ava", "fatima"))
                        if is_arabic(text):
                            edge_voice = "ar-AE-FatimaNeural" if is_female_profile else "ar-AE-HamdanNeural"
                        elif "ryan" in curr_lower or "en-gb" in curr_lower:
                            edge_voice = "hi-IN-MadhurNeural" if re.search(r"[\u0900-\u097f]", text) else "en-GB-RyanNeural"
                        elif is_hindi_or_hinglish(text):
                            edge_voice = "hi-IN-SwaraNeural" if is_female_profile else "hi-IN-MadhurNeural"
                        elif curr_lower in ("ultron", "onyx"):
                            edge_voice = "en-US-ChristopherNeural"
                        elif curr_lower in ("friday", "nova"):
                            edge_voice = "en-US-AvaNeural"
                        else:
                            edge_voice = EDGE_NEURAL_VOICES.get(curr_lower, curr if 'neural' in curr_lower else 'en-US-ChristopherNeural' if curr_lower in ('ultron', 'onyx') else 'en-GB-RyanNeural')
                        played = loop.run_until_complete(self._speak_edge_tts(text, edge_voice))

                    # 5. Final Fallback: Offline pyttsx3 (Microsoft David)
                    # IMPORTANT: Only fall back if TTS genuinely failed, NOT if user aborted via barge-in.
                    # Edge-TTS returns False on abort, which is NOT a failure — it means "stop talking".
                    if not played and not self._abort_utterance.is_set():
                        self._speak_pyttsx3_male(text)

                except Exception as e:
                    log.warning("TTS pipeline error: %s. Falling back to pyttsx3", e)
                    # Only fall back to offline voice if the error wasn't caused by an abort
                    if not self._abort_utterance.is_set():
                        try:
                            self._speak_pyttsx3_male(text)
                        except Exception:
                            pass
                finally:
                    self._is_speaking = False
                    self.tts_queue.task_done()
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()
            except Exception:
                pass

    def _speak_gemini_tts(self, text: str, voice_name: str = "Charon") -> bool:
        """Mark-LIV Signature: Synthesize using Google Gemini Speech API (gemini-2.5-flash-preview-tts).
        Uses in-memory PCM playback via sounddevice when available (no disk I/O).
        """
        try:
            if not self._genai_client or self._genai_api_key != config.gemini_api_key:
                from google import genai
                self._genai_client = genai.Client(api_key=config.gemini_api_key)
                self._genai_api_key = config.gemini_api_key
            
            from google.genai import types
            
            resp = self._genai_client.models.generate_content(
                model="gemini-2.5-flash-preview-tts",
                contents=text,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=voice_name
                            )
                        )
                    )
                )
            )

            candidate = resp.candidates[0] if (resp and resp.candidates) else None
            content = getattr(candidate, 'content', None) if candidate else None
            if not content or not getattr(content, 'parts', None):
                return False
            inline_data = getattr(content.parts[0], 'inline_data', None)
            if not inline_data or not getattr(inline_data, 'data', None):
                return False
            raw_pcm = inline_data.data
            if isinstance(raw_pcm, str):
                import base64
                raw_pcm = base64.b64decode(raw_pcm)

            # Try in-memory playback via sounddevice (zero disk I/O)
            try:
                import sounddevice as sd
                import numpy as np
                pcm_arr = np.frombuffer(raw_pcm, dtype=np.int16)
                if self.stark_filter_enabled:
                    pcm_arr = apply_stark_intercom_filter(pcm_arr, sample_rate=24000, intensity=self.stark_filter_intensity)
                audio_array = pcm_arr.astype(np.float32) / 32768.0
                sd.play(audio_array, samplerate=24000, blocking=False)
                # Wait for playback with abort support
                while sd.get_stream().active and not self._abort_utterance.is_set():
                    sd.sleep(50)
                if self._abort_utterance.is_set():
                    sd.stop()
                return True
            except Exception as sd_err:
                log.debug("sounddevice playback failed (%s), falling back to pygame", sd_err)

            # Fallback: pygame via temp file
            temp_wav = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    temp_wav = f.name
                with wave.open(temp_wav, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(24000)
                    wf.writeframes(raw_pcm)

                if not pygame.mixer.get_init():
                    pygame.mixer.init(frequency=24000, size=-16, channels=2, buffer=1024)
                clock = pygame.time.Clock()
                try:
                    pygame.mixer.music.load(temp_wav)
                    pygame.mixer.music.play()
                    while pygame.mixer.music.get_busy() and not self._abort_utterance.is_set():
                        clock.tick(15)
                finally:
                    try:
                        pygame.mixer.music.stop()
                        pygame.mixer.music.unload()
                    except Exception:
                        pass
                return True
            finally:
                if temp_wav and os.path.exists(temp_wav):
                    try:
                        os.remove(temp_wav)
                    except Exception:
                        pass

        except Exception as e:
            log.debug("Gemini TTS (%s) failed: %s", voice_name, e)
            return False

    def _get_edge_rate(self) -> str:
        """Calculate Edge-TTS rate string from config (e.g. '+28%')."""
        raw = getattr(config, "voice_rate_str", "") or os.getenv("VOICE_RATE", "+28%").strip()
        if "%" in raw:
            return raw if raw.startswith(("+", "-")) else f"+{raw}"
        try:
            val = int(raw)
            if -50 <= val <= 100:
                if val == 0:
                    return "+0%"  # Normal speed (no acceleration)
                return f"+{val}%" if val > 0 else f"{val}%"
        except (ValueError, TypeError):
            pass
        return "+28%"  # Crisp, lively conversational pace

    def set_stark_filter(self, enabled: bool, intensity: float = 0.65) -> bool:
        """Toggle or configure the Stark Intercom acoustic filter."""
        self.stark_filter_enabled = bool(enabled)
        self.stark_filter_intensity = max(0.0, min(1.0, float(intensity)))
        log.info("Stark Intercom Audio Filter %s (intensity=%.2f)", "ENABLED" if self.stark_filter_enabled else "DISABLED", self.stark_filter_intensity)
        return self.stark_filter_enabled

    def _play_audio_stream(self, buf: io.BytesIO) -> bool:
        """Play in-memory audio stream (MP3/WAV) with optional Stark Intercom filter and abort handling."""
        try:
            buf.seek(0)
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=24000, size=-16, channels=2, buffer=1024)

            clock = pygame.time.Clock()
            played_filtered = False

            # 1. Apply Cinematic Stark Intercom Filter via pygame sound array
            if self.stark_filter_enabled:
                try:
                    import numpy as np
                    snd = pygame.mixer.Sound(buf)
                    arr = pygame.sndarray.array(snd)
                    init_res = pygame.mixer.get_init()
                    sr = init_res[0] if init_res else 24000
                    filtered_arr = apply_stark_intercom_filter(arr, sample_rate=sr, intensity=self.stark_filter_intensity)

                    channels = init_res[2] if init_res else 2
                    if channels == 2 and filtered_arr.ndim == 1:
                        filtered_arr = np.column_stack([filtered_arr, filtered_arr])
                    elif channels == 1 and filtered_arr.ndim == 2:
                        filtered_arr = filtered_arr[:, 0]

                    snd_filtered = pygame.sndarray.make_sound(filtered_arr)
                    channel = snd_filtered.play()
                    if channel is not None:
                        while channel.get_busy() and not self._abort_utterance.is_set():
                            clock.tick(15)
                        if self._abort_utterance.is_set():
                            channel.stop()
                        played_filtered = True
                    else:
                        played_filtered = False
                except Exception as filter_err:
                    log.debug("Stark filter render failed (%s), using standard playback", filter_err)

            # 2. Fallback to standard music stream if filter disabled or failed
            if not played_filtered:
                buf.seek(0)
                try:
                    pygame.mixer.music.load(buf)
                    pygame.mixer.music.play()

                    while pygame.mixer.music.get_busy() and not self._abort_utterance.is_set():
                        clock.tick(15)
                finally:
                    try:
                        pygame.mixer.music.stop()
                        pygame.mixer.music.unload()
                    except Exception:
                        pass

            return True
        except Exception as e:
            log.warning("Audio stream playback failed: %s", e)
            return False

    def _speak_openai_tts(self, text: str, voice_name: str = "onyx") -> bool:
        """Synthesize using OpenAI TTS API (tts-1) with Sagar Tamang voices ('onyx', 'nova', etc.)."""
        api_key = getattr(config, "openai_api_key", None) or os.getenv("OPENAI_API_KEY")
        if not api_key:
            return False
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            response = client.audio.speech.create(
                model="tts-1",
                voice=voice_name,
                input=text,
                response_format="mp3"
            )
            buf = io.BytesIO(response.content)
            return self._play_audio_stream(buf)
        except Exception as e:
            log.warning("OpenAI TTS speech failed: %s", e)
            return False

    def _speak_voicestudio_tts(self, text: str, voice_or_profile_id: str = "ultron") -> bool:
        """Synthesize speech using debpalash/VoiceStudio local HTTP endpoint (port 3900)."""
        host = os.getenv("VOICESTUDIO_HOST", "http://127.0.0.1:3900").rstrip("/")
        url = f"{host}/v1/audio/speech"
        try:
            import json
            import urllib.request
            payload = {
                "input": text,
                "voice": voice_or_profile_id,
                "model": "omnivoice",
                "response_format": "mp3",
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                content = resp.read()
                if content:
                    buf = io.BytesIO(content)
                    return self._play_audio_stream(buf)
        except Exception as e:
            log.debug("VoiceStudio local TTS speech failed: %s", e)
        return False

    async def _speak_edge_tts(self, text: str, voice_name: str = "en-US-ChristopherNeural") -> bool:
        """Synthesize with Edge-TTS neural voice and play with Stark Intercom Filter in-memory."""
        try:
            rate_str = self._get_edge_rate()
            comm = edge_tts.Communicate(
                text,
                voice_name,
                rate=rate_str,
                volume="+0%",
                pitch="+0Hz"
            )
            buf = io.BytesIO()
            async for chunk in comm.stream():
                if self._abort_utterance.is_set():
                    return False
                if chunk["type"] == "audio":
                    buf.write(chunk["data"])

            if buf.tell() == 0:
                return False

            return self._play_audio_stream(buf)
        except Exception as e:
            log.debug("Edge-TTS speech error: %s", e)
            return False

    def _speak_pyttsx3_male(self, text: str):
        """Offline fallback using Windows native male voice (David)."""
        co_init = False
        try:
            try:
                import pythoncom
                pythoncom.CoInitialize()
                co_init = True
            except Exception:
                pass

            engine = pyttsx3.init()
            engine.setProperty("rate", config.voice_rate)
            engine.setProperty("volume", config.voice_volume)

            voices = engine.getProperty("voices")
            for v in voices:
                if "david" in v.name.lower() or "male" in v.name.lower():
                    engine.setProperty("voice", v.id)
                    break

            engine.say(text)
            engine.runAndWait()
        except Exception as e:
            log.warning("pyttsx3 offline fallback error: %s", e)
        finally:
            if co_init:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

    def speak(self, text: Any, split_sentences: bool = False):
        """Queue text to be spoken with seamless continuous synthesis (eliminating inter-sentence network pauses).
        If split_sentences is explicitly True, divides sentences into discrete queue items.
        Accepts strings or streaming token iterators/generators.
        """
        if hasattr(text, '__iter__') and not isinstance(text, (str, bytes)):
            return self.speak_streamed(text)

        if not self._tts_enabled or not text:
            return
        clean_text = self._sanitize_for_tts(str(text))
        if clean_text:
            # Cached audio phrases still go through the queue to avoid racing the worker
            # thread over the global audio stream (sounddevice/pygame mixer).

            if split_sentences:
                sentences = re.split(r'(?<=[.!?\n])\s+', clean_text)
                for s in sentences:
                    s_clean = s.strip()
                    if s_clean:
                        self._dispatch_tts_item(s_clean)
                return

            # Continuous synthesis: keep utterances intact up to ~1200 chars for smooth, natural cadence
            # without jarring network reconnect latency or awkward pauses between sentences.
            if len(clean_text) <= 1200:
                self._dispatch_tts_item(clean_text)
            else:
                # For long texts, break on paragraphs or natural clause clusters (~800 chars)
                paragraphs = [p.strip() for p in re.split(r'\n\s*\n+', clean_text) if p.strip()]
                for p in paragraphs:
                    if len(p) <= 1200:
                        self._dispatch_tts_item(p)
                    else:
                        sentences = re.split(r'(?<=[.!?])\s+', p)
                        cur_chunk = []
                        cur_len = 0
                        for s in sentences:
                            s = s.strip()
                            if not s:
                                continue
                            if cur_len + len(s) > 800 and cur_chunk:
                                self._dispatch_tts_item(" ".join(cur_chunk))
                                cur_chunk = [s]
                                cur_len = len(s)
                            else:
                                cur_chunk.append(s)
                                cur_len += len(s)
                        if cur_chunk:
                            self._dispatch_tts_item(" ".join(cur_chunk))


    def speak_streamed(self, token_iterator):
        """Accept an iterator/generator of text chunks (streaming LLM tokens).
        Buffers tokens into complete sentences and dispatches each sentence
        to TTS immediately — so sentence 1 plays while the LLM generates sentence 2.
        
        Returns the full accumulated text.
        """
        if not self._tts_enabled:
            # Still consume the iterator to get full text
            return "".join(token_iterator)

        buffer = ""
        full_text = ""
        sentence_endings = re.compile(r'([.!?;:\n])\s*')

        for token in token_iterator:
            if self._abort_utterance.is_set():
                full_text += token
                continue
            buffer += token
            full_text += token

            # Split on sentence boundaries
            parts = sentence_endings.split(buffer)
            if len(parts) > 2:
                # We have at least one complete clause + punctuation
                clause = parts[0] + parts[1]
                buffer = "".join(parts[2:])

                clean = self._sanitize_for_tts(clause)
                if clean and len(clean) > 3:  # Skip tiny fragments
                    self._dispatch_tts_item(clean)

        # Flush remaining buffer
        if buffer.strip():
            clean = self._sanitize_for_tts(buffer)
            if clean:
                self._dispatch_tts_item(clean)

        return full_text

    # ── Audio Phrase Cache ──────────────────────────────────────────────
    _audio_cache: dict = {}
    _CACHE_MAX = 50

    def cache_phrase(self, text: str, pcm_data: bytes, sample_rate: int = 24000):
        """Cache a synthesized phrase for instant replay."""
        if len(self._audio_cache) >= self._CACHE_MAX:
            # Evict oldest entry
            oldest = next(iter(self._audio_cache))
            del self._audio_cache[oldest]
        self._audio_cache[text] = (pcm_data, sample_rate)

    def _play_cached_audio(self, text: str):
        """Play a cached audio phrase directly from memory."""
        entry = self._audio_cache.get(text)
        if not entry:
            return
        pcm_data, sample_rate = entry
        try:
            import sounddevice as sd
            import numpy as np
            audio = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32) / 32768.0
            self._is_speaking = True
            sd.play(audio, samplerate=sample_rate, blocking=False)
            while sd.get_stream().active and not self._abort_utterance.is_set():
                sd.sleep(50)
            if self._abort_utterance.is_set():
                sd.stop()
        except Exception:
            # Fallback: queue normally
            self.tts_queue.put(text)
        finally:
            self._is_speaking = False

    def stop_speaking(self):
        """Cancel ongoing and queued speech."""
        self._abort_utterance.set()
        while not self.tts_queue.empty():
            try:
                self.tts_queue.get_nowait()
                self.tts_queue.task_done()
            except Exception:
                break
        try:
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
        except Exception:
            pass

    def barge_in(self) -> bool:
        """Full-Duplex Barge-in: Immediately interrupt ongoing and queued speech."""
        was_speaking = self._is_speaking or not self.tts_queue.empty()
        self.stop_speaking()
        return was_speaking

    def _sanitize_for_tts(self, text: str) -> str:
        if not isinstance(text, str):
            text = str(text)
        text = re.sub(r'```[\s\S]*?```', ' [Code block omitted] ', text)
        text = re.sub(r'`([^`]+)`', r'\1', text)  # inline code
        text = re.sub(r'https?://\S+', '', text)  # URLs
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)  # markdown links
        text = re.sub(r'(?:^|(?<=\s))[*_~]{1,3}([^*_~]+?)[*_~]{1,3}(?=\s|$|[.,!?;:])', r'\1', text)  # bold/italic/strike (preserve underscores in identifiers)
        text = re.sub(r'<[^>]+>', '', text)  # strip XML/HTML tags
        text = re.sub(r'^[#*>\s]+', '', text, flags=re.MULTILINE)  # headers/bullets (preserve leading hyphens for negative numbers)
        text = re.sub(r'\|[^\n]+\|', ' ', text)  # tables
        text = re.sub(r'[\U00010000-\U0010ffff\u2600-\u27bf\ufe00-\ufe0f]', '', text)  # emojis+symbols
        return re.sub(r'\s+', ' ', text).strip()

    def listen(self, status_callback: Optional[Callable[[str], None]] = None) -> Optional[str]:
        """Listen to the default microphone and return recognized text.
        Uses on-device Faster-Whisper first for zero cloud latency, with Google Speech fallback.
        """
        try:
            with sr.Microphone() as source:
                if status_callback:
                    status_callback("Adjusting for background noise...")
                self.recognizer.adjust_for_ambient_noise(source, duration=0.8)

                if status_callback:
                    status_callback("Listening... (speak now)")
                audio = self.recognizer.listen(source, timeout=6, phrase_time_limit=15)

                if status_callback:
                    status_callback("Transcribing speech...")

                # Try local offline Faster-Whisper first
                text = None
                try:
                    from wake_word import wake_detector
                    text = wake_detector.whisper.transcribe_audio_data(audio)
                except Exception:
                    pass

                # Fallback to Google Web Speech
                if not text:
                    text = self.recognizer.recognize_google(audio, language="en-IN")

                return text.strip() if text else None
        except sr.WaitTimeoutError:
            if status_callback:
                status_callback("Listening timed out (no speech detected).")
            return None
        except sr.UnknownValueError:
            if status_callback:
                status_callback("Could not understand audio.")
            return None
        except sr.RequestError as e:
            if status_callback:
                status_callback(f"Speech recognition service error: {e}")
            return None
        except Exception as e:
            if status_callback:
                status_callback(f"Microphone error: {e}")
            return None

    def listen_one_shot(self, timeout: int = 7) -> Optional[str]:
        """One-shot listen helper for CLI interactive triggers."""
        try:
            with sr.Microphone() as source:
                self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
                audio = self.recognizer.listen(source, timeout=timeout, phrase_time_limit=12)

                # Try local offline Faster-Whisper first
                text = None
                try:
                    from wake_word import wake_detector
                    text = wake_detector.whisper.transcribe_audio_data(audio)
                except Exception:
                    pass

                if not text:
                    text = self.recognizer.recognize_google(audio, language="en-IN")

                return text.strip() if text else None
        except Exception:
            return None


voice = VoiceEngine()
