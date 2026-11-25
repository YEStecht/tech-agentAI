"""Practice publication requirements shared by both database backends."""


class PracticePublishError(ValueError):
    pass


def publication_requirements(practice, files, stage_plan, safety_checklist):
    rag_ready = bool(
        practice
        and practice.get("vector_store_id")
        and any(
            item.get("status") == "completed"
            and item.get("openai_file_id")
            and item.get("vector_store_file_id")
            for item in files
        )
    )
    requirements = {
        "rag_ready": rag_ready,
        "stage_plan_saved": bool(stage_plan),
        "safety_checklist_saved": bool(safety_checklist),
    }
    requirements["can_publish"] = all(requirements.values())
    return requirements


def require_publishable(requirements):
    if requirements["can_publish"]:
        return
    labels = {
        "rag_ready": "실습 자료 업로드 및 RAG 색인",
        "stage_plan_saved": "검토한 제작 단계 저장",
        "safety_checklist_saved": "검토한 안전 체크리스트 저장",
    }
    missing = [labels[key] for key in labels if not requirements[key]]
    raise PracticePublishError("공개 조건이 충족되지 않았습니다: " + ", ".join(missing))
