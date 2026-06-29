import time         #카카오맵에서 검색되지 않는 경우 구글맵 검색 코드
import re
import math
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from difflib import SequenceMatcher

from selenium.common.exceptions import (
    TimeoutException,
    StaleElementReferenceException,
    ElementClickInterceptedException,
    WebDriverException
)

# 1. 이전 단계 결과 파일 읽기
file_path = "parking_kakao_crawled_현지도검색_미지정대체_최종.csv"
print(f"📁 [{file_path}] 파일을 읽어옵니다...")

try:
    df = pd.read_csv(file_path, encoding='utf-8-sig')
except:
    df = pd.read_csv(file_path, encoding='cp949')

# 연쇄 에러 차단: 타겟 컬럼들의 데이터 타입을 object로 강제 지정
target_cols = ['카카오맵_별점', '카카오맵_리뷰수', '카카오맵_URL', '카카오맵_매칭된_주차장명', '카카오맵_유사도']
for col in target_cols:
    if col not in df.columns:
        df[col] = ""
    df[col] = df[col].astype('object')

print("🌐 구글맵 수집용 우회 크롬 브라우저를 설정합니다...")

GOOGLE_MAPS_URL = "https://www.google.co.kr/maps?hl=ko"

# 주소 검색 위치와 이름 검색 위치 사이 최대 허용 거리
MAX_DISTANCE_M = 500

options = webdriver.ChromeOptions()
options.add_argument("--disable-blink-features=AutomationControlled")
options.add_experimental_option("excludeSwitches", ["enable-automation"])
options.add_experimental_option('useAutomationExtension', False)
options.add_argument("--start-maximized")
options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service, options=options)

driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
    "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
})


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
            time.sleep(2)
    except:
        pass

    try:
        body = driver.find_element(By.TAG_NAME, "body")
        body.send_keys(Keys.ESCAPE)
        time.sleep(0.5)
    except:
        pass


def wait_page_ready(driver, timeout=15):
    """
    페이지 기본 로딩 대기
    """
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except:
        pass


def recover_google_maps_home(driver):
    """
    검색창이 안 잡힐 때 구글맵 메인으로 복구
    """
    print("   🔄 [구글맵 복구] 구글맵 메인으로 다시 접속합니다.")
    driver.get(GOOGLE_MAPS_URL)
    wait_page_ready(driver, timeout=15)
    time.sleep(5)
    handle_google_popups(driver)


def find_visible_google_maps_search_box(driver, timeout=20):
    """
    사진에 보이는 'Google 지도 검색' 입력창을 직접 찾는 함수.
    기존 ID 방식만 쓰지 않고 placeholder / aria-label / role 기반으로도 찾음.
    """
    search_box_candidates = [
        (By.ID, "searchboxinput"),
        (By.CSS_SELECTOR, "input[aria-label*='Google 지도 검색']"),
        (By.CSS_SELECTOR, "input[placeholder*='Google 지도 검색']"),
        (By.CSS_SELECTOR, "input[aria-label*='지도 검색']"),
        (By.CSS_SELECTOR, "input[placeholder*='지도 검색']"),
        (By.CSS_SELECTOR, "input[aria-label*='검색']"),
        (By.CSS_SELECTOR, "input[placeholder*='검색']"),
        (By.CSS_SELECTOR, "input[role='combobox']"),
        (By.XPATH, "//input[contains(@aria-label, '검색') or contains(@placeholder, '검색')]"),
    ]

    end_time = time.time() + timeout

    while time.time() < end_time:
        for by, selector in search_box_candidates:
            try:
                elements = driver.find_elements(by, selector)

                for element in elements:
                    try:
                        if element.is_displayed() and element.is_enabled():
                            return element
                    except StaleElementReferenceException:
                        continue

            except:
                continue

        time.sleep(0.5)

    raise TimeoutException("사진에 보이는 Google 지도 검색창을 찾지 못했습니다.")


def click_google_maps_search_button(driver):
    """
    검색어 입력 후 돋보기 버튼 클릭 시도.
    실패하면 False 반환.
    """
    search_button_candidates = [
        (By.ID, "searchbox-searchbutton"),
        (By.CSS_SELECTOR, "button[aria-label*='검색']"),
        (By.XPATH, "//button[contains(@aria-label, '검색')]"),
    ]

    for by, selector in search_button_candidates:
        try:
            buttons = driver.find_elements(by, selector)

            for button in buttons:
                try:
                    if button.is_displayed() and button.is_enabled():
                        driver.execute_script("arguments[0].click();", button)
                        return True
                except:
                    continue

        except:
            continue

    return False


def search_google_maps_directly(driver, keyword, max_retries=3):
    """
    사진에 보이는 구글 지도 검색창에 직접 검색어를 입력하고 검색 실행.
    """
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            print(f"   🔎 [직접 검색 시도 {attempt}/{max_retries}] 검색창에 입력 준비 중...")

            search_box = find_visible_google_maps_search_box(driver, timeout=20)

            # 검색창 직접 클릭 + 포커스
            try:
                driver.execute_script("arguments[0].click(); arguments[0].focus();", search_box)
            except:
                ActionChains(driver).move_to_element(search_box).click().perform()

            time.sleep(0.7)

            # 기존 검색어 삭제
            try:
                search_box.send_keys(Keys.CONTROL + "a")
                time.sleep(0.2)
                search_box.send_keys(Keys.BACKSPACE)
                time.sleep(0.3)
            except StaleElementReferenceException:
                search_box = find_visible_google_maps_search_box(driver, timeout=10)
                driver.execute_script("arguments[0].click(); arguments[0].focus();", search_box)
                search_box.send_keys(Keys.CONTROL + "a")
                search_box.send_keys(Keys.BACKSPACE)
                time.sleep(0.3)

            # 그래도 값이 남아 있으면 JS로 강제 비우기
            try:
                current_value = search_box.get_attribute("value") or ""
                if current_value.strip():
                    driver.execute_script("""
                        arguments[0].value = '';
                        arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                        arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
                    """, search_box)
                    time.sleep(0.3)
            except:
                pass

            # 검색어 직접 입력
            search_box.send_keys(keyword)
            time.sleep(0.8)

            print(f"   ⌨️ [직접 입력 완료] Google 지도 검색창에 입력됨: {keyword}")

            # 돋보기 버튼 클릭 우선 시도
            clicked = click_google_maps_search_button(driver)

            if clicked:
                print("   🖱️ [검색 실행] 돋보기 버튼 클릭 완료")
            else:
                print("   ⏎ [검색 실행] 돋보기 버튼을 못 찾아 Enter로 검색합니다.")
                search_box.send_keys(Keys.RETURN)

            time.sleep(5.5)
            return True

        except Exception as e:
            last_error = e
            print(f"   ⚠️ [직접 검색 실패 {attempt}/{max_retries}] 검색창 직접 제어 실패: {e}")

            if attempt < max_retries:
                recover_google_maps_home(driver)
            else:
                print("   ❌ [직접 검색 최종 실패] 재시도 횟수를 모두 사용했습니다.")

    raise last_error


def extract_lat_lng_from_url(url):
    """
    구글맵 URL에서 위도/경도를 추출하는 함수.
    우선 장소 좌표에 가까운 !3d, !4d 패턴을 찾고,
    없으면 지도 중심 좌표인 @lat,lng 패턴을 찾음.
    """
    try:
        # 장소 상세 URL에서 자주 나오는 좌표
        match = re.search(r'!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)', url)
        if match:
            return float(match.group(1)), float(match.group(2))

        # 지도 중심 좌표
        match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
        if match:
            return float(match.group(1)), float(match.group(2))

    except:
        pass

    return None


def haversine_distance_m(coord1, coord2):
    """
    두 위도/경도 좌표 사이 거리를 미터 단위로 계산.
    """
    if coord1 is None or coord2 is None:
        return None

    lat1, lon1 = coord1
    lat2, lon2 = coord2

    R = 6371000

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)

    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def get_road_address_from_row(row):
    """
    도로명주소를 가져오는 함수.

    핵심 수정:
    CSV의 B열이 도로명주소라고 했으므로, 컬럼명과 상관없이
    무조건 두 번째 컬럼(row.iloc[1])을 먼저 도로명주소로 사용함.
    """
    # 1순위: B열, 즉 두 번째 컬럼
    try:
        value = str(row.iloc[1]).strip()

        if value and value.lower() not in ['nan', 'none', '']:
            return value

    except:
        pass

    # 혹시 B열이 비어 있을 경우만 기존 컬럼명 후보로 보조 탐색
    address_col_candidates = [
        '도로명주소',
        'road_address',
        '도로명 주소',
        '주소',
        'addr',
        'address'
    ]

    for col in address_col_candidates:
        if col in row.index:
            value = str(row.get(col, '')).strip()
            if value and value.lower() not in ['nan', 'none', '']:
                return value

    return ""


print("📍 구글 지도 공식 페이지 접속 중...")
driver.get(GOOGLE_MAPS_URL)
wait_page_ready(driver, timeout=15)

# 구글맵 초기 로딩 대기
time.sleep(7)

# 초기 팝업 처리
handle_google_popups(driver)

print("\n🔍 [구글맵 2차 대체 프로토콜] 조건불일치 데이터 재조사를 시작합니다.")
print("=" * 80)

processed_count = 0

for index, row in df.iterrows():
    # BD열('카카오맵_별점')이 '조건 불일치 제외'인 항목만 타겟팅
    current_status = str(row.get('카카오맵_별점', '')).strip()
    if current_status != '조건 불일치 제외':
        continue

    raw_name = str(row['pk_name'])

    # 괄호 및 내용물 제거 + 띄어쓰기 제거
    clean_name = re.sub(r'\(.*?\)', '', raw_name).replace(" ", "")

    # '주차' 키워드가 없을 경우 '주차장' 추가
    if "주차" not in clean_name:
        clean_name += "주차장"

    print(f"\n[구글맵 재검색 {processed_count + 1}번째] 원본: {raw_name} ➡️ 구글타겟명: {clean_name}")

    # 검색하는 이름에 '버스' 또는 '화물'이 들어가 있으면 구글맵 검색하지 않고 조건 불일치 제외 처리
    check_name = str(raw_name) + " " + str(clean_name)

    if "버스" in check_name or "화물" in check_name:
        print("   ⏭️ [검색 제외] 이름에 '버스' 또는 '화물'이 포함되어 조건 불일치 제외 처리합니다.")
        df.at[index, '카카오맵_별점'] = "조건 불일치 제외"
        processed_count += 1
        continue

    try:
        # ============================================================
        # 1단계: B열 도로명주소 먼저 검색해서 기준 좌표 확보
        # ============================================================
        road_address = get_road_address_from_row(row)
        address_coord = None

        if road_address:
            print(f"   🧭 [주소 선검색] B열 도로명주소 먼저 검색: {road_address}")

            try:
                search_google_maps_directly(driver, road_address, max_retries=3)
                time.sleep(2)

                address_url = driver.current_url
                address_coord = extract_lat_lng_from_url(address_url)

                if address_coord:
                    print(f"   📌 [주소 좌표 확보] {address_coord}")
                else:
                    print("   ⚠️ [주소 좌표 실패] 도로명주소 검색 결과 URL에서 좌표를 추출하지 못했습니다. 거리 검증은 생략합니다.")

            except Exception as address_error:
                print(f"   ⚠️ [주소 선검색 실패] 도로명주소 검색 실패. 거리 검증은 생략합니다. 사유: {address_error}")
                address_coord = None
        else:
            print("   ⚠️ [주소 없음] B열 도로명주소가 비어 있어 거리 검증은 생략합니다.")

        # ============================================================
        # 2단계: 주차장 이름으로 다시 검색
        # ============================================================
        search_google_maps_directly(driver, clean_name, max_retries=3)

        # 단일 검색 결과(상세 페이지 다이렉트 이동) 여부 판별
        is_direct_page = False
        try:
            title_element = WebDriverWait(driver, 6).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "h1.DUwDvf"))
            )
            if title_element.is_displayed():
                is_direct_page = True
        except:
            pass

        final_element_clicked = False
        best_title = ""
        max_ratio = 0.0

        if is_direct_page:
            best_title = driver.find_element(By.CSS_SELECTOR, "h1.DUwDvf").text.strip()
            max_ratio = SequenceMatcher(None, clean_name, best_title.replace(" ", "")).ratio()
            final_element_clicked = True
            print(f"   📍 [결과 판별] 단일 장소 자동 다이렉트 접속 성공: {best_title} (유사도: {max_ratio * 100:.1f}%)")

        else:
            # 복수 검색 결과 목록(리스트)이 나온 경우 유사도 매칭 진행
            search_results = driver.find_elements(By.CSS_SELECTOR, "a.hfpxzc")

            if not search_results:
                search_results = driver.find_elements(By.CSS_SELECTOR, "[role='feed'] a[href*='/maps/place/']")

            if search_results:
                best_item = None

                for item in search_results:
                    try:
                        title = item.get_attribute("aria-label")

                        if not title:
                            continue

                        title = title.strip()
                        compare_title = title.replace(" ", "")
                        ratio = SequenceMatcher(None, clean_name, compare_title).ratio()

                        if ratio > max_ratio:
                            max_ratio = ratio
                            best_item = item
                            best_title = title

                    except:
                        continue

                if best_item:
                    try:
                        driver.execute_script("arguments[0].click();", best_item)
                        time.sleep(4)
                        final_element_clicked = True
                        print(f"   📍 [결과 판별] 리스트 중 최고 유사도 장소 선택 클릭: {best_title} (유사도: {max_ratio * 100:.1f}%)")
                    except:
                        final_element_clicked = False

        # ============================================================
        # 3단계: 이름 검색 결과 좌표 확보 후 주소 좌표와 거리 검증
        # ============================================================
        name_coord = None
        distance_m = None

        if final_element_clicked:
            try:
                name_url_for_coord = driver.current_url
                name_coord = extract_lat_lng_from_url(name_url_for_coord)

                if address_coord and name_coord:
                    distance_m = haversine_distance_m(address_coord, name_coord)
                    print(f"   📏 [거리 검증] 주소 검색 위치 ↔ 이름 검색 위치 거리: {distance_m:.1f}m")

                    if distance_m > MAX_DISTANCE_M:
                        print(f"   ❌ [거리 불일치] {distance_m:.1f}m로 기준 {MAX_DISTANCE_M}m 초과. 조건 불일치 제외 처리합니다.")
                        df.at[index, '카카오맵_별점'] = "조건 불일치 제외"
                        processed_count += 1
                        continue

                elif address_coord and not name_coord:
                    print("   ⚠️ [거리 검증 생략] 이름 검색 결과 좌표를 추출하지 못했습니다.")

                elif not address_coord:
                    print("   ⚠️ [거리 검증 생략] 주소 좌표가 없어 거리 비교를 하지 않습니다.")

            except Exception as distance_error:
                print(f"   ⚠️ [거리 검증 오류] 거리 계산 실패. 거리 검증은 생략합니다. 사유: {distance_error}")

        # 최종 검증 및 데이터 기록 단계
        if not final_element_clicked:
            print(f"   ❌ [매칭 실패] 구글 검색 결과 없음 또는 클릭 실패")
            df.at[index, '카카오맵_별점'] = "조건 불일치 제외"

        elif max_ratio < 0.7:
            print(f"   ❌ [유사도 부족] 이름 유사도 {max_ratio * 100:.1f}%로 85% 미만이므로 조건 불일치 제외 처리합니다. (이름: {best_title})")
            df.at[index, '카카오맵_별점'] = "조건 불일치 제외"

        elif "주차" not in best_title and "주차장" not in best_title:
            print(f"   ❌ [매칭 실패] 구글 결과 중 '주차' 키워드 누락 (이름: {best_title})")
            df.at[index, '카카오맵_별점'] = "조건 불일치 제외"

        else:
            g_star = "평가 없음"
            g_rev = "0"

            try:
                rating_zone_text = driver.find_element(By.CSS_SELECTOR, "div.F7nice").text.strip()

                rating_match = re.search(r'([0-9\.]+)', rating_zone_text)
                review_match = re.search(r'\(([0-9,]+)\)', rating_zone_text)

                if rating_match:
                    g_star = rating_match.group(1)

                if review_match:
                    g_rev = review_match.group(1).replace(',', '')

            except:
                pass

            g_url = driver.current_url
            print(f"   🟢 [구글맵 갱신 성공] {best_title} | 유사도: {max_ratio * 100:.1f}% | 별점: {g_star} | 리뷰수: {g_rev}개")

            df.at[index, '카카오맵_매칭된_주차장명'] = best_title
            df.at[index, '카카오맵_유사도'] = f"{max_ratio * 100:.1f}%"
            df.at[index, '카카오맵_별점'] = g_star
            df.at[index, '카카오맵_리뷰수'] = g_rev
            df.at[index, '카카오맵_URL'] = g_url

    except Exception as e:
        print(f"   ❌ [행 패스] 구글맵 요소 제어 일시적 지연 (사유: {e})")
        df.at[index, '카카오맵_별점'] = "조건 불일치 제외"

    processed_count += 1

print("=" * 80)
print(f"✅ 구글맵 Fallback 패스 종료! 총 {processed_count}개의 제외 항목 재검토 완료.")

driver.quit()

# 5. 최종 결과 저장
result_file_name = "parking_google_fallback_최종.csv"
df.to_csv(result_file_name, index=False, encoding='utf-8-sig')

print(f"\n🎉 패자부활전 대성공! 최종 보완된 통합 데이터가 [{result_file_name}]으로 안전하게 저장되었습니다!")