"""Teacher dashboard for anonymous learning statistics."""

from datetime import date, timedelta

import altair as alt
import streamlit as st

from secure_database import get_learning_statistics, list_practices
from statistics_service import aggregate_statistics, improvement_notes


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
        practice_id = st.selectbox(
            "실습 선택", list(labels), format_func=labels.get, key="stats_practice"
        )
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
    st.markdown("#### 핵심 지표")
    columns = st.columns(4)
    columns[0].metric("실습 참여 세션", session_count, icon="👥", border=True)
    columns[1].metric(
        "안전 점검 완료", f"{summary['safety_complete']}회",
        delta=f"완료율 {safety_rate:.1f}%", delta_color="off", icon="✅", border=True,
    )
    columns[2].metric("전체 질문", f"{summary['question_count']}건", icon="💬", border=True)
    columns[3].metric(
        "사진 첨부 질문", f"{summary['image_count']}건",
        delta=f"비율 {image_rate:.1f}%", delta_color="off", icon="📷", border=True,
    )

    charts = (
        ("제작 단계별 질문 수", summary["stages"], "#2563eb", "category"),
        ("세부 작업별 질문 수", summary["steps"], "#059669", "category"),
        ("질문 유형별 분포", summary["types"], "#7c3aed", "category"),
        ("날짜별 질문 수 추이", summary["dates"], "#0891b2", "date"),
    )
    for title, values, color, chart_kind in charts:
        st.markdown(f"#### {title}")
        if values:
            chart_data = [
                {"항목": label, "질문 수": count} for label, count in values.items()
            ]
            base = alt.Chart(alt.Data(values=chart_data))
            if chart_kind == "date":
                bars = base.mark_bar(color=color).encode(
                    x=alt.X("항목:T", title=None, axis=alt.Axis(format="%m/%d", labelAngle=0)),
                    y=alt.Y("질문 수:Q", title="질문 수", axis=alt.Axis(tickMinStep=1)),
                    tooltip=[alt.Tooltip("항목:T", title="날짜"), "질문 수:Q"],
                )
                labels = base.mark_text(dy=-8, fontWeight="bold").encode(
                    x="항목:T", y="질문 수:Q", text="질문 수:Q",
                )
                chart = (bars + labels).properties(height=260)
            else:
                bars = base.mark_bar(color=color).encode(
                    y=alt.Y(
                        "항목:N", title=None, sort=None,
                        axis=alt.Axis(labelLimit=500, labelPadding=8),
                    ),
                    x=alt.X("질문 수:Q", title="질문 수", axis=alt.Axis(tickMinStep=1)),
                    tooltip=["항목:N", "질문 수:Q"],
                )
                labels = base.mark_text(dx=8, align="left", fontWeight="bold").encode(
                    y=alt.Y("항목:N", sort=None), x="질문 수:Q", text="질문 수:Q",
                )
                chart = (bars + labels).properties(height=max(180, len(chart_data) * 38))
            st.altair_chart(chart, width="stretch")
            if title in {"제작 단계별 질문 수", "세부 작업별 질문 수"}:
                with st.expander("전체 이름과 질문 수 보기"):
                    st.dataframe(chart_data, hide_index=True, width="stretch")
        else:
            st.info("이 항목에 표시할 질문 데이터가 없습니다.")

    st.markdown("#### 📝 수업 지도 참고")
    notes = improvement_notes(summary)
    if notes:
        st.info("\n\n".join(f"• {note}" for note in notes))
