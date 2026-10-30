"""禁止 `except Exception: pass` 静默吞错。

宽泛捕获本身不一定是问题——可选依赖探测、工具调用边界、守护线程都需要它。
真正的问题是**捕获后什么都不做**：坏了没人知道，只能靠用户报「功能不工作」。

边界降级请至少记一条日志（debug 也行）。这条测试防止新代码再引入静默失败。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# except Exception / BaseException 后面直接跟一个 pass（允许中间的注释）
PATTERN = re.compile(
    r"except\s+(?:Exception|BaseException)\s*:\s*\n"
    r"(?:[ \t]*#[^\n]*\n)*"
    r"[ \t]+pass\b"
)


def test_no_silent_exception_swallowing():
    offenders = []
    targets = [ROOT / "agent.py", *sorted((ROOT / "backend").rglob("*.py"))]
    for path in targets:
        text = path.read_text(encoding="utf-8")
        for match in PATTERN.finditer(text):
            line = text[:match.start()].count("\n") + 1
            offenders.append(f"{path.relative_to(ROOT).as_posix()}:{line}")

    assert not offenders, (
        "不要用 `except Exception: pass` 静默吞错——至少记一条日志，"
        "说明降级到了什么状态。位置：" + "、".join(offenders))
