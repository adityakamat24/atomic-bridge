#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 <backend|frontend|all>"
  exit 1
}

[ "${1-}" ] || usage

deploy_backend() {
  echo "Deploying backend to Fly.io..."
  fly deploy --remote-only
  fly status
}

deploy_frontend() {
  echo "Deploying frontend to Vercel..."
  ( cd frontend && vercel --prod --confirm )
}

case "$1" in
  backend) deploy_backend ;;
  frontend) deploy_frontend ;;
  all) deploy_backend; deploy_frontend ;;
  *) usage ;;
esac
