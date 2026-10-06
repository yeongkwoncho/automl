# JobPilot — AI 채용 검색 & 자동 지원

글로벌 채용 사이트에서 내 커리어와 희망 조건에 맞는 포지션을 찾아서, Claude로 적합도를
평가하고, 포지션마다 커버레터와 답변을 맞춤 작성한 뒤 지원서를 채워 제출하는 CLI 서비스입니다.

```
 ┌──────────┐   ┌───────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
 │ 1 검색    │──▶│ 2 1차 필터 │──▶│ 3 Claude 평가 │──▶│ 4 맞춤 작성   │──▶│ 5 지원        │
 │ 공개 API  │   │ 규칙 기반  │   │ 0–100점       │   │ 커버레터/답변 │   │ dry_run /     │
 │ 8개 소스  │   │ (LLM 없음) │   │ 강점·갭·결격  │   │ (사실만 사용) │   │ review / auto │
 └──────────┘   └───────────┘   └──────────────┘   └──────────────┘   └──────────────┘
                              SQLite: 중복 제거 · 점수 · 지원 이력 · 일일 한도
```

## 지원하는 소스

| 소스 | 범위 | 인증 | 자동 제출 |
|---|---|---|---|
| Greenhouse 채용 보드 | 회사별 (Stripe, Airbnb, Anthropic …) | 없음 | ✅ |
| Lever 채용 보드 | 회사별 | 없음 | ✅ |
| Ashby 채용 보드 | 회사별 (OpenAI, Notion …) | 없음 | ✅ |
| Recruitee 채용 보드 | 회사별, 유럽에 많음 (Invisix …) | 없음 | review 모드만 |
| Workday 채용 사이트 | 회사별, 대기업 (KLA, Micron …) | 없음 | 수동 패킷 (Workday 계정 필요) |
| Remotive | 글로벌 원격 | 없음 | ATS 링크일 때만 |
| RemoteOK | 글로벌 원격 | 없음 | ATS 링크일 때만 |
| Arbeitnow | 유럽 | 없음 | 수동 패킷 |
| Adzuna | 미국·영국·독일·싱가포르 등 약 20개국 | 무료 키 | 수동 패킷 |
| 사람인 | 한국 | API 키 | 수동 패킷 |

Workday는 공식 공개 API가 없어서, 채용 사이트가 브라우저에서 쓰는 JSON 경로를 읽습니다.
로그인이 필요 없는 공개 공고만 읽고, 설정에 넣은 사이트만 요청 간격을 두고 조회합니다.
Workday 쪽에서 경로를 바꾸면 동작하지 않을 수 있습니다.

**LinkedIn, Indeed, Glassdoor는 일부러 넣지 않았습니다.** 이용약관에서 스크래핑과 자동 지원을
금지하고 있어서, 계정이 정지될 수 있습니다. 대신 이 사이트들에 올라오는 공고 대부분은 원래
회사의 Greenhouse/Lever/Ashby 보드에도 올라오니, 관심 있는 회사의 보드를 `sources`에 추가하세요.

## 빠른 시작

```bash
cd jobpilot
pip install -e ".[browser]"        # 폼 자동 입력에는 Playwright가 필요
playwright install chromium        # 브라우저가 없으면 설치

export ANTHROPIC_API_KEY=sk-ant-...

jobpilot init                      # config.yaml, profile.yaml 생성
jobpilot import-resume resume.pdf --force   # 이력서에서 profile.yaml 추출 (내용 꼭 확인)
# config.yaml에서 preferences와 sources 수정

jobpilot check-boards              # 설정한 회사 보드 slug가 맞는지 확인
jobpilot search                    # 공고 수집 + 1차 필터
jobpilot match                     # Claude로 적합도 점수
jobpilot apply                     # 기본값 dry_run: applications/ 폴더에 지원 패킷만 생성
jobpilot status                    # 이력 보기
```

`jobpilot run`은 search → match → apply를 한 번에 실행합니다. 매일 돌리려면 cron에 등록하면 됩니다:

```cron
0 9 * * *  cd ~/jobs && jobpilot run >> jobpilot.log 2>&1
```

## 지원 모드 (`apply.mode`)

| 모드 | 동작 |
|---|---|
| `dry_run` (기본) | 아무것도 보내지 않습니다. 포지션마다 `applications/<날짜-회사-직무>/`에 `APPLICATION.md`(평가, 커버레터, 답변), `cover_letter.txt`, `data.json`을 만듭니다. |
| `review` | 브라우저를 띄워 폼을 채우고, 사용자가 브라우저에서 확인하거나 고친 뒤 터미널에서 `[s]ubmit / [k]skip / [m]anual`을 고릅니다. |
| `auto` | 사람 확인 없이 제출합니다. 단, **Greenhouse/Lever/Ashby 호스트에서만**, **필수 항목이 전부 채워졌고**, **CAPTCHA가 없을 때만** 제출합니다. 조건이 하나라도 안 맞으면 `needs_manual`로 남깁니다. |

`jobpilot apply --mode review`처럼 한 번만 모드를 바꿀 수도 있습니다. dry_run으로 만든 패킷은
나중에 review/auto로 돌릴 때 그대로 재사용하고, `cover_letter.txt`를 직접 고쳤다면 고친 내용이 반영됩니다.
직접 지원한 건은 `jobpilot mark <uid> submitted`로 기록하세요.

### 안전장치

- **사실만 사용**: 프롬프트에서 프로필에 없는 경력·학위·스킬·비자 상태를 만들어내지 못하게 막습니다.
  프로필로 답할 수 없는 질문은 `needs_human`이 되고, auto 모드에서는 제출하지 않습니다.
- **EEO/인구통계 질문**(성별, 인종, 장애, 병역 등)은 LLM이 답하지 않습니다. `profile.eeo`에 적은
  값을 쓰거나, 없으면 "Decline to self-identify"를 고릅니다.
- **동의 체크박스, 라디오 버튼**은 자동으로 체크하지 않습니다. 필수 항목이면 사람이 처리해야 합니다.
- **CAPTCHA는 풀거나 우회하지 않습니다.**
- 일일 제출 한도(`max_per_day`, 기본 10), 같은 회사 재지원 쿨다운(`company_cooldown_days`, 기본 30일),
  제출 간격(`delay_seconds`), 최소 점수(`min_score`, 기본 75).
- 여러 보드에 중복으로 올라온 같은 공고는 한 번만 처리합니다.

## Claude 사용 방식

- 기본 모델은 `claude-opus-5`이고 `llm.model`로 바꿀 수 있습니다. 점수 평가는 `effort: medium`,
  맞춤 작성은 `effort: high`를 씁니다.
- 결과는 Pydantic 스키마로 받습니다(structured outputs). 검증된 객체라서 파싱이 깨지지 않습니다.
- 프로필은 시스템 프롬프트에 넣고 **프롬프트 캐싱**을 걸어 두었습니다. 공고를 수백 개 평가할 때
  입력 토큰 비용이 크게 줄어듭니다.
- **서버측 refusal fallback**(`fallbacks: "default"`)을 켜 두었습니다. 모델이 요청을 거절하면 API가
  권장 대체 모델로 다시 실행합니다. 원하지 않으면 `jobpilot/llm.py`에서 빼면 됩니다.

## 구조

```
jobpilot/
  sources/ats.py        Greenhouse · Lever · Ashby · Recruitee
  sources/workday.py    Workday 채용 사이트
  sources/boards.py     Remotive · RemoteOK · Arbeitnow · Adzuna · 사람인
  filters.py            규칙 기반 1차 필터 (직무명, 제외 키워드, 지역/원격, 게시일, 연봉)
  agent.py              이력서 추출 · 적합도 평가 · 맞춤 작성 (Claude)
  appliers/formfill.py  폼 필드 → 값 매핑 (브라우저 없이 테스트 가능)
  appliers/browser.py   Playwright로 폼 입력 · 제출 · 스크린샷
  pipeline.py           전체 흐름과 한도 적용
  store.py              SQLite
  cli.py
```

새 소스를 추가하려면 `JobSource`를 상속해 `fetch()`에서 `Job`을 yield하고 `sources/__init__.py`에 등록하면 됩니다.

## 테스트

```bash
pip install -e ".[dev]"
pytest
```

HTTP는 모두 mock이고, 브라우저 테스트는 로컬 HTML 폼으로 실제 Chromium을 띄워 입력과 제출을 확인합니다.
Chromium 경로가 다르면 `JOBPILOT_CHROMIUM`을 지정하세요.

## 한계

- ATS 폼 구조는 수시로 바뀝니다. 라벨 텍스트로 필드를 찾기 때문에 id/class 변경에는 강하지만,
  커스텀 드롭다운(React select 등)은 채우지 못할 수 있습니다. 그런 필드는 review 모드에서 직접 채우세요.
- 애그리게이터(Adzuna, 사람인 등) 공고는 외부 사이트로 연결되므로 기본적으로 수동 지원 패킷만 만듭니다.
- 소스 API의 응답 필드는 각 서비스가 공개한 문서를 기준으로 매핑했습니다. 서비스가 스키마를 바꾸면 수정이 필요합니다.
