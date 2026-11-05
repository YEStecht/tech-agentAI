
import hmac
import logging
import os

import streamlit as st
from agent_service import get_setting

st.set_page_config(page_title="기술 제작 실습 · 교사 관리", page_icon="🔐", layout="wide")
logger = logging.getLogger(__name__)

# 학생용 화면과 다른 Streamlit 프로세스/포트에서 실행한다.
# 모든 교사용 기능은 아래 인증 검사를 통과한 뒤에만 렌더링한다.
password = get_setting("TEACHER_PASSWORD")
if not password:
    st.error("교사 인증 설정이 없습니다. 서버 관리자에게 문의하세요.")
    st.stop()

if not st.session_state.get("teacher_authenticated", False):
    st.title("🔐 교사 로그인")
    st.caption("교사 전용 관리 화면입니다.")
    with st.form("teacher_login"):
        entered = st.text_input("교사 비밀번호", type="password")
        submitted = st.form_submit_button("로그인", use_container_width=True)
    if submitted:
        if hmac.compare_digest(entered.encode("utf-8"), password.encode("utf-8")):
            st.session_state.teacher_authenticated = True
            st.rerun()
        else:
            st.error("비밀번호를 확인해 주세요.")
    st.stop()

with st.sidebar:
    st.header("교사 관리")
    menu = st.radio("메뉴", ["실습 관리", "AI 생성 자료 검토", "익명 통계"])
    if st.button("로그아웃", use_container_width=True):
        st.session_state.teacher_authenticated = False
        st.rerun()

st.title("📚 기술 제작 실습 · 교사 관리")
st.caption("교사 인증이 완료되었습니다. 이번 단계에서는 관리 화면의 기본 구조만 제공합니다.")

if menu == "실습 관리":
    st.subheader("📁 실습 관리")
    st.info("다음 단계에서 실습 등록, 파일 업로드·교체·삭제, 자동 RAG 색인을 구현합니다.")
elif menu == "AI 생성 자료 검토":
    st.subheader("✅ AI 생성 자료 검토")
    st.info("다음 단계에서 제작 단계 및 안전 체크리스트 초안 검토·승인 기능을 구현합니다.")
else:
    st.subheader("📊 익명 통계")
    st.info("다음 단계에서 제작 단계별·질문 유형별 통계 대시보드를 구현합니다.")
