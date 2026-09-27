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

    def ready(self) -> bool:
        """模型是否已驻留内存。首次转写会加载（可能还要下载），所以「有能力」和「已就绪」是两回事。"""
        ...

    def warmup(self) -> None:
        """显式把模型加载进内存（首次可能要下载几百 MB）。用于让前端能给用户一个明确的等待提示。"""
        ...


class SpeechSynthesizer(Protocol):
    def synthesize(self, text: str, *, turn_id: str) -> AsyncIterator[bytes]: ...


# 静态默认值：这里**故意**写死 asr/tts 为 False，它描述的是「协议本身不承诺任何实现」。
# 实际对外提供的能力由 create_app 注入的 recognizer 决定（见 app.py 的 speech_capabilities），
# 不要直接改这个字典——它按引用返回给 JSON 并被多次 create_app 共享，改它会串场次。
CAPABILITIES = {"asr": False, "tts": False, "input_modes": ["text"]}
