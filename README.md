# Korean SME Status Lookup

`https://sminfo.mss.go.kr/`의 회사 검색/조회 흐름을 자동화한 CLI + Web 앱입니다.

- CLI: 로그인 세션 저장, 회사 검색, 상세 표 추출(JSON)
- Web: 검색어 입력 후 후보 선택 + 상세 표 확인

## 1) Install

```bash
cd /Users/jeonghoon/Documents/CodexApp/korean-sme-status-lookup
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## 2) Login Session

수동 로그인(권장):

```bash
python -m sminfo_app.cli login
```

- 브라우저가 열리면 직접 로그인
- 로그인 성공이 감지되면 `.data/storage_state.json`에 세션 저장

자동 로그인(선택):

```bash
python -m sminfo_app.cli login --id "YOUR_ID" --password "YOUR_PASSWORD"
```

또는 환경변수:

```bash
export SMINFO_ID="YOUR_ID"
export SMINFO_PASSWORD="YOUR_PASSWORD"
python -m sminfo_app.cli login
```

## 3) CLI Search

```bash
python -m sminfo_app.cli search "회사명"
```

특정 후보를 정확히 선택:

```bash
python -m sminfo_app.cli search "회사명" --company "정확한회사명"
```

JSON 저장:

```bash
python -m sminfo_app.cli search "회사명" --json ./out/result.json
```

## 4) Run Web App

```bash
python -m sminfo_app.web
```

브라우저에서 `http://127.0.0.1:5050` 접속.

## Optional Environment Variables

- `SMINFO_STATE_PATH`: 세션 파일 경로 변경
- `SMINFO_TIMEOUT_MS`: Playwright 타임아웃(ms)

예시:

```bash
export SMINFO_STATE_PATH="/tmp/sminfo_state.json"
export SMINFO_TIMEOUT_MS="60000"
```

## Notes

- 대상 시스템 특성상 브라우저 상의 정상 POST 진입 흐름을 재현합니다.
- 페이지/셀렉터 구조가 바뀌면 일부 로직(검색 입력, 결과 클릭)이 수정될 수 있습니다.
- 본 코드는 공식 API가 아닌 웹 UI 자동화 방식입니다. 서비스 이용약관/정책을 준수해서 사용하세요.
