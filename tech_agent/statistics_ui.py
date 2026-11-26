"""Teacher dashboard for anonymous learning statistics."""

from datetime import date, timedelta

import streamlit as st

from database import get_learning_statistics, list_practices
from statistics_service import aggregate_statistics, improvement_notes


def render_statistics_dashboard():
    st.subheader("📊 익명 학습 통계")
    st.caption("익명 세션은 실제 학생 수가 아니며, 개인 평가가 아닌 수업 개선 참고 자료입니다.")
    try:
        practices = list_practices()
    except Exception:
        st.error("실습 목록을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.")
        return
    if not practices:
        st.info("등록된 실습이 없습니다.")
        return
    labels = {item["id"]: f"#{item['id']} · {item['title']}" for item in practices}
    practice_id = st.selectbox("실습 선택", list(labels), format_func=labels.get, key="stats_practice")
    default_start = date.today() - timedelta(days=29)
    start_col, end_col = st.columns(2)
    start_date = start_col.date_input("조회 시작일", value=default_start)
    end_date = end_col.date_input("조회 종료일", value=date.today())
    if start_date > end_date:
        st.warning("조회 시작일은 종료일보다 늦을 수 없습니다.")
        return
    try:
        summary = aggregate_statistics(get_learning_statistics(practice_id, start_date, end_date))
    except Exception:
        st.error("익명 통계를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.")
        return
    if not summary["session_count"] and not summary["question_count"]:
        st.info("선택한 기간에 수집된 익명 통계가 없습니다.")
        return

    session_count = summary["session_count"]
    safety_rate = summary["safety_complete"] / session_count * 100 if session_count else 0
    image_rate = summary["image_count"] / summary["question_count"] * 100 if summary["question_count"] else 0
    columns = st.columns(4)
    columns[0].metric("익명 참여 세션", session_count)
    columns[1].metric("안전 점검 완료", f"{summary['safety_complete']} ({safety_rate:.1f}%)")
    columns[2].metric("제작 질문", summary["question_count"])
    columns[3].metric("사진 첨부 질문", f"{summary['image_count']} ({image_rate:.1f}%)")

    charts = (
        ("제작 대단계별 질문", summary["stages"], "bar"),
        ("세부 작업별 질문", summary["steps"], "bar"),
        ("질문 유형별 분포", summary["types"], "bar"),
        ("날짜별 질문 추이", summary["dates"], "line"),
    )
    for title, values, chart_type in charts:
        st.markdown(f"#### {title}")
        if values:
            chart_data = {"항목": list(values), "질문 수": list(values.values())}
            if chart_type == "line":
                st.line_chart(chart_data, x="항목", y="질문 수")
            else:
                st.bar_chart(chart_data, x="항목", y="질문 수", horizontal=True)
        else:
            st.caption("표시할 질문 데이터가 없습니다.")

    st.markdown("#### 수업 개선 참고 정보")
    for note in improvement_notes(summary):
        st.info(note)
