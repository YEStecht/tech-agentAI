"""교사용 실습 등록·수정·RAG 자료 관리 화면."""
import hmac
import streamlit as st

from agent_service import get_setting
from database import (
    init_db, create_practice, list_practices, get_practice,
    update_practice, set_vector_store, list_practice_files,
    add_practice_file, get_practice_file, remove_practice_file,
)
from knowledge_service import (
    create_vector_store, upload_and_index, delete_indexed_file,
    SUPPORTED_EXTENSIONS, MAX_FILE_BYTES,
)

st.set_page_config(page_title="기술 제작 실습 · 교사 관리", page_icon="🔐", layout="wide")
init_db()

password = get_setting("TEACHER_PASSWORD")
if not password:
    st.error("교사 비밀번호 설정이 없습니다. 서버 관리자에게 문의하세요.")
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
        st.error("비밀번호를 확인해 주세요.")
    st.stop()

with st.sidebar:
    st.header("교사 관리")
    menu = st.radio("메뉴", ["실습 관리", "AI 생성 자료 검토", "익명 통계"])
    if st.button("로그아웃", use_container_width=True):
        st.session_state.teacher_authenticated = False
        st.rerun()

st.title("📚 기술 제작 실습 · 교사 관리")
st.caption("실습 자료는 교사가 검토·승인하기 전까지 학생에게 공개되지 않습니다.")

if menu == "실습 관리":
    st.subheader("🧩 새 실습 등록")
    with st.form("new_practice", clear_on_submit=True):
        new_title = st.text_input("실습명", placeholder="예: 목재 생활용품 만들기")
        new_description = st.text_area("실습 설명", placeholder="학습 목표와 제작 활동을 간단히 입력하세요.")
        create_submitted = st.form_submit_button("실습 등록", type="primary")
    if create_submitted:
        try:
            practice_id = create_practice(new_title, new_description)
            st.success(f"실습이 등록됐어요! (번호: {practice_id})")
        except ValueError as exc:
            st.warning(str(exc))
        except Exception:
            st.error("실습 등록 중 오류가 발생했습니다. 서버 로그를 확인해 주세요.")

    st.divider()
    st.subheader("📁 등록된 실습 관리")
    practices = list_practices()
    if not practices:
        st.info("아직 등록된 실습이 없습니다. 위에서 첫 실습을 등록해 주세요.")
        st.stop()

    options = {p["id"]: f"#{p['id']} · {p['title']}" for p in practices}
    selected_id = st.selectbox(
        "관리할 실습 선택",
        options=list(options),
        format_func=lambda pid: options[pid],
    )
    practice = get_practice(selected_id)
    if not practice:
        st.warning("실습을 찾지 못했습니다. 페이지를 새로고침해 주세요.")
        st.stop()

    st.caption("🔒 비공개" if not practice["is_published"] else "🟢 공개 상태")
    with st.expander("✏️ 실습 정보 수정"):
        with st.form(f"edit_{selected_id}"):
            edited_title = st.text_input("실습명", value=practice["title"])
            edited_description = st.text_area("실습 설명", value=practice["description"])
            if st.form_submit_button("변경사항 저장"):
                try:
                    update_practice(selected_id, edited_title, edited_description)
                    st.success("실습 정보가 수정됐습니다.")
                    st.rerun()
                except ValueError as exc:
                    st.warning(str(exc))

    st.divider()
    st.subheader("📤 실습 자료 업로드")
    st.write("PDF, DOCX, PPTX, TXT, MD 파일을 등록하면 이 실습 전용 검색 자료로 색인합니다.")
    st.caption("파일당 최대 20MB · 이미지로만 된 PDF는 검색되지 않을 수 있어요.")
    upload = st.file_uploader(
        "실습 매뉴얼이나 안전 자료 선택",
        type=[e.lstrip(".") for e in sorted(SUPPORTED_EXTENSIONS)],
        key=f"upload_{selected_id}",
    )
    if upload is not None and st.button("자료 업로드 및 RAG 색인", type="primary"):
        data = upload.getvalue()
        if not data or len(data) > MAX_FILE_BYTES:
            st.error("파일은 비어 있지 않아야 하며 20MB 이하여야 합니다.")
        else:
            store_id = practice["vector_store_id"]
            try:
                if not store_id:
                    with st.spinner("실습 전용 Vector Store 생성 중..."):
                        store_id = create_vector_store(selected_id, practice["title"])
                    set_vector_store(selected_id, store_id)
                with st.spinner("OpenAI에 업로드하고 검색 색인을 완료하는 중..."):
                    result = upload_and_index(store_id, upload.name, data)
                try:
                    add_practice_file(
                        selected_id, upload.name,
                        result["openai_file_id"],
                        result["vector_store_file_id"],
                        result["status"],
                    )
                except Exception:
                    st.error("원격 색인은 완료됐지만 DB 기록에 실패했습니다. 같은 파일을 다시 올리지 말고 관리자에게 확인해 주세요.")
                    st.code(f"Vector Store: {store_id}\nOpenAI file: {result['openai_file_id']}")
                    st.stop()
                st.success(f"'{upload.name}' 파일 색인이 완료됐어요!")
                st.rerun()
            except Exception as exc:
                st.error(f"파일 업로드 또는 색인에 실패했습니다: {exc}")
                st.info("연결 상태, API 사용 한도, 문서 형식을 확인한 후 다시 시도해 주세요.")

    st.divider()
    st.subheader("📄 등록된 파일")
    files = list_practice_files(selected_id)
    if not files:
        st.info("이 실습에 등록된 파일이 없습니다.")
    else:
        for f in files:
            with st.container(border=True):
                left, right = st.columns([4, 1])
                with left:
                    st.write(f"**{f['filename']}**")
                    st.caption(f"색인 상태: {f['status']} · 등록: {f['created_at']}")
                with right:
                    if st.button("삭제", key=f"delete_{f['id']}"):
                        st.session_state["confirm_delete_file"] = f["id"]
                if st.session_state.get("confirm_delete_file") == f["id"]:
                    st.warning("이 파일을 RAG 검색 자료에서도 삭제할까요? 되돌릴 수 없습니다.")
                    yes, no = st.columns(2)
                    with yes:
                        if st.button("삭제 확인", key=f"confirm_{f['id']}", type="primary"):
                            current_file = get_practice_file(f["id"])
                            if current_file:
                                try:
                                    delete_indexed_file(
                                        practice["vector_store_id"],
                                        current_file["openai_file_id"],
                                    )
                                    remove_practice_file(f["id"])
                                    st.session_state.pop("confirm_delete_file", None)
                                    st.success("파일이 삭제됐습니다.")
                                    st.rerun()
                                except Exception as exc:
                                    st.error(f"원격 파일 삭제 중 오류가 발생했습니다: {exc}")
                                    st.info("DB 기록은 유지했으니, 상태를 확인한 후 다시 시도해 주세요.")
                    with no:
                        if st.button("취소", key=f"cancel_{f['id']}"):
                            st.session_state.pop("confirm_delete_file", None)
                            st.rerun()

    st.caption("현재 단계에서는 실습 자체 삭제와 공개 전환을 지원하지 않습니다. 안전 체크리스트 승인 기능을 구현한 뒤 추가할 예정입니다.")


elif menu == "AI 생성 자료 검토":
    from stage_generator import generate_stage_draft

    st.subheader("🤖 AI 제작 단계 초안 생성")
    st.write(
        "등록된 실습 자료를 바탕으로 AI가 제작 단계 초안을 작성합니다. "
        "교사가 내용을 검토하고 수정할 수 있습니다."
    )

    practices = list_practices()

    if not practices:
        st.info("먼저 '실습 관리'에서 실습을 등록해 주세요.")
    else:
        options = {
            p["id"]: f"#{p['id']} · {p['title']}"
            for p in practices
        }

        selected_id = st.selectbox(
            "제작 단계를 생성할 실습",
            options=list(options),
            format_func=lambda pid: options[pid],
            key="stage_practice_select",
        )

        practice = get_practice(selected_id)
        files = list_practice_files(selected_id)

        if practice:
            st.caption(
                f"등록된 자료: {len(files)}개 · "
                f"공개 상태: {'공개' if practice['is_published'] else '비공개'}"
            )

            if not practice["vector_store_id"]:
                st.warning(
                    "이 실습에는 RAG 자료가 없습니다. "
                    "'실습 관리'에서 자료를 먼저 업로드해 주세요."
                )
            else:
                if st.button(
                    "✨ AI 제작 단계 초안 생성",
                    type="primary",
                    use_container_width=True,
                ):
                    try:
                        with st.spinner(
                            "실습 자료를 검색하고 제작 단계를 작성하는 중..."
                        ):
                            draft = generate_stage_draft(selected_id)

                        st.session_state["stage_draft"] = draft
                        st.session_state["stage_draft_practice"] = selected_id
                        st.success("제작 단계 초안이 생성됐어요!")

                    except Exception as exc:
                        st.error("AI 초안 생성에 실패했습니다.")
                        st.caption(f"오류 내용: {exc}")

            if (
                st.session_state.get("stage_draft_practice")
                == selected_id
                and st.session_state.get("stage_draft")
            ):
                st.divider()
                st.subheader("📝 제작 단계 초안 검토")

                st.info(
                    "AI가 작성한 초안입니다. "
                    "원본 실습 자료와 비교해 정확성을 확인해 주세요."
                )

                edited_draft = st.text_area(
                    "제작 단계 내용",
                    value=st.session_state["stage_draft"],
                    height=400,
                    key=f"stage_editor_{selected_id}",
                )

                st.caption(
                    "현재는 초안 생성과 화면에서의 수정만 지원합니다. "
                    "데이터베이스 저장 및 교사 승인은 다음 단계에서 추가합니다."
                )

                if st.button("수정 내용 임시 반영"):
                    st.session_state["stage_draft"] = edited_draft
                    st.success(
                        "현재 화면의 초안에 반영했습니다. "
                        "영구 저장된 것은 아닙니다."
                    )

else:
    st.subheader("📊 익명 통계")
    st.info(
        "다음 단계에서 실습 단계별·질문 유형별 "
        "익명 통계를 구현합니다."
    )
