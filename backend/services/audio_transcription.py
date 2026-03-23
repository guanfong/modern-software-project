"""
Audio transcription service using OpenAI Whisper (local or API)
"""
import os
import shutil
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

# https://platform.openai.com/docs/guides/speech-to-text — Whisper API formats
OPENAI_WHISPER_EXTENSIONS = frozenset(
    {
        ".flac",
        ".m4a",
        ".mp3",
        ".mp4",
        ".mpeg",
        ".mpga",
        ".oga",
        ".ogg",
        ".wav",
        ".webm",
    }
)


def resolve_hr_briefing_audio_extension(
    filename: Optional[str],
    content_type: Optional[str],
    content: bytes,
) -> str:
    """
    Choose a Whisper-friendly extension for saved bytes. Browsers often omit filenames
    or send application/octet-stream; defaulting to .mp3 mislabels real m4a/mp4 and
    OpenAI returns 400 invalid format.
    """
    if filename:
        suf = Path(filename).suffix.lower()
        if suf in OPENAI_WHISPER_EXTENSIONS:
            return suf

    ct = (content_type or "").split(";")[0].strip().lower()
    ct_map = {
        "audio/m4a": ".m4a",
        "audio/x-m4a": ".m4a",
        "audio/mp4": ".m4a",
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/wave": ".wav",
        "audio/webm": ".webm",
        "audio/ogg": ".ogg",
        "audio/oga": ".oga",
        "application/ogg": ".ogg",
        "audio/flac": ".flac",
        "audio/x-flac": ".flac",
        "video/mp4": ".mp4",
        "audio/aac": ".m4a",
    }
    mapped = ct_map.get(ct)
    if mapped and mapped in OPENAI_WHISPER_EXTENSIONS:
        return mapped

    if len(content) >= 12:
        if content[:3] == b"ID3" or content[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
            return ".mp3"
        if content[0] == 0xFF and (content[1] & 0xE0) == 0xE0:
            return ".mp3"
        if content[4:8] == b"ftyp":
            return ".m4a"
        if content[:4] == b"RIFF" and content[8:12] == b"WAVE":
            return ".wav"
        if content[:4] == b"OggS":
            return ".ogg"
        if content[:4] == b"fLaC":
            return ".flac"
        if content[:4] == b"\x1aE\xdf\xa3":
            return ".webm"

    if filename:
        suf = Path(filename).suffix.lower()
        if suf and suf != ".":
            return suf

    return ".m4a"


# Try to import whisper, but handle any errors gracefully
WHISPER_AVAILABLE = False
whisper = None
try:
    import whisper

    WHISPER_AVAILABLE = True
except (ImportError, TypeError, Exception) as e:
    # Whisper failed to import - this is OK, we'll use OpenAI API as fallback
    WHISPER_AVAILABLE = False
    print(
        f"Warning: Local Whisper not available ({type(e).__name__}). "
        "Will use OpenAI Whisper API as fallback."
    )


class AudioTranscriptionService:
    def __init__(self):
        self.model = None
        self.model_name = "base"  # Use base model for faster processing
        self.whisper_available = WHISPER_AVAILABLE
        self.openai_api_key = os.getenv("OPENAI_API_KEY")

    def _load_model(self):
        """Lazy load Whisper model"""
        if not self.whisper_available:
            raise Exception("Local Whisper is not installed. Using OpenAI API instead.")
        if self.model is None:
            try:
                self.model = whisper.load_model(self.model_name)
            except Exception as e:
                print(f"Error loading Whisper model: {e}. Falling back to OpenAI API.")
                self.whisper_available = False
                raise
        return self.model

    def _ffmpeg_convert_to_mp3(self, src: Path) -> Path:
        """Convert arbitrary audio to mp3 using ffmpeg (must be on PATH)."""
        fd, out = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        out_path = Path(out)
        try:
            proc = subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(src),
                    "-acodec",
                    "libmp3lame",
                    "-q:a",
                    "4",
                    str(out_path),
                ],
                capture_output=True,
                text=True,
                timeout=600,
            )
            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout or "").strip()
                raise RuntimeError(err[:800] if err else f"exit {proc.returncode}")
        except Exception:
            out_path.unlink(missing_ok=True)
            raise
        return out_path

    def _prepare_path_for_openai_whisper(self, audio_path: Path) -> Tuple[Path, Optional[Path]]:
        """
        Return (path_to_send, temp_path_to_delete).
        If format is not accepted by OpenAI, try ffmpeg -> mp3 when ffmpeg is installed.
        """
        suffix = (audio_path.suffix or "").lower()
        ffmpeg = shutil.which("ffmpeg")

        if suffix not in OPENAI_WHISPER_EXTENSIONS:
            if ffmpeg:
                converted = self._ffmpeg_convert_to_mp3(audio_path)
                return converted, converted
            allowed = ", ".join(sorted(e.lstrip(".") for e in OPENAI_WHISPER_EXTENSIONS))
            raise Exception(
                f"Unsupported audio format ({suffix or 'no extension'}). "
                f"OpenAI Whisper accepts: {allowed}. "
                "Convert to mp3 or wav, or install ffmpeg on the server for automatic conversion."
            )

        # M4A/MP4 often use codecs (e.g. ALAC) the API rejects despite the extension;
        # normalize to mp3 when ffmpeg is available.
        if ffmpeg and suffix in {".m4a", ".mp4"}:
            converted = self._ffmpeg_convert_to_mp3(audio_path)
            return converted, converted

        return audio_path, None

    def _transcribe_with_openai(self, audio_path: Path) -> str:
        """Transcribe using OpenAI Whisper API"""
        try:
            from openai import OpenAI

            client = OpenAI(api_key=self.openai_api_key)

            data = audio_path.read_bytes()
            name = audio_path.name
            buf = BytesIO(data)
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=(name, buf),
                response_format="text",
            )
            return transcript
        except ImportError:
            raise Exception("OpenAI library not installed. Please install it with: pip install openai")
        except Exception as e:
            raise Exception(f"Error transcribing with OpenAI API: {str(e)}")

    def transcribe(self, audio_path: Path) -> str:
        """Transcribe audio file to text using local Whisper or OpenAI API"""
        tmp_to_cleanup: Optional[Path] = None
        try:
            if self.whisper_available:
                try:
                    model = self._load_model()
                    result = model.transcribe(str(audio_path))
                    return result["text"]
                except Exception as e:
                    print(f"Local Whisper failed: {e}. Falling back to OpenAI API.")
            if not self.openai_api_key:
                raise Exception("No OpenAI API key found. Please set OPENAI_API_KEY in .env file.")

            to_send, tmp_to_cleanup = self._prepare_path_for_openai_whisper(audio_path)
            return self._transcribe_with_openai(to_send)
        except Exception as e:
            raise Exception(f"Error transcribing audio: {str(e)}")
        finally:
            if tmp_to_cleanup is not None and tmp_to_cleanup != audio_path:
                tmp_to_cleanup.unlink(missing_ok=True)

    async def transcribe_async(self, audio_path: Path) -> str:
        """Async wrapper for transcription"""
        return self.transcribe(audio_path)
