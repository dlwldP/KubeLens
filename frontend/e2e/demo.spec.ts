import { expect, test, type Page } from '@playwright/test'

// 데모 GIF 녹화용 (실제 LLM 필요). infra/demo-gif.sh 가 DEMO=1 로 실행하고,
// 여기서 찍은 프레임(e2e-results/demo-frames/*.png)을 ffmpeg로 GIF로 만든다.
test.skip(!process.env.DEMO, 'DEMO=1 일 때만 실행')
test.setTimeout(300_000)

const FRAMES = 'e2e-results/demo-frames'
let frame = 0

async function snap(page: Page, repeat = 1) {
  for (let i = 0; i < repeat; i++) {
    frame += 1
    await page.screenshot({ path: `${FRAMES}/frame-${String(frame).padStart(4, '0')}.png` })
  }
}

test('demo: S1 CrashLoop 진단', async ({ page }) => {
  await page.goto('/')
  const setup = page.getByRole('heading', { name: '클러스터 설정' })
  if (await setup.isVisible().catch(() => false)) {
    await page.getByRole('button', { name: '연결 테스트' }).click()
    await expect(page.getByTestId('connection-status')).toContainText('연결 성공')
    await snap(page, 3)
    await page.getByRole('button', { name: '저장하고 시작' }).click()
  }
  await page.getByRole('button', { name: '+ 새 진단' }).click()
  await snap(page, 2)

  const input = page.getByTestId('question-input')
  const question = 'kubelens-chaos 네임스페이스의 payment-api가 계속 재시작돼. 왜 그래?'
  for (let i = 4; i <= question.length; i += 4) {
    await input.fill(question.slice(0, i))
    await snap(page)
  }
  await input.fill(question)
  await snap(page, 2)
  await page.getByRole('button', { name: '질문' }).click()

  // 진단이 끝날 때까지 실시간 트레이스를 0.7초 간격으로 기록
  const card = page.getByTestId('diagnosis-card')
  for (let i = 0; i < 400 && !(await card.isVisible()); i++) {
    await snap(page)
    await page.waitForTimeout(700)
  }
  await expect(card).toBeVisible()
  await snap(page, 8)

  await page.getByTestId('evidence-link').first().click()
  await expect(page.getByTestId('evidence-output')).toBeVisible()
  await snap(page, 10)
  await page.keyboard.press('Escape')
  await snap(page, 4)
})
