# app/api_client.py
import json
import os
from pathlib import Path
import tempfile
from uuid import UUID
import urllib3
import requests

# SSL 자가 서명/IP 직접 접근 경고 숨김 처리
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 💡 HTTPS 주소로 확정
BASE_URL = "https://54.117.40.85"


class APIClient:
    def __init__(self, base_url=BASE_URL):
        self.base_url = base_url.rstrip("/")
        self.access_token = None
        self.user_info = None

    def set_token(self, token: str):
        """JWT 토큰 수동 또는 자동 주입"""
        self.access_token = token

    def _get_headers(self):
        headers = {"Content-Type": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def remove_library_score(self, score_id: str):
        """Remove the signed-in user's library entry; 204 has no JSON body."""
        if not self.access_token:
            return False, "로그인 후 악보를 삭제할 수 있습니다."
        try:
            score_id = str(UUID(str(score_id)))
        except (ValueError, TypeError, AttributeError):
            return False, "서버 악보 ID를 확인할 수 없습니다. 다시 동기화해주세요."

        try:
            res = requests.delete(
                f"{self.base_url}/api/me/scores/{score_id}",
                headers={"Authorization": f"Bearer {self.access_token}"},
                verify=False,
                timeout=5,
            )
            if res.status_code == 204:
                return True, ""
            messages = {
                401: "로그인이 만료되었습니다. 다시 로그인해주세요.",
                403: "계정 상태 또는 접근 권한을 확인해주세요.",
                404: "삭제 API 또는 악보 ID를 찾을 수 없습니다.",
                405: "서버에서 악보 삭제 요청을 지원하지 않습니다.",
                503: "서버 DB에 연결할 수 없습니다. 잠시 후 다시 시도해주세요.",
            }
            return False, messages.get(res.status_code, f"악보 삭제 실패 (HTTP {res.status_code})")
        except requests.RequestException:
            return False, "삭제 응답을 받지 못했습니다. 연결을 확인한 뒤 다시 시도해주세요."

    def cache_server_score(self, local_save_dir, score_id, title, notes, is_default=None):
        """Keep server identity separate from locally imported scores."""
        score_id = str(UUID(str(score_id)))
        directory = Path(local_save_dir) / "server"
        if directory.is_symlink():
            raise ValueError("서버 캐시 디렉터리는 심볼릭 링크를 사용할 수 없습니다.")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{score_id}_parsed.json"
        metadata_path = directory / f"{score_id}.meta.json"
        if not isinstance(is_default, bool) and metadata_path.exists():
            previous = json.loads(metadata_path.read_text(encoding="utf-8"))
            is_default = previous.get("is_default") if isinstance(previous, dict) else None
        metadata = {
            "cache_source": "server",
            "score_id": score_id,
            "title": title,
            "is_default": is_default if isinstance(is_default, bool) else None,
        }
        for destination, payload in ((path, notes), (metadata_path, metadata)):
            temporary_path = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as file:
                    temporary_path = Path(file.name)
                    json.dump(payload, file, indent=2, ensure_ascii=False)
                os.replace(temporary_path, destination)
            finally:
                if temporary_path is not None and temporary_path.exists():
                    temporary_path.unlink()
        return str(path)

    def login_with_google_id_token(self, google_id_token: str):
        """구글 ID 토큰으로 백엔드 JWT 토큰 발급"""
        url = f"{self.base_url}/api/auth/google"
        payload = {"id_token": google_id_token}
        try:
            res = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, verify=False, timeout=10)
            if res.status_code in [200, 201]:
                data = res.json()
                self.access_token = data.get("access_token")
                self.user_info = data.get("user")
                print(f"[API] 로그인 성공: {self.user_info.get('username', '사용자')}님 환영합니다.")
                return True, data
            return False, f"HTTP {res.status_code}: {res.text}"
        except Exception as e:
            return False, f"서버 통신 실패: {e}"

    def save_practice_record(self, record_payload: dict):
        """연습 종료/중단 후 결과 전송 (POST /api/practice/records)"""
        # 1. 게스트 모드일 경우 서버 통신 없이 패스
        if not self.access_token:
          print("[기록 저장] 게스트 모드이므로 서버 저장을 생략합니다.")
          return True, {"guest": True}

        url = f"{self.base_url}/api/practice/records"
        try:
          res = requests.post(
              url,
              json=record_payload,
              headers=self._get_headers(),
              verify=False,
              timeout=5,
          )

          # 201(최초 생성) 또는 200(동일 재전송 성공)
          if res.status_code in [200, 201]:
            print(f"[기록 저장 성공] Record ID: {res.json().get('record', {}).get('id')}")
            return True, res.json()
          else:
            print(f"[기록 저장 실패] HTTP {res.status_code}: {res.text}")
            return False, res.text
        except Exception as e:
          print(f"[기록 저장 에러] {e}")
          return False, str(e)
    
    # def sync_scores(self, local_save_dir: str):
    #     """서버로부터 악보 리스트를 받아 로컬 캐시 우선 동기화"""
    #     url = f"{self.base_url}/api/scores/sync"
    #     try:
    #         res = requests.get(url, headers=self._get_headers(), verify=False, timeout=15)
    #         if res.status_code == 200:
    #             payload = res.json()
    #             score_items = payload.get("items", []) if isinstance(payload, dict) else payload

    #             os.makedirs(local_save_dir, exist_ok=True)
    #             download_count = 0

    #             for item in score_items:
    #                 raw_title = item.get("title", "")
    #                 if not raw_title:
    #                     continue

    #                 # 안전한 파일명 생성
    #                 safe_title = "".join(c for c in raw_title if c.isalnum() or c in (" ", "_", "-")).rstrip().lower()
    #                 filename = f"{safe_title}_parsed.json"
    #                 local_path = os.path.join(local_save_dir, filename)

    #                 # 이미 로컬에 있다면 건너뜀
    #                 if os.path.exists(local_path):
    #                     continue

    #                 score_id = item.get("score_id")
    #                 version_id = item.get("selected_version_id")
    #                 notes_data = item.get("data")

    #                 # 1. 상세 엔드포인트 호출 시도 (버전별 또는 스코어별)
    #                 if not notes_data and score_id:
    #                     try:
    #                         # 버전 상세 엔드포인트 시도
    #                         v_url = f"{self.base_url}/api/scores/{score_id}/versions/{version_id}"
    #                         v_res = requests.get(v_url, headers=self._get_headers(), verify=False, timeout=5)
    #                         if v_res.status_code == 200:
    #                             v_json = v_res.json()
    #                             notes_data = v_json.get("data") or v_json.get("notes") or v_json
    #                         else:
    #                             # 일반 상세 엔드포인트 시도
    #                             s_url = f"{self.base_url}/api/scores/{score_id}"
    #                             s_res = requests.get(s_url, headers=self._get_headers(), verify=False, timeout=5)
    #                             if s_res.status_code == 200:
    #                                 s_json = s_res.json()
    #                                 notes_data = s_json.get("data") or s_json.get("notes") or s_json
    #                     except Exception as err:
    #                         print(f"[상세 조회 실패 - {safe_title}] {err}")

    #                 # 2. 서버에 메타데이터만 등록되어 있고 상세 데이터가 아직 없을 경우:
    #                 # 빈 리스트라도 파일로 저장하여 목록 카드에 정상 표시되도록 보장
    #                 if notes_data is None:
    #                     notes_data = []

    #                 with open(local_path, "w", encoding="utf-8") as f:
    #                     json.dump(notes_data, f, indent=2, ensure_ascii=False)
    #                 download_count += 1
    #                 print(f"[동기화 저장 완료] {filename}")

    #             print(f"[동기화] 총 {len(score_items)}개의 서버 악보 중 신규 {download_count}개 반영 완료")
    #             return True, download_count
    #         return False, f"HTTP {res.status_code}: {res.text}"
    #     except Exception as e:
    #         return False, f"동기화 통신 에러: {e}"
    
    # def upload_score(self, title: str, parsed_notes: list, source_type: str = "guitar_pro"):
    #     """
    #     신규 파싱된 악보 백엔드 업로드 (POST /api/scores)
    #     - title: 악보 제목 (문자열)
    #     - source_type: 악보 출처 ("guitar_pro")
    #     - notes: 음표 데이터 리스트 (step, score_line, measure, beat, string, fret, duration_ms, midi_pitch 등)
    #     """
    #     url = f"{self.base_url}/api/scores"

    #     # 💡 팀원이 제공한 Postman 스펙과 100% 일치하는 페이로드
    #     payload = {
    #         "title": title,
    #         "source_type": source_type,
    #         "notes": parsed_notes
    #     }

    #     try:
    #         res = requests.post(
    #             url,
    #             json=payload,
    #             headers=self._get_headers(),
    #             verify=False,
    #             timeout=30
    #         )
    #         print(f"[업로드 응답 상태코드] {res.status_code}")
    #         print(f"[업로드 응답 바디] {res.text}")

    #         if res.status_code in [200, 201]:
    #             return True, res.json()
    #         return False, f"HTTP {res.status_code}: {res.text}"
    #     except Exception as e:
    #         return False, f"업로드 통신 에러: {e}"
          
    def sync_scores(self, local_save_dir: str):
        """서버로부터 악보 리스트를 받아 로컬 캐시 우선 동기화"""
        # 💡 [추가] 토큰이 없으면 오프라인 상태이므로 서버 통신을 아예 시도하지 않고 바로 종료
        if not self.access_token:
            print("[동기화 건너뜀] 비로그인(게스트) 상태이므로 서버 동기화를 생략합니다.")
            return True, 0

        url = f"{self.base_url}/api/scores/sync"
        try:
            res = requests.get(
                url, headers=self._get_headers(), verify=False, timeout=3
            )  # 타임아웃 15초 -> 3초로 단축
            if res.status_code == 200:
                payload = res.json()
                score_items = (
                    payload.get("items", [])
                    if isinstance(payload, dict)
                    else payload
                )

                os.makedirs(local_save_dir, exist_ok=True)
                download_count = 0

                for item in score_items:
                    raw_title = item.get("title", "")
                    if not raw_title:
                        continue

                    score_id = str(UUID(str(item["score_id"])))
                    safe_title = raw_title
                    local_path = Path(local_save_dir) / "server" / f"{score_id}_parsed.json"

                    # Keep the original skip-download behavior and retain server identity.
                    if local_path.exists():
                        with local_path.open(encoding="utf-8") as file:
                            notes = json.load(file)
                        self.cache_server_score(
                            local_save_dir, score_id, raw_title, notes, item.get("is_default"),
                        )
                        continue

                    version_id = item.get("selected_version_id")
                    notes_data = item.get("data")

                    if not notes_data and score_id:
                        try:
                            v_url = f"{self.base_url}/api/scores/{score_id}/versions/{version_id}"
                            v_res = requests.get(
                                v_url,
                                headers=self._get_headers(),
                                verify=False,
                                timeout=2,
                            )
                            if v_res.status_code == 200:
                                v_json = v_res.json()
                                notes_data = (
                                    v_json.get("data")
                                    or v_json.get("notes")
                                    or v_json
                                )
                            else:
                                s_url = f"{self.base_url}/api/scores/{score_id}"
                                s_res = requests.get(
                                    s_url,
                                    headers=self._get_headers(),
                                    verify=False,
                                    timeout=2,
                                )
                                if s_res.status_code == 200:
                                    s_json = s_res.json()
                                    notes_data = (
                                        s_json.get("data")
                                        or s_json.get("notes")
                                        or s_json
                                    )
                        except Exception as err:
                            print(f"[상세 조회 실패 - {safe_title}] {err}")

                    if notes_data is None:
                        notes_data = []

                    self.cache_server_score(
                        local_save_dir, score_id, raw_title, notes_data, item.get("is_default"),
                    )
                    download_count += 1
                    print(f"[동기화 저장 완료] {score_id}")

                print(
                    f"[동기화] 총 {len(score_items)}개의 서버 악보 중 신규 {download_count}개 반영 완료"
                )
                return True, download_count
            return False, f"HTTP {res.status_code}: {res.text}"
        except Exception as e:
            return False, f"동기화 통신 에러: {e}"

    def upload_score(
        self,
        title: str,
        parsed_notes: list,
        source_type: str = "guitar_pro",
    ):
        """신규 파싱된 악보 백엔드 업로드 (POST /api/scores)"""
        # 💡 [추가] 토큰이 없으면 오프라인(게스트) 모드이므로 30초 대기 없이 즉시 로컬 모드로 반환
        if not self.access_token:
            print(
                "[업로드 생략] 비로그인(게스트) 상태이므로 서버 업로드를 건너뛰고 로컬에서만 사용합니다."
            )
            return (
                False,
                "GUEST_MODE",
            )  # 또는 True, {"message": "local only"}

        url = f"{self.base_url}/api/scores"

        payload = {
            "title": title,
            "source_type": source_type,
            "notes": parsed_notes,
        }

        try:
            res = requests.post(
                url,
                json=payload,
                headers=self._get_headers(),
                verify=False,
                timeout=3,  # 💡 30초 대기 -> 3초로 대폭 단축 (서버 죽어있을 때 즉시 넘어가도록)
            )
            print(f"[업로드 응답 상태코드] {res.status_code}")
            print(f"[업로드 응답 바디] {res.text}")

            if res.status_code in [200, 201]:
                return True, res.json()
            return False, f"HTTP {res.status_code}: {res.text}"
        except Exception as e:
            return False, f"업로드 통신 에러: {e}"
          
    def logout(self):
        """세션 토큰 및 사용자 정보 초기화"""
        self.access_token = None
        self.user_info = None
        print("[세션 종료] 사용자 토큰 및 세션 정보가 초기화되었습니다.")
    
api_client = APIClient()
