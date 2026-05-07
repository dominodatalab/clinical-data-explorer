#!/bin/bash

set -euo pipefail

token=${1:-}
app_url=${2:-${CDE_APP_URL:-}}
dataset_name=${3:-${CDE_DATASET_NAME:-}}
dataset_id=${4:-${CDE_DATASET_ID:-}}

if [[ -z "$token" || -z "$app_url" || -z "$dataset_name" || -z "$dataset_id" ]]; then
  echo "Usage: $0 <token> [app_url] [dataset_name] [dataset_id]"
  echo
  echo "You can also provide the target with env vars:"
  echo "  CDE_APP_URL=<app url>"
  echo "  CDE_DATASET_NAME=<dataset path>"
  echo "  CDE_DATASET_ID=<dataset id>"
  exit 1
fi

curl "${app_url%/}/dataset/load" \
  -H 'Accept: */*' \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $token" \
  --data-raw "$(printf '{"dataset":"%s","datasetId":"%s"}' "$dataset_name" "$dataset_id")"
