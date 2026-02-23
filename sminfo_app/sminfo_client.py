from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import Browser, Locator, Page, Playwright, sync_playwright

from .config import (
    BASE_URL,
    DEFAULT_BROWSER_CHANNEL,
    DEFAULT_META_PATH,
    DEFAULT_STATE_PATH,
    DEFAULT_TIMEOUT_MS,
    SEARCH_MENU_ID,
    SEARCH_PATH,
)
from .models import Candidate, SearchResult, TableData

_FINANCIAL_KEYWORDS = (
    "재무",
    "실적",
    "매출",
    "영업",
    "순이익",
    "당기",
    "자산",
    "부채",
    "회계",
    "연도",
)

_QUERY_INPUT_SELECTORS = (
    "input[name='cmQuery']",
    "input#cmQuery",
    "input[title*='검색']",
    "input[placeholder*='검색']",
    "input[type='search']",
    "input[type='text']",
)

_SEARCH_BUTTON_SELECTORS = (
    "button:has-text('검색')",
    "a:has-text('검색')",
    "input[type='submit'][value*='검색']",
    ".btn_search",
)

_LOGIN_ID_SELECTORS = (
    "#id",
    "input[name='id']",
    "#login_id",
    "input[name='login_id']",
)

_LOGIN_PW_SELECTORS = (
    "#pwd",
    "input[name='pwd']",
    "#login_password",
    "input[name='login_password']",
)

_LOGIN_BUTTON_SELECTORS = (
    "button:has-text('로그인')",
    "input[type='submit'][value*='로그인']",
    ".login_btn",
)

_CANDIDATE_TABLE_EXTRACT_SCRIPT = """
(keyword) => {
  const normalize = (s) => (s || "").replace(/\\s+/g, " ").trim();
  const kw = normalize(keyword).toLowerCase();
  const out = [];

  const guessTitle = (table) => {
    const caption = table.querySelector("caption");
    if (caption && normalize(caption.innerText)) return normalize(caption.innerText);

    let prev = table.previousElementSibling;
    while (prev) {
      const text = normalize(prev.innerText || prev.textContent || "");
      if (text && text.length <= 60) return text;
      prev = prev.previousElementSibling;
    }
    return "";
  };

  const push = (name, rowText, tableTitle) => {
    const n = normalize(name);
    if (!n) return;

    const row = normalize(rowText);
    const hay = (n + " " + row).toLowerCase();

    let score = 0;
    if (kw) {
      if (n.toLowerCase() === kw) score += 100;
      if (n.toLowerCase().includes(kw)) score += 60;
      if (row.toLowerCase().includes(kw)) score += 20;
    }

    out.push({
      name: n,
      row_text: row,
      table_title: normalize(tableTitle),
      match_score: score,
    });
  };

  const tables = Array.from(document.querySelectorAll("table"));
  tables.forEach((table) => {
    const title = guessTitle(table);
    const rows = Array.from(table.querySelectorAll("tbody tr, tr"));

    rows.forEach((row) => {
      const links = Array.from(row.querySelectorAll("a"));
      if (!links.length) return;
      const rowText = normalize(row.innerText || row.textContent || "");
      links.forEach((a) => {
        push(a.innerText || a.textContent || "", rowText, title);
      });
    });
  });

  return out;
}
"""

_GENERIC_LINK_CANDIDATE_SCRIPT = """
(keyword) => {
  const normalize = (s) => (s || "").replace(/\\s+/g, " ").trim();
  const kw = normalize(keyword).toLowerCase();
  const out = [];

  const blockedExact = new Set([
    "로그인", "회원가입", "홈", "사이트맵", "검색", "조회", "닫기",
    "메뉴", "다음", "이전", "상세보기", "more"
  ]);

  const links = Array.from(document.querySelectorAll("a"));
  links.forEach((a) => {
    const text = normalize(a.innerText || a.textContent || "");
    if (!text) return;
    if (text.length < 2 || text.length > 70) return;

    const lower = text.toLowerCase();
    if (blockedExact.has(lower)) return;

    let score = 0;
    if (kw) {
      if (lower === kw) score += 100;
      if (lower.includes(kw)) score += 60;
    }

    out.push({
      name: text,
      row_text: "",
      table_title: "",
      match_score: score,
    });
  });

  return out;
}
"""

_JS_SUBMIT_SEARCH_SCRIPT = """
({ keyword }) => {
  const form = document.forms["search"] || document.querySelector("form[name='search']");
  if (!form) return false;

  const setValue = (name, value) => {
    if (!form[name]) return;
    form[name].value = value;
  };

  setValue("cmQuery", keyword);
  setValue("cmQueryEncoding", encodeURIComponent(keyword));
  setValue("cmQueryOption", "00");
  setValue("cmPageNo", "1");
  setValue("mode", "");
  setValue("clickcontrol", "disable");
  setValue("htmlvalue", keyword);

  form.method = "post";
  form.target = "_self";
  form.action = "/gc/sf/GSF002R0.print";
  form.submit();
  return true;
}
"""

_TABLE_EXTRACT_SCRIPT = """
() => {
  const normalize = (s) => (s || "").replace(/\\s+/g, " ").trim();

  const guessTitle = (table) => {
    const caption = table.querySelector("caption");
    if (caption && normalize(caption.innerText)) return normalize(caption.innerText);

    const titleNode = table.closest("section, article, div")?.querySelector("h1, h2, h3, h4, strong");
    if (titleNode && normalize(titleNode.innerText)) return normalize(titleNode.innerText);

    let prev = table.previousElementSibling;
    while (prev) {
      const text = normalize(prev.innerText || prev.textContent || "");
      if (text && text.length <= 80) return text;
      prev = prev.previousElementSibling;
    }
    return "";
  };

  const getRows = (table) => {
    const bodyRows = Array.from(table.querySelectorAll("tbody tr"));
    const rows = bodyRows.length ? bodyRows : Array.from(table.querySelectorAll("tr"));

    return rows
      .map((tr) => Array.from(tr.querySelectorAll("th, td")).map((cell) => normalize(cell.innerText || cell.textContent || "")))
      .filter((row) => row.length && row.some((cell) => cell !== ""));
  };

  return Array.from(document.querySelectorAll("table"))
    .map((table) => {
      const headers = Array.from(table.querySelectorAll("thead th")).map((th) => normalize(th.innerText || th.textContent || "")).filter(Boolean);
      const rows = getRows(table);
      return {
        title: guessTitle(table),
        headers,
        rows,
      };
    })
    .filter((table) => table.rows.length > 0);
}
"""

_JS_CLICK_LINK_SCRIPT = """
(targetName) => {
  const normalize = (s) => (s || "").replace(/\\s+/g, " ").trim();
  const target = normalize(targetName);

  if (!target) return false;

  const links = Array.from(document.querySelectorAll("a"));
  const exact = links.find((link) => normalize(link.innerText || link.textContent || "") === target);
  if (exact) {
    exact.click();
    return true;
  }

  const contains = links.find((link) => normalize(link.innerText || link.textContent || "").includes(target));
  if (contains) {
    contains.click();
    return true;
  }

  return false;
}
"""


class NotLoggedInError(RuntimeError):
    """Raised when the session is missing or expired."""


class SearchError(RuntimeError):
    """Raised when search page interaction fails."""


class SminfoClient:
    def __init__(
        self,
        state_path: str | Path | None = None,
        meta_path: str | Path | None = None,
        headless: bool = True,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        browser_channel: str | None = DEFAULT_BROWSER_CHANNEL,
    ) -> None:
        self.state_path = Path(state_path) if state_path else DEFAULT_STATE_PATH
        self.meta_path = Path(meta_path) if meta_path else DEFAULT_META_PATH
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.browser_channel = (browser_channel or "").strip()

    def has_saved_session(self) -> bool:
        return self.state_path.exists()

    def get_saved_username(self) -> str | None:
        if not self.meta_path.exists():
            env_name = self._normalize_space(os.getenv("SMINFO_ID", ""))
            return env_name or None

        try:
            raw = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except Exception:
            env_name = self._normalize_space(os.getenv("SMINFO_ID", ""))
            return env_name or None

        username = self._normalize_space(str(raw.get("username", "")))
        if username:
            return username

        env_name = self._normalize_space(os.getenv("SMINFO_ID", ""))
        return env_name or None

    def get_login_status_text(self) -> str:
        username = self.get_saved_username()
        if username:
            return f'"{username}"으로 로그인 중'
        if self.has_saved_session():
            return "로그인 세션 사용 중"
        return "로그인 세션 없음"

    def login(
        self,
        username: str | None = None,
        password: str | None = None,
        headless: bool = False,
        manual_wait_seconds: int = 300,
    ) -> Path:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as pw:
            browser = self._launch_browser(pw, headless=headless)
            context = browser.new_context(locale="ko-KR")
            page = context.new_page()
            page.set_default_timeout(self.timeout_ms)

            self._open_search_page_via_post(page, query="")

            if self._is_login_page(page):
                if username and password:
                    self._perform_login(page, username, password)
                else:
                    print(
                        f"브라우저에서 로그인 완료 후 {manual_wait_seconds}초 이내에 자동으로 세션을 저장합니다."
                    )
                    ok = self._wait_until_logged_in(page, timeout_seconds=manual_wait_seconds)
                    if not ok:
                        browser.close()
                        raise NotLoggedInError(
                            "로그인 완료를 감지하지 못했습니다. 다시 시도하세요."
                        )

            if self._is_login_page(page):
                browser.close()
                raise NotLoggedInError("로그인에 실패했습니다. 아이디/비밀번호를 확인하세요.")

            detected_username = self._extract_logged_in_username(page)
            fallback_username = self._normalize_space(username or "")
            self._write_session_meta(detected_username or fallback_username or None)
            context.storage_state(path=str(self.state_path))
            browser.close()

        return self.state_path

    def search_company(
        self,
        query: str,
        company_name: str | None = None,
    ) -> SearchResult:
        query = self._normalize_space(query)
        if not query:
            raise ValueError("검색어를 입력하세요.")

        if not self.has_saved_session():
            raise NotLoggedInError(
                f"저장된 로그인 세션이 없습니다: {self.state_path}"
            )

        with sync_playwright() as pw:
            browser = self._launch_browser(pw, headless=self.headless)
            context = browser.new_context(
                storage_state=str(self.state_path), locale="ko-KR"
            )
            page = context.new_page()
            page.set_default_timeout(self.timeout_ms)

            self._open_search_page_via_post(page, query="")

            if self._is_login_page(page):
                browser.close()
                raise NotLoggedInError(
                    "로그인 세션이 만료되었습니다. login 명령으로 다시 로그인하세요."
                )

            self._submit_search_query(page, query)
            candidates = self._extract_candidates(page, query)
            if not candidates:
                result_count = self._read_result_count(page)
                if result_count == 0:
                    browser.close()
                    raise SearchError(
                        f"'{query}' 검색 결과가 0건입니다. (사이트 제한 또는 조회 조건 문제일 수 있습니다.)"
                    )

                browser.close()
                raise SearchError(
                    f"'{query}' 검색 결과 후보를 찾지 못했습니다. 로그인 세션을 갱신 후 다시 시도하세요."
                )

            selected = self._choose_candidate(candidates, company_name)
            tables: list[TableData] = []

            if selected:
                self._click_company_link(page, selected.name)
                self._wait_for_page_settle(page)
                tables = self._extract_relevant_tables(page)

            browser.close()

        return SearchResult(
            query=query,
            candidates=candidates,
            selected=selected,
            performance_tables=tables,
        )

    def _perform_login(self, page: Page, username: str, password: str) -> None:
        id_input = self._find_first_visible_locator(page, _LOGIN_ID_SELECTORS)
        pw_input = self._find_first_visible_locator(page, _LOGIN_PW_SELECTORS)

        if id_input is None or pw_input is None:
            raise SearchError("로그인 입력 필드를 찾지 못했습니다.")

        id_input.fill(username)
        pw_input.fill(password)

        submit = self._find_first_visible_locator(page, _LOGIN_BUTTON_SELECTORS)
        if submit:
            submit.click()
        else:
            pw_input.press("Enter")

        ok = self._wait_until_logged_in(page, timeout_seconds=30)
        if not ok:
            raise NotLoggedInError("로그인에 실패했습니다. 아이디/비밀번호를 확인하세요.")

    def _wait_until_logged_in(self, page: Page, timeout_seconds: int) -> bool:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            page.wait_for_timeout(1000)
            if not self._is_login_page(page):
                return True
        return False

    def _open_search_page_via_post(self, page: Page, query: str) -> None:
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.evaluate(
            """
            ({ baseUrl, path, menuId, keyword }) => {
              const form = document.createElement("form");
              form.method = "POST";
              form.action = `${baseUrl}${path}`;

              const add = (name, value) => {
                const input = document.createElement("input");
                input.type = "hidden";
                input.name = name;
                input.value = value;
                form.appendChild(input);
              };

              add("cmMenuId", menuId);
              add("cmQuery", keyword);
              add("mode", "fast");

              document.body.appendChild(form);
              form.submit();
            }
            """,
            {
                "baseUrl": BASE_URL,
                "path": SEARCH_PATH,
                "menuId": SEARCH_MENU_ID,
                "keyword": query,
            },
        )
        self._wait_for_page_settle(page)

    def _fill_query_and_submit(self, page: Page, query: str) -> bool:
        query_input = self._find_first_visible_locator(page, _QUERY_INPUT_SELECTORS)
        if query_input is None:
            return False

        query_input.fill(query)
        query_input.press("Enter")
        self._wait_for_page_settle(page)

        # Enter로 검색되지 않은 경우를 대비한 fallback 클릭
        if not self._extract_candidates(page, query):
            button = self._find_first_visible_locator(page, _SEARCH_BUTTON_SELECTORS)
            if button:
                button.click()
                self._wait_for_page_settle(page)
        return True

    def _submit_search_query(self, page: Page, query: str) -> bool:
        try:
            submitted = page.evaluate(_JS_SUBMIT_SEARCH_SCRIPT, {"keyword": query})
        except Exception:
            submitted = False

        if submitted:
            self._wait_for_page_settle(page)
            return True

        # 검색 스크립트 진입이 실패하면 초기 POST 방식으로 재시도
        self._open_search_page_via_post(page, query=query)
        return True

    def _extract_candidates(self, page: Page, query: str) -> list[Candidate]:
        deduped: dict[tuple[str, str], Candidate] = {}

        self._merge_candidate_rows(
            page=page,
            script=_CANDIDATE_TABLE_EXTRACT_SCRIPT,
            query=query,
            deduped=deduped,
        )

        if not deduped:
            self._merge_candidate_rows(
                page=page,
                script=_GENERIC_LINK_CANDIDATE_SCRIPT,
                query=query,
                deduped=deduped,
            )

        ordered = sorted(
            deduped.values(),
            key=lambda c: (c.match_score, len(c.row_text), -len(c.name), c.name),
            reverse=True,
        )

        if query:
            matched = [candidate for candidate in ordered if candidate.match_score > 0]
            if matched:
                return matched[:50]
            return []

        return ordered[:50]

    def _merge_candidate_rows(
        self,
        page: Page,
        script: str,
        query: str,
        deduped: dict[tuple[str, str], Candidate],
    ) -> None:
        for frame in page.frames:
            try:
                raw = frame.evaluate(script, query)
            except Exception:
                continue

            if not isinstance(raw, list):
                continue

            for item in raw:
                name = self._normalize_space(str(item.get("name", "")))
                if not name:
                    continue

                row_text = self._normalize_space(str(item.get("row_text", "")))
                table_title = self._normalize_space(str(item.get("table_title", "")))
                score = int(item.get("match_score", 0))

                key = (name, row_text)
                prev = deduped.get(key)
                candidate = Candidate(
                    name=name,
                    row_text=row_text,
                    table_title=table_title,
                    match_score=score,
                )
                if prev is None or candidate.match_score > prev.match_score:
                    deduped[key] = candidate

    def _choose_candidate(
        self,
        candidates: list[Candidate],
        company_name: str | None,
    ) -> Candidate | None:
        if not candidates:
            return None

        if company_name:
            target = self._normalize_space(company_name).lower()
            exact = [c for c in candidates if c.name.lower() == target]
            if exact:
                return sorted(exact, key=lambda c: c.match_score, reverse=True)[0]

            contains = [c for c in candidates if target in c.name.lower()]
            if contains:
                return sorted(contains, key=lambda c: c.match_score, reverse=True)[0]

            raise SearchError(f"'{company_name}' 후보를 찾지 못했습니다.")

        return candidates[0]

    def _click_company_link(self, page: Page, company_name: str) -> None:
        target = self._normalize_space(company_name)

        for frame in page.frames:
            try:
                exact = frame.get_by_role("link", name=target, exact=True)
                if exact.count() > 0:
                    exact.first.click()
                    self._wait_for_page_settle(page)
                    return
            except Exception:
                pass

        for frame in page.frames:
            try:
                partial = frame.locator("a").filter(has_text=target)
                if partial.count() > 0:
                    partial.first.click()
                    self._wait_for_page_settle(page)
                    return
            except Exception:
                pass

        for frame in page.frames:
            try:
                clicked = frame.evaluate(_JS_CLICK_LINK_SCRIPT, target)
                if clicked:
                    self._wait_for_page_settle(page)
                    return
            except Exception:
                continue

        raise SearchError(f"회사 링크를 클릭하지 못했습니다: {company_name}")

    def _extract_relevant_tables(self, page: Page) -> list[TableData]:
        all_tables: list[TableData] = []

        for frame in page.frames:
            try:
                raw_tables = frame.evaluate(_TABLE_EXTRACT_SCRIPT)
            except Exception:
                continue

            if not isinstance(raw_tables, list):
                continue

            for item in raw_tables:
                rows = item.get("rows", [])
                if not rows:
                    continue

                table = TableData(
                    title=self._normalize_space(str(item.get("title", ""))),
                    headers=[
                        self._normalize_space(str(header))
                        for header in item.get("headers", [])
                        if self._normalize_space(str(header))
                    ],
                    rows=[
                        [self._normalize_space(str(cell)) for cell in row]
                        for row in rows
                        if any(self._normalize_space(str(cell)) for cell in row)
                    ],
                    frame_url=frame.url,
                )
                if table.rows:
                    all_tables.append(table)

        if not all_tables:
            return []

        scored = sorted(
            ((self._score_table(table), table) for table in all_tables),
            key=lambda pair: pair[0],
            reverse=True,
        )
        relevant = [table for score, table in scored if score >= 4]

        if relevant:
            return relevant[:10]

        # 키워드를 못 찾은 경우라도 첫 3개 표는 반환
        return [table for _, table in scored[:3]]

    def _score_table(self, table: TableData) -> int:
        blob_parts = [table.title, *table.headers]
        for row in table.rows[:20]:
            blob_parts.extend(row)

        blob = " ".join(blob_parts)
        score = 0

        if re.search(r"20\\d{2}", blob):
            score += 3

        for keyword in _FINANCIAL_KEYWORDS:
            if keyword in blob:
                score += 2

        if len(table.headers) >= 2:
            score += 1

        if len(table.rows) >= 2:
            score += 1

        return score

    def _find_first_visible_locator(
        self,
        page: Page,
        selectors: tuple[str, ...],
    ) -> Locator | None:
        for frame in page.frames:
            for selector in selectors:
                locator = frame.locator(selector).first
                try:
                    if locator.count() == 0:
                        continue
                    if locator.is_visible():
                        return locator
                except Exception:
                    continue
        return None

    def _is_login_page(self, page: Page) -> bool:
        url = page.url or ""
        if "CMM004R0" in url or "CMM004R1" in url:
            return True

        id_input = self._find_first_visible_locator(page, _LOGIN_ID_SELECTORS)
        pw_input = self._find_first_visible_locator(page, _LOGIN_PW_SELECTORS)
        return id_input is not None and pw_input is not None

    def _wait_for_page_settle(self, page: Page) -> None:
        try:
            page.wait_for_load_state("networkidle", timeout=self.timeout_ms)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(700)

    def _extract_logged_in_username(self, page: Page) -> str | None:
        script = """
        () => {
          const normalize = (s) => (s || "").replace(/\\s+/g, " ").trim();
          const blocked = new Set(["로그인", "로그아웃", "회원가입", "나의정보"]);
          const out = [];

          const push = (value) => {
            const v = normalize(value);
            if (!v) return;
            if (v.length < 2 || v.length > 60) return;
            if (blocked.has(v)) return;
            out.push(v);
          };

          document.querySelectorAll("input[name='cmId'], input[name='id']").forEach((input) => {
            push(input.value || input.getAttribute("value") || "");
          });

          const profileNode = document.querySelector(".user, .my_info, .member, .login_info");
          if (profileNode) push(profileNode.textContent || "");

          const anchors = Array.from(document.querySelectorAll("a"));
          anchors.forEach((a) => {
            const t = normalize(a.innerText || a.textContent || "");
            if (t.endsWith("님")) push(t.replace(/님$/, ""));
          });

          const unique = Array.from(new Set(out));
          return unique.length ? unique[0] : null;
        }
        """
        for frame in page.frames:
            try:
                raw = frame.evaluate(script)
            except Exception:
                continue
            username = self._normalize_space(str(raw or ""))
            if username:
                return username
        return None

    def _write_session_meta(self, username: str | None) -> None:
        self.meta_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"username": self._normalize_space(username or ""), "saved_at": int(time.time())}
        self.meta_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _read_result_count(self, page: Page) -> int | None:
        try:
            raw = page.main_frame.evaluate(
                """
                () => {
                  const text = (document.body && document.body.innerText) || "";
                  const match = text.match(/검색결과\\s*([0-9,]+)\\s*건/);
                  return match ? match[1] : null;
                }
                """
            )
        except Exception:
            return None

        if raw is None:
            return None
        try:
            return int(str(raw).replace(",", "").strip())
        except ValueError:
            return None

    def _launch_browser(self, pw: Playwright, headless: bool) -> Browser:
        channel = self.browser_channel.lower()
        if not channel or channel == "chromium":
            return pw.chromium.launch(headless=headless)

        try:
            return pw.chromium.launch(headless=headless, channel=channel)
        except PlaywrightError as exc:
            print(
                f"경고: 브라우저 채널 '{channel}' 실행 실패. Chromium으로 전환합니다. ({exc})"
            )
            return pw.chromium.launch(headless=headless)

    @staticmethod
    def _normalize_space(value: str) -> str:
        return re.sub(r"\\s+", " ", value or "").strip()
