"""从 MIT 许可的 GitHub 仓库导入中文面试题库（补充英文的 InterviewForge）。

来源（均为 MIT 许可；本文件只导入题目、不复制答案，来源标注在数据与 README 中）：
  - bcefghj/ai-agent-interview-guide      AI Agent 面试八股文（有 Q&A 结构，抽取质量高）
  - aceliuchanghong/FAQ_Of_LLM_Interview  大模型算法岗 FAQ（笔记型，用文件名/标题作题）

用法：
  python interview/import_github_bank.py          # 浅克隆到临时目录并导入
  python interview/import_github_bank.py --fresh  # 忽略已有克隆，重新拉取

产物：interview/data/zh_questions.json（统一 schema，带 source/lang 字段）
"""
import json
import pathlib
import re
import subprocess
import sys
import tempfile

DATA_DIR = pathlib.Path(__file__).parent / "data"
OUT_PATH = DATA_DIR / "zh_questions.json"
CLONE_ROOT = pathlib.Path(tempfile.gettempdir()) / "interview_banks"

SOURCES = [
    {"repo": "bcefghj/ai-agent-interview-guide", "dir": "ai-agent-guide",
     "role": "AI Agent 开发",
     # 白名单：只取技术内容；排除 学习路线图/企业招聘/简历模板/STAR面试稿 等求职向目录
     "include": ("docs/01-面试八股文", "docs/06-面试问答集")},
    {"repo": "aceliuchanghong/FAQ_Of_LLM_Interview", "dir": "llm-faq",
     "role": "大模型算法工程师",
     # 白名单：技术各篇；排除 3-面试问题记录（作者个人求职日志）/thoughts/using_files
     "include": ("1-大模型应用基础", "2-大模型优化技术", "4-分布式训练篇",
                 "5-高效微调篇", "6-强化学习基础", "pytorch")},
]

# 看起来像问题的标题（真正的疑问词才算；"原理/介绍"是主题词，不算）
QUESTION_HINTS = ("什么", "为什么", "如何", "怎么", "怎样", "区别", "是否", "哪些", "？", "?")

# 元文件/操作性文件名——不是面试主题，跳过
META_HINTS = ("readme", "使用", "入门", "基本用法", "基本操作", "编写", "代码",
              "参数解释", "说明", "进阶", "tutorial", "guide", "环境配置")


def clean_title(text: str) -> str:
    """清理标题：去 markdown 标记/编号前缀/多余空白。"""
    text = re.sub(r"[#*`>\-—]+", " ", text)
    text = re.sub(r"^\s*\d+(?:[.\-、]\d+)*[.\-、]?\s*", "", text)
    text = re.sub(r"^[0-9]+-[^\d]*", "", text)          # 形如 "1-大模型应用基础" 的目录编号
    return " ".join(text.split()).strip(" .：:")


def looks_like_question(text: str) -> bool:
    return any(hint in text for hint in QUESTION_HINTS)


def extract_from_agent_guide(markdown: str) -> list[str]:
    """从 AI Agent 八股文里抽题：优先 `### Q13：xxx`，其次 `## 3. xxx` 小节标题。"""
    questions = []
    for line in markdown.splitlines():
        match = re.match(r"^###\s*Q\d+\s*[：:]\s*(.+)", line.strip())
        if match:
            questions.append(clean_title(match.group(1)))
            continue
        match = re.match(r"^##\s+\d+(?:\.\d+)*\s*[.、]?\s*(.+)$", line.strip())
        if match:
            title = clean_title(match.group(1))
            if (title and len(title) > 4
                    and not title.startswith(("目录", "综合面试题库", "参考", "附录"))):
                questions.append(title)
    return [q for q in questions if len(q) >= 6]


def question_from_filename(name: str) -> str:
    """笔记型仓库：用文件名生成一道面试题（文件名本身就是主题）。"""
    topic = clean_title(pathlib.Path(name).stem)
    if not topic:
        return ""
    if any(hint in topic.lower() for hint in META_HINTS):   # readme/使用说明之类，不是题目
        return ""
    if looks_like_question(topic):
        return topic if topic.endswith(("？", "?")) else f"{topic}？"
    return f"请讲讲「{topic}」的关键点，并结合实际场景举例。"


def category_from_path(relative: pathlib.Path) -> str:
    """用所在目录名（去编号）作为类别。"""
    parts = [clean_title(part) for part in relative.parts[:-1]]
    parts = [p for p in parts if p and p not in ("docs", "README")]
    return parts[-1] if parts else "通用"


def build_entries(questions: list[str], role: str, category: str, source: str,
                  seen: set) -> list[dict]:
    entries = []
    for question in questions:
        key = re.sub(r"\s+", "", question)
        if len(key) < 6 or key in seen:
            continue
        seen.add(key)
        entries.append({
            "question": question,
            "keywords": [],
            "role": role,
            "category": category,
            "level": "",
            "stage": "",
            "lang": "zh",
            "source": source,
        })
    return entries


def clone(repo: str, target: pathlib.Path, fresh: bool = False) -> pathlib.Path:
    if target.is_dir() and not fresh:
        return target
    if target.exists():
        subprocess.run(["rm", "-rf", str(target)], check=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"  克隆 {repo} …")
    subprocess.run(["git", "clone", "--depth", "1", "-q",
                    f"https://github.com/{repo}.git", str(target)], check=True)
    return target


def main(fresh: bool = False) -> None:
    all_entries: list[dict] = []
    seen: set = set()
    for source in SOURCES:
        root = clone(source["repo"], CLONE_ROOT / source["dir"], fresh)
        entries = []

        def wanted(md: pathlib.Path) -> bool:
            rel = md.relative_to(root).as_posix()
            return any(rel.startswith(prefix) for prefix in source["include"])

        if source["dir"] == "ai-agent-guide":
            for md in sorted(root.glob("docs/**/*.md")):
                if not wanted(md):
                    continue
                text = md.read_text(encoding="utf-8", errors="replace")
                entries += build_entries(extract_from_agent_guide(text), source["role"],
                                         category_from_path(md.relative_to(root)),
                                         source["repo"], seen)
        else:
            for md in sorted(root.glob("**/*.md")):
                if not wanted(md) or any(part.startswith(".") for part in md.relative_to(root).parts):
                    continue
                question = question_from_filename(md.name)
                entries += build_entries([question], source["role"],
                                         category_from_path(md.relative_to(root)),
                                         source["repo"], seen)
        print(f"  {source['repo']}: {len(entries)} 题")
        all_entries += entries

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(all_entries, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"合计 {len(all_entries)} 题 → {OUT_PATH}")


if __name__ == "__main__":
    main(fresh="--fresh" in sys.argv)
