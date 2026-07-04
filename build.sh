#!/usr/bin/env bash
# Render build script.
#
# Installs the system packages the vision pipeline needs BEFORE the pip
# install runs. Without these two the sync silently degrades to
# text-only extraction:
#
#   * poppler-utils — pdf2image renders PDF pages to images. Vision
#     pipeline REQUIRES this. Without it,
#     `vision_pfd_service.extract()` catches the ImportError and returns
#     None, and parse_pdf silently falls back to pdfplumber (which reads
#     text but doesn't produce `vision_pages`). Without `vision_pages`,
#     every field mapper (PFD / P&ID / Vendor) skips the file and the
#     sync summary shows 0 updates applied.
#
#   * tesseract-ocr — the OCR fallback in parse_pdf calls
#     pytesseract.image_to_string. If a PDF is scanned (no embedded
#     text), pdfplumber returns empty strings and the code re-tries
#     via OCR — which needs both tesseract AND poppler (for rendering).
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
