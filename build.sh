#!/usr/bin/env bash
# Render build script.
#
# The primary vision pipeline (vision_pfd_service.py) renders PDF pages
# via pypdfium2, which bundles its own PDF engine in the pip wheel — no
# system package needed for it. What's left below is only for the
# secondary pdfplumber + Tesseract OCR fallback path (used when
# ANTHROPIC_API_KEY isn't set, or the vision call itself fails):
#
#   * poppler-utils — pdf2image renders PDF pages to images for the OCR
#     fallback in parse_pdf._ocr_pages.
#
#   * tesseract-ocr — the OCR fallback calls pytesseract.image_to_string.
#     If a PDF is scanned (no embedded text), pdfplumber returns empty
#     strings and the code re-tries via OCR — which needs both tesseract
#     AND poppler (for rendering).
#
# Without these two, that fallback path silently degrades to text-only
# extraction for scanned PDFs — it no longer affects the main vision path.
#
# Configure Render's "Build Command" to:
#   ./build.sh
# (make sure this file is executable in the repo: `git update-index
# --chmod=+x build.sh` on Windows, or `chmod +x` from WSL/Mac.)
set -euo pipefail

echo "==> Installing system dependencies (poppler-utils, tesseract-ocr)"
# Render's native Python builder runs as root — apt-get works directly.
apt-get update -qq
apt-get install -y --no-install-recommends poppler-utils tesseract-ocr

echo "==> poppler + tesseract versions:"
pdftoppm -v 2>&1 | head -1 || true
tesseract --version 2>&1 | head -1 || true

echo "==> Installing Python dependencies"
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Applying Alembic migrations"
alembic upgrade head

echo "==> Build complete"
