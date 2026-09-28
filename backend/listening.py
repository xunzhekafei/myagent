"""回答过程中的打断：边录边滚动转写，够条件就问面试官要不要插话。

真流式 ASR 我们没有（faster-whisper 是批式、30 秒窗口），但把「录到现在的音频」整段
重新转写只要一秒左右（实测 30 秒缓冲 1.1s、50 秒 1.84s），所以每 8 秒重转一次，
效果就等同于边说边出字。**不需要任何新模型或新依赖。**

浏览器那边每 2 秒送来一块音频（MediaRecorder 的 dataavailable）。这些块是 webm 流的
**片段**，不是独立文件——所以这里只做追加，转写时解的是「到目前为止的拼接」。
实测被截断的 webm 能正常解码（截到 10%/50%/90%/98% 都行，解出时长与比例成正比）。

这个模块只负责「听」和「问」；真正落库由 InterviewService.interrupt_with 做。
"""
import asyncio
import logging
import threading
import time
import uuid

logger = logging.getLogger(__name__)

# 判断节奏。8 秒是「够快能抓住问题」和「别太费」之间的折中：
# 一次 60 秒的回答大约判断 7 次。
JUDGE_INTERVAL = 8.0

# 闸门。**光靠 prompt 拦不住不停打断**——每 8 秒都有新文本，判断模型总能找到点什么问。
# 数字照技能里各档的追问上限（1/2/3）来，免得温和档也能被连着打断三次。
GATES = {
    "温和": {"min_chars": 80, "min_new": 60, "cooldown": 30.0, "max_interrupts": 1},
    "标准": {"min_chars": 50, "min_new": 40, "cooldown": 25.0, "max_interrupts": 2},
    "压力": {"min_chars": 30, "min_new": 25, "cooldown": 20.0, "max_interrupts": 3},
}
DEFAULT_GATE = GATES["标准"]

# 「需句末标点」是关键那条：8 秒的中文通常才 25–40 字，多半停在句子中间，
# 在那里打断正是让功能显得坏掉的原因。只在句子讲完的地方才考虑插话。
SENTENCE_END = "。！？…!?"

MIN_AUDIO_BYTES = 4096          # 第一块往往只有容器头，转不出东西
MAX_AUDIO_BYTES = 8 * 1024 * 1024
MAX_SECONDS = 120.0             # 和前端 MAX_RECORD_MS 对齐
SLOW_PASS_SECONDS = 4.0         # 单次重转超时太久就放缓节奏，别一直占着识别器的锁
MAX_TRANSCRIBE_FAILURES = 3     # 连续解不出来就放弃这个监听会话


class ListenSession:
    """一段录音期间的监听：累积音频 → 滚动重转 → 过闸门 → 问面试官。"""

    def __init__(self, session_id, anchor_id, pressure, *, service, recognizer, judge):
        self.listen_id = uuid.uuid4().hex
        self.session_id = session_id
        # 锚点：开始监听时最后一条面试官消息的 id。打断落库前要确认它还是最后一条，
        # 否则那半截回答会被算到下一道题上（见 service.interrupt_with）。
        self.anchor_id = anchor_id
        self.pressure = pressure if pressure in GATES else "标准"
        self.service = service
        self.recognizer = recognizer
        self.judge = judge

        self._audio = bytearray()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self.transcript = ""
        self.judged_text = ""
        self.interrupts = 0
        self.last_judged_at = 0.0
        self._failures = 0
        self.stopped_reason = ""

    # ---- 生命周期 ----

    def start(self):
        self._thread = threading.Thread(target=self._loop, name=f"listen-{self.listen_id[:6]}",
                                        daemon=True)
        self._thread.start()

    def stop(self, reason="", timeout=5.0):
        self.stopped_reason = reason
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            # 判断调用可能正在网络上，join 未必等得到；守护线程不会拖住进程退出
            self._thread.join(timeout)

    @property
    def alive(self):
        return self._thread is not None and self._thread.is_alive()

    # ---- 音频 ----

    def append(self, payload: bytes):
        with self._lock:
            if len(self._audio) + len(payload) > MAX_AUDIO_BYTES:
                raise ValueError("这段录音太长了，请分段")
            self._audio.extend(payload)

    def snapshot(self) -> bytes:
        with self._lock:
            return bytes(self._audio)

    # ---- 主循环 ----

    def _loop(self):
        started = time.monotonic()
        wait = JUDGE_INTERVAL
        while not self._stop.wait(wait):
            wait = JUDGE_INTERVAL
            if time.monotonic() - started > MAX_SECONDS:
                self.stopped_reason = "超时"
                return
            payload = self.snapshot()
            if len(payload) < MIN_AUDIO_BYTES:
                continue

            began = time.perf_counter()
            try:
                text = self._transcribe(payload)
            except Exception as error:
                self._failures += 1
                logger.warning("滚动转写失败（第 %d 次）：%s", self._failures, error)
                if self._failures >= MAX_TRANSCRIBE_FAILURES:
                    self.stopped_reason = "转写连续失败"
                    return
                continue
            self._failures = 0
            elapsed = time.perf_counter() - began
            if elapsed > SLOW_PASS_SECONDS:
                # 重转开始拖时间了（缓冲变长），放缓节奏——它全程握着识别器的锁，
                # 而候选人此刻按「停止录音」用的正是同一个模型
                wait = JUDGE_INTERVAL * 2

            if not text or text == self.transcript:
                continue
            self.transcript = text
            print(f"[监听] {self.listen_id[:6]} 已转写 {len(text)} 字"
                  f"（新增 {len(text) - len(self.judged_text)}）: …{text[-24:]}", flush=True)
            if not self._passes_gates(text):
                continue

            self.last_judged_at = time.monotonic()
            self.judged_text = text
            verdict = self._ask_judge(text)
            if not verdict:
                print(f"[监听] {self.listen_id[:6]} 判断结果：继续听", flush=True)
                continue
            if self.service.interrupt_with(self.session_id, text, verdict, self.anchor_id):
                self.interrupts += 1
                self.stopped_reason = "已打断"
                return

    def _transcribe(self, payload: bytes) -> str:
        recognizer = self.recognizer

        async def run():
            async def chunks():
                yield payload
            latest = ""
            async for transcript in recognizer.transcribe(chunks(), mime_type="audio/webm"):
                latest = transcript.text
            return latest

        return asyncio.run(run())

    def _passes_gates(self, text: str) -> bool:
        gate = GATES.get(self.pressure, DEFAULT_GATE)
        if len(text) < gate["min_chars"]:
            return False
        if len(text) - len(self.judged_text) < gate["min_new"]:
            return False
        if not any(mark in text[len(self.judged_text):] for mark in SENTENCE_END):
            return False
        if self.interrupts >= gate["max_interrupts"]:
            return False
        if time.monotonic() - self.last_judged_at < gate["cooldown"]:
            return False
        return True

    def _ask_judge(self, partial: str):
        """问面试官要不要打断。返回要说的那句话，或 None（继续听）。

        判断失败一律当作「不打断」——安全降级，宁可漏也不能误伤。
        """
        try:
            return self.judge(partial, self.pressure, self._context())
        except Exception:
            logger.exception("打断判断失败，本轮不打断")
            return None

    def _context(self) -> dict:
        """判断需要的上下文：当前问题 + 最近两轮问答。

        只给最近两轮——判断器要的是「他有没有答非所问 / 自相矛盾」，
        给多了既费 token 又容易让它去纠结更早的内容。
        """
        session = self.service.store.get(self.session_id)
        question = ""
        pairs: list[tuple[str, str]] = []
        for message in session["messages"]:
            if message["status"] != "completed":
                continue
            if message["role"] == "assistant":
                question = message["text"]
            elif question:
                pairs.append((question, message["text"]))
                question = ""
        return {"role": session.get("role") or "技术",
                "question": question or (pairs[-1][0] if pairs else ""),
                "recent": pairs[-2:]}


class ListenManager:
    """按场次管理监听会话。一个场次同时只能有一个（否则两个判断线程抢着打断）。"""

    def __init__(self, service, recognizer, judge):
        self.service = service
        self.recognizer = recognizer
        self.judge = judge
        self._sessions: dict[str, ListenSession] = {}
        self._lock = threading.RLock()

    def start(self, session_id: str, anchor_id: str, pressure: str) -> ListenSession:
        with self._lock:
            previous = self._sessions.pop(session_id, None)
            if previous is not None:
                previous.stop("被新的监听替换")
            listen = ListenSession(session_id, anchor_id, pressure,
                                   service=self.service, recognizer=self.recognizer,
                                   judge=self.judge)
            self._sessions[session_id] = listen
            listen.start()
            return listen

    def get(self, session_id: str, listen_id: str) -> ListenSession | None:
        with self._lock:
            listen = self._sessions.get(session_id)
            if listen is None or listen.listen_id != listen_id:
                return None
            return listen

    def stop(self, session_id: str, listen_id: str = "") -> bool:
        with self._lock:
            listen = self._sessions.get(session_id)
            if listen is None:
                return False
            if listen_id and listen.listen_id != listen_id:
                return False
            self._sessions.pop(session_id, None)
        listen.stop("前端结束")
        return True

    def close(self, timeout=5.0):
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for listen in sessions:
            listen.stop("服务关闭", timeout=timeout)
