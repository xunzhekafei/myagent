"""本地语音识别：faster-whisper（CTranslate2）。

依赖只在本模块内部 import，而且只在 `build_recognizer()` 里——CI 只装
requirements-web.txt + requirements-dev.txt，模块级 import 会把 faster-whisper
变成硬依赖，也会让每次 `import backend.app` 都慢几秒。

实现的是 speech.py 里那个**流式**协议：批式后端天然满足它——先把音频块收完，
用 to_thread 跑推理（推理不能在事件循环上跑），只 yield 一个 final=True 的转写。
这样 speech.py 一个字都不用改，将来要接真流式也还有位置。
"""
import asyncio
import io
import os
import pathlib
import sys
import threading
import uuid

from .speech import Transcript

# large-v3-turbo 的中文准确率明显好于 small，fp16 约 1.6GB，8GB 显存够用
DEFAULT_MODEL = "large-v3-turbo"
# initial_prompt 干两件事：
#  1) 开头一句指定普通话，否则 Whisper 系列常输出**繁体**——候选人看到繁体字会以为是 bug；
#  2) 列出领域术语做偏置。面试回答里全是术语，而 Whisper 对它们很敏感：
#     实测不列术语时「向量召回」会被听成「向梁照回」，列上就对了，而且更快（少走弯路）。
#     不列术语还会丢掉句子边界（整段没有标点）。
# 想换词表用 WHISPER_PROMPT 覆盖；设成空字符串则完全不传 prompt。
ZH_PROMPT = (
    "以下是普通话的技术面试回答。"
    "常见术语：检索增强生成、向量召回、重排序、大模型、微调、推理、量化、"
    "注意力机制、智能体、提示词、上下文、幻觉、准确率、延迟、吞吐、"
    "事件循环、闭包、渲染、打包、组件。"
)


def _register_cuda_dlls():
    """Windows 上把 nvidia-* wheel 的 bin 目录注册进 DLL 搜索路径。

    Python 3.8+ 不再用 PATH 解析扩展模块的 DLL 依赖，pip 装的 nvidia-cublas-cu12 /
    nvidia-cudnn-cu12 把 DLL 放在 site-packages/nvidia/*/bin，不注册的话
    ctranslate2 会报「Library cublas64_12.dll is not found or cannot be loaded」。
    """
    if sys.platform != "win32" or not hasattr(os, "add_dll_directory"):
        return
    try:
        import nvidia
    except Exception:
        return
    for root in list(getattr(nvidia, "__path__", [])):
        for bin_dir in pathlib.Path(root).glob("*/bin"):
            try:
                os.add_dll_directory(str(bin_dir))
            except OSError:
                pass


class WhisperRecognizer:
    """懒加载 + GPU 优先、CPU 兜底。模型不在启动时加载，首次转写或显式预热才加载。"""

    def __init__(self, model_name=None, device=None, language=None, cpu_threads=None):
        self.model_name = model_name or os.environ.get("WHISPER_MODEL") or DEFAULT_MODEL
        self.device = (device or os.environ.get("WHISPER_DEVICE") or "auto").lower()
        self.language = (language if language is not None
                         else os.environ.get("WHISPER_LANGUAGE", "zh")) or None
        self.cpu_threads = cpu_threads or int(os.environ.get("WHISPER_CPU_THREADS", "6"))
        self.download_root = os.environ.get("WHISPER_MODEL_DIR") or None
        # 显式设成空串表示不要 prompt
        self.prompt = (os.environ.get("WHISPER_PROMPT", ZH_PROMPT) or None) if self.language == "zh" else None
        self._model = None
        # 串行化推理与加载：限制显存/内存占用、避免并发时延迟抖动
        self._lock = threading.RLock()

    def ready(self) -> bool:
        return self._model is not None

    def warmup(self) -> None:
        self._load()

    # ---- 加载 ----

    def _candidates(self):
        if self.device == "cpu":
            return [("cpu", "int8")]
        if self.device == "cuda":
            return [("cuda", "float16")]
        return [("cuda", "float16"), ("cpu", "int8")]     # auto

    def _load(self):
        with self._lock:
            if self._model is not None:
                return self._model
            from faster_whisper import WhisperModel

            _register_cuda_dlls()
            failure = None
            for device, compute_type in self._candidates():
                try:
                    print(f"[语音] 加载 {self.model_name}（device={device}, "
                          f"compute_type={compute_type}）…首次使用可能需要下载模型", flush=True)
                    model = WhisperModel(self.model_name, device=device, compute_type=compute_type,
                                         cpu_threads=self.cpu_threads, download_root=self.download_root)
                except Exception as error:
                    # 缺 DLL 时抛的是 RuntimeError，不是 ImportError/OSError，必须宽catch
                    failure = error
                    print(f"[语音] {device} 不可用（{type(error).__name__}: {error}），尝试下一个", flush=True)
                    continue
                self._model = model
                print("[语音] 模型就绪", flush=True)
                return self._model
            raise RuntimeError(f"语音模型加载失败：{failure}")

    # ---- 识别 ----

    def _run(self, payload: bytes) -> str:
        model = self._load()
        with self._lock:
            segments, _info = model.transcribe(
                io.BytesIO(payload),      # 不能传裸 bytes——会被当成文件路径，中文路径上尤其危险
                language=self.language,
                initial_prompt=self.prompt,
                vad_filter=True,          # 1–2 秒静音正是 Whisper 产生幻觉的地方
                condition_on_previous_text=False,
            )
            # segments 是惰性生成器，真正的解码发生在迭代时——必须在锁内消费完
            return "".join(segment.text for segment in segments).strip()

    async def transcribe(self, audio, *, mime_type: str):
        payload = b"".join([chunk async for chunk in audio])
        utterance_id = uuid.uuid4().hex
        if not payload:
            yield Transcript("", True, utterance_id)
            return
        text = await asyncio.to_thread(self._run, payload)     # 推理不能占着事件循环
        yield Transcript(text, True, utterance_id)


def build_recognizer():
    """按环境构建识别器；faster-whisper 不可用就返回 None（不抛异常）。"""
    try:
        import faster_whisper  # noqa: F401
    except Exception as error:
        print(f"[语音] 未启用（{type(error).__name__}: {error}）", flush=True)
        return None
    return WhisperRecognizer()
