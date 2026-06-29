import time
import re
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from difflib import SequenceMatcher

# 1. 원본 파일 읽기
file_path = "parking_raw_20260628_0108.csv"
print(f"📁 [{file_path}] 파일을 읽어옵니다...")

try:
    df = pd.read_csv(file_path, encoding='utf-8-sig')
except:
    df = pd.read_csv(file_path, encoding='cp949')

# 결과 컬럼 정의
new_cols = ['카카오맵_매칭된_주차장명', '카카오맵_유사도', '카카오맵_별점', '카카오맵_리뷰수', '카카오맵_URL', '카카오맵_후기_URL']
for col in new_cols:
    if col not in df.columns:
        df[col] = ""

print("브라우저를 실행합니다...")
service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service)

print("\n🔍 [사용자 정의 5단계 정밀 프로토콜] 크롤링을 시작합니다.")
print("=" * 80)

processed_count = 0  

for index, row in df.iterrows():
    # Y열이 '카카오'인 항목만 타겟팅
    if str(row.get('coord_src', '')).strip() != '카카오':
        continue

    raw_name = str(row['pk_name'])
    lat = str(row['latitude']).strip()
    lng = str(row['longitude']).strip()
    
    # 괄호/내용물 제거 및 띄어쓰기 제거
    clean_name = re.sub(r'\(.*?\)', '', raw_name).replace(" ", "")
    
    # '주차'가 없을 경우 '주차장'을 추가
    if "주차" not in clean_name:
        clean_name += "주차장"
        
    print(f"\n[진행률: {processed_count + 1}번째 검색] 원본: {raw_name} ➡️ 타겟명: {clean_name}")

    try:
        # 카카오맵 접속
        driver.get("https://map.kakao.com/")
        
        # 콜드 스타트 방어: 검색창 대기
        search_box = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "search.keyword.query"))
        )
        driver.execute_script("arguments[0].click();", search_box)
        
        # ➡️ 1단계: 위도와 경도 검색 후 이동
        search_box.send_keys(Keys.CONTROL + "a")
        search_box.send_keys(Keys.BACKSPACE)
        search_box.send_keys(f"{lat}, {lng}")
        search_box.send_keys(Keys.RETURN)
        print(f"   📍 [1단계] 위경도 좌표 검색 완료 (지도 이동)")
        time.sleep(2) 
        
        # ➡️ 2단계: '현 지도 내 장소검색' 띄어쓰기 완벽 반영
        # 요소가 클릭 가능하지 않아도 DOM에 존재하기만 하면 잡아서 JS로 강제 클릭합니다.
        bound_label = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.XPATH, "//label[contains(text(), '현 지도 내 장소검색')]"))
        )
        checkbox_id = bound_label.get_attribute("for")
        bound_checkbox = driver.find_element(By.ID, checkbox_id)
        
        if not bound_checkbox.is_selected():
            driver.execute_script("arguments[0].click();", bound_label)
            print("   📍 [2단계] '현 지도 내 장소검색' 글씨 클릭 완료 (체크 활성화)")
            time.sleep(1)
        else:
            print("   📍 [2단계] 이미 체크되어 있습니다.")

        # ➡️ 3단계: 주차장 이름 입력 및 최종 검색
        search_box = driver.find_element(By.ID, "search.keyword.query")
        driver.execute_script("arguments[0].click();", search_box)
        search_box.send_keys(Keys.CONTROL + "a")
        search_box.send_keys(Keys.BACKSPACE)
        search_box.send_keys(clean_name)
        search_box.send_keys(Keys.RETURN)
        print("   📍 [3단계] 정제된 주차장 이름으로 최종 검색 실행")
        time.sleep(2) 
        
        # ➡️ 4단계: 매칭 결과 분석 및 출력
        search_results = driver.find_elements(By.CSS_SELECTOR, "li.PlaceItem.clickArea")
        
        if not search_results:
            print("   ❌ [검색 실패] 현재 지도 반경 내에 해당 주차장이 없습니다.")
            df.at[index, '카카오맵_별점'] = "조건 불일치 제외"
        else:
            best_element = None
            best_title = ""
            max_ratio = 0.0

            # 유사도 검사 루프
            for item in search_results:
                try:
                    title = item.find_element(By.CSS_SELECTOR, "a.link_name").text.strip()
                    compare_title = title.replace(" ", "")
                    ratio = SequenceMatcher(None, clean_name, compare_title).ratio()
                    
                    if ratio > max_ratio:
                        max_ratio = ratio
                        best_element = item
                        best_title = title
                except:
                    continue

            if not best_element or "주차" not in best_title:
                print(f"   ❌ [검색 실패] 결과 중 주차 키워드를 가진 장소가 없음 (이름: {best_title})")
                df.at[index, '카카오맵_별점'] = "조건 불일치 제외"
            else:
                # 별점 추출
                try:
                    k_star = best_element.find_element(By.CSS_SELECTOR, "em.num").text.strip()
                    if not k_star or k_star == "0.0": k_star = "평가 없음"
                except: k_star = "평가 없음"
                    
                # 리뷰수 추출
                k_rev = "0"
                try:
                    review_text = best_element.find_element(By.CSS_SELECTOR, "a[data-id='review']").text
                    k_rev = re.sub(r'[^0-9]', '', review_text)
                except:
                    try:
                        review_text = best_element.find_element(By.CSS_SELECTOR, "a.review").text
                        k_rev = re.sub(r'[^0-9]', '', review_text)
                    except:
                        try:
                            rating_zone = best_element.find_element(By.CSS_SELECTOR, ".rating").text
                            match = re.search(r'리뷰\s*([0-9]+)', rating_zone)
                            if match: k_rev = match.group(1)
                        except: k_rev = "0"
                            
                if not k_rev: k_rev = "0"
                
                # URL 추출
                try:
                    k_url = best_element.find_element(By.CSS_SELECTOR, "a.moreview").get_attribute("href")
                    k_review_url = k_url + "#review"
                except:
                    k_url, k_review_url = "없음", "없음"
                    
                similarity_percent = f"{max_ratio * 100:.1f}%"
                print(f"   🟢 [4단계 결과 출력] {best_title} ({similarity_percent}) | 별점: {k_star} | 리뷰수: {k_rev}개")
                
                # 데이터프레임 저장
                df.at[index, '카카오맵_매칭된_주차장명'] = best_title
                df.at[index, '카카오맵_유사도'] = similarity_percent
                df.at[index, '카카오맵_별점'] = k_star
                df.at[index, '카카오맵_리뷰수'] = k_rev
                df.at[index, '카카오맵_URL'] = k_url
                df.at[index, '카카오맵_후기_URL'] = k_review_url

        # ➡️ 5단계: 다음 검색의 독립성을 보장하기 위해 글씨를 다시 눌러 체크 해제 (초기화)
        try:
            bound_label = driver.find_element(By.XPATH, "//label[contains(text(), '현 지도 내 장소검색')]")
            checkbox_id = bound_label.get_attribute("for")
            bound_checkbox = driver.find_element(By.ID, checkbox_id)
            
            if bound_checkbox.is_selected():
                driver.execute_script("arguments[0].click();", bound_label)
                print("   📍 [5단계] 차기 루프 안전 보장을 위해 글씨 클릭 완료 (체크 해제)")
                time.sleep(1)
        except:
            pass

    except Exception as e:
        print(f"   ❌ [시스템 오류] 에러 발생으로 이번 행 패스 (사유: {e})")
        df.at[index, '카카오맵_별점'] = "조건 불일치 제외"
        
    processed_count += 1

print("=" * 80)
print(f"✅ 전체 데이터 장정 종료! 총 {processed_count}개의 데이터 처리가 끝났습니다.")
driver.quit()

# 5. 최종 결과 저장
result_file_name = "parking_kakao_crawled_현지도검색_최종.csv"
df.to_csv(result_file_name, index=False, encoding='utf-8-sig')

print(f"\n🎉 작업 대성공! 완벽한 순서로 제어된 데이터가 [{result_file_name}]으로 안전하게 저장되었습니다!")