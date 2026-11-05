import logging
import os
import uuid

import streamlit as st
from agents import SQLiteSession
from agent_service import ask_agent, create_agent

st.set_page_config(page_title="기술 제작 실습 AI 도우미", page_icon="🔨", layout="centered")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

st.title("🔨 기술 제작 실습 AI 도우미")
st.caption("교사가 제공한 실습 자료를 바탕으로 제작 과정의 문제를 스스로 해결해 보세요.")

if "student_session_id" not in st.session_state:
    st.session_state.student_session_id = uuid.uuid4().hex
if "messages" not in st.session_state:
    st.session_state.messages = []

# 1단계는 기존 SQLiteSession 방식을 유지한다.
# 학생마다 고유 세션 ID를 사용하며, 향후 익명 통계 구현 시 저장 정책을 별도로 적용한다.
if "agent_session" not in st.session_state:
    os.makedirs(".local_data", exist_ok=True)
    st.session_state.agent_session = SQLiteSession(
        st.session_state.student_session_id, ".local_data/student_conversations.db"
    )

try:
    agent = create_agent()
except ValueError:
    st.error("AI 서비스가 아직 설정되지 않았습니다. 담당 교사에게 알려주세요.")
    st.stop()

with st.sidebar:
    st.subheader("이용 안내")
    st.write("제작 중 어려운 점을 편하게 질문해 보세요.")
    st.info("위험한 상황에서는 작업을 즉시 멈추고 선생님에게 알려주세요.")
    if st.button("새 대화 시작", use_container_width=True):
        st.session_state.student_session_id = uuid.uuid4().hex
        st.session_state.agent_session = SQLiteSession(
            st.session_state.student_session_id, ".local_data/student_conversations.db"
        )
        st.session_state.messages = []
        st.rerun()

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

question = st.chat_input("제작 과정에서 어떤 점이 어려운가요?")
if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with st.spinner("실습 자료를 확인하고 있어요..."):
            try:
                answer = ask_agent(agent, question, st.session_state.agent_session)
            except Exception:
                logger.exception("학생 AI 응답 처리 실패")
                st.error("답변을 불러오지 못했어요. 잠시 후 다시 시도하거나 선생님에게 알려주세요.")
            else:
                st.markdown(answer)
                st.session_state.messages.append({"role": "assistant", "content": answer})