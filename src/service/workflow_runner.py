"""Autonomous single-band GALFIT workflow used by the HTTP task service."""

import asyncio
import datetime
import os
import re
import subprocess
from typing import Any, Awaitable, Callable

from src.tools.fourier_mode_analysis import fourier_mode_analysis
from src.tools.residual_analysis import component_analysis
from src.tools.run_galfit import run_galfit


WorkflowEventCallback = Callable[[dict[str, Any]], Awaitable[None]]
_TERMINAL_PATTERNS = (
    r"锁定.*终态", r"终态模型", r"拟合结束", r"无需进一步", r"不再增加",
    r"无需.*增加", r"接受.*最终", r"accept.*terminal", r"terminal model",
)
_VERDICT_FENCE_RE = re.compile(
    r"```verdict\s*\n?\s*(PASS|FAIL)\s*\n?\s*```", re.IGNORECASE
)
_VERDICT_LINE_RE = re.compile(r"^\s*VERDICT\s*[:：]\s*(PASS|FAIL)\s*$", re.IGNORECASE | re.MULTILINE)


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def _is_terminal_component_decision(analysis: str) -> bool:
    return any(re.search(pattern, analysis, re.IGNORECASE) for pattern in _TERMINAL_PATTERNS)


def _expand_command(command: list[str], replacements: dict[str, str]) -> list[str]:
    return [replacements.get(part, part) for part in command]


async def _run_command(command: list[str], replacements: dict[str, str], timeout: int) -> str:
    expanded = _expand_command(command, replacements)

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(expanded, capture_output=True, text=True, check=False, timeout=timeout)

    proc = await asyncio.to_thread(run)
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(f"command failed with code {proc.returncode}: {output}")
    return output


def _next_feedme(output: str, fallback: str) -> str:
    match = re.search(r"NEXT_FEEDME\s*=\s*(\S+)", output)
    return os.path.abspath(match.group(1)) if match else fallback


def _write_report(report_file: str, task_id: str, initial_feedme: str,
                  final_result: dict[str, Any], rounds: list[dict[str, Any]],
                  audit_output: str, fourier_result: dict[str, Any] | None) -> None:
    archive_dir = os.path.dirname(final_result.get("round_status_file", ""))
    lines = [
        f"# Workflow Report: {task_id}", "", f"- Initial feedme: `{initial_feedme}`",
        f"- Best archive: `{os.path.basename(archive_dir)}`", "- Best-round audit: `PASS`", "",
        "## Rounds", "",
    ]
    for item in rounds:
        result = item["result"]
        lines.extend([
            f"### Round {item['round_number']}", "", f"- Feedme: `{item['feedme']}`",
            f"- Status: `{result.get('status', '')}`", f"- Summary: `{result.get('summary_file', '')}`",
            f"- Component analysis: `{item.get('component_analysis', {}).get('analysis_file', '')}`", "",
        ])
    lines.extend(["## Best-round audit", "", "```text", audit_output, "```", ""])
    if fourier_result:
        lines.extend(["## Fourier", "", f"- Status: `{fourier_result.get('status', '')}`",
                      f"- Analysis: `{fourier_result.get('analysis_file', '')}`", ""])
    lines.extend(["## Machine Output", "", "```json",
                  f'{{"best_turn":"{os.path.basename(archive_dir)}"}}', "```", ""])
    with open(report_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


async def run_galfit_workflow(
    *, task_id: str, feedme: str, options: list[str] | None,
    emit_event: WorkflowEventCallback, max_rounds: int = 8,
    agent_command: list[str] | None = None, agent_timeout: int = 600,
    verifier_command: list[str] | None = None, verifier_timeout: int = 600,
) -> dict[str, Any]:
    """Alternate fitting and component analysis; require an external six-dimensional audit before lock."""
    feedme = os.path.abspath(feedme)
    galaxy_dir = os.path.dirname(feedme)
    working_note = os.path.join(galaxy_dir, "working_note.md")
    report_file = os.path.join(galaxy_dir, f"workflow_report_{task_id}.md")
    current_feedme = feedme
    rounds: list[dict[str, Any]] = []
    if not os.path.exists(working_note):
        with open(working_note, "w", encoding="utf-8") as f:
            f.write(f"# Working Note: {os.path.basename(galaxy_dir)}\n\n")

    await emit_event({"type": "workflow_started", "task_id": task_id, "feedme": feedme,
                      "max_rounds": max_rounds, "created_at": _utc_now_iso()})
    candidate: dict[str, Any] | None = None
    for round_number in range(1, max_rounds + 1):
        await emit_event({"type": "galfit_round_started", "round_number": round_number,
                          "feedme": current_feedme, "created_at": _utc_now_iso()})
        result = await run_galfit(current_feedme, options or ["-o"])
        record = {"round_number": round_number, "feedme": current_feedme, "result": result}
        rounds.append(record)
        await emit_event({"type": "galfit_round_finished", "round_number": round_number,
                          "status": result.get("status"), "result": result, "created_at": _utc_now_iso()})
        if result.get("status") != "success":
            return {"status": "failure", "message": "GALFIT round failed", "rounds": rounds, "result": result}

        image_file, summary_file = result.get("image_file"), result.get("summary_file")
        if not image_file or not summary_file:
            return {"status": "failure", "message": "GALFIT result is missing image or summary", "rounds": rounds}
        analysis = await asyncio.to_thread(
            component_analysis, image_file, summary_file, working_note,
            "Scientific objective: autonomous single-band GALFIT workflow. Decide whether the model is terminal or specify the next feedme modification.",
        )
        record["component_analysis"] = analysis
        await emit_event({"type": "component_analysis_finished", "round_number": round_number,
                          "status": analysis.get("status"), "analysis_file": analysis.get("analysis_file"),
                          "created_at": _utc_now_iso()})
        if analysis.get("status") != "success":
            return {"status": "failure", "message": "component_analysis failed", "rounds": rounds, "analysis": analysis}
        if _is_terminal_component_decision(analysis.get("analysis", "")):
            candidate = result
            break
        if not agent_command:
            return {"status": "needs_action", "message": "model update required; configure agent_command",
                    "rounds": rounds, "current_feedme": current_feedme, "analysis_file": analysis.get("analysis_file")}
        output = await _run_command(agent_command, {"{feedme}": current_feedme,
                                    "{analysis_file}": analysis.get("analysis_file", ""),
                                    "{working_note_file}": working_note}, agent_timeout)
        current_feedme = _next_feedme(output, current_feedme)
        await emit_event({"type": "feedme_updated_by_agent", "round_number": round_number,
                          "next_feedme": current_feedme, "created_at": _utc_now_iso()})
    if candidate is None:
        return {"status": "max_rounds_reached", "message": f"workflow reached max_rounds={max_rounds}", "rounds": rounds}

    locked_round_dir = os.path.dirname(candidate["round_status_file"])
    if not verifier_command:
        await emit_event({"type": "best_round_verification_required", "locked_round_dir": locked_round_dir,
                          "created_at": _utc_now_iso()})
        return {"status": "needs_verification", "message": "terminal candidate found; configure verifier_command before locking",
                "rounds": rounds, "candidate_result": candidate, "locked_round_dir": locked_round_dir}
    audit_output = await _run_command(verifier_command, {
        "{galaxy_dir}": galaxy_dir, "{locked_round_dir}": locked_round_dir,
        "{working_note_file}": working_note, "{mode}": "single-band",
    }, verifier_timeout)
    # Prefer the verifier's required final fenced block. Do not treat incidental
    # PASS/FAIL words in the six-dimensional report as the overall verdict.
    matches = _VERDICT_FENCE_RE.findall(audit_output)
    if not matches:
        matches = _VERDICT_LINE_RE.findall(audit_output)
    verdict = matches[-1].upper() if matches else "FAIL"
    await emit_event({"type": "best_round_verification_finished", "verdict": verdict,
                      "locked_round_dir": locked_round_dir, "created_at": _utc_now_iso()})
    if verdict != "PASS":
        return {"status": "verification_failed", "message": "best-round audit did not pass",
                "verdict": verdict, "audit_output": audit_output, "rounds": rounds, "candidate_result": candidate}

    fourier_result = await asyncio.to_thread(
        fourier_mode_analysis, candidate["image_file"], os.path.basename(galaxy_dir),
        "Best model passed the six-dimensional best-round audit.",
    )
    _write_report(report_file, task_id, feedme, candidate, rounds, audit_output, fourier_result)
    await emit_event({"type": "workflow_finished", "status": "success", "report_file": report_file,
                      "created_at": _utc_now_iso()})
    return {"status": "success", "message": "workflow completed and audit passed", "rounds": rounds,
            "final_result": candidate, "audit_output": audit_output,
            "fourier_result": fourier_result, "report_file": report_file}
