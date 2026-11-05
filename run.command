#!/bin/bash
set -e
cd "$(dirname "$0")"
trap 'echo "Launch failed. Check your internet connection and try again."; read -r -p "Press Enter to close..."' ERR
unset PYTHONHOME PYTHONPATH
export PYTHONNOUSERSITE=1 PYTHONUTF8=1
export MAMBA_ROOT_PREFIX="$PWD/.runtime/mamba"
mkdir -p .runtime
case "$(uname -m)" in
  arm64) platform=osx-arm64 ;;
  x86_64) platform=osx-64 ;;
  *) echo "Unsupported architecture"; exit 1 ;;
esac
manager="$PWD/.runtime/micromamba"
prefix="$PWD/.runtime/env"
if [ ! -x "$manager" ]; then
  echo "Downloading application setup tool..."
  curl -fL --retry 3 "https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-$platform" -o "$manager.download"
  chmod +x "$manager.download"
  mv "$manager.download" "$manager"
fi
if [ ! -f .runtime/ready ]; then
  installed=false
  for channel in https://conda.anaconda.org/conda-forge https://prefix.dev/conda-forge; do
    echo "Installing runtime from $channel ..."
    if "$manager" create -y --no-rc --override-channels --repodata-ttl 0 --retry-clean-cache -c "$channel" -p "$prefix" python=3.12 pip ffmpeg; then
      installed=true
      break
    fi
    echo "Package download or resolution failed; trying the alternate server..."
  done
  if [ "$installed" != true ]; then
    echo "Cannot install runtime. Check internet/proxy access to conda.anaconda.org and prefix.dev."
    false
  fi
  echo 1 > .runtime/ready
fi
"$manager" run -p "$prefix" python tools/bootstrap.py "$@"
