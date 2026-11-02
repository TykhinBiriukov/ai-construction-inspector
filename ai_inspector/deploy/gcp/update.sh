#!/bin/bash
set -euo pipefail
cd /opt/inspector-backend
exec 9>update.lock
flock -n 9 || exit 0
git -C source fetch origin feat/ai-inspector-development
revision=$(git -C source rev-parse origin/feat/ai-inspector-development)
if [ -f deployed-revision ] && [ "$(cat deployed-revision)" = "$revision" ]; then exit 0; fi
git -C source checkout --detach "$revision"
docker build -f source/ai_inspector/deploy/gcp/Dockerfile -t "inspector-backend:$revision" source
export INSPECTOR_REVISION="$revision"
docker-compose -p inspector-backend -f source/ai_inspector/deploy/gcp/compose.yaml up -d
for attempt in $(seq 1 30); do
  if docker-compose -p inspector-backend -f source/ai_inspector/deploy/gcp/compose.yaml exec -T backend python -c 'import os,urllib.request,json; r=urllib.request.Request("http://localhost:8200/deployment-health",headers={"X-Inspector-Gateway-Token":os.environ["INSPECTOR_GATEWAY_TOKEN"]}); assert json.load(urllib.request.urlopen(r,timeout=5))["revision"]==os.environ["INSPECTOR_REVISION"]'; then
    printf '%s\n' "$revision" > deployed-revision
    exit 0
  fi
  sleep 2
done
exit 1
