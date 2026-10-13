#!/usr/bin/env bash
# 데모 GIF 생성: 실행 중인 KubeLens에 S1 질문을 보내는 과정을 프레임으로 찍어 docs/demo.gif로 만든다.
# 전제: make phase0 · make setup 완료, ANTHROPIC_API_KEY를 설정하고 make api 실행 중, ffmpeg 설치.
set -euo pipefail

FRAMES=frontend/e2e-results/demo-frames
command -v ffmpeg >/dev/null || { echo "❌ ffmpeg가 필요합니다 (brew install ffmpeg / apt install ffmpeg)" >&2; exit 1; }

rm -rf "$FRAMES"
(
  cd frontend
  [ -n "${PW_CHROMIUM_PATH:-}" ] || npx playwright install chromium
  DEMO=1 npx playwright test e2e/demo.spec.ts
)
ls "$FRAMES"/frame-*.png >/dev/null 2>&1 || { echo "❌ 프레임이 없습니다" >&2; exit 1; }

ffmpeg -y -loglevel error -framerate 3 -i "$FRAMES/frame-%04d.png" \
  -vf "scale=1100:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse" \
  docs/demo.gif
echo "✅ docs/demo.gif ($(du -h docs/demo.gif | cut -f1))"
