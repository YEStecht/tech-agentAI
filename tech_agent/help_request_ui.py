"""Teacher UI for short-lived anonymous help-request queues."""

from datetime import datetime

import streamlit as st

from auth import AuthenticationError
from help_request_service import waiting_seconds
from secure_database import authenticated_actor_context
from secure_database import (
    complete_help_request, end_help_session, get_active_help_session,
    get_help_settings, list_pending_help_requests, list_practices,
    save_help_settings, start_help_session,
)


def _clock(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone().strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return "-"


def _duration(seconds):
    minutes, remainder = divmod(seconds, 60)
    return f"{minutes}분 {remainder}초" if minutes else f"{remainder}초"


def render_help_management(id_token):
    st.subheader("🙋 도움 요청 관리")
    practices = list_practices()
    if not practices:
        st.info("먼저 실습을 등록해 주세요.")
        return
    labels = {item["id"]: item["title"] for item in practices}
    practice_id = st.selectbox(
        "대상 실습", options=list(labels), format_func=labels.get, key="help_practice_id"
    )
    settings = get_help_settings(practice_id)
    with st.form(f"help_settings_{practice_id}"):
        enabled = st.toggle("도움 요청 기능 활성화", value=bool(settings.get("enabled")))
        class_label = st.text_input(
            "수업 학급 또는 수업 구분명", value=settings.get("class_label") or ""
        )
        group_count = st.number_input(
            "모둠 수", min_value=1, max_value=30, value=int(settings.get("group_count", 1))
        )
        max_seats = st.number_input(
            "모둠당 최대 좌석 수", min_value=1, max_value=12,
            value=int(settings.get("max_seats", 1)),
        )
        if st.form_submit_button("설정 저장", type="primary"):
            try:
                save_help_settings(practice_id, enabled, class_label, group_count, max_seats)
                st.success("도움 요청 설정을 저장했습니다.")
                st.rerun()
            except ValueError as exc:
                st.warning(str(exc))

    session = get_active_help_session(practice_id)
    if not settings.get("enabled"):
        st.info("현재 도움 요청 기능이 비활성화되어 있습니다. AI 상담은 계속 사용할 수 있습니다.")
        return
    if not session:
        if st.button("수업 시작", type="primary", use_container_width=True):
            try:
                start_help_session(practice_id)
                st.rerun()
            except ValueError as exc:
                st.warning(str(exc))
        return

    with st.container(border=True):
        st.markdown(f"**진행 중 · {session['class_label']}**")
        st.code(session["join_code"], language=None)
        st.caption(
            f"{session['group_count']}개 모둠 · 모둠당 최대 {session['max_seats']}석 · "
            f"시작 {_clock(session['started_at'])}"
        )
        if st.button("수업 종료", type="secondary", use_container_width=True):
            end_help_session(session["id"])
            st.rerun()

    @st.fragment(run_every="7s")
    def request_queue():
        try:
            with authenticated_actor_context(id_token):
                requests = list_pending_help_requests(session["id"])
                left, right = st.columns([3, 1])
                left.metric("대기 중인 도움 요청", len(requests))
                if right.button(
                    "새로고침", key=f"help_refresh_{session['id']}", use_container_width=True
                ):
                    st.rerun(scope="fragment")
                if not requests:
                    st.info("기다리는 학생이 없습니다.")
                    return
                for item in requests:
                    stage = item.get("stage_title") or "미분류"
                    if item.get("step_title"):
                        stage += f" → {item['step_title']}"
                    with st.container(border=True):
                        st.markdown(
                            f"**{item['group_number']}모둠 · {item['seat_number']}번 좌석**"
                        )
                        st.write(f"제작 단계 · {stage}")
                        st.caption(
                            f"요청 {_clock(item['requested_at'])} · "
                            f"대기 {_duration(waiting_seconds(item))}"
                        )
                        if st.button(
                            "처리 완료", key=f"complete_help_{item['id']}",
                            use_container_width=True,
                        ):
                            complete_help_request(item["id"], session["id"])
                            st.rerun(scope="fragment")
        except AuthenticationError:
            st.warning("로그인 정보가 만료되었거나 교사 권한이 변경되었습니다. 다시 로그인해 주세요.")
            if st.button("다시 로그인", key=f"help_relogin_{session['id']}"):
                st.session_state.pop("firebase_id_token", None)
                st.session_state["firebase_logout_requested"] = True
                st.rerun()

    request_queue()

