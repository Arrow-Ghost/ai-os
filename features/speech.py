"""
Ears: recording audio and turning it into text.

Transcription runs on Groq's hosted Whisper, so there is no local model to
install and no GPU setup -- the audio file is uploaded and text comes back.

PRIVACY -- both tools here are tiered DANGER on purpose:
  speech.record      switches on your microphone
  speech.transcribe  uploads audio to Groq, and audio cannot be redacted

Neither is irreversible in the usual sense. They are gated because "the agent
turned on the mic without asking" is the exact failure nobody forgives. If
voice is the core of your demo, lower them deliberately in config/policy.yaml:

    governance:
      tier_overrides:
        speech.record: write
        speech.transcribe: write
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from servant.sdk import Tier, ToolError, tool

MAX_SECONDS = 300


def _recorder(destination: Path, seconds: int) -> tuple[list[str], str]:
    """Pick a recording command. PipeWire first, then PulseAudio, then ALSA."""
    if shutil.which("pw-record"):
        return ["pw-record", "--rate", "16000", "--channels", "1",
                "--target", "0", str(destination)], "pw-record"
    if shutil.which("parecord"):
        return ["parecord", "--rate=16000", "--channels=1",
                "--file-format=wav", str(destination)], "parecord"
    if shutil.which("arecord"):
        return ["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1",
                "-d", str(seconds), str(destination)], "arecord"
    raise ToolError("no recorder found (install pipewire-utils, pulseaudio-utils or alsa-utils)")


@tool(
    name="speech.record",
    tier=Tier.DANGER,
    params={"seconds": "How long to record, 1-300", "name": "Optional file name"},
    undo="delete the .wav file printed in the result",
)
def speech_record(ctx, seconds: int = 10, name: str = "") -> str:
    """Record from the microphone to a wav file. TURNS ON YOUR MIC."""
    if not 1 <= seconds <= MAX_SECONDS:
        raise ToolError(f"seconds must be between 1 and {MAX_SECONDS}, got {seconds}")

    stem = name.strip() or f"rec-{time.strftime('%Y%m%d-%H%M%S')}"
    destination = ctx.state_dir("speech") / f"{Path(stem).stem}.wav"
    cmd, backend = _recorder(destination, seconds)

    ctx.log(f"recording {seconds}s via {backend}")
    try:
        # pw-record and parecord stream until killed; arecord honours -d itself.
        if backend == "arecord":
            subprocess.run(cmd, check=True, capture_output=True, timeout=seconds + 15)
        else:
            process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(seconds)
            process.terminate()
            process.wait(timeout=10)
    except subprocess.CalledProcessError as exc:
        raise ToolError(f"{backend} failed: {exc.stderr.decode()[:200]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"{backend} timed out") from exc

    if not destination.exists() or destination.stat().st_size < 1024:
        raise ToolError(f"{backend} produced no audio -- is a microphone connected and unmuted?")

    ctx.memory.remember("speech.last_recording", str(destination))
    return f"{destination} ({destination.stat().st_size // 1024}KB, {seconds}s via {backend})"


@tool(
    name="speech.transcribe",
    tier=Tier.DANGER,
    params={"path": "Audio file to transcribe. Leave empty to use the last recording."},
    undo="nothing to undo locally, but the audio has already been sent to Groq",
)
def speech_transcribe(ctx, path: str = "") -> str:
    """Turn an audio file into text. UPLOADS THE AUDIO to Groq's Whisper."""
    if path:
        audio = ctx.check_path(path)
    else:
        last = ctx.memory.recall("speech.last_recording")
        if not last:
            raise ToolError("no path given and no previous recording -- run speech.record first")
        audio = ctx.check_path(last)

    if not audio.is_file():
        raise ToolError(f"no such audio file: {audio}")

    ctx.log(f"transcribing {audio.name}")
    text = ctx.transcribe(audio)
    # Someone may well have said a password out loud.
    return ctx.scrub(text) or "(no speech detected)"
