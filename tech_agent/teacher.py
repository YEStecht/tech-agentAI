"""교사용 실습 등록·수정·RAG 자료 관리 화면."""
from pathlib import Path

import streamlit as st

from auth import AuthenticationError, admin_email, get_setting, verify_id_token
from firebase_auth_component import firebase_google_auth
from secure_database import (
    init_db, create_practice, list_practices, get_practice,
    update_practice, set_vector_store, list_practice_files,
    add_practice_file, get_practice_file, remove_practice_file, save_material_pages,
)
import secure_database
from teacher_permissions import (
    add_teacher, authorize, delete_teacher, list_teachers, set_teacher_active,
)
from knowledge_service import (
    create_vector_store, upload_and_index, delete_indexed_file,
    SUPPORTED_EXTENSIONS, MAX_FILE_BYTES,
)
from publication_ui import render_publication_controls
from material_service import render_pdf_pages

st.set_page_config(page_title="기술 제작 실습 · 교사 관리", page_icon="🔐", layout="wide")
st.markdown(
    """
    <style>
    .stApp { background: linear-gradient(180deg, #f4fbff 0, #fffdf7 32rem); }
    .stApp button { min-height: 2.65rem; border-radius: .75rem; white-space: normal; }
    .stApp input, .stApp textarea { font-size: 1rem; }
    .stApp p, .stApp li { color: #334155; overflow-wrap: anywhere; }
    .stApp label p { font-size: 1rem; line-height: 1.45; }
    @media (max-width: 640px) {
        .stApp h1 { font-size: 1.8rem; }
        .stApp h2 { font-size: 1.35rem; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)
firebase_config = {
    "apiKey": get_setting("FIREBASE_API_KEY"),
    "authDomain": get_setting("FIREBASE_AUTH_DOMAIN"),
    "projectId": get_setting("FIREBASE_PROJECT_ID"),
    "appId": get_setting("FIREBASE_APP_ID"),
}
if not admin_email() or not all(firebase_config.values()):
    st.error("관리자 이메일 또는 Firebase 웹 설정이 누락되어 교사 로그인을 차단했습니다.")
    st.stop()

logout_requested = st.session_state.pop("firebase_logout_requested", False)
result = firebase_google_auth(firebase_config, logout=logout_requested)
if result and result.get("token"):
    st.session_state["firebase_id_token"] = result["token"]
elif result and result.get("token") is None:
    st.session_state.pop("firebase_id_token", None)

try:
    identity = verify_id_token(st.session_state.get("firebase_id_token"))
    actor = authorize(identity)
except AuthenticationError as exc:
    st.session_state.pop("firebase_id_token", None)
    secure_database.set_actor(None)
    st.title("🔐 교사 Google 로그인")
    st.warning(str(exc))
    st.caption("승인된 교사 계정만 접근할 수 있습니다. 위의 Google 로그인 버튼을 사용하세요.")
    if result and result.get("token"):
        if st.button("로그아웃"):
            st.session_state["firebase_logout_requested"] = True
            st.rerun()
    st.stop()

secure_database.set_actor(actor)
init_db()

with st.sidebar:
    st.header("🧑‍🏫 교사 메뉴")
    menus = ["🧩 실습 관리", "🙋 도움 요청 관리", "📊 학습 통계"]
    if actor["role"] == "admin":
        menus.append("👥 교사 권한 관리")
    menu = st.radio("이동할 메뉴", menus)
    st.divider()
    st.caption(f"로그인 · {actor['email']}")
    if st.button("로그아웃", use_container_width=True):
        st.session_state.pop("firebase_id_token", None)
        secure_database.set_actor(None)
        st.session_state["firebase_logout_requested"] = True
        st.rerun()

st.title("📚 기술 제작 실습 · 교사 관리")
st.caption("실습 자료는 교사가 검토·승인하기 전까지 학생에게 공개되지 않습니다.")

if menu == "🧩 실습 관리":
    st.info("① 기본 정보 → ② 자료·RAG → ③ 제작 단계 검토 → ④ 안전 체크리스트 검토 → ⑤ 공개")
    st.subheader("① 등록된 실습 기본 정보")
    practices = list_practices()
    if not practices:
        st.info("아직 등록된 실습이 없습니다. 아래에서 첫 실습을 등록해 주세요.")

    if practices:
        options = {p["id"]: f"#{p['id']} · {p['title']}" for p in practices}
        selected_id = st.selectbox(
            "관리할 실습 선택",
            options=list(options),
            format_func=lambda pid: options[pid],
        )
    with st.expander("➕ 새 실습 등록"):
        with st.form("new_practice", clear_on_submit=True):
            new_title = st.text_input("실습명", placeholder="예: 목재 생활용품 만들기")
            new_description = st.text_area("실습 설명", placeholder="학습 목표와 제작 활동을 간단히 입력하세요.")
            create_submitted = st.form_submit_button("실습 등록", type="primary")
        if create_submitted:
            try:
                practice_id = create_practice(new_title, new_description)
                st.success(f"실습이 등록됐어요! (번호: {practice_id})")
                st.rerun()
            except ValueError as exc:
                st.warning(str(exc))
            except Exception:
                st.error("실습 등록 중 오류가 발생했습니다. 서버 로그를 확인해 주세요.")
    if not practices:
        st.stop()

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
    st.subheader("② 실습 자료 업로드 및 RAG 색인")
    st.info(
        "PDF 자료를 등록하면 AI 검색과 학생용 시각 자료 표시 기능을 함께 사용할 수 있습니다. "
        "시각 자료 활용을 위해 PDF 형식을 권장합니다."
    )
    st.write(
        "PPTX도 AI 검색용으로 등록할 수 있습니다. 학생에게 시각 자료가 필요하면 "
        "등록된 PPTX에 같은 내용의 PDF를 추가로 연결할 수 있습니다."
    )
    st.caption("파일당 최대 20MB · 이미지로만 된 PDF는 검색되지 않을 수 있어요.")
    upload = st.file_uploader(
        "실습 매뉴얼이나 안전 자료 선택",
        type=[e.lstrip(".") for e in sorted(SUPPORTED_EXTENSIONS)],
        max_upload_size=20,
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
                    file_id = add_practice_file(
                        selected_id, upload.name,
                        result["openai_file_id"],
                        result["vector_store_file_id"],
                        result["status"],
                    )
                except Exception:
                    st.error("원격 색인은 완료됐지만 DB 기록에 실패했습니다. 같은 파일을 다시 올리지 말고 관리자에게 확인해 주세요.")
                    st.code(f"Vector Store: {store_id}\nOpenAI file: {result['openai_file_id']}")
                    st.stop()
                if Path(upload.name).suffix.lower() == ".pdf":
                    try:
                        save_material_pages(file_id, render_pdf_pages(data), upload.name)
                    except Exception as exc:
                        st.warning(f"검색 색인은 완료됐지만 페이지 이미지 준비에 실패했습니다: {exc}")
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
                    if f.get("visual_status") == "completed":
                        st.caption(f"🖼️ 시각 자료 {f.get('page_count', 0)}페이지 준비됨")
                    else:
                        st.caption("시각 자료 없음 · 기존 자료는 원본 PDF 재등록이 필요합니다.")
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
                if f.get("visual_status") != "completed":
                    visual_pdf = st.file_uploader(
                        "시각 표시용 PDF (Vector Store에 중복 색인하지 않음)",
                        type=["pdf"], key=f"visual_pdf_{f['id']}", max_upload_size=20,
                        help="PPTX는 PowerPoint 없이 안정적으로 표시하기 위해 같은 자료의 PDF를 등록해 주세요.",
                    )
                    if visual_pdf is not None and st.button(
                        "페이지 이미지 준비", key=f"prepare_visual_{f['id']}"
                    ):
                        try:
                            pages = render_pdf_pages(visual_pdf.getvalue())
                            save_material_pages(f["id"], pages, visual_pdf.name)
                            st.success("시각 자료 페이지를 준비했습니다.")
                            st.rerun()
                        except Exception as exc:
                            st.warning(f"시각 자료를 준비하지 못했습니다: {exc}")

    st.divider()
    from stage_ui import render_stage_editor
    from safety_ui import render_safety_editor

    stage_tab, safety_tab = st.tabs(["③ 제작 단계 생성·검토", "④ 안전 체크리스트 생성·검토"])
    with stage_tab:
        render_stage_editor()
    with safety_tab:
        render_safety_editor()

    st.divider()
    st.subheader("⑤ 실습 공개")
    render_publication_controls(selected_id)

elif menu == "🙋 도움 요청 관리":
    from help_request_ui import render_help_management

    render_help_management(st.session_state.get("firebase_id_token"))

elif menu == "📊 학습 통계":
    from statistics_ui import render_statistics_dashboard

    render_statistics_dashboard()

else:
    st.subheader("👥 교사 권한 관리")
    st.caption("이 화면은 웹앱 접근 권한을 관리합니다. Google 계정 자체는 변경하거나 삭제하지 않습니다.")
    with st.container(border=True):
        st.markdown("**🛡️ 최고 관리자**")
        st.write(admin_email())
        st.caption("ADMIN_EMAIL 설정으로 보호됨 · 항상 활성 · 비활성화 및 삭제 불가")
    with st.form("add_teacher", clear_on_submit=True):
        teacher_email = st.text_input("교사 Google 이메일")
        school_id = st.text_input("학교 식별자", help="같은 값을 가진 승인 교사끼리 자료를 공동 관리합니다.")
        if st.form_submit_button("교사 등록", type="primary"):
            try:
                add_teacher(actor, teacher_email, school_id)
                st.success("교사를 등록했습니다.")
                st.rerun()
            except ValueError as exc:
                st.warning(str(exc))
    teachers = [
        teacher for teacher in list_teachers(actor)
        if str(teacher.get("email", "")).casefold() != admin_email().casefold()
    ]
    if not teachers:
        st.info("등록된 일반 교사가 없습니다.")
    for teacher in teachers:
        email = teacher.get("email", "")
        with st.container(border=True):
            st.markdown(f"**{email}**")
            st.write(f"학교 식별자 · {teacher.get('school_id', '-')}")
            st.caption(f"상태 · {'🟢 활성' if teacher.get('active') else '⚪ 비활성'} · 등록일 {teacher.get('created_at', '-')}")
            active_col, delete_col = st.columns(2)
            if active_col.button(
                "비활성화" if teacher.get("active") else "활성화",
                key=f"active_{teacher['id']}", use_container_width=True,
            ):
                set_teacher_active(actor, email, not teacher.get("active")); st.rerun()
            confirm_key = f"confirm_teacher_delete_{teacher['id']}"
            if delete_col.button("접근 권한 삭제", key=f"delete_{teacher['id']}", use_container_width=True):
                st.session_state[confirm_key] = True
            if st.session_state.get(confirm_key):
                st.warning("이 교사의 웹앱 접근 권한을 삭제할까요? Google 계정은 삭제되지 않습니다.")
                confirm_col, cancel_col = st.columns(2)
                if confirm_col.button("권한 삭제 확인", key=f"delete_confirm_{teacher['id']}", type="primary"):
                    delete_teacher(actor, email); st.session_state.pop(confirm_key, None); st.rerun()
                if cancel_col.button("취소", key=f"delete_cancel_{teacher['id']}"):
                    st.session_state.pop(confirm_key, None); st.rerun()
