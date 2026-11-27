"""Teacher UI for generating, editing, and saving safety checklists."""

import streamlit as st

from secure_database import (
    get_practice, get_safety_checklist, list_practice_files, list_practices,
    save_safety_checklist,
)
from safety_editor import (
    SafetyChecklistConflictError, add_item, cleaned_items, delete_item,
    move_item, validate_items,
)
from safety_generator import generate_safety_checklist


ORIGIN_LABELS = {
    "source": "자료 명시",
    "ai_suggested": "AI 추가 제안",
    "teacher_added": "교사 직접 추가",
}


def _clear_safety_widget_keys(state, practice_id):
    prefixes = (
        f"safety_question_{practice_id}_",
        f"safety_origin_{practice_id}_",
        f"safety_source_{practice_id}_",
    )
    for key in list(state):
        if key.startswith(prefixes):
            del state[key]


def render_safety_editor():
    st.subheader("🦺 AI 안전 체크리스트 검토 및 저장")
    notice = st.session_state.pop("safety_editor_notice", None)
    if notice:
        st.success(notice)
    st.info("AI 추가 제안은 교사가 실습 자료와 실제 환경을 기준으로 검토해 주세요.")
    practices = list_practices()
    if not practices:
        st.info("먼저 '실습 관리'에서 실습을 등록해 주세요.")
        return

    options = {item["id"]: f"#{item['id']} · {item['title']}" for item in practices}
    practice_id = st.selectbox(
        "안전 체크리스트를 관리할 실습",
        options=list(options),
        format_func=lambda value: options[value],
        key="safety_practice_select",
    )
    practice = get_practice(practice_id)
    files = list_practice_files(practice_id)
    drafts = st.session_state.setdefault("safety_drafts", {})
    versions = st.session_state.setdefault("safety_versions", {})
    saved_versions = st.session_state.setdefault("safety_saved_versions", {})
    dirty = st.session_state.setdefault("safety_dirty", {})
    pending_delete = st.session_state.setdefault("safety_pending_delete", {})
    pending_replace = st.session_state.setdefault("safety_pending_replace", {})

    try:
        saved = get_safety_checklist(practice_id)
    except Exception as exc:
        saved = None
        st.error("저장된 안전 체크리스트를 불러오지 못했습니다.")
        st.caption(f"오류 내용: {exc}")
    if practice_id not in drafts and saved:
        drafts[practice_id] = {"items": cleaned_items(saved["items"])}
        versions[practice_id] = versions.get(practice_id, 0) + 1
        saved_versions[practice_id] = saved["version"]
        dirty[practice_id] = False

    def sync_inputs():
        if practice_id not in drafts:
            return
        changed = False
        version = versions.get(practice_id, 0)
        for index, item in enumerate(drafts[practice_id]["items"]):
            for field in ("question", "origin", "source"):
                key = f"safety_{field}_{practice_id}_{version}_{index}"
                if key in st.session_state and item[field] != st.session_state[key]:
                    item[field] = st.session_state[key]
                    changed = True
        if changed:
            dirty[practice_id] = True

    def rerun_changed():
        dirty[practice_id] = True
        versions[practice_id] = versions.get(practice_id, 0) + 1
        st.rerun()

    def generate_new():
        try:
            with st.spinner("실습 자료를 검색하고 안전 체크리스트를 작성하는 중..."):
                generated = generate_safety_checklist(practice_id)
            _clear_safety_widget_keys(st.session_state, practice_id)
            drafts[practice_id] = generated
            versions[practice_id] = versions.get(practice_id, 0) + 1
            dirty[practice_id] = True
            pending_delete.pop(practice_id, None)
            pending_replace.pop(practice_id, None)
            st.session_state["safety_editor_notice"] = "AI 안전 체크리스트를 생성했습니다."
            st.rerun()
        except Exception as exc:
            st.error("AI 생성에 실패했습니다. 기존 편집 내용은 유지됩니다.")
            st.caption(f"오류 내용: {exc}")

    def load_latest():
        try:
            latest = get_safety_checklist(practice_id)
        except Exception as exc:
            st.error("저장본을 불러오지 못했습니다. 현재 편집 내용은 유지됩니다.")
            st.caption(f"오류 내용: {exc}")
            return
        if not latest:
            st.warning("저장된 안전 체크리스트가 없습니다.")
            return
        _clear_safety_widget_keys(st.session_state, practice_id)
        drafts[practice_id] = {"items": cleaned_items(latest["items"])}
        saved_versions[practice_id] = latest["version"]
        dirty[practice_id] = False
        pending_delete.pop(practice_id, None)
        pending_replace.pop(practice_id, None)
        versions[practice_id] = versions.get(practice_id, 0) + 1
        st.session_state["safety_editor_notice"] = "최신 안전 체크리스트를 불러왔습니다."
        st.rerun()

    sync_inputs()
    st.caption(f"등록된 자료: {len(files)}개 · 저장과 학생 공개는 별도로 처리됩니다.")
    if not practice["vector_store_id"]:
        st.warning("이 실습에는 RAG 자료가 없습니다.")
    else:
        generate_label = (
            "✨ AI 안전 체크리스트 다시 생성" if practice_id in drafts
            else "✨ AI 안전 체크리스트 생성"
        )
        if st.button(generate_label, use_container_width=True):
            if practice_id in drafts:
                pending_replace[practice_id] = "regenerate"
                st.rerun()
            else:
                generate_new()
        if pending_replace.get(practice_id) == "regenerate":
            st.warning("현재 편집 체크리스트가 새 AI 결과로 교체됩니다. 계속할까요?")
            yes, no = st.columns(2)
            if yes.button("AI 재생성 확인", key=f"confirm_safety_regenerate_{practice_id}"):
                generate_new()
            if no.button("취소", key=f"cancel_safety_regenerate_{practice_id}"):
                pending_replace.pop(practice_id, None)
                st.rerun()

    if practice_id not in drafts:
        return
    items = drafts[practice_id]["items"]
    version = versions.get(practice_id, 0)
    st.divider()
    metric, status = st.columns([1, 3])
    metric.metric("안전 점검 항목", f"{len(items)}개")
    status.info(
        "● 저장되지 않은 수정 있음"
        if dirty.get(practice_id, False)
        else f"✓ 저장됨 · 버전 {saved_versions.get(practice_id, '-')}"
    )
    save_col, load_col, add_col = st.columns([2, 1, 1])
    with save_col:
        if st.button("💾 안전 체크리스트 저장", type="primary", use_container_width=True):
            sync_inputs()
            errors = validate_items(items)
            if errors:
                st.warning("\n\n".join(errors))
            else:
                try:
                    normalized = cleaned_items(items)
                    new_version = save_safety_checklist(
                        practice_id, normalized, saved_versions.get(practice_id)
                    )
                    drafts[practice_id] = {"items": normalized}
                    saved_versions[practice_id] = new_version
                    dirty[practice_id] = False
                    versions[practice_id] = version + 1
                    st.session_state["safety_editor_notice"] = "안전 체크리스트를 저장했습니다."
                    st.rerun()
                except SafetyChecklistConflictError:
                    st.error("다른 교사가 먼저 저장했습니다. 현재 편집 내용은 유지됩니다.")
                except Exception as exc:
                    st.error("저장에 실패했습니다. 현재 편집 내용은 유지됩니다.")
                    st.caption(f"오류 내용: {exc}")
    with load_col:
        if st.button("저장본 불러오기", key="load_safety", use_container_width=True):
            if dirty.get(practice_id, False):
                pending_replace[practice_id] = "load"
                st.rerun()
            else:
                load_latest()
    with add_col:
        if st.button("항목 추가", use_container_width=True):
            sync_inputs()
            add_item(items)
            rerun_changed()
    if pending_replace.get(practice_id) == "load":
        st.warning("저장되지 않은 수정 내용이 사라집니다. 최신 저장본을 불러올까요?")
        yes, no = st.columns(2)
        if yes.button("불러오기 확인", key=f"confirm_safety_load_{practice_id}"):
            load_latest()
        if no.button("취소", key=f"cancel_safety_load_{practice_id}"):
            pending_replace.pop(practice_id, None)
            st.rerun()

    for index, item in enumerate(items):
        label = item["question"] or "새 안전 점검 항목"
        with st.expander(f"{index + 1}. {label}", expanded=(index == 0)):
            item["question"] = st.text_area(
                "안전 점검 질문 *", value=item["question"], height=90,
                key=f"safety_question_{practice_id}_{version}_{index}",
            )
            origins = list(ORIGIN_LABELS)
            item["origin"] = st.selectbox(
                "항목 구분 *", origins, index=origins.index(item["origin"]),
                format_func=lambda value: ORIGIN_LABELS[value],
                key=f"safety_origin_{practice_id}_{version}_{index}",
            )
            item["source"] = st.text_input(
                "자료 근거" + (" *" if item["origin"] == "source" else " (선택)"),
                value=item["source"],
                key=f"safety_source_{practice_id}_{version}_{index}",
            )
            up, down, remove = st.columns(3)
            if up.button("↑ 항목", key=f"safety_up_{practice_id}_{version}_{index}", disabled=index == 0):
                sync_inputs(); move_item(items, index, -1); rerun_changed()
            if down.button("↓ 항목", key=f"safety_down_{practice_id}_{version}_{index}", disabled=index == len(items) - 1):
                sync_inputs(); move_item(items, index, 1); rerun_changed()
            if remove.button("항목 삭제", key=f"safety_delete_{practice_id}_{version}_{index}"):
                pending_delete[practice_id] = index
                st.rerun()
            if pending_delete.get(practice_id) == index:
                st.warning("이 안전 점검 항목을 삭제할까요?")
                yes, no = st.columns(2)
                if yes.button("삭제 확인", key=f"confirm_safety_delete_{practice_id}_{version}_{index}"):
                    sync_inputs(); delete_item(items, index); pending_delete.pop(practice_id, None); rerun_changed()
                if no.button("취소", key=f"cancel_safety_delete_{practice_id}_{version}_{index}"):
                    pending_delete.pop(practice_id, None); st.rerun()
