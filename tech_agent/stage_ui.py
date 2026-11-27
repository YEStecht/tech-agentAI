"""Teacher UI for editing and saving hierarchical production stages."""

import streamlit as st

from secure_database import (
    get_practice, get_stage_plan, list_practice_files, list_practices,
    save_stage_plan,
)
from stage_editor import (
    StagePlanConflictError, add_stage, add_step, cleaned_stages, counts,
    delete_stage, delete_step, move_stage, move_step, transfer_step,
    validate_stages,
)
from stage_generator import generate_stage_draft


def _clear_stage_widget_keys(state, practice_id):
    prefixes = (
        f"stage_title_{practice_id}_",
        f"step_title_{practice_id}_",
        f"activity_{practice_id}_",
        f"source_{practice_id}_",
        f"move_target_{practice_id}_",
    )
    for key in list(state):
        if key.startswith(prefixes):
            del state[key]


def render_stage_editor():
    st.subheader("🤖 AI 제작 단계 검토 및 저장")
    notice = st.session_state.pop("stage_editor_notice", None)
    if notice:
        st.success(notice)
    practices = list_practices()
    if not practices:
        st.info("먼저 '실습 관리'에서 실습을 등록해 주세요.")
        return

    options = {item["id"]: f"#{item['id']} · {item['title']}" for item in practices}
    practice_id = st.selectbox(
        "제작 단계를 생성할 실습",
        options=list(options),
        format_func=lambda value: options[value],
        key="stage_practice_select",
    )
    practice = get_practice(practice_id)
    files = list_practice_files(practice_id)
    drafts = st.session_state.setdefault("stage_drafts", {})
    versions = st.session_state.setdefault("stage_versions", {})
    saved_versions = st.session_state.setdefault("stage_saved_versions", {})
    dirty = st.session_state.setdefault("stage_dirty", {})
    pending = st.session_state.setdefault("stage_pending_delete", {})
    pending_replace = st.session_state.setdefault("stage_pending_replace", {})

    try:
        saved_plan = get_stage_plan(practice_id)
    except Exception as exc:
        saved_plan = None
        st.error("저장된 제작 단계를 불러오지 못했습니다.")
        st.caption(f"오류 내용: {exc}")

    if practice_id not in drafts and saved_plan:
        drafts[practice_id] = {"stages": cleaned_stages(saved_plan["stages"])}
        versions[practice_id] = versions.get(practice_id, 0) + 1
        saved_versions[practice_id] = saved_plan["version"]
        dirty[practice_id] = False

    def sync_inputs():
        if practice_id not in drafts:
            return
        changed = False
        version = versions.get(practice_id, 0)
        for stage_index, stage in enumerate(drafts[practice_id]["stages"]):
            stage_key = f"stage_title_{practice_id}_{version}_{stage_index}"
            if stage_key in st.session_state and stage["title"] != st.session_state[stage_key]:
                stage["title"] = st.session_state[stage_key]
                changed = True
            for step_index, step in enumerate(stage["steps"]):
                prefix = f"{practice_id}_{version}_{stage_index}_{step_index}"
                for field, name in (("title", "step_title"), ("activity", "activity"), ("source", "source")):
                    key = f"{name}_{prefix}"
                    if key in st.session_state and step[field] != st.session_state[key]:
                        step[field] = st.session_state[key]
                        changed = True
        if changed:
            dirty[practice_id] = True

    def rerun_changed():
        dirty[practice_id] = True
        versions[practice_id] = versions.get(practice_id, 0) + 1
        st.rerun()

    def generate_new_draft():
        try:
            with st.spinner("실습 자료를 검색하고 제작 단계를 작성하는 중..."):
                generated = generate_stage_draft(practice_id)
            _clear_stage_widget_keys(st.session_state, practice_id)
            drafts[practice_id] = generated
            versions[practice_id] = versions.get(practice_id, 0) + 1
            dirty[practice_id] = True
            pending.pop(practice_id, None)
            pending_replace.pop(practice_id, None)
            st.session_state["stage_editor_notice"] = "AI 제작 단계 초안을 생성했습니다."
            st.rerun()
        except Exception as exc:
            st.error("AI 초안 생성에 실패했습니다. 기존 편집 내용은 유지됩니다.")
            st.caption(f"오류 내용: {exc}")

    def load_latest_plan():
        try:
            latest = get_stage_plan(practice_id)
        except Exception as exc:
            st.error("저장본을 불러오지 못했습니다. 현재 편집 내용은 유지됩니다.")
            st.caption(f"오류 내용: {exc}")
            return
        if not latest:
            st.warning("저장된 제작 단계가 없습니다. 현재 편집 내용은 유지됩니다.")
            return
        _clear_stage_widget_keys(st.session_state, practice_id)
        drafts[practice_id] = {"stages": cleaned_stages(latest["stages"])}
        saved_versions[practice_id] = latest["version"]
        dirty[practice_id] = False
        pending.pop(practice_id, None)
        pending_replace.pop(practice_id, None)
        versions[practice_id] = versions.get(practice_id, 0) + 1
        st.session_state["stage_editor_notice"] = "최신 저장본을 불러왔습니다."
        st.rerun()

    sync_inputs()
    st.caption(
        f"등록된 자료: {len(files)}개 · "
        f"공개 상태: {'공개' if practice['is_published'] else '비공개'}"
    )

    if not practice["vector_store_id"]:
        st.warning("이 실습에는 RAG 자료가 없습니다. 실습 관리에서 자료를 먼저 업로드해 주세요.")
    else:
        if st.button("✨ AI 제작 단계 초안 생성", use_container_width=True):
            if practice_id in drafts:
                pending_replace[practice_id] = "regenerate"
                st.rerun()
            else:
                generate_new_draft()
        if pending_replace.get(practice_id) == "regenerate":
            st.warning("현재 편집 초안이 새 AI 결과로 교체됩니다. 계속할까요?")
            confirm, cancel = st.columns(2)
            if confirm.button("AI 재생성 확인", key=f"confirm_regenerate_{practice_id}"):
                generate_new_draft()
            if cancel.button("취소", key=f"cancel_regenerate_{practice_id}"):
                pending_replace.pop(practice_id, None)
                st.rerun()

    if practice_id not in drafts:
        return

    stages = drafts[practice_id]["stages"]
    version = versions.get(practice_id, 0)
    stage_count, step_count = counts(stages)
    st.divider()
    st.subheader("📝 제작 단계 편집")
    first, second, status = st.columns([1, 1, 2])
    first.metric("상위 제작 단계", f"{stage_count}개")
    second.metric("세부 작업", f"{step_count}개")
    status.info(
        "● 저장되지 않은 수정 있음"
        if dirty.get(practice_id, False)
        else f"✓ 저장됨 · 버전 {saved_versions.get(practice_id, '-')}"
    )

    save_col, load_col, add_col = st.columns([2, 1, 1])
    with save_col:
        if st.button("💾 제작 단계 저장", type="primary", use_container_width=True):
            sync_inputs()
            errors = validate_stages(stages)
            if errors:
                st.warning("\n\n".join(errors))
            else:
                try:
                    normalized = cleaned_stages(stages)
                    new_version = save_stage_plan(
                        practice_id, normalized, saved_versions.get(practice_id)
                    )
                    drafts[practice_id] = {"stages": normalized}
                    saved_versions[practice_id] = new_version
                    dirty[practice_id] = False
                    versions[practice_id] = version + 1
                    st.session_state["stage_editor_notice"] = "제작 단계를 저장했습니다."
                    st.rerun()
                except StagePlanConflictError:
                    st.error("다른 교사가 먼저 저장했습니다. 현재 편집 내용은 유지됩니다.")
                except Exception as exc:
                    st.error("저장에 실패했습니다. 현재 편집 내용은 유지됩니다.")
                    st.caption(f"오류 내용: {exc}")
    with load_col:
        if st.button("저장본 불러오기", use_container_width=True):
            if dirty.get(practice_id, False):
                pending_replace[practice_id] = "load"
                st.rerun()
            else:
                load_latest_plan()
    with add_col:
        if st.button("단계 추가", use_container_width=True):
            sync_inputs()
            add_stage(stages)
            rerun_changed()

    st.caption("자료 근거는 선택 입력입니다. 저장은 학생 공개 상태를 변경하지 않습니다.")
    if pending_replace.get(practice_id) == "load":
        st.warning("저장되지 않은 수정 내용이 사라집니다. 최신 저장본을 불러올까요?")
        confirm, cancel = st.columns(2)
        if confirm.button("불러오기 확인", key=f"confirm_load_{practice_id}"):
            load_latest_plan()
        if cancel.button("취소", key=f"cancel_load_{practice_id}"):
            pending_replace.pop(practice_id, None)
            st.rerun()
    for stage_index, stage in enumerate(stages):
        label = stage["title"] or "새 제작 단계"
        with st.expander(
            f"{stage_index + 1}단계. {label}",
            expanded=(stage_index == 0),
        ):
            stage["title"] = st.text_input(
                "상위 제작 단계명 *",
                value=stage["title"],
                key=f"stage_title_{practice_id}_{version}_{stage_index}",
            )
            up, down, add_col, remove_col = st.columns(4)
            if up.button("↑ 단계", key=f"stage_up_{practice_id}_{version}_{stage_index}", disabled=stage_index == 0):
                sync_inputs(); move_stage(stages, stage_index, -1); rerun_changed()
            if down.button("↓ 단계", key=f"stage_down_{practice_id}_{version}_{stage_index}", disabled=stage_index == len(stages) - 1):
                sync_inputs(); move_stage(stages, stage_index, 1); rerun_changed()
            if add_col.button("작업 추가", key=f"add_step_{practice_id}_{version}_{stage_index}"):
                sync_inputs(); add_step(stages, stage_index); rerun_changed()
            if remove_col.button("단계 삭제", key=f"delete_stage_{practice_id}_{version}_{stage_index}"):
                pending[practice_id] = ("stage", stage_index, None); st.rerun()

            if pending.get(practice_id) == ("stage", stage_index, None):
                st.warning("이 단계와 포함된 모든 세부 작업을 삭제할까요?")
                yes, no = st.columns(2)
                if yes.button("삭제 확인", key=f"confirm_stage_{practice_id}_{version}_{stage_index}"):
                    sync_inputs(); delete_stage(stages, stage_index); pending.pop(practice_id, None); rerun_changed()
                if no.button("취소", key=f"cancel_stage_{practice_id}_{version}_{stage_index}"):
                    pending.pop(practice_id, None); st.rerun()

            for step_index, step in enumerate(stage["steps"]):
                _render_step(
                    stages, practice_id, version, stage_index, step_index,
                    sync_inputs, rerun_changed, pending,
                )


def _render_step(stages, practice_id, version, stage_index, step_index, sync, rerun, pending):
    step = stages[stage_index]["steps"][step_index]
    prefix = f"{practice_id}_{version}_{stage_index}_{step_index}"
    with st.container(border=True):
        st.markdown(f"**세부 작업 {stage_index + 1}-{step_index + 1}**")
        step["title"] = st.text_input("작업명 *", value=step["title"], key=f"step_title_{prefix}")
        step["activity"] = st.text_area(
            "학생 활동 *", value=step["activity"], height=100, key=f"activity_{prefix}"
        )
        step["source"] = st.text_input(
            "자료 근거 (선택)", value=step["source"], key=f"source_{prefix}"
        )
        up, down, remove = st.columns(3)
        if up.button("↑ 작업", key=f"step_up_{prefix}", disabled=step_index == 0):
            sync(); move_step(stages, stage_index, step_index, -1); rerun()
        if down.button("↓ 작업", key=f"step_down_{prefix}", disabled=step_index == len(stages[stage_index]["steps"]) - 1):
            sync(); move_step(stages, stage_index, step_index, 1); rerun()
        if remove.button("작업 삭제", key=f"delete_step_{prefix}"):
            pending[practice_id] = ("step", stage_index, step_index); st.rerun()

        if len(stages) > 1:
            target_col, move_col = st.columns([3, 1])
            targets = [index for index in range(len(stages)) if index != stage_index]
            target = target_col.selectbox(
                "다른 단계로 이동",
                targets,
                format_func=lambda index: f"{index + 1}단계. {stages[index]['title'] or '새 제작 단계'}",
                key=f"move_target_{prefix}",
            )
            if move_col.button("이동", key=f"move_step_stage_{prefix}"):
                sync(); transfer_step(stages, stage_index, step_index, target); rerun()

        if pending.get(practice_id) == ("step", stage_index, step_index):
            st.warning("이 세부 작업을 삭제할까요?")
            yes, no = st.columns(2)
            if yes.button("삭제 확인", key=f"confirm_step_{prefix}"):
                sync(); delete_step(stages, stage_index, step_index); pending.pop(practice_id, None); rerun()
            if no.button("취소", key=f"cancel_step_{prefix}"):
                pending.pop(practice_id, None); st.rerun()
