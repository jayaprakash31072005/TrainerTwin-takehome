"""Take-home latency.jsonl + transcript logging for TrainerTwin submission."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ClientConnectedFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.processors.frame_processor import FrameDirection


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _log_paths() -> tuple[Path, Path]:
    root = Path(os.getenv("TAKEHOME_LOG_DIR", str(_repo_root())))
    return root / "latency.jsonl", root / "transcript.txt"


class TakehomeLatencyObserver(BaseObserver):
    """Writes one latency.jsonl line per user→bot response turn."""

    def __init__(self, jsonl_path: Path, **kwargs):
        super().__init__(observe_every_push=False, **kwargs)
        self._jsonl_path = jsonl_path
        self._session_start: float | None = None
        self._turn = 0
        self._waiting_for_user = False
        self._user_stopped_ms: int | None = None
        self._stt_final_ms: int | None = None
        self._llm_first_token_ms: int | None = None
        self._tts_first_audio_ms: int | None = None
        self._interrupted = False
        self._bot_was_speaking = False

    def _ms(self, when: float) -> int:
        assert self._session_start is not None
        return int(round((when - self._session_start) * 1000))

    def _reset_cycle(self) -> None:
        self._user_stopped_ms = None
        self._stt_final_ms = None
        self._llm_first_token_ms = None
        self._tts_first_audio_ms = None
        self._interrupted = False

    async def on_push_frame(self, data: FramePushed) -> None:
        if data.direction != FrameDirection.DOWNSTREAM:
            return

        frame = data.frame
        now = time.time()

        if isinstance(frame, ClientConnectedFrame):
            if self._session_start is None:
                self._session_start = now
                self._jsonl_path.write_text("", encoding="utf-8")
            return

        if self._session_start is None:
            return

        if isinstance(frame, VADUserStoppedSpeakingFrame):
            # Actual silence (not turn-detection release): VAD time minus stop_secs.
            user_quiet = frame.timestamp - frame.stop_secs
            self._waiting_for_user = True
            self._reset_cycle()
            self._user_stopped_ms = self._ms(user_quiet)
        elif isinstance(frame, TranscriptionFrame) and not isinstance(
            frame, InterimTranscriptionFrame
        ):
            if self._waiting_for_user:
                self._stt_final_ms = self._ms(now)
        elif isinstance(frame, LLMTextFrame):
            if self._waiting_for_user and self._llm_first_token_ms is None:
                self._llm_first_token_ms = self._ms(now)
        elif isinstance(frame, TTSAudioRawFrame):
            if self._waiting_for_user and self._tts_first_audio_ms is None:
                self._tts_first_audio_ms = self._ms(now)
        elif isinstance(frame, InterruptionFrame):
            if self._bot_was_speaking:
                self._interrupted = True
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._bot_was_speaking = True
            if self._waiting_for_user and self._user_stopped_ms is not None:
                self._turn += 1
                record = {
                    "turn": self._turn,
                    "user_stopped_ms": self._user_stopped_ms,
                    "stt_final_ms": self._stt_final_ms,
                    "llm_first_token_ms": self._llm_first_token_ms,
                    "tts_first_audio_ms": self._tts_first_audio_ms,
                    "interrupted": self._interrupted,
                }
                with self._jsonl_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(record) + "\n")
                logger.info("Latency turn {}: {}", self._turn, record)
                self._waiting_for_user = False
                self._reset_cycle()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_was_speaking = False


class TranscriptObserver(BaseObserver):
    """Append-only transcript with interruption markers."""

    def __init__(self, transcript_path: Path, **kwargs):
        super().__init__(observe_every_push=False, **kwargs)
        self._path = transcript_path
        self._bot_buffer: list[str] = []
        self._started = False

    async def on_push_frame(self, data: FramePushed) -> None:
        if data.direction != FrameDirection.DOWNSTREAM:
            return

        frame = data.frame
        if isinstance(frame, ClientConnectedFrame) and not self._started:
            self._started = True
            self._path.write_text("", encoding="utf-8")

        if isinstance(frame, TranscriptionFrame) and not isinstance(
            frame, InterimTranscriptionFrame
        ):
            text = (frame.text or "").strip()
            if text:
                self._append("user", text)
        elif isinstance(frame, LLMTextFrame):
            chunk = (frame.text or "").strip()
            if chunk:
                self._bot_buffer.append(chunk)
        elif isinstance(frame, BotStartedSpeakingFrame):
            if self._bot_buffer:
                self._append("assistant", "".join(self._bot_buffer))
                self._bot_buffer.clear()
        elif isinstance(frame, InterruptionFrame):
            if self._bot_buffer:
                self._append("assistant", "".join(self._bot_buffer) + " [INTERRUPTED]")
                self._bot_buffer.clear()
            else:
                self._append("system", "[user interrupted bot]")


    def _append(self, role: str, text: str) -> None:
        with self._path.open("a", encoding="utf-8") as f:
            f.write(f"{role.upper()}: {text}\n")


def build_takehome_observers() -> list[BaseObserver]:
    jsonl_path, transcript_path = _log_paths()
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    latency_observer = UserBotLatencyObserver()

    @latency_observer.event_handler("on_latency_measured")
    async def _log_latency(_observer, latency_seconds: float):
        logger.info("User→bot latency: {:.3f}s", latency_seconds)

    @latency_observer.event_handler("on_latency_breakdown")
    async def _log_breakdown(_observer, breakdown):
        for line in breakdown.turn_contribution_lines():
            logger.debug(line)

    return [
        latency_observer,
        TakehomeLatencyObserver(jsonl_path),
        TranscriptObserver(transcript_path),
    ]


def summarize_latency_jsonl(path: Path | None = None) -> dict[str, float | int]:
    """Compute p50/p95 of (tts_first_audio_ms - user_stopped_ms) from latency.jsonl."""
    path = path or _log_paths()[0]
    if not path.is_file():
        return {}

    gaps: list[int] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        u = row.get("user_stopped_ms")
        t = row.get("tts_first_audio_ms")
        if u is not None and t is not None:
            gaps.append(int(t) - int(u))

    if not gaps:
        return {"turns": 0}

    gaps.sort()
    n = len(gaps)

    def pct(p: float) -> int:
        idx = min(n - 1, max(0, int(round(p * (n - 1)))))
        return gaps[idx]

    return {
        "turns": n,
        "p50_user_stop_to_first_audio_ms": pct(0.50),
        "p95_user_stop_to_first_audio_ms": pct(0.95),
        "min_ms": gaps[0],
        "max_ms": gaps[-1],
    }
