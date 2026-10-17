#!/usr/bin/env bash
set -u

# 이 스크립트의 책임:
# - 검증용 모델 실행기들을 순서대로 실행한다.
# - 각 실행 뒤 새로 만들어진 result/run_* 폴더를 찾는다.
# - 결과 폴더 이름 앞에 모델 이름을 붙여서 구분하기 쉽게 만든다.
#
# Git Bash 사용:
#   bash tools/valid/models/run_all_models.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
RESULT_ROOT="${PROJECT_ROOT}/tools/valid/result"

MODEL_NAMES=(
  "best06"
  "best07"
  "best08_v1"
  "best08_v2"
  "best09"
  "rot_v1"
  "rot_v2"
)

list_result_dirs() {
  if [[ ! -d "${RESULT_ROOT}" ]]; then
    return 0
  fi

  find "${RESULT_ROOT}" -mindepth 1 -maxdepth 1 -type d | sort
}

find_new_result_dir() {
  local before_file="$1"
  local after_file="$2"

  while IFS= read -r dir; do
    if ! grep -Fxq "${dir}" "${before_file}"; then
      printf '%s\n' "${dir}"
      return 0
    fi
  done < "${after_file}"

  tail -n 1 "${after_file}" 2>/dev/null || true
}

succeeded=()
failed=()

total="${#MODEL_NAMES[@]}"
for ((i=0; i<total; i++)); do
  model_name="${MODEL_NAMES[$i]}"
  printf '[%d/%d] Running %s...\n' "$((i + 1))" "${total}" "${model_name}"

  before_file="$(mktemp)"
  after_file="$(mktemp)"
  list_result_dirs > "${before_file}"

  # 모든 모델은 동일한 Python 실행기를 사용하고 모델 이름만 인자로 전달합니다.
  if uv run python "${PROJECT_ROOT}/tools/valid/models/run.py" "${model_name}"; then
    list_result_dirs > "${after_file}"
    new_dir="$(find_new_result_dir "${before_file}" "${after_file}")"

    if [[ -z "${new_dir}" ]]; then
      failed+=("${model_name}: No result directory was created.")
      printf '[%d/%d] Failed %s -> no result directory\n' "$((i + 1))" "${total}" "${model_name}"
      rm -f "${before_file}" "${after_file}"
      continue
    fi

    base_name="$(basename "${new_dir}")"
    if [[ "${base_name}" == "${model_name}_"* ]]; then
      final_dir="${new_dir}"
    else
      final_dir="${RESULT_ROOT}/${model_name}_${base_name}"
      # 같은 이름의 과거 결과가 있어도 삭제하지 않습니다.
      # 충돌한 경우 실행 시각을 덧붙여 두 검증 결과를 모두 보존합니다.
      if [[ -e "${final_dir}" ]]; then
        final_dir="${RESULT_ROOT}/${model_name}_${base_name}_$(date +%Y%m%d_%H%M%S)"
      fi
      mv "${new_dir}" "${final_dir}"
    fi

    succeeded+=("${model_name}: ${final_dir}")
    printf '[%d/%d] Finished %s -> %s\n' "$((i + 1))" "${total}" "${model_name}" "${final_dir}"
  else
    failed+=("${model_name}: validation run failed")
    printf '[%d/%d] Failed %s\n' "$((i + 1))" "${total}" "${model_name}"
  fi

  rm -f "${before_file}" "${after_file}"
done

printf '\n=== Validation Summary ===\n'

if [[ "${#succeeded[@]}" -gt 0 ]]; then
  printf 'Succeeded:\n'
  for item in "${succeeded[@]}"; do
    printf '  - %s\n' "${item}"
  done
fi

if [[ "${#failed[@]}" -gt 0 ]]; then
  printf 'Failed:\n'
  for item in "${failed[@]}"; do
    printf '  - %s\n' "${item}"
  done
fi

if [[ "${#succeeded[@]}" -eq 0 && "${#failed[@]}" -eq 0 ]]; then
  printf 'No models were executed.\n'
fi
