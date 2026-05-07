#!/bin/bash

set -euo pipefail

# This serially calls the data load endpoint
# assumes that the access tokens are in the shell as
# environment variables named after the users that
# they belong to, and that the users are named "pract1", "pract2", etc

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
total_users=${1:-10}

for i in $(seq 1 $total_users)
do
  v=pract$i
  echo "Loading data for $v"
  token=${!v-}
  if [[ -z "$token" ]]; then
    echo "Missing token environment variable: $v"
    exit 1
  fi
  "$script_dir/test_data_load_req.sh" "$token"
  echo "Done"
done
