"""Anonymous student join and teacher-help request controls."""

import time

import streamlit as st

from database import (
    cancel_help_request, create_help_request, get_help_session,
    get_student_help_request, has_active_help_session, join_help_session,
)
from help_request_service import normalize_code


def _state_key(practice_id, name):
    return f"help_{name}_{practice_id}"


def _join_allowed(practice_id):
    key = _state_key(practice_id, "join_attempts")
    now = time.time()
    attempts = [value for value in st.session_state.get(key, []) if now - value < 60]
    st.session_state[key] = attempts
    return len(attempts) < 5


def _record_join_failure(practice_id):
    st.session_state[_state_key(practice_id, "join_attempts")].append(time.time())


def render_student_help(practice_id, student_session_id, stage):
    joined_key = _state_key(practice_id, "joined")
    joined = st.session_state.get(joined_key)
    if not joined and not has_active_help_session(practice_id):
        return

    with st.container(border=True):
        st.markdown("### 🙋 선생님 도움이 필요해?")
        st.caption("다치거나 긴급한 안전 문제라면 기다리지 말고 즉시 선생님께 직접 알려.")
        if not joined:
            verified_key = _state_key(practice_id, "verified_session")
            session = st.session_state.get(verified_key)
            if not session:
                with st.form(f"verify_help_code_{practice_id}"):
                    code = st.text_input("수업 코드", max_chars=8).upper()
                    submitted = st.form_submit_button("코드 확인", use_container_width=True)
                if submitted:
                    if not _join_allowed(practice_id):
                        st.warning("참여 시도가 너무 많아. 잠시 후 다시 시도해 줘.")
                    else:
                        session = join_help_session(practice_id, normalize_code(code))
                        if not session:
                            _record_join_failure(practice_id)
                            st.warning("수업 코드를 확인해 줘. 종료된 수업에는 참여할 수 없어.")
                        else:
                            st.session_state[verified_key] = session
                            st.rerun()
                return
            with st.form(f"join_help_seat_{practice_id}"):
                group = st.selectbox("모둠 번호", range(1, int(session["group_count"]) + 1))
                seat = st.selectbox("좌석 번호", range(1, int(session["max_seats"]) + 1))
                if st.form_submit_button("수업 참여", use_container_width=True):
                    current_session = join_help_session(practice_id, session["join_code"])
                    if not current_session:
                        st.session_state.pop(verified_key, None)
                        st.warning("수업이 종료됐어. 새 수업 코드를 확인해 줘.")
                    else:
                            st.session_state[joined_key] = {
                                "session_id": session["id"], "group_number": int(group),
                                "seat_number": int(seat), "class_label": session["class_label"],
                            }
                            st.session_state.pop(verified_key, None)
                            st.rerun()
            return

        session = get_help_session(joined["session_id"])
        st.write(
            f"**{joined['class_label']} · {joined['group_number']}모둠 "
            f"{joined['seat_number']}번 좌석**"
        )
        current = get_student_help_request(joined["session_id"], student_session_id)
        if not session or not session.get("active"):
            st.info("수업이 종료되어 지금은 도움을 요청할 수 없어.")
            return

        if current and current.get("status") == "waiting":
            @st.fragment(run_every="7s")
            def waiting_status():
                latest = get_student_help_request(joined["session_id"], student_session_id)
                if not latest or latest.get("status") != "waiting":
                    st.rerun()
                st.info("선생님을 기다려 주세요.")
                refresh, cancel = st.columns(2)
                if refresh.button("상태 새로고침", key=f"help_status_refresh_{practice_id}",
                                  use_container_width=True):
                    st.rerun(scope="fragment")
                if cancel.button("요청 취소", key=f"help_cancel_{latest['id']}",
                                 use_container_width=True):
                    cancel_help_request(
                        latest["id"], joined["session_id"], student_session_id
                    )
                    st.rerun()
            waiting_status()
            return
        if current and current.get("status") == "completed":
            st.success("선생님이 도움 요청을 처리했어. 필요하면 다시 요청할 수 있어.")
        elif current and current.get("status") == "cancelled":
            st.caption("이전 도움 요청이 취소됐어. 필요하면 다시 요청할 수 있어.")
        if st.button("선생님 도움 요청", type="primary", use_container_width=True,
                     key=f"request_teacher_help_{practice_id}"):
            try:
                create_help_request(
                    joined["session_id"], practice_id, student_session_id,
                    joined["group_number"], joined["seat_number"], stage,
                )
                st.rerun()
            except ValueError as exc:
                st.warning(str(exc))

