"""Future providers implement these contracts; partial ASR never submits a turn."""
from dataclasses import dataclass
from typing import AsyncIterator, Protocol


@dataclass(frozen=True)
class Transcript:
    text: str
    final: bool
    utterance_id: str


class SpeechRecognizer(Protocol):
    def transcribe(self, audio: AsyncIterator[bytes], *, mime_type: str) -> AsyncIterator[Transcript]: ...


class SpeechSynthesizer(Protocol):
    def synthesize(self, text: str, *, turn_id: str) -> AsyncIterator[bytes]: ...


CAPABILITIES = {"asr": False, "tts": False, "input_modes": ["text"]}
