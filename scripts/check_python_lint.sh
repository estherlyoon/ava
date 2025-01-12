#!/bin/bash

GIT_ROOT=$(git rev-parse --show-toplevel)
PYFMT="${PYFMT:-black} --line-length 120"

set -eu

if [ $# -eq 0 ]; then
  echo "$(basename "$0") <paths>" >&2
  exit 1
fi

LINT=${PYLINT:-pylint}
ARGS="-j 0 --rcfile=${GIT_ROOT}/.pylintrc --suggestion-mode=n"
ROOTS=("$@")
FAILED=
PRUNE_PATHS="cava/nightwatch llvm third_party"
PRUNE_NAMES=".git build* nightwatch* third_party*"
TEST_DIRS="${GIT_ROOT}/tests/"
# lint warnings that are useless for tests since tests are run for ci anyway
# eliminate unused-argument since it is triggered by pytest harnesses whose value is not needed.
TEST_NOLINT="import-error no-name-in-module unused-argument"

emit_prunes() {
  { for p in ${PRUNE_PATHS}; do echo "-path ${p} -prune -o -path ./${p} -prune -o"; done; \
    for p in ${PRUNE_NAMES}; do echo "-name ${p} -prune -o"; done; } | xargs
}

emit_test_nolint() {
  for msg in ${TEST_NOLINT}; do echo "-d ${msg}"; done | xargs
}

is_test() {
  for d in ${TEST_DIRS}; do
    if [[ "$(readlink -f "$1")" =~ ^${d}.* ]]
    then
      return 0
    fi
  done
  return 1
}

TEST_ARGS="${ARGS} $(emit_test_nolint)"

pushd "$GIT_ROOT" > /dev/null

# shellcheck disable=SC2046
while read -r -d '' filename; do
  if is_test "${filename}"
  then
    args="${TEST_ARGS}"
  else
    args="${ARGS}"
  fi
  # shellcheck disable=SC2086
  if ! ${LINT} ${args} "${filename}"
  then
    echo "${filename} NOT OK"
    FAILED=1
  fi
done < <(find "${ROOTS[@]}" $(emit_prunes) -name '*.py' -print0)

popd > /dev/null

if [ -n "${FAILED}" ]; then
  exit 1
fi
