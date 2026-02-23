from __future__ import annotations

import re
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import Locator, Page, sync_playwright

from .config import BASE_URL, DEFAULT_STATE_PATH, DEFAULT_TIMEOUT_MS, SEARCH_MENU_ID, SEARCH_PATH
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
    if (kw && !hay.includes(kw)) return;

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
        headless: bool = True,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> None:
        self.state_path = Path(state_path) if state_path else DEFAULT_STATE_PATH
        self.headless = headless
        self.timeout_ms = timeout_ms

    def has_saved_session(self) -> bool:
        return self.state_path.exists()

    def login(
        self,
        username: str | None = None,
        password: str | None = None,
        headless: bool = False,
        manual_wait_seconds: int = 300,
    ) -> Path:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=headless)
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
            browser = pw.chromium.launch(headless=self.headless)
            context = browser.new_context(
                storage_state=str(self.state_path), locale="ko-KR"
            )
            page = context.new_page()
            page.set_default_timeout(self.timeout_ms)

            self._open_search_page_via_post(page, query=query)

            if self._is_login_page(page):
                browser.close()
                raise NotLoggedInError(
                    "로그인 세션이 만료되었습니다. login 명령으로 다시 로그인하세요."
                )

            candidates = self._extract_candidates(page, query)
            if not candidates:
                self._fill_query_and_submit(page, query)
                candidates = self._extract_candidates(page, query)

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

    def _fill_query_and_submit(self, page: Page, query: str) -> None:
        query_input = self._find_first_visible_locator(page, _QUERY_INPUT_SELECTORS)
        if query_input is None:
            raise SearchError("검색 입력 필드를 찾지 못했습니다.")

        query_input.fill(query)
        query_input.press("Enter")
        self._wait_for_page_settle(page)

        # Enter로 검색되지 않은 경우를 대비한 fallback 클릭
        if not self._extract_candidates(page, query):
            button = self._find_first_visible_locator(page, _SEARCH_BUTTON_SELECTORS)
            if button:
                button.click()
                self._wait_for_page_settle(page)

    def _extract_candidates(self, page: Page, query: str) -> list[Candidate]:
        deduped: dict[tuple[str, str], Candidate] = {}

        for frame in page.frames:
            try:
                raw = frame.evaluate(_CANDIDATE_TABLE_EXTRACT_SCRIPT, query)
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

        ordered = sorted(
            deduped.values(),
            key=lambda c: (c.match_score, len(c.row_text), c.name),
            reverse=True,
        )
        return ordered[:50]

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

    @staticmethod
    def _normalize_space(value: str) -> str:
        return re.sub(r"\\s+", " ", value or "").strip()
