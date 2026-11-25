"""Teacher controls for publishing and unpublishing a practice."""

import streamlit as st

from database import get_practice, get_publish_requirements, set_practice_published
from publication import PracticePublishError


REQUIREMENTS = (
    ("rag_ready", "실습 자료 및 RAG 색인", "자료를 업로드하고 색인을 완료해 주세요."),
    ("stage_plan_saved", "검토한 제작 단계 저장본", "AI 생성 자료 검토에서 제작 단계를 저장해 주세요."),
    ("safety_checklist_saved", "검토한 안전 체크리스트 저장본", "AI 생성 자료 검토에서 안전 체크리스트를 저장해 주세요."),
)


def render_publication_controls(practice_id):
    notice = st.session_state.pop("publication_notice", None)
    if notice:
        st.success(notice)
    try:
        practice = get_practice(practice_id)
        requirements = get_publish_requirements(practice_id)
    except Exception as exc:
        st.error(f"공개 조건을 확인하지 못했습니다: {exc}")
        return

    published = bool(practice["is_published"])
    st.subheader("📢 실습 공개 관리")
    st.info("현재 공개 상태: " + ("🟢 공개" if published else "🔒 비공개"))
    for key, label, help_text in REQUIREMENTS:
        if requirements[key]:
            st.success(f"✅ {label}")
        else:
            st.warning(f"❌ {label} — {help_text}")

    target = not published
    label = "실습 공개" if target else "실습 비공개 전환"
    if st.button(
        label,
        key=f"publication_toggle_{practice_id}",
        disabled=target and not requirements["can_publish"],
        use_container_width=True,
    ):
        st.session_state["publication_pending"] = (practice_id, target)
        st.rerun()

    if st.session_state.get("publication_pending") != (practice_id, target):
        return
    st.warning(
        "세 가지 공개 조건을 다시 확인하고 이 실습을 공개할까요?"
        if target else "이 실습을 학생에게 보이지 않도록 비공개로 전환할까요?"
    )
    confirm, cancel = st.columns(2)
    if confirm.button("전환 확인", key=f"confirm_publication_{practice_id}", type="primary"):
        try:
            set_practice_published(practice_id, target)
            st.session_state.pop("publication_pending", None)
            st.session_state["publication_notice"] = (
                "실습을 공개했습니다." if target else "실습을 비공개로 전환했습니다."
            )
            st.rerun()
        except PracticePublishError as exc:
            st.session_state.pop("publication_pending", None)
            st.error(str(exc))
        except Exception as exc:
            st.error(f"공개 상태 변경에 실패했습니다: {exc}")
    if cancel.button("취소", key=f"cancel_publication_{practice_id}"):
        st.session_state.pop("publication_pending", None)
        st.rerun()
