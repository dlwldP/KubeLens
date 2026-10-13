import { expect, test } from '@playwright/test'

// 클러스터 설정 → 질문 → 실시간 트레이스 → 진단 카드 → 증거 모달 → 새로고침 후 히스토리
// (CI에서는 대본 LLM으로 돌리므로 진단 내용이 아니라 화면 흐름을 검증한다)
test('diagnosis flow', async ({ page }) => {
  await page.goto('/')

  await expect(page.getByRole('heading', { name: '클러스터 설정' })).toBeVisible()
  await expect(page.getByTestId('context-select').locator('option')).not.toHaveCount(0)
  await page.getByRole('button', { name: '연결 테스트' }).click()
  await expect(page.getByTestId('connection-status')).toContainText('연결 성공')
  await page.getByRole('button', { name: '저장하고 시작' }).click()

  const question = '지금 클러스터에 문제 있는 거 있어?'
  await page.getByTestId('question-input').fill(question)
  await page.getByRole('button', { name: '질문' }).click()

  await expect(page.getByTestId('diagnosis-card')).toBeVisible()
  await expect(page.getByTestId('trace-item')).toHaveCount(2)
  await expect(page.getByTestId('trace-item').first()).toContainText('list_pods')
  await page.screenshot({ path: 'e2e-results/main.png' })

  await expect(page.getByTestId('evidence-link').first()).toHaveText('증거 tc_1')
  await page.getByTestId('evidence-link').first().click()
  await expect(page.getByTestId('evidence-modal')).toBeVisible()
  await expect(page.getByTestId('evidence-output')).toContainText('STATUS')
  await page.screenshot({ path: 'e2e-results/evidence.png' })
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('evidence-modal')).toBeHidden()

  // F6: 새로고침 후에도 세션 유지
  await page.reload()
  await page.getByTestId('session-list').getByText(question).click()
  await expect(page.getByTestId('diagnosis-card')).toBeVisible()
  await expect(page.getByTestId('trace-item')).toHaveCount(2)
})
