# 面试官评测集

用可重复的用例衡量**面试官问得好不好**，而不是只测代码有没有 bug。

`tests/` 里的 153 个测试验证的是「消息能落库」「锚点失效会放弃」这类**行为正确性**。
它们回答不了另一个问题：改了一句技能文档或 prompt，面试官是变好了还是变差了？
这个目录就是回答那个问题的。

## 怎么跑

需要 `ANTHROPIC_API_KEY`（和正常运行面试一样）。

```powershell
# 追问质量（13 条，约 40 秒）
python evals/run_eval.py

# 行为约束（5 条：一次一问、不表扬、碎片输入……）
python evals/run_eval.py --group behavior

# 评分质量（4 条，会跑完整报告，约 2 分钟）
python evals/run_eval.py --group scoring

# 全部
python evals/run_eval.py --group all

# 单条、看原始输出
python evals/run_eval.py --case attribution_vague --verbose

# 重复 3 次，看稳定度（模型有随机性）
python evals/run_eval.py --case score_excellent --group scoring --repeat 3

# 只跑确定性检查，不调裁判
python evals/run_eval.py --no-judge
```

结果写到 `evals/results/latest.md`（人看）和 `latest.json`（机器读）。
全部通过退出码为 0，有失败为 1——可以直接接进 CI。

## 判定是两层

**第一层：确定性检查**（`judge.py:deterministic`，零成本）

- `one_question`：一次问几个问句。超过 `max_questions`（默认 2）判失败；
  正好 2 个只记提示，因为「Recall@20 是多少？有没有漏？」是同一个追问的两种说法。
- `no_praise`：有没有正面反馈。只收「答得不错」这类明确评价，
  不收「不错」「很好」——实测会把转述候选人的话「你说性能提升很不错」误判成表扬。
- `forbid` / `max_chars` / `require_any`

**第二层：LLM 裁判**（`judge.py:judge`）

同一个意思有很多种问法，关键词匹配一定会误判，所以「有没有问到点子上」交给模型判断。
裁判还会检查一条全局规则：**同一轮里不能问两个相互独立的问题**。

裁判调用失败时 `pass=None`，不计入通过率——不能因为裁判挂了就判候选人赢。

## 用例怎么写

`cases/followup.yaml` 里每条长这样：

```yaml
- id: attribution_vague
  group: followup
  title: 「主要是我负责的」应追问个人边界
  role: AI 应用开发
  pressure: 标准
  messages:                       # 面试官接下来要接的那段对话
    - role: assistant
      text: 先请你做一个自我介绍……
    - role: user
      text: 我做过一个检索增强的问答系统，整个项目主要是我负责的。
  expect:
    should:                       # 裁判要判断「真的问到了」
      - 追问个人贡献边界：哪一部分是他亲手做的，其余是谁负责的
    should_not:
      - 再让他把项目介绍一遍
  checks:
    one_question: true
    no_praise: true
    forbid: ["请继续"]
```

`should` 要写成**可判断的具体行为**。「追问得更深入一点」这种没法判；
「追问基线是多少、在什么数据上测的」才能判。

跨轮行为（换项目、跨项目对比）可以用 `memory:` 预置一份笔记状态，
不必真的跑一整场多轮面试：

```yaml
  memory:
    phase: compare
    compare_points: ["A 里说主导、B 里说参与——问他差异"]
    projects:
      - {name: 项目A, done: true, turns: 3, dimensions_asked: [归属, 指标]}
      - {name: 项目B, done: true, turns: 3, dimensions_asked: [归属, 指标]}
```

`two_projects_switch` 和 `cross_project_role` 就是这么写的。

评分用例在 `cases/scoring.yaml`，断言是数值区间：

```yaml
  assertions:
    min_overall: 7.0                     # 优秀的回答至少 7 分
    max_overall: 5.5                     # 空洞的回答不能超过 5.5
    max_dimension: {技术正确性: 6}        # 说错概念要扣这一项
    evidence_contains: ["HNSW", "kappa"] # 证据必须引用原话
```

## 写用例时容易踩的坑

**1. 候选人那句话要符合它的语境。**
曾经有一个用例，面试官刚问完自我介绍，候选人回的是「这个系统已经上线了」——
面试官于是说「我请的是自我介绍，你还没有介绍自己」。那是**用例写错了**，
不是面试官错了。写完之后读一遍，确认它像一段真实的对话。

**2. 别让候选人一句话里混两个信号。**
「用了 Redis，QPS 一下上去了」同时有「技术选型」和「指标无口径」两个追问点，
面试官去追数字是合理的，用例却只期望追选型——会误判。一次只放一个信号。

**3. 埋的坑要是真的坑。**
最早写了「召回 top-k 取 20，重排对前 100 条精排」——这本身矛盾，
面试官扣分是对的。用例里的「好回答」要经得起推敲。

**4. 期望别按当前模型的行为写。**
`should` 应该来自技能文档的规则（`skills/mock-interviewer/SKILL.md`），
不是来自「模型现在这么答，所以就这么写」。否则评测只会确认现状。

## 当前基线

模型 DeepSeek，2026-10。**单次运行在 18–19/22 之间波动**，不要拿一次结果下结论。

| 组 | 典型通过 | 主要问题 |
| --- | --- | --- |
| followup | 13/15 | 爱追指标不爱追故障路径；面对术语堆砌只问项目归属，不追问术语作用 |
| behavior | 5/5 | — |
| scoring | 4/4 | 区分度良好（8.0 / 6.9 / 2.4 / 1.3）；同一份好回答 std 0.15、空洞回答 std 0.06 |

跨项目两条（`two_projects_switch` / `cross_project_role`）依赖
`backend/interview_memory.py` 的笔记注入。设 `INTERVIEW_MEMORY=off` 可以整体关掉
（不注入、不更新、不额外调用模型），那两条会退化成普通追问用例——正好用来做 A/B 对照。

### 关于随机性

同一个 prompt、同一份输入，模型的输出仍然会变：

- 追问类用例会**时而通过时而失败**（`attribution_vague` 两次运行一过一挂）
- 评分加入分档标尺后，同一份好回答的三次总分是 **7.88 / 8.12 / 8.25（std 0.15）**；
  校准前是 7.38 / 7.62 / 8.12（std 0.31）。空洞回答更稳：2.5 / 2.62 / 2.50（std 0.06）

所以：

- 调 prompt 前后对比时，**至少 `--repeat 3`**，看通过率而不是单次结果
- 判断一个改动是否有效，比较两组的**平均通过率**，不要看某一条的得失
- 新增用例先跑 3 次，确认它在当前实现下是稳定通过还是稳定失败——
  如果它自己就摇摆，这条用例测不出任何东西

## 不放进 CI

评测要真调模型，有成本也有延迟，所以 `tests/` 的 `pytest.ini` 不包含它。
想接 CI 的话建议单独一个 workflow、手动触发，或者只跑 `--no-judge`。
