# TradePilot — 매매 판단 복기 & 전략 개선

매수한 거래뿐 아니라 **매수하지 않은 판단까지** 기록하고, 장 마감 후 그 판단의 근거가 옳았는지
채점합니다. 결과를 규칙별로 누적해서 다음 거래일에 쓸 개선된 전략을 만듭니다.

주문은 내지 않습니다. 판단 기록, 채점, 전략 파일 갱신만 합니다.

```
 장중                 장 마감 후 `tradepilot review`                         다음 거래일
 ┌─────────────┐     ┌────────────┐  ┌────────────┐  ┌──────────────┐     ┌──────────────┐
 │ 판단 기록    │────▶│ 결과 채점   │─▶│ 규칙별 누적 │─▶│ 전략 갱신     │────▶│ `brief`       │
 │ buy/sell/   │     │ 당일·1일·5일│  │ 적중률·수익 │  │ (상한 있는    │     │ active.yaml  │
 │ hold/pass   │     │ good/bad   │  │ + Claude    │  │  가중치 조정) │     │              │
 │ + 근거 규칙  │     │            │  │  근거 평가  │  │              │     │              │
 └─────────────┘     └────────────┘  └────────────┘  └──────────────┘     └──────────────┘
```

## 핵심 개념

- **판단(decision)**: `buy`, `sell`, `hold`, `pass`(매수 안 함) 중 하나. 판단마다 근거가 된 전략 규칙 id
  (`--reasons`), 반대 근거(`--against`), 그때 본 지표 값(`--signal`)을 남깁니다.
- **채점**: 판단 가격 대비 당일 종가(0일), 1일, 5일 후 수익률로 good / bad / neutral을 매깁니다.
  - buy, hold: 오르면 good, 내리면 bad
  - pass: 올랐으면 bad(놓친 기회), 내렸으면 good(손실 회피)
  - sell: 이후 올랐으면 bad, 내렸으면 good
  - 기준(기본 ±1%)보다 작은 움직임은 neutral
- **근거 평가 (Claude)**: 결과와 따로 판단합니다. 규칙대로 했는데 손실이면 `bad_luck`, 규칙을 어겼는데
  수익이면 `luck`으로 구분해서 운과 실력을 섞지 않습니다.
- **전략 갱신**: 규칙별 적중률(주 기준 5일)로 가중치를 조정합니다. 과적합을 막기 위해 다음 제한을 둡니다.
  - 표본이 `min_samples`(기본 20)건 이상 쌓인 규칙만 조정합니다.
  - 한 번에 가중치는 최대 ±25%까지만 바꾸고, 범위는 0.1–3.0입니다.
  - 적중률이 계속 낮은 규칙은 삭제하지 않고 비활성화합니다.
  - Claude가 제안한 파라미터 변경은 기존 규칙의 숫자만 같은 한도 안에서 바꿉니다. 새 규칙은 아이디어로만 보여줍니다.
- **버전 관리**: 전략은 매번 새 버전으로 저장되고 바뀐 이유가 남습니다. 승인된 버전이
  `strategies/active.yaml`로 내보내져서 매매 도구가 다음 거래일에 읽을 수 있습니다.

## 빠른 시작

```bash
cd tradepilot
pip install -e .            # Yahoo 시세를 쓰려면: pip install -e ".[yfinance]"
export ANTHROPIC_API_KEY=sk-ant-...

tradepilot init             # config.yaml, strategy.yaml 생성
# strategy.yaml의 규칙을 실제로 쓰는 매매 규칙으로 바꾸세요 (예시는 스윙 매매).
```

### 1. 장중: 판단 기록

```bash
# 매수
tradepilot log 005930 buy --price 71200 --reasons breakout_20d,volume_surge \
  --signal vol_ratio=2.4 --signal gap_ma20_pct=4.1 --confidence 0.7 --note "20일 고가 돌파"

# 매수하지 않은 판단도 꼭 기록
tradepilot log 000660 pass --reasons overextended --against breakout_20d \
  --signal gap_ma20_pct=13.2 --note "돌파했지만 이격 과다"
```

또는 CSV로 한 번에 넣습니다:

```csv
date,time,symbol,action,price,reasons,against,confidence,note,signal_rsi
2026-10-14,09:05,005930,buy,71200,breakout_20d;volume_surge,,0.7,돌파,64
2026-10-14,10:30,000660,pass,,overextended,breakout_20d,,이격 과다,78
```

```bash
tradepilot import decisions.csv
```

### 2. 장 마감 후: 리뷰

```bash
tradepilot review
```

- 결과가 나온 판단을 모두 채점합니다. 오늘 판단은 당일 종가, 과거 판단은 1일과 5일 후입니다.
- 규칙별 누적 성과를 계산하고, Claude가 오늘 판단과 결과가 확정된 과거 판단의 근거를 평가합니다.
- `journal/2026-10-14.md`에 리포트를 쓰고, 전략을 갱신해 `strategies/active.yaml`을 다시 씁니다.

### 3. 다음 거래일: 브리핑

```bash
tradepilot brief           # 활성 규칙(가중치 순), 이번에 바뀐 점, 최근 교훈
tradepilot stats           # 규칙별 n / good / bad / 적중률 / 평균 수익률
```

### 4. 전략 연구용 데이터 누적

```bash
tradepilot template          # 오늘 판단을 적을 CSV 양식 (헤더만; 예시는 화면에 출력)
tradepilot import decisions-2026-10-15.csv
tradepilot research          # research/dataset.csv + research/report-날짜.md
tradepilot export --out my.csv
```

`research/dataset.csv`는 판단 1건이 1행인 연구용 데이터셋입니다. pandas, 엑셀, 백테스터에 바로 넣을 수 있습니다.
- 판단 정보: 날짜, 요일, 시각, 종목, 판단, 가격, 확신도, 전략 버전
- 근거: 근거 규칙과 반대 규칙
- 지표: 모든 `signal_*` 값
- 결과: 0·1·5일 수익률과 good/bad 라벨, 기간 중 최대 상승과 최대 하락
- Claude 평가: 근거 품질, 결과의 원인(실력/운)

`research` 리포트는 다음 전략을 고칠 때 볼 질문에 답합니다.
1. 판단 유형별 성과. pass의 평균 수익률이 buy보다 높으면 필터가 너무 엄격하다는 신호입니다.
2. 신호 구간별 성과. 예: `vol_ratio`가 어느 구간일 때 수익이 났는지
3. 규칙 조합별 성과
4. 가장 비쌌던 pass(놓친 기회)
5. 근거 품질과 결과. 규칙대로 했을 때 실제로 성과가 더 좋았는지
6. 확신도 보정

결과가 확정된 판단이 적으면 분석을 보류하고 기록을 계속하라고 안내합니다.

### 전략 버전 관리

```bash
tradepilot strategy list
tradepilot strategy diff 3       # v3에서 무엇이 왜 바뀌었는지
tradepilot strategy approve 3    # 승인 대기 중인 변경 적용
tradepilot strategy reject 3
tradepilot strategy load --file strategy.yaml   # 직접 고친 전략을 새 버전으로
```

## 자동 승인 (`auto_approve`)

| 값 | 동작 |
|---|---|
| `none` | 모든 변경이 `strategy approve`를 기다립니다. |
| `stats` (기본) | 통계 기반 가중치 조정(상한 있음)은 다음 거래일 전략에 자동 반영됩니다. Claude 제안은 승인 대기합니다. |
| `all` | Claude의 파라미터 제안까지 자동 반영합니다. |

## 시세 데이터

- `csv` (기본): `prices/<종목>.csv`에 `date,open,high,low,close,volume`을 둡니다. 한글 헤더
  (일자, 시가, 고가, 저가, 종가, 거래량)도 읽고, HTS에서 내보낸 파일을 그대로 써도 됩니다.
- `yfinance`: Yahoo Finance에서 가져옵니다. 국내 종목은 `005930.KS`(코스피), `035720.KQ`(코스닥)처럼 기록하세요.

## 매일 자동 실행

```cron
# 평일 16:10 KST 장 마감 후 리뷰
10 16 * * 1-5  cd ~/trading && tradepilot review >> review.log 2>&1
```

## 주의

- 하루 결과는 노이즈입니다. 규칙 조정은 누적 표본으로만 하도록 만들었지만, 표본이 적은 초기에는
  가중치가 거의 움직이지 않는 것이 정상입니다.
- 채점은 일봉 기준이고 수수료와 슬리피지는 `win_threshold_pct`에 포함해서 생각합니다.
- 투자 판단과 책임은 사용자에게 있습니다. 이 도구는 기록과 복기를 돕습니다.
