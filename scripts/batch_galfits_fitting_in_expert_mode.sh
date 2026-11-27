#!/bin/bash
#
# batch_galfits_fitting_in_expert_mode.sh — batch driver for EXPERT-GUIDED
# multi-band (GalfitS) beam-search galaxy fitting (workflow_galfits_s1.md).
#
# PURPOSE
#   For every galaxy directory under <dir> (each containing a GalfitS *.lyric
#   file) that ALSO has an entry in the expert components file, launch one
#   headless Claude agent that executes the expert-guided multi-band fitting
#   workflow (src/prompts/workflow_galfits_s1.md) through the galmcp MCP tool
#   server. The astronomer-provided component inventory T is injected into the
#   prompt as a HARD search-space constraint (the workflow registers it via
#   beam_set_constraint with mode="galfits"), then the run's deliverable
#   (analysis_report_*.md — also the hard gate for the SED stages) is
#   validated. Galaxies with an existing report are skipped, so the script is
#   restartable; interrupted runs resume their previous agent session.
#
# EXPERT COMPONENTS FILE (default: <dir>/expert_components.txt, override: -c)
#   One galaxy per line, fields separated by a single vertical bar '|'
#   (chosen over tab because vi may expand tabs to spaces):
#
#       # comments and blank lines are ignored
#       KILOGAS_350 | bulge(sersic,psf), disk(sersic), bar(sersic)
#       KILOGAS_351 | disk(sersic)
#
#   The components field is passed VERBATIM into the workflow prompt and uses
#   that prompt's grammar: component(candidate-model,...) separated by commas.
#   The whole entry must fit on ONE line (the field can never contain a
#   newline). Valid slot names: bulge, disk, bar, agn, nucleus, fourier, f1,
#   singlesersic. lens / edgedisk / companion / outerdisk are recognized but
#   outside the multi-band solution space: the workflow records the conflict
#   and fits the legal subset. Unknown slot names (typos) abort the batch
#   BEFORE any fitting starts (full pre-flight validation, all errors at
#   once). Galaxies with a *.lyric but NO spec line are SKIPPED (reported in
#   the final summary); spec lines without a matching galaxy directory are
#   pre-flight errors.
#
# SESSION / TARGET CONSISTENCY
#   The session ledger (session_mapping.tsv in <dir>) stores the expert
#   components string actually used by each session (7th column; the arm
#   column is fixed to "s1mb" to distinguish from the single-band "s1" arm).
#   On resume, if the components file entry for a galaxy differs from the
#   recorded one, the resume is REFUSED for that galaxy (status
#   t_changed_needs_force) — re-run with --force to start a fresh session
#   under the new target.
#
# REQUIREMENTS (expert machine)
#   bash 4+, python3, the `claude` CLI (authenticated), a GalfitS setup
#   reachable by the MCP server (GALFITS_BIN + GS_DATA_PATH), and an MCP
#   config JSON passed explicitly via --mcp-config (REQUIRED — there is
#   deliberately NO default and no auto-resolution: the config contains
#   machine-specific paths, binaries and VLM endpoint credentials). Minimal
#   example:
#
#     {
#       "mcpServers": {
#         "galmcp": {
#           "command": "python3",
#           "args": ["/abs/path/to/galaxy_morphology_mcp/src/mcp_server.py",
#                    "--transport", "stdio"],
#           "env": {
#             "GALFITS_BIN": "/usr/local/bin/galfits",
#             "GS_DATA_PATH": "/abs/path/to/galfits_data",
#             "OPENAI_API_KEY": "<your-key>",
#             "OPENAI_BASE_URL": "<your-vlm-endpoint>",
#             "OPENAI_MODEL": "<your-vlm-model>"
#           }
#         }
#       }
#     }
#
# OUTPUTS (per galaxy directory)
#   analysis_report_*.md                      final report (the deliverable;
#                                             the SED stages run after it)
#   <galaxy>_<session>_attempt<N>.log/.json   per-attempt agent log and result
#   <dir>/session_mapping.tsv                 galaxy x session resume ledger
#
# EXAMPLES
#   # all spec-listed galaxies under /data/expert_sample_mb
#   ./batch_galfits_fitting_in_expert_mode.sh -d /data/expert_sample_mb \
#       --mcp-config /home/astronomer/galmcp_mb.json
#   # re-run one galaxy from scratch (also the escape after editing its T)
#   ./batch_galfits_fitting_in_expert_mode.sh -d /data/expert_sample_mb \
#       --mcp-config /home/astronomer/galmcp_mb.json \
#       --target-galaxies KILOGAS_350 --force
#

usage() {
    cat <<'USAGE'
Usage: batch_galfits_fitting_in_expert_mode.sh -d <galaxy-set-dir> --mcp-config <file> [options]

Options:
  -d, --dir <dir>             Galaxy-set directory (required)
  --mcp-config <file>         MCP config file (REQUIRED — no default; see the
                              minimal JSON example in the script header)
  -c, --components-file <f>   Expert components file
                              (default: <dir>/expert_components.txt)
  -g, --gpu <id>              CUDA_VISIBLE_DEVICES (default: 0; only relevant
                              when the MCP server's VLM runs on a local GPU)
  --model <model>             Claude model. No default: when omitted, the
                              flag is not passed and the claude CLI uses its
                              own configured default model
  --target-galaxies <a,b,c>   Process only the named subset (comma-separated; empty = all)
  --fallback-model <model>    Fallback model on overload (optional)
  --budget <USD>              Per-galaxy cost cap (optional)
  --timeout <sec>             Per-galaxy timeout (default: 14400 = 4h; SIGKILL 60s after)
  --retries <n>               Max rate-limit rests; each rests a fixed 18 min
                              (default: 10, i.e. give up after 3 h of rate limiting)
  --continue-on-limit         On exhausted rate-limit retries, record the failure and
                              move on instead of aborting the whole batch
  --force                     Re-process completed galaxies (a NEW report is
                              required); also the escape hatch after editing a
                              galaxy's expert components (refused resumes)
  -h, --help                  Show this help

Expert components file format (one galaxy per line, '|' separator):
  KILOGAS_350 | bulge(sersic,psf), disk(sersic), bar(sersic)
  KILOGAS_351 | disk(sersic)
Valid slots: bulge, disk, bar, agn, nucleus, fourier, f1, singlesersic
(out-of-space but recognized: lens, edgedisk, companion, outerdisk — recorded
as a conflict by the workflow, the legal subset is fitted)
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
components_file=""
model=""
fallback_model=""
max_budget=""
mcp_config=""
timeout_sec=14400      # per-galaxy timeout (4 h — mb runs include SED stages)
max_retries=10         # max rate-limit rests (each a fixed 18 min wait)
rl_wait=1080           # fixed rest after each rate-limit hit (seconds; 18 min)
force=false
continue_on_limit=false
target_galaxies=()     # subset to process; empty = all galaxies in <dir>

# ---------------- Argument parsing ----------------
while [[ $# -gt 0 ]]; do
    case $1 in
        -d|--dir)
            search_dir="$2"; shift 2 ;;
        -g|--gpu)
            gpu="$2"; shift 2 ;;
        -c|--components-file)
            components_file="$2"; shift 2 ;;
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

# --mcp-config is REQUIRED: the config carries machine-specific paths,
# binaries and VLM credentials — auto-resolution would silently pick up a
# stale developer-machine config, so it is deliberately absent here.
if [ -z "$mcp_config" ]; then
    echo "Error: --mcp-config is required (there is no default on purpose)."
    echo "       It must point at an MCP config JSON for the galmcp server on"
    echo "       THIS machine; see the minimal example in this script's header."
    exit 1
fi
if [ ! -f "$mcp_config" ]; then
    echo "Error: --mcp-config file not found: $mcp_config"
    exit 1
fi

search_dir=${search_dir%/}
if [ -z "$components_file" ]; then
    components_file="${search_dir}/expert_components.txt"
fi
if [ ! -f "$components_file" ]; then
    echo "Error: expert components file not found: $components_file"
    echo "       (default location is <dir>/expert_components.txt; override with -c)"
    echo "       Expected format, one galaxy per line:"
    echo "         KILOGAS_350 | bulge(sersic,psf), disk(sersic), bar(sersic)"
    echo "       Galaxies with a *.lyric but no spec line are skipped."
    exit 1
fi

if ! [[ "$timeout_sec" =~ ^[1-9][0-9]*$ ]]; then
    echo "Error: --timeout must be a positive integer, got: $timeout_sec"
    exit 1
fi
if ! [[ "$max_retries" =~ ^[0-9]+$ ]]; then
    echo "Error: --retries must be a non-negative integer, got: $max_retries"
    exit 1
fi

export CUDA_VISIBLE_DEVICES="$gpu"

# Project root: walk up from the script directory until
# src/prompts/workflow_galfits_s1.md is found (the script may live in the
# repo root or in a subdirectory such as scripts/).
script_dir="$(cd "$(dirname "$0")" && pwd)"
project_root="$script_dir"
for _ in 1 2 3; do
    if [ -f "${project_root}/src/prompts/workflow_galfits_s1.md" ]; then
        break
    fi
    project_root="$(dirname "$project_root")"
done

prompt_template="${project_root}/src/prompts/workflow_galfits_s1.md"
if [ ! -f "$prompt_template" ]; then
    echo "Error: expert workflow prompt not found: $prompt_template"
    echo "       (this script must live inside the galaxy_morphology_mcp repository)"
    exit 1
fi

# ---------------- Expert components pre-flight ----------------
# Parse the whole spec file and cross-check it against the galaxy
# directories BEFORE launching anything: all problems are reported at once
# with line numbers; any error aborts the batch. stdout carries the valid
# entries as TSV (galaxy<TAB>components); diagnostics go to stderr.
spec_map=$(python3 - "$search_dir" "$components_file" <<'PY'
import os, re, sys

root, spec_file = sys.argv[1], sys.argv[2]
VALID = {"bulge", "disk", "bar", "agn", "nucleus", "fourier", "f1", "singlesersic"}
OUT_OF_SPACE = {"lens", "edgedisk", "companion", "outerdisk"}

errors, warnings, entries = [], [], {}

with open(spec_file) as f:
    for lineno, raw in enumerate(f, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.count("|") != 1:
            errors.append(
                f"line {lineno}: expected exactly one '|' separator, "
                f"found {line.count('|')}: {line[:80]}")
            continue
        name, comp = (part.strip() for part in line.split("|"))
        if not name:
            errors.append(f"line {lineno}: empty galaxy name")
            continue
        if not comp:
            errors.append(f"line {lineno}: empty components field for '{name}'")
            continue
        if name in entries:
            errors.append(f"line {lineno}: duplicate entry for galaxy '{name}'")
            continue
        if comp.count("(") != comp.count(")"):
            errors.append(
                f"line {lineno} ({name}): unbalanced parentheses: {comp}")
            continue
        # Top-level split on commas that are NOT inside parentheses.
        tokens, depth, cur = [], 0, ""
        for ch in comp:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == "," and depth == 0:
                tokens.append(cur)
                cur = ""
            else:
                cur += ch
        tokens.append(cur)
        line_bad = False
        for tok in tokens:
            tok = tok.strip()
            m = re.match(r"^([A-Za-z]+)\s*(?:\((.*)\))?$", tok)
            if not m:
                errors.append(
                    f"line {lineno} ({name}): cannot parse component token '{tok}' "
                    f"(expected 'name' or 'name(model,...)')")
                line_bad = True
                continue
            slot = m.group(1).lower()
            if slot in OUT_OF_SPACE:
                warnings.append(
                    f"line {lineno} ({name}): '{tok}' is outside the multi-band "
                    f"solution space — the workflow will record the conflict "
                    f"and fit the legal subset")
            elif slot not in VALID:
                errors.append(
                    f"line {lineno} ({name}): unknown component '{tok}' — typo? "
                    f"(valid: {', '.join(sorted(VALID))}; recognized but "
                    f"out-of-space: {', '.join(sorted(OUT_OF_SPACE))})")
                line_bad = True
        if not line_bad:
            entries[name] = comp

# Cross-check against the galaxy directories (a galaxy dir = has a *.lyric).
lyric_dirs = set()
try:
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d)
        if os.path.isdir(p) and any(fn.endswith(".lyric") for fn in os.listdir(p)):
            lyric_dirs.add(d)
except OSError as e:
    errors.append(f"cannot list {root}: {e}")

for name in entries:
    if name not in lyric_dirs:
        errors.append(
            f"spec entry '{name}' has no matching galaxy directory under {root} "
            f"(or the directory contains no *.lyric)")
unlisted = sorted(lyric_dirs - set(entries))
for n in unlisted:
    warnings.append(f"galaxy '{n}' has no expert spec line -> will be SKIPPED")

for w in warnings:
    print(f"WARNING: {w}", file=sys.stderr)
for e in errors:
    print(f"ERROR: {e}", file=sys.stderr)
if unlisted:
    print(f"NOTE: {len(unlisted)} galaxies without a spec line will be skipped.",
          file=sys.stderr)
if errors:
    print(f"{len(errors)} error(s) in {spec_file}; fix them and re-run.",
          file=sys.stderr)
    sys.exit(1)
for name in sorted(entries):
    print(f"{name}\t{entries[name]}")
PY
) || exit 1

spec_galaxies=()
spec_components=()
if [ -n "$spec_map" ]; then
    while IFS=$'\t' read -r g c; do
        spec_galaxies+=("$g")
        spec_components+=("$c")
    done <<< "$spec_map"
fi

# Look up the expert components string for a galaxy directory name.
lookup_components() {
    local g="$1" i
    for i in "${!spec_galaxies[@]}"; do
        if [[ "${spec_galaxies[$i]}" == "$g" ]]; then
            printf '%s' "${spec_components[$i]}"
            return 0
        fi
    done
    return 1
}

echo "Expert-mode multi-band batch fitting configuration:"
echo "  Search dir: $search_dir"
echo "  Expert components file: $components_file (${#spec_galaxies[@]} galaxies listed)"
echo "  MCP config: $mcp_config (explicit, required)"
echo "  GPU: $gpu (CUDA_VISIBLE_DEVICES)"
if [ -n "$model" ]; then
    echo "  Model: $model"
else
    echo "  Model: claude CLI default (--model not passed)"
fi
[ -n "$fallback_model" ] && echo "  Fallback model: $fallback_model"
[ -n "$max_budget" ]     && echo "  Per-galaxy budget cap: \$${max_budget}"
echo "  Per-galaxy timeout: ${timeout_sec}s ($((timeout_sec/3600))h$(((timeout_sec%3600)/60))m), SIGKILL 60s after expiry"
echo "  Rate-limit handling: fixed $((${rl_wait}/60)) min rest per hit, up to ${max_retries} rests, same-session resume"
echo "  On exhausted rate-limit retries: $([ "$continue_on_limit" = true ] && echo 'skip galaxy and continue' || echo 'abort the batch')"
echo "  Force re-run: $force"
echo ""

# Session ledger (TSV). Same 7-column schema as the single-band expert-mode
# driver; the arm column is fixed to "s1mb" and a 7th column stores the
# expert components string used by the session, so a resume under a CHANGED
# target can be detected and refused.
mapping_file="${search_dir}/session_mapping.tsv"
if [ ! -s "$mapping_file" ]; then
    echo -e "galaxy\tarm\tsession_id\tattempts\tstatus\tlog_file\texpert_components" > "$mapping_file"
fi

completed=0
skipped=0
skipped_nospec=0
skipped_nospec_summary=""
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

# Iterate over all galaxy directories containing a .lyric config file.
for d in "$search_dir"/*/; do
    [ -d "$d" ] || continue
    dir_name=${d%/}

    if ! ls "$dir_name"/*.lyric 1>/dev/null 2>&1; then
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

    # Expert-mode gate: no spec line -> skip (confirmed policy).
    components=$(lookup_components "$galaxy_name") || {
        echo "Skip $dir_name (no expert spec line in $components_file)"
        skipped_nospec=$((skipped_nospec + 1))
        skipped_nospec_summary="${skipped_nospec_summary}  - ${galaxy_name}\n"
        continue
    }

    # Skip galaxies that already have an analysis report (unless --force).
    if [ "$force" = false ] && ls "$dir_name"/analysis_report_*.md 1>/dev/null 2>&1; then
        echo "Skip $dir_name (already completed: analysis report present)"
        skipped=$((skipped + 1))
        continue
    fi

    echo "================================================"
    echo "Starting expert-mode multi-band fit: $dir_name"
    echo "  Expert components (T): $components"
    echo "================================================"

    # Locate the input .lyric (prefer one without an _iter suffix).
    lyric_file=$(ls "$dir_name"/*.lyric 2>/dev/null | grep -v '_iter' | head -1)
    if [ -z "$lyric_file" ]; then
        lyric_file=$(ls "$dir_name"/*.lyric 2>/dev/null | head -1)
    fi
    lyric_base=$(basename "$lyric_file")

    # Build the fitting prompt from workflow_galfits_s1.md, filling BOTH
    # placeholders: {expert_components} (the spec line's components field,
    # VERBATIM — passed as a python argv, never f-string-interpolated, so
    # parentheses/commas survive intact) and {argument}.
    prompt=$(python3 - "$prompt_template" "$dir_name" "$lyric_base" "$components" <<'PY'
from pathlib import Path
import sys

template_path, galaxy_dir, lyric_base, expert_components = sys.argv[1:5]
template = Path(template_path).read_text()
argument = (
    f"Fully automatically analyze and fit the multi-band galaxy in directory {galaxy_dir}.\n"
    f"Enter {galaxy_dir} and locate the root configuration file ({lyric_base}).\n"
    f"Run the complete multi-band workflow through the SED and joint image-SED\n"
    f"fitting stages. Write all outputs (working_note.md exports and the final\n"
    f"analysis_report_*.md) in English."
)
out = template.replace("{expert_components}", expert_components)
out = out.replace("{argument}", argument)
print(out)
PY
)

    # Cross-restart resume: use the latest session recorded for this galaxy
    # under arm "s1mb". Refuse the resume when the expert components string
    # changed since that session was recorded (the stored agent context was
    # built under the old target) — --force starts a fresh session instead.
    existing_sid=""
    if [ "$force" = false ]; then
        existing_sid=$(awk -F'\t' -v g="$galaxy_name" -v a="s1mb" \
            '$1==g && $2==a {sid=$3} END{print sid}' "$mapping_file" 2>/dev/null)
        if [ -n "$existing_sid" ]; then
            existing_t=$(awk -F'\t' -v g="$galaxy_name" -v a="s1mb" \
                '$1==g && $2==a {t=$7} END{print t}' "$mapping_file" 2>/dev/null)
            if [ "$existing_t" != "$components" ]; then
                echo "REFUSED: expert components for $galaxy_name changed since session $existing_sid was recorded:"
                echo "    ledger T: ${existing_t:-<none>}"
                echo "    spec   T: $components"
                echo "  Resuming an old session under a new target would mix contexts."
                echo "  Re-run with --force to fit this galaxy from scratch with the new T."
                echo -e "${galaxy_name}\ts1mb\t${existing_sid}\t-\tt_changed_needs_force\t\t${components}" >> "$mapping_file"
                failed=$((failed + 1))
                failed_summary="${failed_summary}  - ${galaxy_name} [s1mb] t_changed_needs_force (use --force)\n"
                continue
            fi
        fi
    fi
    if [[ -n "$existing_sid" ]]; then
        session_id="$existing_sid"
        is_resume=true
        echo "Found an s1mb session with matching T, resuming with --resume: $session_id"
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

        echo "[Attempt $attempt] galaxy: $galaxy_name | arm: s1mb | session: $session_id | rl-rests: $rl_rests/$max_retries | log: $log_file"

        # Pre-create the log files to avoid a tail race.
        : > "$log_file"
        : > "$result_json"

        # Start time of this attempt: the deliverable check only accepts
        # reports generated/updated after this moment.
        start_ts=$(date +%s)

        # Assemble the claude arguments (headless mode). --model is passed
        # ONLY when explicitly given (no developer-machine default).
        claude_args=(
            -p
            --output-format json
            --dangerously-skip-permissions
            --mcp-config "$mcp_config"
            --strict-mcp-config
            --verbose
        )
        [ -n "$model" ] && claude_args+=(--model "$model")
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
                echo -e "${galaxy_name}\ts1mb\t${session_id}\t${attempt}\t${galaxy_failed_reason}\t${last_log}\t${components}" >> "$mapping_file"
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

    # Record the session mapping (arm fixed to "s1mb", T in the 7th column).
    status_col="${galaxy_failed_reason:-ok}"
    [ "$galaxy_done" = true ] && status_col="ok"
    echo -e "${galaxy_name}\ts1mb\t${session_id}\t${attempt}\t${status_col}\t${last_log}\t${components}" >> "$mapping_file"

    if [ "$galaxy_done" = true ]; then
        echo "Galaxy $dir_name completed. Log: $last_log"
        completed=$((completed + 1))
    else
        echo "Galaxy $dir_name incomplete (reason: ${galaxy_failed_reason:-unknown}). Log: $last_log"
        failed=$((failed + 1))
        failed_summary="${failed_summary}  - ${galaxy_name} [s1mb] ${galaxy_failed_reason:-unknown}\n"
    fi
done

echo ""
echo "============================================================"
echo "Expert-mode multi-band galaxy batch fitting finished."
echo "  Completed: $completed | Failed: $failed | Skipped (done): $skipped | Skipped (no spec): $skipped_nospec"
[ -n "$skipped_nospec_summary" ] && { echo "  Skipped, no expert spec:"; echo -e "$skipped_nospec_summary"; }
[ -n "$failed_summary" ] && { echo "  Failures:"; echo -e "$failed_summary"; }
echo "  Session ledger: $mapping_file"
echo "============================================================"
