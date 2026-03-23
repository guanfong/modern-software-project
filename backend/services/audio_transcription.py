"""
Audio transcription service using OpenAI Whisper (local or API)
"""
import os
import shutil
import subprocess
import tempfile
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
        if suffix in OPENAI_WHISPER_EXTENSIONS:
            return audio_path, None

        if shutil.which("ffmpeg"):
            converted = self._ffmpeg_convert_to_mp3(audio_path)
            return converted, converted

        allowed = ", ".join(sorted(e.lstrip(".") for e in OPENAI_WHISPER_EXTENSIONS))
        raise Exception(
            f"Unsupported audio format ({suffix or 'no extension'}). "
            f"OpenAI Whisper accepts: {allowed}. "
            "Convert to mp3 or wav, or install ffmpeg on the server for automatic conversion."
        )

    def _transcribe_with_openai(self, audio_path: Path) -> str:
        """Transcribe using OpenAI Whisper API"""
        try:
            from openai import OpenAI

            client = OpenAI(api_key=self.openai_api_key)

            with open(audio_path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
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
