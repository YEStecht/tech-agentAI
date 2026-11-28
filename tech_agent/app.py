import logging
import os
import random
import uuid
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
from agents import SQLiteSession

from agent_service import (
    ask_agent, create_agent, resolve_stage, validated_question_type,
)
from database import (
    create_learning_session, get_student_practice_data, init_db, list_published_practices,
    mark_learning_safety, record_learning_question,
)
from image_service import prepare_image
from safety_agent_service import (
    create_safety_agent, evaluate_safety_answer, generate_safety_questions,
)
from safety_editor import is_choice_checklist, is_correct_choice
from statistics_service import classify_question


st.set_page_config(page_title="기술 제작 실습 AI 도우미", page_icon="🛠️", layout="centered")
st.markdown(
    """
    <style>
    .stApp { background: linear-gradient(180deg, #f4fbff 0, #fffdf7 28rem); }
    .stApp button { min-height: 2.75rem; border-radius: .8rem; white-space: normal; }
    .stApp input, .stApp textarea { font-size: 1rem; }
    .stApp p, .stApp li { color: #334155; overflow-wrap: anywhere; }
    .stApp label { padding-block: .25rem; }
    .stApp label p { font-size: 1.05rem; line-height: 1.55; }
    .stApp img { max-width: 100%; height: auto; }
    @media (max-width: 640px) {
        .stApp h1 { font-size: 1.8rem; }
        .stApp h2 { font-size: 1.35rem; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
SESSION_DB = Path(__file__).resolve().parent.parent / ".local_data" / "student_conversations.db"
try:
    init_db()
except Exception:
    logger.exception("데이터 저장소 초기화 실패")


def reset_conversation(practice_id, reset_learning_session=False):
    session_id = f"practice-{practice_id}-{uuid.uuid4().hex}"
    os.makedirs(SESSION_DB.parent, exist_ok=True)
    st.session_state.student_session_id = session_id
    st.session_state.agent_session = SQLiteSession(session_id, str(SESSION_DB))
    st.session_state.messages = []
    st.session_state.pop(f"detected_stage_{practice_id}", None)
    st.session_state.pop(f"stage_conflict_{practice_id}", None)
    if reset_learning_session or "learning_session_id" not in st.session_state:
        st.session_state.learning_session_id = f"learning-{practice_id}-{uuid.uuid4().hex}"
        st.session_state.learning_started_at = datetime.now(timezone.utc).isoformat()


def reset_safety_check(practice_id):
    for key in list(st.session_state):
        if key.startswith("student_safety_check_"):
            del st.session_state[key]
    session_id = f"safety-{practice_id}-{uuid.uuid4().hex}"
    os.makedirs(SESSION_DB.parent, exist_ok=True)
    st.session_state.safety_session_id = session_id
    st.session_state.safety_agent_session = SQLiteSession(session_id, str(SESSION_DB))
    st.session_state.safety_questions = []
    st.session_state.safety_question_index = 0
    st.session_state.safety_messages = []
    st.session_state.safety_feedback = None
    st.session_state.safety_option_orders = {}
    st.session_state.safety_complete = False
    st.session_state.practice_started = False


def leave_practice():
    st.session_state.selected_practice_id = None
    for key in (
        "student_session_id", "agent_session", "messages", "safety_session_id",
        "safety_agent_session", "safety_questions", "safety_question_index",
        "safety_messages", "safety_complete", "practice_started",
        "safety_feedback",
        "safety_option_orders",
        "learning_started_at",
        "learning_session_id",
    ):
        st.session_state.pop(key, None)
    for key in list(st.session_state):
        if key.startswith(("student_safety_check_", "selected_stage_", "selected_step_",
                           "specify_stage_", "detected_stage_", "stage_conflict_")):
            del st.session_state[key]


st.title("🛠️ 기술 실습 AI 도우미")
st.session_state.setdefault("selected_practice_id", None)

if st.session_state.selected_practice_id is None:
    st.caption("오늘 진행할 실습을 선택하세요.")
    st.subheader("실습 선택")
    try:
        practices = list_published_practices()
    except Exception:
        logger.exception("공개 실습 조회 실패")
        st.error("실습 목록을 불러오지 못했어요. 잠시 후 다시 시도해 주세요.")
        st.stop()
    if not practices:
        st.info("지금 참여할 수 있는 공개 실습이 없어요. 선생님께 확인해 주세요.")
        st.stop()
    for practice in practices:
        with st.container(border=True):
            st.markdown(f"### {practice['title']}")
            st.write(practice.get("description") or "실습 설명이 아직 없습니다.")
            if st.button("실습 시작", key=f"start_practice_{practice['id']}", type="primary"):
                st.session_state.selected_practice_id = practice["id"]
                reset_conversation(practice["id"], reset_learning_session=True)
                reset_safety_check(practice["id"])
                st.rerun()
    st.stop()

practice_id = st.session_state.selected_practice_id
try:
    student_data = get_student_practice_data(practice_id)
except Exception:
    logger.exception("학생용 실습 데이터 조회 실패")
    student_data = None

if not student_data:
    st.error("이 실습은 현재 이용할 수 없어요. 비공개로 전환되었거나 자료를 불러오지 못했습니다.")
    if st.button("실습 선택으로 돌아가기", key="return_from_unavailable"):
        leave_practice()
        st.rerun()
    st.stop()

practice = student_data["practice"]
st.session_state.setdefault(
    "learning_session_id", f"learning-{practice_id}-{uuid.uuid4().hex}"
)
st.session_state.setdefault("learning_started_at", datetime.now(timezone.utc).isoformat())


def record_stat(operation, *args, **kwargs):
    try:
        operation(*args, **kwargs)
    except Exception:
        logger.exception("익명 학습 통계 기록 실패")


record_stat(
    create_learning_session, practice_id, st.session_state.learning_session_id,
    st.session_state.learning_started_at,
)
st.subheader(f"📘 {practice['title']}")
st.caption("안전 점검 · 제작 질문 · AI 자가 진단")

if st.button("← 실습 선택으로 돌아가기", key="return_to_practices"):
    leave_practice()
    st.rerun()

if "safety_agent_session" not in st.session_state:
    reset_safety_check(practice_id)

if not st.session_state.safety_complete:
    st.info("🛡️ 안전 점검 진행 중")
    if is_choice_checklist(student_data["safety_items"]):
        items = student_data["safety_items"]
        question_index = st.session_state.safety_question_index
        current = items[question_index]
        option_order = st.session_state.safety_option_orders.setdefault(
            question_index,
            random.sample(current["options"], k=len(current["options"])),
        )
        st.caption(f"안전 문항 {question_index + 1} / {len(items)}")
        st.progress(question_index / len(items))
        st.subheader(current["question"])
        st.caption(
            "이 점검의 통과가 실제 안전이나 도구 사용 허가를 의미하지 않습니다. "
            "위험한 도구는 반드시 선생님의 지도와 확인을 받아 사용하세요."
        )

        feedback = st.session_state.safety_feedback
        if feedback and feedback["question_index"] == question_index:
            if feedback["correct"]:
                st.success(f"정답입니다. {current['explanation']}")
                if st.button(
                    "제작 실습으로 이동" if question_index == len(items) - 1 else "다음 문항",
                    type="primary", use_container_width=True,
                ):
                    st.session_state.safety_question_index += 1
                    st.session_state.safety_feedback = None
                    if st.session_state.safety_question_index == len(items):
                        st.session_state.safety_complete = True
                        st.session_state.practice_started = True
                        record_stat(
                            mark_learning_safety, st.session_state.learning_session_id,
                            checklist_complete=True, ai_complete=True,
                        )
                    st.rerun()
            else:
                st.error(f"다시 선택해 보세요. {current['explanation']}")

        if not feedback or not feedback["correct"]:
            for option_index, option in enumerate(option_order):
                if st.button(
                    option,
                    key=f"safety_choice_{practice_id}_{question_index}_{option_index}",
                    use_container_width=True,
                ):
                    st.session_state.safety_feedback = {
                        "question_index": question_index,
                        "correct": is_correct_choice(current, option),
                    }
                    st.rerun()
        st.stop()

    if not st.session_state.safety_questions:
        st.subheader("1. 안전 체크리스트")
        checked_count = 0
        for index, item in enumerate(student_data["safety_items"]):
            if st.checkbox(
                item["question"],
                key=f"student_safety_check_{practice_id}_{index}",
            ):
                checked_count += 1
        total_count = len(student_data["safety_items"])
        st.write(f"확인 완료: **{checked_count} / {total_count}**")
        st.progress(checked_count / total_count if total_count else 0)
        if checked_count < total_count:
            st.info("모든 안전 항목을 직접 확인해야 AI 안전 점검으로 진행할 수 있어요.")
            st.stop()

        record_stat(
            mark_learning_safety, st.session_state.learning_session_id,
            checklist_complete=True,
        )
    else:
        with st.expander("✅ 완료한 안전 체크리스트 다시 보기"):
            for index, item in enumerate(student_data["safety_items"], start=1):
                st.markdown(f"**{index}. {item['question']}**")

    st.subheader("2. AI 대화형 안전 점검")
    try:
        safety_agent = create_safety_agent(
            student_data["vector_store_id"], student_data["safety_items"]
        )
    except ValueError:
        st.error("AI 안전 점검이 아직 준비되지 않았어요. 선생님께 알려 주세요.")
        st.stop()

    if not st.session_state.safety_questions:
        if st.button("AI 안전 점검 시작", type="primary", use_container_width=True):
            try:
                st.session_state.safety_questions = generate_safety_questions(
                    safety_agent, st.session_state.safety_agent_session
                )
                st.rerun()
            except Exception:
                logger.exception("안전 질문 생성 실패")
                st.error("안전 질문을 불러오지 못했어요. 확인한 체크 항목은 그대로 유지됩니다.")
        st.stop()

    for message in st.session_state.safety_messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
    question_index = st.session_state.safety_question_index
    current_question = st.session_state.safety_questions[question_index]
    st.info(f"안전 질문 {question_index + 1}/{len(st.session_state.safety_questions)}\n\n{current_question}")
    safety_answer = st.chat_input("안전한 행동을 설명해 보세요.", key="safety_chat_input")
    if safety_answer:
        try:
            turn = evaluate_safety_answer(
                safety_agent, current_question, safety_answer,
                st.session_state.safety_agent_session,
            )
        except Exception:
            logger.exception("안전 답변 판정 실패")
            st.error("답변을 확인하지 못했어요. 현재 진행 상태는 그대로 유지됩니다.")
        else:
            st.session_state.safety_messages.extend([
                {
                    "role": "assistant",
                    "content": f"**안전 질문 {question_index + 1}**\n\n{current_question}",
                },
                {"role": "user", "content": safety_answer},
                {
                    "role": "assistant",
                    "content": ("⚠️ " if turn.status == "danger" else "") + turn.message,
                },
            ])
            if turn.status == "passed":
                st.session_state.safety_question_index += 1
                if st.session_state.safety_question_index == len(st.session_state.safety_questions):
                    st.session_state.safety_complete = True
                    record_stat(
                        mark_learning_safety, st.session_state.learning_session_id,
                        checklist_complete=True, ai_complete=True,
                    )
            st.rerun()
    st.stop()

if not st.session_state.practice_started:
    st.success("안전 체크리스트와 AI 안전 점검을 모두 마쳤어요.")
    st.warning(
        "안전 점검 완료가 실제 안전이나 도구 사용 승인을 보장하지 않습니다. "
        "위험한 도구는 반드시 선생님의 지도와 확인을 받아 사용하세요."
    )
    if st.button("제작 실습 시작", type="primary", use_container_width=True):
        st.session_state.practice_started = True
        st.rerun()
    st.stop()

stage_key = f"selected_stage_{practice_id}"
step_key = f"selected_step_{practice_id}"
specify_stage_key = f"specify_stage_{practice_id}"
detected_stage_key = f"detected_stage_{practice_id}"
stage_conflict_key = f"stage_conflict_{practice_id}"


def reset_selected_step():
    st.session_state.pop(step_key, None)


current_stage = None
with st.sidebar:
    st.subheader("📘 현재 실습")
    st.write(practice["title"])
    st.success("✅ 안전 점검 완료")
    with st.expander("안전 체크리스트 다시 보기"):
        st.caption("위험할 때는 작업을 멈추고 선생님께 알려 주세요.")
        for index, item in enumerate(student_data["safety_items"], start=1):
            st.markdown(f"**✅ {index}. {item['question']}**")
    with st.expander("✏️ 제작 단계 직접 지정 (선택)"):
        st.caption("기본은 AI 자동 파악입니다. 필요한 경우에만 직접 지정하세요.")
        if st.checkbox("제작 단계 직접 지정 또는 수정", key=specify_stage_key):
            stage_options = [
                f"{index + 1}단계. {stage['title']}"
                for index, stage in enumerate(student_data["stages"])
            ]
            selected_stage_label = st.selectbox(
                "대단계", options=stage_options, key=stage_key, on_change=reset_selected_step,
            )
            stage_index = stage_options.index(selected_stage_label)
            selected_stage = student_data["stages"][stage_index]
            step_options = [
                f"{stage_index + 1}-{index + 1}. {step['title']}"
                for index, step in enumerate(selected_stage["steps"])
            ]
            selected_step_label = st.selectbox("세부 작업", options=step_options, key=step_key)
            step_index = step_options.index(selected_step_label)
            selected_step = selected_stage["steps"][step_index]
            current_stage = {
                "stage_index": stage_index, "step_index": step_index,
                "stage_title": selected_stage["title"], "step_title": selected_step["title"],
                "activity": selected_step["activity"],
            }
    with st.expander("📋 전체 제작 단계 참고"):
        for stage_index, stage in enumerate(student_data["stages"], start=1):
            st.markdown(f"**{stage_index}단계. {stage['title']}**")
            for step_index, step in enumerate(stage["steps"], start=1):
                st.markdown(f"{stage_index}-{step_index}. **{step['title']}**")
                st.caption(step["activity"])

detected_stage = st.session_state.get(detected_stage_key)
if current_stage:
    st.caption(f"📍 학생 지정 · {current_stage['stage_title']} → {current_stage['step_title']}")
if detected_stage:
    st.caption(f"📍 AI 판단 · {detected_stage['stage_title']} → {detected_stage['step_title']}")
elif not current_stage:
    st.caption("📍 현재 단계 · 질문 후 AI가 자동으로 파악해요")
if st.session_state.get(stage_conflict_key):
    st.warning("단계가 서로 달라요. 직접 지정한 단계와 사진 속 작업을 확인해 주세요.")

if "agent_session" not in st.session_state:
    reset_conversation(practice_id)
try:
    agent = create_agent(student_data["vector_store_id"])
except ValueError:
    st.error("AI 서비스가 아직 준비되지 않았어요. 선생님께 알려 주세요.")
    st.stop()

with st.sidebar:
    st.info("위험한 상황에서는 작업을 즉시 멈추고 선생님께 알려 주세요.")
    with st.expander("대화 관리"):
        st.caption("현재 대화 내용이 모두 지워집니다.")
        if st.button("새 대화 시작", use_container_width=True):
            reset_conversation(practice_id)
            st.rerun()

st.subheader("💬 AI에게 물어보기")
if not st.session_state.messages:
    st.info("어려운 점을 글이나 사진으로 알려 주세요. AI가 현재 단계를 함께 파악해요.")
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message.get("has_image"):
            st.caption("📷 사진 첨부됨 (원본은 저장하지 않음)")

st.caption("📷 얼굴·이름표는 빼고 제작물만 찍어 주세요. 원본 사진은 저장하지 않아요.")
submission = st.chat_input(
    "어려운 점을 쓰거나 제작물 사진을 첨부해 주세요.",
    accept_file=True,
    file_type=["jpg", "jpeg", "png"],
    max_upload_size=5,
    key=f"diagnosis_chat_input_{practice_id}",
)
if submission:
    question = submission if isinstance(submission, str) else submission.text
    files = [] if isinstance(submission, str) else submission.files
    uploaded_image = files[0] if files else None
    image = None
    image_mime_type = None
    if uploaded_image:
        try:
            image, image_mime_type = prepare_image(uploaded_image.getvalue())
        except ValueError as exc:
            st.error(str(exc))
            st.stop()
    if not question.strip() and not image:
        st.stop()
    display_question = question.strip() or "이 사진에서 확인할 점을 알고 싶어요."
    event_id = uuid.uuid4().hex
    occurred_at = datetime.now(timezone.utc).isoformat()
    st.session_state.messages.append({
        "role": "user", "content": display_question, "has_image": bool(image),
    })
    with st.chat_message("user"):
        st.markdown(display_question)
        if image:
            st.image(image, caption="첨부한 제작물 사진", width="stretch")
    with st.chat_message("assistant"):
        with st.spinner("실습 자료를 확인하고 있어요..."):
            response_success = False
            inferred_stage = None
            try:
                turn = ask_agent(
                    agent, display_question, student_data["stages"],
                    st.session_state.agent_session, image, image_mime_type,
                    selected_stage=current_stage,
                )
            except Exception:
                logger.exception("학생 AI 응답 처리 실패")
                st.error("답변을 불러오지 못했어요. 잠시 후 다시 시도하거나 선생님께 알려 주세요.")
            else:
                response_success = True
                answer = turn.answer
                stage_resolution = resolve_stage(
                    student_data["stages"], turn, selected_stage=current_stage,
                    has_image=bool(image),
                )
                inferred_stage = stage_resolution["inferred"]
                if inferred_stage:
                    st.session_state[detected_stage_key] = inferred_stage
                    st.caption(
                        f"AI 판단 단계: {inferred_stage['stage_title']} → "
                        f"{inferred_stage['step_title']}"
                    )
                else:
                    st.session_state.pop(detected_stage_key, None)
                    st.caption("AI 판단 단계: 미분류")
                st.session_state[stage_conflict_key] = stage_resolution["conflict"]
                if stage_resolution["conflict"]:
                    st.warning(
                        "학생 지정 단계와 사진에서 파악한 단계가 다릅니다. "
                        "단계를 확정하지 않고 현재 작업을 확인할게요."
                    )
                st.markdown(answer)
                st.session_state.messages.append({"role": "assistant", "content": answer})
            finally:
                statistic_stage = (
                    stage_resolution["confirmed"] if response_success else None
                )
                question_type = (
                    validated_question_type(turn, classify_question(display_question))
                    if response_success else classify_question(display_question)
                )
                record_stat(record_learning_question, {
                    "event_id": event_id,
                    "practice_id": practice_id,
                    "session_id": st.session_state.learning_session_id,
                    "occurred_at": occurred_at,
                    "stage_index": statistic_stage["stage_index"] if statistic_stage else None,
                    "stage_title": statistic_stage["stage_title"] if statistic_stage else None,
                    "step_index": statistic_stage["step_index"] if statistic_stage else None,
                    "step_title": statistic_stage["step_title"] if statistic_stage else None,
                    "stage_plan_version": student_data.get("stage_plan_version"),
                    "question_type": question_type,
                    "has_image": bool(image),
                    "ai_response_success": response_success,
                })
