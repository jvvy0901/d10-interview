from datetime import date

import streamlit as st

import coach
from llm import LLMError, ask_json
from prompts import EVAL_SCHEMA, INTERVIEWER_SCHEMA, RETRY_SCHEMA

st.set_page_config(page_title="AI 면접 코치", page_icon="🎤")

DEFAULTS = {
    "stage": "start",  # start → interview → done
    "profile": None,
    "questions": [],
    "idx": 0,
    "used": 0,  # 지금 질문에서 한 꼬리 질문 수
    "turns": [],  # 지금 질문에서 오간 대화 [(누가, 글)]
    "messages": [],  # 화면에 그리는 전체 대화 [(role, 글)]
    "understanding": None,
    "records": [],  # 끝난 질문별 기록 [{no, question, turns}]
    "evaluation": None,
    "retries": {},  # 질문 번호 → {answer, after}
}

CRITERIA_LABELS = {
    "specific": "구체성",
    "relevance": "직무 연관",
    "structure": "구조",
    "evidence": "근거 · 검증",
}


def reset():
    for key, value in DEFAULTS.items():
        st.session_state[key] = value if not isinstance(value, (list, dict)) else type(value)()


def init_state():
    for key in DEFAULTS:
        if key not in st.session_state:
            reset()
            break


def start_interview(profile):
    questions = coach.pick_questions(coach.load_questions(), profile["job"])
    reset()
    st.session_state.stage = "interview"
    st.session_state.profile = profile
    st.session_state.questions = questions
    show_question(0)


def show_question(idx):
    question = st.session_state.questions[idx]["질문"]
    st.session_state.idx = idx
    st.session_state.used = 0
    st.session_state.turns = [("면접관", question)]
    st.session_state.messages.append(("assistant", f"**Q{idx + 1}.** {question}"))


def handle_answer(answer):
    s = st.session_state
    profile = s.profile
    turns = s.turns + [("지원자", answer)]
    try:
        result = ask_json(
            coach.build_instructions(
                profile["job"], profile["company"], profile["experience"], s.questions[s.idx]
            ),
            coach.build_user_input(turns, s.understanding),
            INTERVIEWER_SCHEMA,
            "interviewer",
        )
    except LLMError as e:
        st.error(str(e))
        return

    s.messages.append(("user", answer))
    s.understanding = coach.clamp_understanding(result["understanding"], s.understanding)

    if coach.can_follow_up(result["follow_up"], s.used):
        s.used += 1
        s.turns = turns + [("면접관", result["reply"])]
        s.messages.append(("assistant", result["reply"]))
        st.rerun()

    # 꼬리 질문을 이미 했으면 AI 가 더 묻자고 해도 넘어간다 (reply 에 꼬리 질문이 섞여 있어 쓰지 않는다)
    if result["follow_up"]:
        s.messages.append(("assistant", "충분히 들었어요. 다음 질문으로 넘어갈게요."))
    else:
        s.messages.append(("assistant", result["reply"]))
        turns = turns + [("면접관", result["reply"])]

    s.records.append({"no": s.idx + 1, "question": s.questions[s.idx]["질문"], "turns": turns})
    if s.idx + 1 < coach.TOTAL_QUESTIONS:
        show_question(s.idx + 1)
    else:
        s.stage = "done"
    st.rerun()


def start_screen():
    st.title("AI 면접 코치")
    st.caption("직군과 내 경험을 알려 주면 면접관이 5문제를 물어봐요.")
    with st.form("start"):
        name = st.text_input("이름")
        job = st.selectbox("직군", coach.JOBS, index=coach.JOBS.index("디자인"))
        company = st.text_input("회사", value="클래스로")
        experience = st.text_area(
            "내 경험", height=160,
            placeholder="수업 · 팀플 · 개인 프로젝트 등 면접에서 이야기하고 싶은 경험을 적어 주세요.",
        )
        submitted = st.form_submit_button("면접 시작")

    if not submitted:
        return
    name, company, experience = name.strip(), company.strip(), experience.strip()
    if not name or not company:
        st.warning("이름과 회사를 적어 주세요.")
    elif len(experience) < coach.MIN_EXPERIENCE_CHARS:
        st.warning(
            f"경험을 {coach.MIN_EXPERIENCE_CHARS}자 이상 적어 주세요. (지금 {len(experience)}자)"
        )
    else:
        try:
            start_interview(
                {"name": name, "job": job, "company": company, "experience": experience}
            )
        except (ValueError, OSError) as e:
            st.error(f"질문을 불러오지 못했어요. data/questions.csv 를 확인해 주세요. ({e})")
            return
        st.rerun()


def sidebar():
    s = st.session_state
    with st.sidebar:
        st.header("상태창")
        n = min(s.idx + 1, coach.TOTAL_QUESTIONS)
        st.progress(n / coach.TOTAL_QUESTIONS, text=f"질문 {n}/{coach.TOTAL_QUESTIONS}")
        st.metric("면접관이 본 이해도", "-" if s.understanding is None else f"{s.understanding}점")
        if st.button("처음부터 다시"):
            reset()
            st.rerun()


def run_evaluation():
    s = st.session_state
    p = s.profile
    try:
        s.evaluation = ask_json(
            coach.build_evaluator_instructions(p["job"], p["company"], p["experience"]),
            coach.build_eval_input(s.records),
            EVAL_SCHEMA,
            "evaluation",
        )
        s.retries = {}
    except LLMError as e:
        st.error(str(e))


def evaluation_tab():
    s = st.session_state
    if st.button("위원 4명에게 평가받기"):
        with st.spinner("위원 4명이 따로 보고 있어요..."):
            run_evaluation()

    ev = s.evaluation
    if not ev:
        return

    st.subheader("위원 4명")
    average = coach.panel_average(ev["panel"])
    st.metric("위원 평균", "-" if average is None else f"{average} / {coach.PANEL_MAX}")
    st.dataframe(
        [
            {"위원": p["role"], "반론": p["against"], "좋은 점": p["good"], "점수": score}
            for p, score in zip(ev["panel"], coach.panel_scores(ev["panel"]))
        ],
        hide_index=True,
        width="stretch",
    )
    st.markdown(f"**판단이 갈린 곳** · {ev['conflict']}")
    st.markdown(f"**모두 같은 판단** · {ev['agree']}")

    st.subheader("답변별 평가")
    questions = {r["no"]: r["question"] for r in s.records}
    for a in ev["answers"]:
        total = coach.answer_total(a)
        title = f"Q{a['no']} · {total}/{coach.CRITERION_MAX * len(coach.CRITERIA)}점"
        with st.expander(f"{title} — {questions.get(a['no'], '')}"):
            for key, score in coach.answer_scores(a).items():
                st.write(f"{CRITERIA_LABELS[key]}: {score}/{coach.CRITERION_MAX}")
            st.markdown(f"**가장 약한 점** · {a['weak']}")
            if coach.needs_rewrite(a):
                st.markdown(f"**고친 답**\n\n{a['rewrite']}")
                if a["check"]:
                    st.markdown("**사람이 확인할 것**")
                    for item in a["check"]:
                        st.markdown(f"- {item}")

    st.subheader("다음 연습에서 고칠 것")
    for item in ev["next"]:
        st.markdown(f"- {item}")


def run_retry(no, question, answer):
    s = st.session_state
    p = s.profile
    try:
        after = ask_json(
            coach.build_retry_instructions(p["job"], p["company"], p["experience"]),
            coach.build_retry_input(question, answer),
            RETRY_SCHEMA,
            "retry",
        )
    except LLMError as e:
        st.error(str(e))
        return
    s.retries[no] = {"answer": answer, "after": after}


def retry_tab():
    s = st.session_state
    if not s.evaluation:
        st.info("먼저 '📊 평가' 탭에서 평가를 받아 주세요.")
        return

    ordered = coach.lowest_first(s.evaluation["answers"])
    if not ordered:
        st.info("다시 답할 질문이 없어요.")
        return

    questions = {r["no"]: r["question"] for r in s.records}
    full = coach.CRITERION_MAX * len(coach.CRITERIA)
    by_no = {a["no"]: a for a in ordered}
    no = st.selectbox(
        "다시 답할 질문 (점수 낮은 순)",
        [a["no"] for a in ordered],
        format_func=lambda n: f"Q{n} · {coach.answer_total(by_no[n])}/{full}점 — {questions.get(n, '')}",
    )
    before = by_no[no]
    st.markdown(f"**지난 점수** · {coach.answer_total(before)}/{full}점")
    st.markdown(f"**약한 점** · {before['weak']}")
    previous = next((r for r in s.records if r["no"] == no), None)
    if previous:
        with st.expander("지난 답 보기"):
            for who, text in previous["turns"][1:]:
                st.markdown(f"**{who}** · {text}")

    answer = st.text_area("다시 답하기", key=f"retry_answer_{no}", height=160)
    if st.button("다시 채점", key=f"retry_button_{no}"):
        if not answer.strip():
            st.warning("답을 적은 뒤에 눌러 주세요.")
        else:
            with st.spinner("다시 채점하고 있어요..."):
                run_retry(no, questions.get(no, ""), answer.strip())

    retry = s.retries.get(no)
    if not retry:
        return
    result = coach.compare(before, retry["after"])
    st.metric(f"Q{no} 후 점수", f"{result['after']}/{full}", delta=result["delta"])
    st.dataframe(
        [
            {
                "기준": CRITERIA_LABELS[r["key"]],
                "전": r["before"],
                "후": r["after"],
                "차이": r["delta"],
            }
            for r in result["rows"]
        ],
        hide_index=True,
        width="stretch",
    )
    st.markdown(f"**지금 가장 약한 점** · {retry['after']['weak']}")


def chat_history():
    for role, text in st.session_state.messages:
        with st.chat_message(role):
            st.markdown(text)


def interview_screen():
    s = st.session_state
    st.title(f"{s.profile['company']} · {s.profile['job']} 면접")

    if s.stage == "done":
        chat_tab, eval_tab, retry_tab_ = st.tabs(["🎤 면접", "📊 평가", "🔁 재도전"])
        with chat_tab:
            chat_history()
            st.success("면접이 끝났어요. '📊 평가' 탭에서 위원 4명에게 평가받아 보세요.")
        with eval_tab:
            evaluation_tab()
        with retry_tab_:
            retry_tab()
        if s.evaluation:
            report = coach.build_report(
                s.profile, s.records, s.evaluation, s.retries, date.today().isoformat()
            )
            st.download_button(
                "면접리포트.md 내려받기", report, file_name="면접리포트.md", mime="text/markdown"
            )
        return

    chat_history()

    answer = st.chat_input("답변을 입력하세요")
    if answer:
        if not answer.strip():
            st.info("답변을 적어 주세요.")
        else:
            with st.chat_message("user"):
                st.markdown(answer)
            with st.spinner("면접관이 듣고 있어요..."):
                handle_answer(answer.strip())


init_state()
if st.session_state.stage == "start":
    start_screen()
else:
    sidebar()
    interview_screen()
