#!/usr/bin/env bash
# Installa il checkpoint locale SDXL Base 1.0 per ComfyUI.
# Nessun token Hugging Face, nessun modello committato nel repository.
#
# Fonte ufficiale verificata il 2026-08-04:
#   repo    : https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0
#   file    : sd_xl_base_1.0.safetensors      (~6.94 GB)
#   licenza : CreativeML Open RAIL++-M   (tag Hugging Face: "openrail++")
#   gated   : false -> download diretto, nessuna accettazione interattiva
#
# Uso:
#   ICE_COMFYUI_ROOT=/opt/ComfyUI scripts/install_local_model.sh
#   scripts/install_local_model.sh --skip-test
set -euo pipefail

FILE_NAME="sd_xl_base_1.0.safetensors"
REPO_PAGE="https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0"
DOWNLOAD_URL="https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/${FILE_NAME}?download=true"
LICENSE_NAME="CreativeML Open RAIL++-M"
LICENSE_URL="https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/blob/main/LICENSE.md"

COMFYUI_ROOT="${ICE_COMFYUI_ROOT:-$HOME/ComfyUI}"
COMFYUI_URL="${ICE_COMFYUI_URL:-http://127.0.0.1:8188}"
SKIP_TEST=0
[[ "${1:-}" == "--skip-test" ]] && SKIP_TEST=1

echo "=== Installazione modello locale (SDXL Base 1.0) ==="
echo "Repository ufficiale : ${REPO_PAGE}"
echo "Licenza              : ${LICENSE_NAME} (${LICENSE_URL})"
echo "File                 : ${FILE_NAME}"
echo

CKPT_DIR="${COMFYUI_ROOT}/models/checkpoints"
mkdir -p "${CKPT_DIR}"
TARGET="${CKPT_DIR}/${FILE_NAME}"
HASH_FILE="${TARGET}.sha256"

# --- 1) esiste già? ----------------------------------------------------------
if [[ -f "${TARGET}" ]]; then
  echo "Checkpoint già presente: ${TARGET}"
  echo "Nessun download necessario."
else
  echo "Download da fonte ufficiale (~6.9 GB)..."
  TMP="${TARGET}.part"
  rm -f "${TMP}"
  if command -v curl >/dev/null 2>&1; then
    curl -L --fail --progress-bar -o "${TMP}" "${DOWNLOAD_URL}"
  elif command -v wget >/dev/null 2>&1; then
    wget -O "${TMP}" "${DOWNLOAD_URL}"
  else
    echo "Serve curl o wget. In alternativa scarica manualmente:" >&2
    echo "  ${REPO_PAGE} -> Files and versions -> ${FILE_NAME}" >&2
    echo "e copialo in: ${CKPT_DIR}" >&2
    exit 1
  fi
  mv "${TMP}" "${TARGET}"
  echo "Download completato."
fi

# --- 2) checksum -------------------------------------------------------------
if [[ -f "${HASH_FILE}" ]]; then
  echo "Checksum già registrato:"
  cat "${HASH_FILE}"
else
  echo "Calcolo SHA-256 (alcuni minuti su 6.9 GB)..."
  if command -v sha256sum >/dev/null 2>&1; then
    (cd "${CKPT_DIR}" && sha256sum "${FILE_NAME}") > "${HASH_FILE}"
  else
    (cd "${CKPT_DIR}" && shasum -a 256 "${FILE_NAME}") > "${HASH_FILE}"
  fi
  cat "${HASH_FILE}"
  echo "Registrato in: ${HASH_FILE}"
fi

# --- 3) istruzioni -----------------------------------------------------------
cat <<EOF

Il modello deve trovarsi in:
  ${TARGET}

Configura l'engine (opzionale: i default coincidono già):
  ICE_COMFYUI_MODEL_FAMILY=sdxl
  ICE_COMFYUI_CHECKPOINT=${FILE_NAME}
  ICE_COMFYUI_WORKFLOW=sdxl_background.json

NOTA: i file .safetensors NON vanno mai committati (sono in .gitignore).
EOF

# --- 4) generazione di prova -------------------------------------------------
if [[ "${SKIP_TEST}" == "1" ]]; then
  echo "Generazione di prova saltata (--skip-test)."
  exit 0
fi

PY="$(dirname "$0")/../.venv/bin/python"
[[ -x "${PY}" ]] || PY="python3"
echo
echo "Generazione di prova via ComfyUI (${COMFYUI_URL})..."
ICE_COMFYUI_URL="${COMFYUI_URL}" "${PY}" -m src.cli comfyui test-generation --page pensiero_essenziale_it
