import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
from agents import SQLiteSession

from agent_service import ask_agent, create_agent
from database import (
    create_learning_session, get_student_practice_data, init_db, list_published_practices,
    mark_learning_safety, record_learning_question,
)
from image_service import prepare_image
from safety_agent_service import (
    create_safety_agent, evaluate_safety_answer, generate_safety_questions,
)
from statistics_service import classify_question


st.set_page_config(page_title="기술 제작 실습 AI 도우미", page_icon="🛠️", layout="centered")
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
    st.session_state.safety_complete = False
    st.session_state.practice_started = False


def leave_practice():
    st.session_state.selected_practice_id = None
    for key in (
        "student_session_id", "agent_session", "messages", "safety_session_id",
        "safety_agent_session", "safety_questions", "safety_question_index",
        "safety_messages", "safety_complete", "practice_started",
        "learning_started_at",
        "learning_session_id",
    ):
        st.session_state.pop(key, None)
    for key in list(st.session_state):
        if key.startswith(("student_safety_check_", "selected_stage_", "selected_step_")):
            del st.session_state[key]


st.title("🛠️ 기술 제작 실습 AI 도우미")
st.caption("교사가 준비한 실습을 선택하고 제작 과정의 문제를 스스로 해결해 보세요.")
st.session_state.setdefault("selected_practice_id", None)

if st.session_state.selected_practice_id is None:
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
st.subheader(practice["title"])
if practice.get("description"):
    st.write(practice["description"])

if st.button("← 실습 선택으로 돌아가기", key="return_to_practices"):
    leave_practice()
    st.rerun()

if "safety_agent_session" not in st.session_state:
    reset_safety_check(practice_id)

if not st.session_state.safety_complete:
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


def reset_selected_step():
    st.session_state.pop(step_key, None)


st.subheader("현재 제작 단계")
st.caption("실제 작업 상황이 바뀌면 언제든 단계를 다시 선택해도 돼요.")
stage_options = [
    f"{index + 1}단계. {stage['title']}"
    for index, stage in enumerate(student_data["stages"])
]
selected_stage_label = st.selectbox(
    "대단계",
    options=stage_options,
    key=stage_key,
    on_change=reset_selected_step,
)
stage_index = stage_options.index(selected_stage_label)
selected_stage = student_data["stages"][stage_index]
step_options = [
    f"{stage_index + 1}-{index + 1}. {step['title']}"
    for index, step in enumerate(selected_stage["steps"])
]
selected_step_label = st.selectbox(
    "세부 작업",
    options=step_options,
    key=step_key,
)
step_index = step_options.index(selected_step_label)
selected_step = selected_stage["steps"][step_index]
current_stage = {
    "stage_index": stage_index,
    "step_index": step_index,
    "stage_title": selected_stage["title"],
    "step_title": selected_step["title"],
    "activity": selected_step["activity"],
}
with st.container(border=True):
    st.markdown(f"**지금 하는 일: {selected_stage['title']} → {selected_step['title']}**")
    st.write(selected_step["activity"])

stage_tab, safety_tab = st.tabs(["제작 단계", "안전 체크리스트"])
with stage_tab:
    for stage_index, stage in enumerate(student_data["stages"], start=1):
        with st.expander(f"{stage_index}단계. {stage['title']}"):
            for step_index, step in enumerate(stage["steps"], start=1):
                st.markdown(f"**{stage_index}-{step_index}. {step['title']}**")
                st.write(step["activity"])
                if step.get("source"):
                    st.caption(f"자료 근거: {step['source']}")
with safety_tab:
    st.caption("체크리스트는 안전을 돕는 자료이며, 위험할 때는 작업을 멈추고 선생님께 알려 주세요.")
    for index, item in enumerate(student_data["safety_items"], start=1):
        st.markdown(f"**✅ {index}. {item['question']}**")

if "agent_session" not in st.session_state:
    reset_conversation(practice_id)
try:
    agent = create_agent(student_data["vector_store_id"])
except ValueError:
    st.error("AI 서비스가 아직 준비되지 않았어요. 선생님께 알려 주세요.")
    st.stop()

with st.sidebar:
    st.subheader("현재 실습")
    st.write(practice["title"])
    st.subheader("현재 제작 단계")
    st.write(f"{selected_stage['title']} → {selected_step['title']}")
    st.info("위험한 상황에서는 작업을 즉시 멈추고 선생님께 알려 주세요.")
    if st.button("새 대화 시작", use_container_width=True):
        reset_conversation(practice_id)
        st.rerun()

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message.get("has_image"):
            st.caption("📷 사진 첨부됨 (원본은 저장하지 않음)")

st.caption("얼굴, 이름표 등 개인정보가 보이지 않는 제작물 사진만 첨부해 주세요. 원본 사진은 저장하지 않아요.")
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
            try:
                answer = ask_agent(
                    agent, display_question, current_stage, st.session_state.agent_session,
                    image, image_mime_type,
                )
            except Exception:
                logger.exception("학생 AI 응답 처리 실패")
                st.error("답변을 불러오지 못했어요. 잠시 후 다시 시도하거나 선생님께 알려 주세요.")
            else:
                response_success = True
                st.markdown(answer)
                st.session_state.messages.append({"role": "assistant", "content": answer})
            finally:
                record_stat(record_learning_question, {
                    "event_id": event_id,
                    "practice_id": practice_id,
                    "session_id": st.session_state.learning_session_id,
                    "occurred_at": occurred_at,
                    "stage_index": current_stage["stage_index"],
                    "stage_title": current_stage["stage_title"],
                    "step_index": current_stage["step_index"],
                    "step_title": current_stage["step_title"],
                    "stage_plan_version": student_data.get("stage_plan_version"),
                    "question_type": classify_question(display_question),
                    "has_image": bool(image),
                    "ai_response_success": response_success,
                })
