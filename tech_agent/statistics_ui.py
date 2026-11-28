"""Teacher dashboard for privacy-safe learning statistics and Q&A records."""

from datetime import date, datetime, timedelta
from html import escape

import altair as alt
import streamlit as st

from secure_database import get_learning_statistics, get_question_answers, list_practices
from statistics_service import KST, QUESTION_TYPES, aggregate_statistics, filter_question_answers, improvement_notes


def _period():
    today = datetime.now(KST).date()
    preset = st.selectbox("기간", ["오늘", "최근 7일", "최근 30일", "전체", "직접 기간 설정"], index=2, key="stats_period")
    if preset == "오늘": return today, today
    if preset == "최근 7일": return today - timedelta(days=6), today
    if preset == "최근 30일": return today - timedelta(days=29), today
    if preset == "전체": return date(2000, 1, 1), today
    start_col, end_col = st.columns(2)
    return start_col.date_input("조회 시작일", value=today - timedelta(days=29)), end_col.date_input("조회 종료일", value=today)


def _horizontal_chart(title, values, color):
    st.markdown(f"#### {title}")
    if not values:
        st.info("이 항목에 표시할 질문 데이터가 없습니다.")
        return
    data = [{"항목": label, "질문 수": count} for label, count in values.items()]
    base = alt.Chart(alt.Data(values=data))
    bars = base.mark_bar(color=color).encode(
        y=alt.Y("항목:N", title=None, sort=None, axis=alt.Axis(labelLimit=700, labelPadding=8)),
        x=alt.X("질문 수:Q", title="질문 수", axis=alt.Axis(tickMinStep=1)), tooltip=["항목:N", "질문 수:Q"],
    )
    labels = base.mark_text(dx=8, align="left", fontWeight="bold").encode(y=alt.Y("항목:N", sort=None), x="질문 수:Q", text="질문 수:Q")
    st.altair_chart((bars + labels).properties(height=max(180, len(data) * 42)), width="stretch")
    with st.expander("전체 이름과 질문 수 보기"):
        st.dataframe(data, hide_index=True, width="stretch")


TYPE_COLORS = ("#2563eb", "#dc2626", "#d97706", "#7c3aed", "#059669", "#64748b")


def _keyword_cloud(keywords, question_count, keyword_question_count):
    st.markdown("#### 주요 질문 키워드")
    st.caption(
        f"전체 질문 {question_count}건 중 질문·답변 기록이 수집된 {keyword_question_count}건을 대상으로 집계합니다. "
        "기록 시작 시점에 따라 두 수는 다를 수 있습니다."
    )
    if not keywords:
        st.info("표시할 핵심 키워드가 아직 없습니다. 새 질문·답변 기록부터 집계됩니다.")
        return
    maximum = max(keywords.values())
    colors = ("#1d4ed8", "#047857", "#7c3aed", "#c2410c", "#0e7490")
    words = [
        f'<span title="{count}회" style="font-size:{16 + round(24 * count / maximum)}px;color:{colors[index % len(colors)]};font-weight:700;margin:4px 7px;line-height:1.35">{escape(keyword)}</span>'
        for index, (keyword, count) in enumerate(keywords.items())
    ]
    st.markdown('<div style="display:flex;flex-wrap:wrap;align-items:center;justify-content:center;font-family:Arial,\'Noto Sans KR\',\'Malgun Gothic\',sans-serif;padding:4px">' + "".join(words) + "</div>", unsafe_allow_html=True)
    with st.expander("키워드 빈도표 보기", expanded=False):
        st.dataframe([{"키워드": key, "빈도": value} for key, value in keywords.items()], hide_index=True, width="stretch")


def _question_type_chart(values):
    st.markdown("#### 질문 유형별 분포")
    ordered = [(kind, values.get(kind, 0), color) for kind, color in zip(QUESTION_TYPES, TYPE_COLORS) if values.get(kind)]
    if not ordered:
        st.info("질문 유형 데이터가 없습니다.")
        return
    total = sum(count for _, count, _ in ordered)
    data = [{"유형": kind, "질문 수": count, "비율": count / total} for kind, count, _ in ordered]
    chart_column, table_column = st.columns([1, 1.15])
    chart = alt.Chart(alt.Data(values=data)).mark_arc(innerRadius=62, outerRadius=105).encode(
        theta=alt.Theta("질문 수:Q"),
        color=alt.Color(
            "유형:N", legend=None,
            scale=alt.Scale(domain=[kind for kind, _, _ in ordered], range=[color for _, _, color in ordered]),
        ),
        tooltip=["유형:N", "질문 수:Q", alt.Tooltip("비율:Q", format=".1%")],
    )
    center = alt.Chart(alt.Data(values=[{"전체": f"{total}건"}])).mark_text(
        fontSize=22, fontWeight="bold", color="#334155"
    ).encode(text="전체:N")
    chart_column.altair_chart((chart + center).properties(height=280), width="stretch")
    rows = "".join(
        f'<tr><td><span style="color:{color};font-size:1.2rem">●</span> {escape(kind)}</td>'
        f'<td style="text-align:right">{count}건</td><td style="text-align:right">{count / total:.1%}</td></tr>'
        for kind, count, color in ordered
    )
    table_column.markdown(
        '<table style="width:100%;margin-top:1.5rem"><thead><tr><th>유형</th><th style="text-align:right">건수</th>'
        f'<th style="text-align:right">비율</th></tr></thead><tbody>{rows}</tbody></table>',
        unsafe_allow_html=True,
    )


def _question_answers(records):
    st.markdown("#### 학생 질문 및 AI 답변")
    if not records:
        st.info("선택한 기간에 저장된 질문·답변 기록이 없습니다. 기존 통계에는 원문이 추가되지 않습니다.")
        return
    sort_col, stage_col, type_col = st.columns(3)
    sort_order = sort_col.selectbox("정렬", ["최신순", "과거순"], key="qa_sort")
    stages = ["전체"] + sorted({item.get("stage_title") or "미분류" for item in records})
    stage = stage_col.selectbox("제작 단계", stages, key="qa_stage")
    question_type = type_col.selectbox("질문 유형", ["전체", *QUESTION_TYPES], key="qa_type")
    image = st.selectbox("사진 활용", ["전체", "사진 있음", "사진 없음"], key="qa_image")
    search = st.text_input("질문 및 답변 검색", placeholder="키워드를 입력하세요.", key="qa_search")
    filtered = filter_question_answers(records, stage, question_type, image, search)
    filtered.sort(key=lambda item: str(item.get("occurred_at", "")), reverse=sort_order == "최신순")
    page_size = 10
    page_count = max(1, (len(filtered) + page_size - 1) // page_size)
    page = st.number_input("페이지", min_value=1, max_value=page_count, value=1, step=1)
    st.caption(f"검색 결과 {len(filtered)}건 · {page}/{page_count}페이지")
    for item in filtered[(page - 1) * page_size:page * page_size]:
        occurred = item.get("occurred_at", "")
        try:
            occurred = datetime.fromisoformat(str(occurred).replace("Z", "+00:00")).astimezone(KST).strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            pass
        stage_label = item.get("stage_title") or "미분류"
        if item.get("step_title"): stage_label += f" → {item['step_title']}"
        photo = " · 📷 사진 활용" if item.get("has_image") else ""
        st.caption(f"{occurred} · {stage_label} · {item.get('question_type', '기타')}{photo}")
        st.markdown(f"**학생 질문:** {item.get('student_question', '')}")
        with st.expander("AI 답변 보기"):
            if item.get("ai_response_success"): st.write(item.get("ai_answer") or "저장된 답변이 없습니다.")
            else: st.warning("AI 답변 제공에 실패한 질문입니다.")


def render_statistics_dashboard():
    st.subheader("📊 학습 통계")
    st.caption("익명 세션은 실제 학생 수가 아니며, 개인 평가가 아닌 수업 개선 참고 자료입니다.")
    try:
        practices = list_practices()
    except Exception:
        st.error("실습 목록을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.")
        return
    if not practices:
        st.info("등록된 실습이 없습니다.")
        return
    with st.container(border=True):
        st.markdown("#### 🔎 조회 조건")
        labels = {item["id"]: f"#{item['id']} · {item['title']}" for item in practices}
        practice_id = st.selectbox("실습 선택", list(labels), format_func=labels.get, key="stats_practice")
        start_date, end_date = _period()
    if start_date > end_date:
        st.warning("조회 시작일은 종료일보다 늦을 수 없습니다.")
        return
    try:
        data = get_learning_statistics(practice_id, start_date, end_date)
        records = get_question_answers(practice_id, start_date, end_date)
        summary = aggregate_statistics({**data, "records": records})
    except Exception:
        st.error("학습 통계를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.")
        return
    dashboard_tab, records_tab = st.tabs(["통계 대시보드", "질문·답변 관리"])
    with dashboard_tab:
        if not summary["session_count"] and not summary["question_count"]:
            st.info("선택한 기간에 수집된 익명 통계가 없습니다.")
        else:
            sessions, questions = summary["session_count"], summary["question_count"]
            safety_rate = summary["safety_complete"] / sessions * 100 if sessions else 0
            image_rate = summary["image_count"] / questions * 100 if questions else 0
            st.markdown("#### 핵심 지표")
            columns = st.columns(4)
            columns[0].metric("실습 참여 세션", sessions, icon="👥", border=True)
            columns[1].metric("안전 점검 완료", f"{summary['safety_complete']}회", delta=f"완료율 {safety_rate:.1f}%", delta_color="off", icon="✅", border=True)
            columns[2].metric("전체 질문", f"{questions}건", icon="💬", border=True)
            columns[3].metric("사진 활용 질문", f"{summary['image_count']}건", delta=f"비율 {image_rate:.1f}%", delta_color="off", icon="📷", border=True)
            if questions > len(records):
                st.info("질문·답변 기록 기능 시작 이전 통계에는 상세 질문과 AI 답변이 없습니다.")
            _question_type_chart(summary["types"])
            _keyword_cloud(summary["keywords"], questions, summary["keyword_question_count"])
            _horizontal_chart("제작 단계별 질문 수", summary["stages"], "#2563eb")
            _horizontal_chart("세부 활동별 질문 수", summary["steps"], "#059669")

            st.markdown("#### 날짜별 질문 수 추이")
            if summary["dates"]:
                daily = [
                    {"날짜": key, "표시 날짜": f"{int(key[5:7])}/{int(key[8:10])}", "질문 수": value}
                    for key, value in summary["dates"].items() if key != "미분류"
                ]
                chart = alt.Chart(alt.Data(values=daily)).mark_line(point=alt.OverlayMarkDef(size=80), color="#0891b2").encode(
                    x=alt.X("표시 날짜:N", title=None, sort=[item["표시 날짜"] for item in daily], axis=alt.Axis(labelAngle=0)),
                    y=alt.Y("질문 수:Q", axis=alt.Axis(tickMinStep=1)),
                    tooltip=[alt.Tooltip("날짜:N"), "질문 수:Q"],
                )
                labels = alt.Chart(alt.Data(values=daily)).mark_text(dy=-12, fontWeight="bold").encode(
                    x=alt.X("표시 날짜:N", sort=[item["표시 날짜"] for item in daily]), y="질문 수:Q", text="질문 수:Q"
                )
                st.altair_chart((chart + labels).properties(height=260), width="stretch")
            else:
                st.info("일별 질문 데이터가 없습니다.")
            st.markdown("#### 📝 수업 지도 참고")
            notes = improvement_notes(summary)
            st.info("\n\n".join(f"• {note}" for note in notes) if notes else "요약할 질문 데이터가 없습니다.")
    with records_tab:
        st.caption("향후 교사 코멘트 기능을 이 화면에 추가할 예정입니다.")
        _question_answers(records)
