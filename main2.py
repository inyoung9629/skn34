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
file_path = "parking_kakao_crawled_상세페이지_후기업데이트.csv"
print(f"📁 [{file_path}] 파일을 읽어옵니다...")

try:
    df = pd.read_csv(file_path, encoding='utf-8-sig')
except:
    df = pd.read_csv(file_path, encoding='cp949')

# 결과 컬럼 정의
new_cols = ['카카오맵_매칭된_주차장명', '카카오맵_유사도', '카카오맵_별점', '카카오맵_리뷰수', '카카오맵_URL', '카카오맵_후기_URL']

# 모든 결과 컬럼 데이터 타입을 object로 강제 통일 (타입 에러 방지)
for col in new_cols:
    if col not in df.columns:
        df[col] = ""
    df[col] = df[col].astype('object')

print("브라우저를 실행합니다...")
service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service)

print("\n🔍 [사용자 정의 5단계 정밀 프로토콜] 크롤링을 시작합니다.")
print("=" * 80)

processed_count = 0  

for index, row in df.iterrows():
    # Y열(coord_src) 가져오기
    coord_src = str(row.get('coord_src', '')).strip()
    
    if coord_src in ['nan', 'NaN', 'None']:
        coord_src = ''
        
    # ====================================================================
    # 🚨 [대소문자 완벽 방어] .lower()를 붙여 대문자 '서울API', 소문자 '서울api' 모두 공평하게 소문자로 변환 후 비교합니다.
    # ====================================================================
    if coord_src.lower() not in ['서울api', '']:
        continue

    raw_name = str(row['pk_name'])
    
    # 괄호/내용물 제거 및 띄어쓰기 제거
    clean_name = re.sub(r'\(.*?\)', '', raw_name).replace(" ", "")
    
    if "주차" not in clean_name:
        clean_name += "주차장"
        
    print(f"\n[진행률: {processed_count + 1}번째 검색] 원본: {raw_name} ➡️ 타겟명: {clean_name} (출처: {coord_src if coord_src != '' else '공백'})")

    try:
        # 카카오맵 접속
        driver.get("https://map.kakao.com/")
        
        # 검색창 대기
        search_box = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "search.keyword.query"))
        )
        driver.execute_script("arguments[0].click();", search_box)
        
        # ➡️ 1단계: 위도와 경도 검색 후 이동 (NaN 방어)
        is_lat_nan = pd.isna(row.get('latitude')) or str(row.get('latitude')).strip().lower() in ['nan', '']
        is_lng_nan = pd.isna(row.get('longitude')) or str(row.get('longitude')).strip().lower() in ['nan', '']
        
        if is_lat_nan or is_lng_nan:
            addr = str(row.get('pk_address', '')).strip()
            if not addr or addr in ['nan', 'NaN', 'None']:
                addr = raw_name
            search_target = addr
            print(f"   📍 [1단계] 위경도 공백 감지 ➡️ 대체 주소로 위치 이동 ({search_target})")
        else:
            lat = str(row['latitude']).strip()
            lng = str(row['longitude']).strip()
            search_target = f"{lat}, {lng}"
            print(f"   📍 [1단계] 위경도 좌표 검색 완료 (지도 이동): {search_target}")
            
        search_box.send_keys(Keys.CONTROL + "a")
        search_box.send_keys(Keys.BACKSPACE)
        search_box.send_keys(search_target)
        search_box.send_keys(Keys.RETURN)
        time.sleep(2) 
        
        # 지도 축소 조치
        try:
            html_element = driver.find_element(By.TAG_NAME, "html")
            for _ in range(2):
                html_element.send_keys(Keys.SUBTRACT)
                time.sleep(0.2)
        except:
            pass
        
        # ➡️ 2단계: '현 지도 내 장소검색' 활성화
        bound_label = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.XPATH, "//label[contains(text(), '현 지도 내 장소검색')]"))
        )
        checkbox_id = bound_label.get_attribute("for")
        bound_checkbox = driver.find_element(By.ID, checkbox_id)
        
        if not bound_checkbox.is_selected():
            driver.execute_script("arguments[0].click();", bound_label)
            print("   📍 [2단계] '현 지도 내 장소검색' 체크 완료")
            time.sleep(1)

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
            print("   ❌ [검색 실패] 현재 지도 반경 내에 장소가 없습니다.")
            df.at[index, '카카오맵_별점'] = "조건 불일치 제외"
        else:
            best_element = None
            best_title = ""
            max_ratio = 0.0

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

            if not best_element or ("주차" not in best_title and max_ratio < 0.6):
                print(f"   ❌ [검색 실패] 매칭 조건 불일치 (이름: {best_title})")
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
                
                # 데이터프레임 순차 저장
                df.at[index, '카카오맵_매칭된_주차장명'] = best_title
                df.at[index, '카카오맵_유사도'] = similarity_percent
                df.at[index, '카카오맵_별점'] = k_star
                df.at[index, '카카오맵_리뷰수'] = k_rev
                df.at[index, '카카오맵_URL'] = k_url
                df.at[index, '카카오맵_후기_URL'] = k_review_url

        # ➡️ 5단계: 초기화
        try:
            bound_label = driver.find_element(By.XPATH, "//label[contains(text(), '현 지도 내 장소검색')]")
            checkbox_id = bound_label.get_attribute("for")
            bound_checkbox = driver.find_element(By.ID, checkbox_id)
            
            if bound_checkbox.is_selected():
                driver.execute_script("arguments[0].click();", bound_label)
                print("   📍 [5단계] 차기 루프 안전 보장을 위해 체크 해제 완료")
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
result_file_name = "parking_kakao_crawled_현지도검색_미지정대체_최종.csv"
df.to_csv(result_file_name, index=False, encoding='utf-8-sig')

print(f"\n🎉 작업 대성공! 데이터가 [{result_file_name}]으로 안전하게 저장되었습니다!")