import random

import pandas as pd

import prompts

TOTAL_QUESTIONS = 5
MAX_FOLLOW_UPS = 1
MAX_UNDERSTANDING_STEP = 20  # 프롬프트 규칙 5: 한 번에 20 넘게 바꾸지 않는다

JOBS = ["개발", "디자인", "PO", "마케팅"]
MIN_EXPERIENCE_CHARS = 30

CRITERIA = ["specific", "relevance", "structure", "evidence"]
CRITERION_MAX = 5
PANEL_MAX = 10
REWRITE_BELOW = 14  # 답변 점수(0~20)가 이 값 미만일 때만 고친 답을 보여 준다


def load_questions(path="data/questions.csv"):
    return pd.read_csv(path, encoding="utf-8-sig")


def pick_questions(df, job, rng=None):
    """자기소개 1 + 공통 1 + 직무 1 + 경험 1 + 상황 1 = 5개, 이 순서."""
    rng = rng or random.Random()
    common = df[df["직군"] == "공통"]
    intro = common[common["질문"].str.contains("자기소개")]
    others = common.drop(intro.index)
    mine = df[df["직군"] == job]

    pools = [
        intro,
        others,
        mine[mine["유형"] == "직무"],
        mine[mine["유형"] == "경험"],
        mine[mine["유형"] == "상황"],
    ]
    if any(pool.empty for pool in pools):
        raise ValueError(f"질문 은행에 '{job}' 질문이 부족해요.")

    picked = [pool.iloc[rng.randrange(len(pool))] for pool in pools]
    return [{"유형": r["유형"], "질문": r["질문"], "보는 점": r["보는 점"]} for r in picked]


def can_follow_up(follow_up, used):
    """AI가 더 묻자고 해도 질문당 꼬리 질문은 MAX_FOLLOW_UPS 까지."""
    return bool(follow_up) and used < MAX_FOLLOW_UPS


def clamp_understanding(value, prev=None):
    """이해도는 0~100, 직전 값이 있으면 한 번에 ±20 안으로."""
    value = max(0, min(100, int(value)))
    if prev is not None:
        value = max(prev - MAX_UNDERSTANDING_STEP, min(prev + MAX_UNDERSTANDING_STEP, value))
    return value


def build_context(job, company, experience):
    return prompts.CONTEXT.format(company=company, job=job, experience=experience)


def build_instructions(job, company, experience, question):
    return prompts.INTERVIEWER.format(
        persona=prompts.PERSONAS[job],
        context=build_context(job, company, experience),
        experience=experience,
        question=question["질문"],
        point=question["보는 점"],
    )


def build_user_input(turns, prev_understanding):
    """이 질문에서 오간 대화(면접관/지원자)와 직전 이해도를 AI에게 넘길 입력으로."""
    lines = [f"[직전 understanding] {prev_understanding if prev_understanding is not None else '없음'}"]
    for who, text in turns:
        lines.append(f"[{who}] {text}")
    return "\n".join(lines)


def build_evaluator_instructions(job, company, experience):
    return prompts.EVALUATOR.format(context=build_context(job, company, experience))


def build_eval_input(records):
    """질문별 기록 [{no, question, turns}] 을 평가 위원회에 넘길 면접 기록 글로."""
    blocks = []
    for r in records:
        lines = [f"[답변 {r['no']}] 질문: {r['question']}"]
        lines += [f"{who}: {text}" for who, text in r["turns"][1:]]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _clamp(value, low, high):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return low


def panel_scores(panel):
    return [_clamp(p.get("score"), 0, PANEL_MAX) for p in panel]


def panel_average(panel):
    """위원 점수(0~10)를 잘라서 평균. AI 가 쓴 합계는 쓰지 않는다."""
    scores = panel_scores(panel)
    return round(sum(scores) / len(scores), 1) if scores else None


def answer_scores(answer):
    return {c: _clamp(answer.get(c), 0, CRITERION_MAX) for c in CRITERIA}


def answer_total(answer):
    """기준 4개(각 0~5)의 합 0~20."""
    return sum(answer_scores(answer).values())


def needs_rewrite(answer):
    return answer_total(answer) < REWRITE_BELOW


def build_retry_instructions(job, company, experience):
    return prompts.RETRY.format(context=build_context(job, company, experience))


def build_retry_input(question, answer):
    return f"[질문] {question}\n[지원자의 답] {answer}"


def lowest_first(answers):
    """답변 평가를 점수 낮은 순으로 (같으면 질문 번호 순)."""
    return sorted(answers, key=lambda a: (answer_total(a), a.get("no", 0)))


def compare(before, after):
    """전 → 후. 기준별 전/후/차이와 합계 차이를 코드가 계산한다."""
    b, a = answer_scores(before), answer_scores(after)
    return {
        "before": answer_total(before),
        "after": answer_total(after),
        "delta": answer_total(after) - answer_total(before),
        "rows": [{"key": c, "before": b[c], "after": a[c], "delta": a[c] - b[c]} for c in CRITERIA],
    }


def _cell(text):
    return str(text).replace("|", "/").replace("\n", " ")


def _table(header, rows):
    lines = ["| " + " | ".join(header) + " |", "|" + " --- |" * len(header)]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def build_report(profile, records, evaluation, retries, today):
    """면접리포트.md — 위원 평균 · 답변 점수 · 재도전 전후. 숫자는 모두 코드가 계산한다 (AI 문장 없음)."""
    labels = {"specific": "구체성", "relevance": "직무 연관", "structure": "구조", "evidence": "근거 · 검증"}
    full = CRITERION_MAX * len(CRITERIA)
    questions = {r["no"]: r["question"] for r in records}
    answers = {a["no"]: a for a in evaluation["answers"]}
    ordered = lowest_first(evaluation["answers"])

    average = panel_average(evaluation["panel"])
    totals = [answer_total(a) for a in ordered]

    out = [
        "# 면접 리포트",
        f"- 지원자: {profile['name']} · {profile['company']} · {profile['job']} 신입",
        f"- 작성일: {today}",
        "",
        "## 1. Before — 첫 평가",
        f"- 위원 평균: **{average} / {PANEL_MAX}**" if average is not None else "- 위원 평균: -",
        f"- 답변 점수 평균: **{round(sum(totals) / len(totals), 1)} / {full}**" if totals else "- 답변 점수 평균: -",
        "",
        _table(
            ["위원", "점수"],
            [(p["role"], s) for p, s in zip(evaluation["panel"], panel_scores(evaluation["panel"]))],
        ),
        "",
        _table(
            ["질문", "내용", *labels.values(), f"합계/{full}"],
            [
                (f"Q{a['no']}", questions.get(a["no"], ""), *answer_scores(a).values(), answer_total(a))
                for a in sorted(evaluation["answers"], key=lambda a: a["no"])
            ],
        ),
        "",
        "## 2. After — 재도전",
    ]

    compared = []
    for no in sorted(retries):
        if no in answers:
            compared.append((no, compare(answers[no], retries[no]["after"])))
    if not compared:
        out.append("아직 재도전한 질문이 없어요.")
    for no, c in compared:
        out += [
            "",
            f"### Q{no} {questions.get(no, '')}",
            f"- 점수: {c['before']} → **{c['after']}** / {full} ({c['delta']:+d})",
            "",
            _table(
                ["기준", "전", "후", "차이"],
                [(labels[r["key"]], r["before"], r["after"], f"{r['delta']:+d}") for r in c["rows"]],
            ),
        ]

    out += ["", "## 3. Bridge — 다음 연습"]
    if compared:
        mean_delta = round(sum(c["delta"] for _, c in compared) / len(compared), 1)
        out.append(f"- 재도전 {len(compared)}문제의 평균 점수 변화: **{mean_delta:+}**")
    todo = [a for a in ordered if a["no"] not in retries]
    if todo:
        out.append("- 아직 재도전하지 않은 질문 (점수 낮은 순): " + ", ".join(
            f"Q{a['no']}({answer_total(a)}/{full})" for a in todo
        ))
    low = [f"Q{a['no']}" for a in ordered if needs_rewrite(a)]
    out.append(f"- {REWRITE_BELOW}점 미만 질문: " + (", ".join(low) if low else "없음"))
    return "\n".join(out) + "\n"
