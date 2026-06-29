import time   # 구글 리뷰창 추적 코드
import pandas as pd

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from selenium.common.exceptions import (
    TimeoutException,
    StaleElementReferenceException,
    ElementClickInterceptedException,
    WebDriverException
)

# ============================================================
# 속도 설정
# ============================================================

PAGE_LOAD_SLEEP = 2.5
REVIEW_TAB_TIMEOUT = 8
ROW_DELAY = 0.6

# 오류가 자주 나면 아래처럼 늘리면 됨
# PAGE_LOAD_SLEEP = 4
# REVIEW_TAB_TIMEOUT = 12
# ROW_DELAY = 1.2


# ============================================================
# 1. CSV 파일 읽기
# ============================================================

file_path = "parking_google_fallback_최종.csv"
print(f"📁 [{file_path}] 파일을 읽어옵니다...")

try:
    df = pd.read_csv(file_path, encoding="utf-8-sig")
except:
    df = pd.read_csv(file_path, encoding="cp949")

print(f"✅ CSV 로드 완료: {len(df)}행, {len(df.columns)}열")

# 엑셀 기준:
# BF열 = 58번째 열 = 파이썬 index 57
# BG열 = 59번째 열 = 파이썬 index 58
BF_INDEX = 57
BG_INDEX = 58

bf_col = df.columns[BF_INDEX]

if len(df.columns) > BG_INDEX:
    bg_col = df.columns[BG_INDEX]
else:
    bg_col = "카카오맵_후기_URL"
    df[bg_col] = ""

print(f"🔗 BF열 링크 컬럼: {bf_col}")
print(f"📝 BG열 저장 컬럼: {bg_col}")

df[bg_col] = df[bg_col].astype("object")


# ============================================================
# 2. 크롬 브라우저 설정
# ============================================================

print("🌐 구글맵 리뷰 URL 수집용 크롬 브라우저를 설정합니다...")

options = webdriver.ChromeOptions()
options.page_load_strategy = "eager"

options.add_argument("--disable-blink-features=AutomationControlled")
options.add_experimental_option("excludeSwitches", ["enable-automation"])
options.add_experimental_option("useAutomationExtension", False)
options.add_argument("--start-maximized")
options.add_argument(
    "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service, options=options)

driver.set_page_load_timeout(15)

driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
    "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
})


# ============================================================
# 3. 보조 함수
# ============================================================

def handle_google_popups(driver):
    """
    구글맵 화면을 가리는 안내/동의/확인 팝업 제거 시도
    """
    try:
        popup_buttons = driver.find_elements(
            By.XPATH,
            "//span[contains(text(), '동의') or contains(text(), '확인') or contains(text(), '나중에') or contains(text(), '닫기')]"
        )

        if popup_buttons:
            driver.execute_script("arguments[0].click();", popup_buttons[0])
            print("   👋 [팝업 처리] 구글 안내/동의 팝업창을 제거했습니다.")
            time.sleep(1)
    except:
        pass

    try:
        body = driver.find_element(By.TAG_NAME, "body")
        body.send_keys(Keys.ESCAPE)
        time.sleep(0.2)
    except:
        pass


def is_google_maps_link(url):
    """
    BF열 링크가 구글맵 링크인지 확인.
    카카오 링크는 제외.
    """
    if not url:
        return False

    url_text = str(url).strip().lower()

    if url_text in ["nan", "none", ""]:
        return False

    if "google" not in url_text:
        return False

    if "kakao" in url_text:
        return False

    return True


def wait_place_detail_panel(driver, timeout=8):
    """
    구글맵 장소 상세 카드가 뜰 때까지 대기.
    h1.DUwDvf는 장소 상세 카드의 제목 영역.
    """
    try:
        title = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "h1.DUwDvf"))
        )

        if title.is_displayed():
            print(f"   📍 [장소 상세 카드 확인] {title.text.strip()}")
            return True

    except:
        pass

    return False


def click_first_search_result_if_needed(driver):
    """
    링크를 열었는데 장소 상세 카드가 아니라 검색 결과 목록만 나온 경우,
    첫 번째 검색 결과를 클릭해서 상세 카드로 진입.
    """
    try:
        if wait_place_detail_panel(driver, timeout=3):
            return True

        print("   ⚠️ [상세 카드 없음] 검색 결과 목록에서 첫 번째 장소를 클릭합니다.")

        first_result_candidates = [
            (By.CSS_SELECTOR, "a.hfpxzc"),
            (By.XPATH, "//a[contains(@href, '/maps/place/')]"),
        ]

        for by, selector in first_result_candidates:
            results = driver.find_elements(by, selector)

            for item in results:
                try:
                    if item.is_displayed() and item.is_enabled():
                        driver.execute_script("arguments[0].click();", item)
                        time.sleep(2)
                        return wait_place_detail_panel(driver, timeout=6)
                except:
                    continue

    except:
        pass

    return False


def click_review_tab_if_exists(driver, timeout=8):
    """
    장소 상세 카드 안의 정확한 '리뷰' 탭만 클릭.
    검색 결과 목록의 '리뷰 없음', 리뷰 문구, 다른 버튼은 클릭하지 않도록 엄격하게 제한.
    """
    end_time = time.time() + timeout

    while time.time() < end_time:
        try:
            # 장소 상세 카드의 제목 h1이 있는지 먼저 확인
            detail_ready = wait_place_detail_panel(driver, timeout=2)
            if not detail_ready:
                time.sleep(0.3)
                continue

            # 핵심:
            # h1.DUwDvf 뒤쪽에 나오는 role='tab' 버튼 중 텍스트가 정확히 리뷰인 것만 찾음.
            # 왼쪽 검색 결과 목록의 '리뷰 없음' 같은 문구는 제외됨.
            strict_review_tab_xpaths = [
                "//h1[contains(@class, 'DUwDvf')]/following::button[@role='tab' and .//*[normalize-space(text())='리뷰']][1]",
                "//h1[contains(@class, 'DUwDvf')]/following::button[@role='tab' and normalize-space(.)='리뷰'][1]",
                "//h1[contains(@class, 'DUwDvf')]/following::*[@role='tab' and .//*[normalize-space(text())='리뷰']][1]",
                "//h1[contains(@class, 'DUwDvf')]/following::*[@role='tab' and normalize-space(.)='리뷰'][1]",
                "//button[@role='tab' and @aria-label='리뷰']",
                "//*[@role='tab' and @aria-label='리뷰']",
            ]

            for xpath in strict_review_tab_xpaths:
                try:
                    elements = driver.find_elements(By.XPATH, xpath)

                    for element in elements:
                        try:
                            text = element.text.strip()
                            aria = element.get_attribute("aria-label") or ""

                            # '리뷰 없음', '리뷰 7개' 같은 다른 요소 방지
                            # 탭 자체는 보통 text가 '리뷰' 또는 aria-label이 '리뷰'
                            if text != "리뷰" and aria.strip() != "리뷰":
                                continue

                            if not element.is_displayed() or not element.is_enabled():
                                continue

                            old_url = driver.current_url

                            try:
                                driver.execute_script(
                                    "arguments[0].scrollIntoView({block: 'center'});",
                                    element
                                )
                                time.sleep(0.1)
                            except:
                                pass

                            try:
                                driver.execute_script("arguments[0].click();", element)
                            except:
                                ActionChains(driver).move_to_element(element).click().perform()

                            time.sleep(1.2)

                            new_url = driver.current_url

                            print("   🖱️ [리뷰 탭 클릭 완료] 장소 상세 카드의 '리뷰' 탭을 클릭했습니다.")

                            if old_url == new_url:
                                print("   ⚠️ [참고] 리뷰 클릭 후 URL 변화가 거의 없습니다. 현재 URL을 저장합니다.")

                            return True

                        except (StaleElementReferenceException, ElementClickInterceptedException, WebDriverException):
                            continue

                except:
                    continue

        except:
            pass

        time.sleep(0.3)

    return False


# ============================================================
# 4. BF열 구글 링크만 열어서 리뷰 탭 URL 수집
# ============================================================

print("\n🔍 BF열의 구글맵 링크에서 리뷰 탭 URL 수집을 시작합니다.")
print("=" * 80)

processed_count = 0
success_count = 0
skip_count = 0
no_review_count = 0
fail_count = 0

for index, row in df.iterrows():
    bf_url = str(row.get(bf_col, "")).strip()

    if not is_google_maps_link(bf_url):
        skip_count += 1
        continue

    current_bg = str(row.get(bg_col, "")).strip()
    if current_bg and current_bg.lower() not in ["nan", "none", ""]:
        print(f"\n[스킵] index {index} | BG열에 이미 값이 있음")
        skip_count += 1
        continue

    processed_count += 1

    print(f"\n[리뷰 URL 수집 {processed_count}번째] index {index}")
    print(f"   🔗 BF URL: {bf_url}")

    try:
        try:
            driver.get(bf_url)
        except TimeoutException:
            print("   ⚠️ [페이지 로딩 지연] 완전 로딩 전에 진행합니다.")

        time.sleep(PAGE_LOAD_SLEEP)
        handle_google_popups(driver)

        # 검색 결과 목록만 뜨는 경우 첫 번째 장소 클릭
        click_first_search_result_if_needed(driver)

        # 장소 상세 카드 안의 정확한 리뷰 탭 클릭
        review_clicked = click_review_tab_if_exists(driver, timeout=REVIEW_TAB_TIMEOUT)

        if not review_clicked:
            no_review_count += 1
            print("   ⏭️ [리뷰 탭 없음] 장소 상세 카드 안에서 정확한 '리뷰' 탭을 찾지 못해 넘어갑니다.")
            time.sleep(ROW_DELAY)
            continue

        review_url = driver.current_url

        df.at[index, bg_col] = review_url
        success_count += 1

        print(f"   ✅ [BG 저장 완료] {review_url}")

    except Exception as e:
        fail_count += 1
        print(f"   ❌ [실패] 리뷰 탭 URL 수집 실패. 이 행은 넘어갑니다. 사유: {e}")

    time.sleep(ROW_DELAY)


print("=" * 80)
print("✅ 리뷰 URL 수집 종료")
print(f"   처리 대상 구글 링크 수: {processed_count}")
print(f"   성공: {success_count}")
print(f"   리뷰 없음/탭 없음: {no_review_count}")
print(f"   실패: {fail_count}")
print(f"   스킵: {skip_count}")

driver.quit()


# ============================================================
# 5. 결과 저장
# ============================================================

result_file_name = "parking_google_review_url_BG추가.csv"
df.to_csv(result_file_name, index=False, encoding="utf-8-sig")

print(f"\n🎉 완료! 리뷰 탭 URL이 BG열에 저장된 파일이 [{result_file_name}]으로 저장되었습니다.")

