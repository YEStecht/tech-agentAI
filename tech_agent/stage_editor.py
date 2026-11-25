"""Pure helpers for editing and validating hierarchical production stages."""

from copy import deepcopy


class StagePlanConflictError(RuntimeError):
    """Raised when a saved plan changed after it was loaded."""


def new_stage():
    return {"title": "", "steps": []}


def new_step():
    return {"title": "", "activity": "", "source": ""}


def add_stage(stages):
    stages.append(new_stage())


def delete_stage(stages, stage_index):
    del stages[stage_index]


def move_stage(stages, stage_index, offset):
    target = stage_index + offset
    if 0 <= target < len(stages):
        stages[stage_index], stages[target] = stages[target], stages[stage_index]
        return True
    return False


def add_step(stages, stage_index):
    stages[stage_index]["steps"].append(new_step())


def delete_step(stages, stage_index, step_index):
    del stages[stage_index]["steps"][step_index]


def move_step(stages, stage_index, step_index, offset):
    steps = stages[stage_index]["steps"]
    target = step_index + offset
    if 0 <= target < len(steps):
        steps[step_index], steps[target] = steps[target], steps[step_index]
        return True
    return False


def transfer_step(stages, stage_index, step_index, target_stage_index):
    if stage_index == target_stage_index:
        return False
    step = stages[stage_index]["steps"].pop(step_index)
    stages[target_stage_index]["steps"].append(step)
    return True


def counts(stages):
    return len(stages), sum(len(stage["steps"]) for stage in stages)


def cleaned_stages(stages):
    return [
        {
            "title": stage.get("title", "").strip(),
            "steps": [
                {
                    "title": step.get("title", "").strip(),
                    "activity": step.get("activity", "").strip(),
                    "source": step.get("source", "").strip(),
                }
                for step in stage.get("steps", [])
            ],
        }
        for stage in deepcopy(stages)
    ]


def validate_stages(stages):
    errors = []
    if not stages:
        errors.append("상위 제작 단계를 하나 이상 추가해 주세요.")
        return errors
    for stage_index, stage in enumerate(stages, start=1):
        if not stage.get("title", "").strip():
            errors.append(f"{stage_index}단계의 단계명을 입력해 주세요.")
        steps = stage.get("steps", [])
        if not steps:
            errors.append(f"{stage_index}단계에 세부 작업을 하나 이상 추가해 주세요.")
        for step_index, step in enumerate(steps, start=1):
            prefix = f"세부 작업 {stage_index}-{step_index}"
            if not step.get("title", "").strip():
                errors.append(f"{prefix}의 작업명을 입력해 주세요.")
            if not step.get("activity", "").strip():
                errors.append(f"{prefix}의 학생 활동을 입력해 주세요.")
    return errors
