import { describe, expect, it } from 'vitest'
import { parseSSE } from './api'

describe('parseSSE', () => {
  it('완성된 이벤트만 꺼내고 덜 받은 조각은 rest로 남긴다', () => {
    const { events, rest } = parseSSE(
      'event: thinking\ndata: {"message":"계획"}\n\nevent: tool_start\ndata: {"id":"a"',
    )
    expect(events).toEqual([{ type: 'thinking', data: { message: '계획' } }])
    expect(rest).toBe('event: tool_start\ndata: {"id":"a"')
  })

  it('조각으로 나뉘어 들어와도 이어 붙이면 같은 결과', () => {
    const full =
      'event: tool_start\ndata: {"id":"a","ref":"tc_1","tool_name":"get_nodes","args":{}}\n\n' +
      'event: final\ndata: {"message_id":3,"valid":true,"diagnosis":null,"validation_errors":[],"usage":{}}\n\n'
    const seen: string[] = []
    let buffer = ''
    for (let i = 0; i < full.length; i += 7) {
      const parsed = parseSSE(buffer + full.slice(i, i + 7))
      buffer = parsed.rest
      seen.push(...parsed.events.map((e) => e.type))
    }
    expect(seen).toEqual(['tool_start', 'final'])
    expect(buffer).toBe('')
  })

  it('CRLF 줄바꿈과 여러 줄 data를 처리한다', () => {
    const { events } = parseSSE('event: error\r\ndata: {"message":\r\ndata: "x"}\r\n\r\n')
    expect(events).toEqual([{ type: 'error', data: { message: 'x' } }])
  })

  it('한국어 같은 멀티바이트 문자를 그대로 보존한다', () => {
    const { events } = parseSSE('event: thinking\ndata: {"message":"증거 수집 중 (남은 도구 호출 7회)"}\n\n')
    expect(events[0]).toEqual({ type: 'thinking', data: { message: '증거 수집 중 (남은 도구 호출 7회)' } })
  })
})
