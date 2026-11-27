#!/bin/bash
#
# batch_galfit_fitting.sh — batch driver for the single-band GALFIT beam-search
# galaxy-fitting experiments.
#
# PURPOSE
#   For every galaxy directory under <dir> (each containing a GALFIT *.feedme
#   file), launch one headless Claude agent that executes the full fitting
#   workflow (src/prompts/workflow_galfit.md) through the galmcp MCP tool
#   server, then validate the run's deliverable (analysis_report_*.md).
#   Galaxies with an existing report are skipped, so the script is
#   restartable; interrupted runs resume their previous agent session.
#
# EXPERIMENTAL ARMS (--arm)
#   baseline         Full pipeline: orchestrator agent + VLM surveyor.
#   no_global_state  Ablation: the cross-round state digest is withheld from
#                    the surveyor's prompt (enforced server-side).
#   no_verdict_gate  Ablation: best-round selection uses metrics only; a
#                    physicality FAIL no longer disqualifies a round from
#                    becoming the current best s*.
#   single_agent     Ablation: the VLM surveyor is disabled; the orchestrator
#                    itself performs perception and authors candidate actions.
#   The selected arm is passed to beam_init by the agent (per the injected
#   note below), persisted in the beam-search state graph, and enforced by
#   server code on every subsequent call — it cannot drift mid-run. Verify a
#   run's arm via beam_status ("ablations" field) or the "Ablation arm" line
#   in the galaxy's working_note.md.
#
# BEAM WIDTH (--beam-width)
#   Beam width W of the search (sensitivity study: W=1 reproduces the greedy
#   best-first baseline). The beam_width note is injected into the prompt only
#   when the flag is given explicitly; the default (5) is the paper's main
#   configuration and injects nothing, keeping the prompt identical to the
#   baseline runs.
#
# NOTE ON PROMPT LANGUAGE
#   Every prompt fragment this script generates (base argument, arm notes,
#   beam-width note) is English, and the agent is instructed to write all
#   outputs (working_note.md, analysis_report_*.md) in English. The workflow
#   template itself (src/prompts/workflow_galfit.md) is left in its original
#   form as used for the paper runs.
#
# REQUIREMENTS
#   bash 4+, python3, the `claude` CLI (authenticated), an MCP config JSON
#   (--mcp-config, or auto-resolved from the project root), and galaxy
#   directories each containing at least one *.feedme file plus its
#   image / mask / PSF inputs.
#
# OUTPUTS (per galaxy directory)
#   analysis_report_*.md                      final report (the deliverable)
#   <galaxy>_<session>_attempt<N>.log/.json   per-attempt agent log and result
#   <search_dir>/session_mapping.tsv          galaxy x arm session/resume ledger
#
# EXAMPLES
#   # main configuration over all galaxies
#   ./batch_galfit_fitting.sh -d /data/sample
#   # ablation arms (use one directory per arm to keep results separate)
#   ./batch_galfit_fitting.sh -d /data/sample_nogs --arm no_global_state
#   ./batch_galfit_fitting.sh -d /data/sample_novg --arm no_verdict_gate
#   ./batch_galfit_fitting.sh -d /data/sample_1ag  --arm single_agent
#   # beam-width sensitivity (greedy baseline)
#   ./batch_galfit_fitting.sh -d /data/sample_w1 --beam-width 1
#

usage() {
    cat <<'USAGE'
Usage: batch_galfit_fitting.sh -d <galaxy-set-dir> [options]

Options:
  -d, --dir <dir>             Galaxy-set directory (required)
  -g, --gpu <id>              CUDA_VISIBLE_DEVICES (default: 0)
  --arm <name>                Experimental arm: baseline | no_global_state |
                              no_verdict_gate | single_agent (default: baseline)
  --beam-width <w>            Beam width; the beam_width note is injected into the
                              prompt only when this flag is given explicitly
                              (default: 5, not injected)
  --prompt-extra-file <file>  Append free-form extra instructions to the prompt (optional)
  --mcp-config <file>         MCP config file (default: auto-resolved:
                              .mcp.json > newest mcp-local-*.json in project root)
  --model <model>             Claude model (default: glm-5.1)
  --target-galaxies <a,b,c>   Process only the named subset (comma-separated; empty = all)
  --fallback-model <model>    Fallback model on overload (optional)
  --budget <USD>              Per-galaxy cost cap (optional)
  --timeout <sec>             Per-galaxy timeout (default: 10800 = 3h; SIGKILL 60s after)
  --retries <n>               Max rate-limit rests; each rests a fixed 18 min
                              (default: 10, i.e. give up after 3 h of rate limiting)
  --continue-on-limit         On exhausted rate-limit retries, record the failure and
                              move on instead of aborting the whole batch
  --force                     Re-process completed galaxies (a NEW report is required)
  -h, --help                  Show this help

Experimental arms:
  baseline         full pipeline (orchestrator + VLM surveyor)
  no_global_state  cross-round state digest withheld from the surveyor
  no_verdict_gate  best-round selection by metrics only (physicality verdict not gating)
  single_agent     VLM surveyor disabled; the orchestrator performs perception itself
USAGE
}

# On Ctrl+C / SIGTERM: kill all child processes and exit.
cleanup() {
    local exit_code=${1:-130}
    echo -e "\nInterrupt received, terminating all child processes..."
    local pids=$(jobs -p 2>/dev/null)
    if [ -n "$pids" ]; then
        for pid in $pids; do
            local cmd=$(ps -p $pid -o args= 2>/dev/null | cut -c1-80)
            echo "  Terminating PID $pid: $cmd"
        done
        kill $pids 2>/dev/null
        sleep 1
        local alive=""
        for pid in $pids; do
            kill -0 $pid 2>/dev/null && alive="$alive $pid"
        done
        if [ -n "$alive" ]; then
            echo "  The following did not respond to SIGTERM, sending SIGKILL:$alive"
            kill -9 $alive 2>/dev/null
            sleep 0.5
        fi
        for pid in $pids; do
            if kill -0 $pid 2>/dev/null; then
                echo "  WARNING: PID $pid is still alive!"
            else
                echo "  Confirmed: PID $pid terminated"
            fi
        done
    else
        echo "  No child processes to terminate"
    fi
    echo "Batch run aborted."
    exit "$exit_code"
}
trap cleanup INT
trap 'cleanup 143' TERM

# ---------------- Defaults ----------------
search_dir=""
gpu=0
arm="baseline"
beam_width=5
beam_width_explicit=false
model="glm-5.1"
fallback_model=""
max_budget=""
mcp_config=""
timeout_sec=10800      # per-galaxy timeout (3 h)
max_retries=10         # max rate-limit rests (each a fixed 18 min wait)
rl_wait=1080           # fixed rest after each rate-limit hit (seconds; 18 min)
force=false
continue_on_limit=false
prompt_extra_file=""
target_galaxies=()     # subset to process; empty = all galaxies in <dir>

# ---------------- Argument parsing ----------------
while [[ $# -gt 0 ]]; do
    case $1 in
        -d|--dir)
            search_dir="$2"; shift 2 ;;
        -g|--gpu)
            gpu="$2"; shift 2 ;;
        --arm)
            arm="$2"; shift 2 ;;
        --beam-width)
            beam_width="$2"; beam_width_explicit=true; shift 2 ;;
        --prompt-extra-file)
            prompt_extra_file="$2"; shift 2 ;;
        --mcp-config)
            mcp_config="$2"; shift 2 ;;
        --model)
            model="$2"; shift 2 ;;
        --target-galaxies|--target_galaxies)
            IFS=',' read -ra target_galaxies <<< "$2"; shift 2 ;;
        --fallback-model)
            fallback_model="$2"; shift 2 ;;
        --budget)
            max_budget="$2"; shift 2 ;;
        --timeout)
            timeout_sec="$2"; shift 2 ;;
        --retries)
            max_retries="$2"; shift 2 ;;
        --continue-on-limit)
            continue_on_limit=true; shift ;;
        --force)
            force=true; shift ;;
        -h|--help)
            usage; exit 0 ;;
        *)
            echo "Error: unknown option: $1"
            usage
            exit 1 ;;
    esac
done

# ---------------- Validation ----------------
if [ -z "$search_dir" ]; then
    echo "Error: -d/--dir is required"
    usage
    exit 1
fi

if [ ! -d "$search_dir" ]; then
    echo "Error: directory does not exist: $search_dir"
    exit 1
fi

case "$arm" in
    baseline|no_global_state|no_verdict_gate|single_agent) ;;
    *)
        echo "Error: invalid --arm value: $arm (expected: baseline | no_global_state | no_verdict_gate | single_agent)"
        exit 1 ;;
esac

if ! [[ "$beam_width" =~ ^[1-9][0-9]*$ ]]; then
    echo "Error: --beam-width must be a positive integer, got: $beam_width"
    exit 1
fi

if [ -n "$prompt_extra_file" ]; then
    if [ ! -f "$prompt_extra_file" ]; then
        echo "Error: --prompt-extra-file not found: $prompt_extra_file"
        exit 1
    fi
fi

search_dir=${search_dir%/}
export CUDA_VISIBLE_DEVICES="$gpu"

# Project root: walk up from the script directory until
# src/prompts/workflow_galfit.md is found (the script may live in the repo
# root or in a subdirectory such as scripts/).
script_dir="$(cd "$(dirname "$0")" && pwd)"
project_root="$script_dir"
for _ in 1 2 3; do
    if [ -f "${project_root}/src/prompts/workflow_galfit.md" ]; then
        break
    fi
    project_root="$(dirname "$project_root")"
done

# MCP config resolution: explicit --mcp-config > <project_root>/.mcp.json >
# newest mcp-local-*.json in the project root > error.
if [ -z "$mcp_config" ]; then
    if [ -f "${project_root}/.mcp.json" ]; then
        mcp_config="${project_root}/.mcp.json"
    else
        mcp_config=$(ls -1t "${project_root}"/mcp-local-*.json 2>/dev/null | head -1)
    fi
fi

if [ -z "$mcp_config" ] || [ ! -f "$mcp_config" ]; then
    echo "Error: no MCP config found (tried \${project_root}/.mcp.json and mcp-local-*.json)"
    echo "Please pass --mcp-config explicitly."
    exit 1
fi

prompt_template="${project_root}/src/prompts/workflow_galfit.md"
if [ ! -f "$prompt_template" ]; then
    echo "Error: workflow prompt not found: $prompt_template"
    exit 1
fi

# ---------------- Prompt note construction ----------------
# Plain bash strings passed to python via argv (never f-string-interpolated),
# so the literal braces inside the JSON snippets survive intact.
arm_note=""
case "$arm" in
    baseline)
        arm_note="" ;;
    no_global_state)
        arm_note='Note: when calling beam_init, pass the parameter ablations_json={"no_global_state": true}.' ;;
    no_verdict_gate)
        arm_note='Note: when calling beam_init, pass the parameter ablations_json={"no_verdict_gate": true}.' ;;
    single_agent)
        arm_note='Note: when calling beam_init, pass the parameter ablations_json={"single_agent": true}. During the iterative fitting, after each beam_record_fit call do NOT call survey_round; perform the perception and candidate generation yourself instead: (a) read the comparison PNG, the summary file and the galfit.NN file of the current round yourself; (b) following the same JSON contract as the surveyor, author the physicality verdict and the generated candidates yourself: {"physicality_verdict": {...}, "candidates": [...]}; (c) call beam_enqueue_candidates(galaxy_dir, response_json, state_label) and pass its validation in one go, or, on an E_SCHEMA validation failure, read the returned issues, fix them yourself and call again. All other conventions remain unchanged.' ;;
esac

# Beam-width note: injected only when --beam-width is given explicitly.
beam_width_note=""
if [ "$beam_width_explicit" = true ]; then
    beam_width_note="Note: when calling beam_init, pass beam_width=${beam_width}."
fi

extra_note=""
if [ -n "$prompt_extra_file" ]; then
    extra_note=$(cat "$prompt_extra_file")
fi

# Join the non-empty fragments with newlines.
prompt_note=""
for part in "$arm_note" "$beam_width_note" "$extra_note"; do
    if [ -n "$part" ]; then
        if [ -z "$prompt_note" ]; then
            prompt_note="$part"
        else
            prompt_note="${prompt_note}"$'\n'"${part}"
        fi
    fi
done

echo "Single-band batch fitting configuration:"
echo "  Search dir: $search_dir"
echo "  Arm: $arm"
if [ "$beam_width_explicit" = true ]; then
    echo "  Beam width: ${beam_width} (injected into prompt)"
else
    echo "  Beam width: ${beam_width} (default, not injected into prompt)"
fi
[ -n "$prompt_extra_file" ] && echo "  Extra instructions file: $prompt_extra_file"
echo "  MCP config: $mcp_config"
echo "  GPU: $gpu (CUDA_VISIBLE_DEVICES)"
echo "  Model: $model"
[ -n "$fallback_model" ] && echo "  Fallback model: $fallback_model"
[ -n "$max_budget" ]     && echo "  Per-galaxy budget cap: \$${max_budget}"
echo "  Per-galaxy timeout: ${timeout_sec}s ($((timeout_sec/3600))h$(((timeout_sec%3600)/60))m), SIGKILL 60s after expiry"
echo "  Rate-limit handling: fixed $((${rl_wait}/60)) min rest per hit, up to ${max_retries} rests ($((${max_retries}*${rl_wait}/60)) min = 3 h), same-session resume"
echo "  On exhausted rate-limit retries: $([ "$continue_on_limit" = true ] && echo 'skip galaxy and continue' || echo 'abort the batch')"
echo "  Force re-run: $force"
echo ""

# Session ledger (TSV), keyed by galaxy x arm so sessions are never resumed
# across different experimental arms. Rows in the legacy 4-column format
# (no arm column) are safely ignored by the resume lookup below.
mapping_file="${search_dir}/session_mapping.tsv"
if [ ! -s "$mapping_file" ]; then
    echo -e "galaxy\tarm\tsession_id\tattempts\tstatus\tlog_file" > "$mapping_file"
fi

completed=0
skipped=0
failed=0
failed_summary=""

# Keyword fallbacks for rate-limit / missing-session detection. The Chinese
# terms match the localized API error texts — do not remove them.
rate_limit_pattern='rate.limit|quota|overloaded|credit|billing|Usage limit|too many requests|API Error: 529|访问量过大|稍后再试|服务繁忙|请求过于频繁|当前访问'
session_missing_pattern='(no|without|cannot|could.?not|unable to).{0,30}session|session.{0,40}(not.{0,15}found|does.?not.{0,15}exist|missing|invalid|unavailable)|no.*matching.*session|invalid.*session|无法.{0,8}(找到|恢复).{0,8}会话|会话.{0,8}(不.{0,6}存在|丢失|无效|未.{0,6}找到|无法恢复)|找不到.{0,8}会话'

# Parse the `claude -p --output-format json` result.
# Output: "subtype|status|session_id|snippet"  status ∈ {ok,error,parse_error}
# claude may return a JSON array ([init, assistant, result]); take the last
# element with type == result.
parse_json_result() {
    python3 - "$1" <<'PY'
import json, sys
def emit(subtype, status, sid, snippet=""):
    # Strip separators/newlines and truncate so the output stays one line.
    snippet = (snippet or "").replace("|", "/").replace("\t", " ").replace("\n", " ").strip()
    print(f"{subtype}|{status}|{sid or ''}|{snippet[:300]}")
try:
    with open(sys.argv[1]) as f:
        data = json.load(f)
except Exception:
    emit("parse_error", "error", "")
    sys.exit(0)
if isinstance(data, list):
    result_obj = None
    for item in data:
        if isinstance(item, dict) and item.get("type") == "result":
            result_obj = item
    if result_obj is None:
        result_obj = data[-1] if data and isinstance(data[-1], dict) else {}
    data = result_obj
if not isinstance(data, dict):
    emit("unknown", "error", "")
    sys.exit(0)
subtype  = data.get("subtype", "unknown") or "unknown"
is_error = data.get("is_error", True)
sid      = data.get("session_id", "") or ""
snippet  = data.get("result", "") or ""
status   = "error" if is_error else "ok"
emit(subtype, status, sid, snippet)
PY
}

# Iterate over all galaxy directories containing a .feedme config file.
for d in "$search_dir"/*/; do
    [ -d "$d" ] || continue
    dir_name=${d%/}

    if ! ls "$dir_name"/*.feedme 1>/dev/null 2>&1; then
        continue
    fi

    galaxy_name=$(basename "$dir_name")

    # Optional subset filter (--target-galaxies), matched by exact name.
    if [[ ${#target_galaxies[@]} -gt 0 ]]; then
        found=false
        for t in "${target_galaxies[@]}"; do
            [[ "$t" == "$galaxy_name" ]] && { found=true; break; }
        done
        [[ "$found" = false ]] && continue
    fi

    # Skip galaxies that already have an analysis report (unless --force).
    if [ "$force" = false ] && ls "$dir_name"/analysis_report_*.md 1>/dev/null 2>&1; then
        echo "Skip $dir_name (already completed: analysis report present)"
        skipped=$((skipped + 1))
        continue
    fi

    echo "================================================"
    echo "Starting single-band fit: $dir_name [arm=$arm]"
    echo "================================================"

    # Locate the input .feedme (prefer one without an _iter suffix).
    feedme_file=$(ls "$dir_name"/*.feedme 2>/dev/null | grep -v '_iter' | head -1)
    if [ -z "$feedme_file" ]; then
        feedme_file=$(ls "$dir_name"/*.feedme 2>/dev/null | head -1)
    fi
    feedme_base=$(basename "$feedme_file")

    # Build the fitting prompt from workflow_galfit.md. The note is assembled
    # on the bash side and passed as the 4th argv — python performs no
    # f-string interpolation on it, so literal braces survive intact.
    # The base argument is English and instructs the agent to write all
    # outputs in English (the workflow template itself may be in another
    # language).
    prompt=$(python3 - "$prompt_template" "$dir_name" "$feedme_base" "$prompt_note" <<'PY'
from pathlib import Path
import sys

template_path, galaxy_dir, feedme_base, note = sys.argv[1:5]
template = Path(template_path).read_text()
argument = (
    f"Fully automatically analyze and fit the galaxy in directory {galaxy_dir}.\n"
    f"Enter {galaxy_dir} and locate the main configuration file ({feedme_base}).\n"
    f"Write all outputs (working_note.md and the final analysis_report_*.md) in English."
)
if note:
    argument = argument + "\n" + note
print(template.replace("{argument}", argument))
PY
)

    # Cross-restart resume: use the latest session recorded for this galaxy
    # under the SAME arm. --force always starts a fresh session.
    existing_sid=""
    if [ "$force" = false ]; then
        existing_sid=$(awk -F'\t' -v g="$galaxy_name" -v a="$arm" \
            '$1==g && $2==a {sid=$3} END{print sid}' "$mapping_file" 2>/dev/null)
    fi
    if [[ -n "$existing_sid" ]]; then
        session_id="$existing_sid"
        is_resume=true
        echo "Found a same-arm session, resuming with --resume: $session_id"
    else
        session_id=$(uuidgen 2>/dev/null || python3 -c "import uuid; print(uuid.uuid4())")
        if [ -z "$session_id" ]; then
            echo "Error: cannot generate a UUID (install uuidgen or python3)"
            exit 1
        fi
        is_resume=false
    fi

    attempt=0
    rl_rests=0          # rate-limit rests taken so far (fixed 18 min each)
    galaxy_done=false
    galaxy_failed_reason=""
    last_log=""

    # The loop is bounded by explicit breaks only: non-rate-limit failures
    # and timeouts do not retry; rate limiting is governed by the separate
    # rl_rests counter (max_retries rests of a fixed rl_wait each).
    while true; do
        attempt=$((attempt + 1))

        log_file="${dir_name}/${galaxy_name}_${session_id}_attempt${attempt}.log"
        result_json="${dir_name}/${galaxy_name}_${session_id}_attempt${attempt}.json"
        last_log="$log_file"

        echo "[Attempt $attempt] galaxy: $galaxy_name | arm: $arm | session: $session_id | rl-rests: $rl_rests/$max_retries | log: $log_file"

        # Pre-create the log files to avoid a tail race.
        : > "$log_file"
        : > "$result_json"

        # Start time of this attempt: the deliverable check only accepts
        # reports generated/updated after this moment.
        start_ts=$(date +%s)

        # Assemble the claude arguments (headless mode).
        claude_args=(
            -p
            --output-format json
            --dangerously-skip-permissions
            --model "$model"
            --mcp-config "$mcp_config"
            --strict-mcp-config
            --verbose
        )
        # First attempt: --resume when resuming a previous session, otherwise
        # --session-id for a new one; all retries resume the same session.
        if [[ $attempt -eq 1 && "$is_resume" = false ]]; then
            claude_args+=(--session-id "$session_id")
        else
            claude_args+=(--resume "$session_id")
        fi
        [ -n "$fallback_model" ] && claude_args+=(--fallback-model "$fallback_model")
        [ -n "$max_budget" ]     && claude_args+=(--max-budget-usd "$max_budget")

        # timeout is the direct child; -k 60 guarantees a SIGKILL even if the
        # agent ignores SIGTERM. stdout = JSON result, stderr = verbose log.
        timeout -k 60 "$timeout_sec" claude "${claude_args[@]}" "$prompt" \
            > "$result_json" 2> "$log_file" &
        runner_pid=$!   # pid of timeout, not of claude itself
        tail -f "$log_file" 2>/dev/null &
        tail_pid=$!

        wait $runner_pid 2>/dev/null
        runner_exit=$?
        kill $tail_pid 2>/dev/null
        wait $tail_pid 2>/dev/null

        # User interrupt (SIGINT, exit code 130).
        if [ $runner_exit -eq 130 ]; then
            echo ""
            echo "User interrupt (Ctrl+C) detected, exiting the batch."
            exit 130
        fi

        # Timeout (timeout returns 124; -k guarantees the child is dead).
        if [ $runner_exit -eq 124 ]; then
            echo "Warning: galaxy $dir_name timed out (>${timeout_sec}s), not retrying."
            galaxy_failed_reason="timeout"
            break
        fi

        # Parse the JSON result. The result element may still carry
        # subtype="success" with is_error=true; the real reason is in the
        # result text, e.g. "API Error: 529 ...".
        parsed=$(parse_json_result "$result_json")
        result_subtype="${parsed%%|*}"
        _rest="${parsed#*|}"
        result_status="${_rest%%|*}"
        _rest="${_rest#*|}"
        result_sid="${_rest%%|*}"
        result_snippet="${_rest#*|}"
        # Prefer the session_id from the JSON (fallback: the pre-generated one).
        [ -n "$result_sid" ] && session_id="$result_sid"

        echo "  exit=$runner_exit | subtype=$result_subtype | status=$result_status"
        [ -n "$result_snippet" ] && echo "  result: $result_snippet"

        # Rate limiting / overload: subtype marker or keyword hits in the log
        # and the JSON result (the error text often appears in the result
        # field while the stderr log may be empty, so both files are checked).
        is_rate_limit=false
        if [[ "$result_subtype" == *"rate_limit"* ]] || [[ "$result_subtype" == *"overloaded"* ]] || \
           grep -qiE "$rate_limit_pattern" "$log_file" "$result_json" 2>/dev/null; then
            is_rate_limit=true
        fi

        if [ "$is_rate_limit" = true ]; then
            # Give up only when the rest budget is already spent: at most
            # max_retries (10) rests of a fixed rl_wait (18 min) each, i.e.
            # 3 h of rate limiting before abandoning this galaxy.
            if [ $rl_rests -ge $max_retries ]; then
                echo "============================================================"
                echo "Galaxy $dir_name still rate-limited after $max_retries rests ($((${max_retries}*${rl_wait}/60)) min = 3 h), giving up!"
                echo "Log: $log_file"
                echo "============================================================"
                galaxy_failed_reason="rate_limit_exhausted"
                if [ "$continue_on_limit" = true ]; then
                    echo "(--continue-on-limit is set: recording the failure and moving on)"
                    break
                fi
                # Record the mapping row before aborting, so a restart can
                # locate and resume this galaxy.
                echo -e "${galaxy_name}\t${arm}\t${session_id}\t${attempt}\t${galaxy_failed_reason}\t${last_log}" >> "$mapping_file"
                exit 1
            fi
            rl_rests=$((rl_rests + 1))
            echo "Warning: rate limited (attempt $attempt). Resting a fixed ${rl_wait}s ($((${rl_wait}/60)) min) [rest $rl_rests/$max_retries], then resuming session $session_id ..."
            sleep "$rl_wait"
            continue
        fi

        # Fallback: if the first --resume fails because the stored session no
        # longer exists (claude --resume <bad-id> errors out immediately),
        # start a fresh session. Costs no retry budget and triggers once.
        # Requires an actual failure (runner_exit != 0) to avoid false hits
        # from successful logs.
        if [[ $attempt -eq 1 && "$is_resume" = true && $runner_exit -ne 0 ]] && \
           { grep -qiE "$session_missing_pattern" "$log_file" 2>/dev/null || \
             grep -qiE "$session_missing_pattern" "$result_json" 2>/dev/null; }; then
            echo "Warning: session $session_id cannot be resumed (likely cleaned up), falling back to a new session."
            session_id=$(uuidgen 2>/dev/null || python3 -c "import uuid; print(uuid.uuid4())")
            is_resume=false
            attempt=0
            continue
        fi

        # Genuine non-rate-limit failure: no retry.
        if [ $runner_exit -ne 0 ] || [ "$result_status" != "ok" ]; then
            echo "Warning: claude exited abnormally (exit=$runner_exit, subtype=$result_subtype), not retrying."
            echo "Galaxy $dir_name may be incomplete; check the log: $log_file"
            galaxy_failed_reason="exit=${runner_exit},subtype=${result_subtype}"
            break
        fi

        # claude reports success — still verify the deliverable. Only reports
        # generated/updated after this attempt started count, so a --force
        # re-run cannot be satisfied by a stale report from an earlier run.
        new_report=$(find "$dir_name" -maxdepth 1 -name 'analysis_report_*.md' \
            -newermt "@${start_ts}" -print -quit 2>/dev/null)
        if [ -n "$new_report" ]; then
            galaxy_done=true
            echo "  Deliverable verified: $new_report"
        else
            echo "Warning: claude returned success but no new analysis_report_*.md was produced; treating as incomplete."
            echo "Galaxy $dir_name may be incomplete; check the log: $log_file"
            galaxy_failed_reason="missing_report"
        fi
        break
    done

    # Record the session mapping (with arm and outcome status).
    status_col="${galaxy_failed_reason:-ok}"
    [ "$galaxy_done" = true ] && status_col="ok"
    echo -e "${galaxy_name}\t${arm}\t${session_id}\t${attempt}\t${status_col}\t${last_log}" >> "$mapping_file"

    if [ "$galaxy_done" = true ]; then
        echo "Galaxy $dir_name completed. Log: $last_log"
        completed=$((completed + 1))
    else
        echo "Galaxy $dir_name incomplete (reason: ${galaxy_failed_reason:-unknown}). Log: $last_log"
        failed=$((failed + 1))
        failed_summary="${failed_summary}  - ${galaxy_name} [${arm}] ${galaxy_failed_reason:-unknown}\n"
    fi
done

echo ""
echo "============================================================"
echo "Galaxy batch fitting finished."
echo "  Completed: $completed | Failed: $failed | Skipped: $skipped"
[ -n "$failed_summary" ] && { echo "  Failures:"; echo -e "$failed_summary"; }
echo "  Session ledger: $mapping_file"
echo "============================================================"
