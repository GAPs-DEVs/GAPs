import hashlib
import json
import os
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


def hash_password(password: str) -> str:
    """비밀번호 SHA-256 단방향 암호화 해시 함수"""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


class LoginPage(QWidget):
    # 로그인 성공 시 유저 정보(dict)를 메인 윈도우로 쏘아 올리는 시그널
    login_success = Signal(dict)

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

        # 💡 로컬 유저 DB 파일 경로 (향후 서버 API로 쉽게 전환 가능)
        current_dir = os.path.dirname(os.path.abspath(__file__)) # pages 폴더
        project_root = os.path.dirname(current_dir)             # test 루트 폴더
        self.db_dir = os.path.join(project_root, "data")
        os.makedirs(self.db_dir, exist_ok=True)
        self.users_file = os.path.join(self.db_dir, "users.json")
        self._ensure_user_db()

        self.is_signup_mode = False  # False: 로그인 모드, True: 회원가입 모드
        self.init_ui()

    def _ensure_user_db(self):
        """유저 파일이 없으면 기본 생성 (테스트용 기본 계정 포함)"""
        if not os.path.exists(self.users_file):
            default_users = {
                "test@test.com": {
                    "username": "기타꿈나무",
                    "password_hash": hash_password("1234"),
                    "created_at": "2026-01-01",
                }
            }
            with open(self.users_file, "w", encoding="utf-8") as f:
                json.dump(default_users, f, indent=2, ensure_ascii=False)

    def init_ui(self):
        # 전체 톤앤매너: 기존 SelectPage와 통일된 부드러운 라이트 그레이 카드 스타일
        self.setStyleSheet("""
            QWidget {
                font-family: 'Apple SD Gothic Neo', 'Malgun Gothic', 'Arial';
                background-color: #f5f5f7;
                color: #222222;
            }
            /* 로그인 중앙 카드 */
            QFrame#card {
                background-color: #ffffff;
                border: 1px solid #e5e5ea;
                border-radius: 16px;
            }
            QLabel {
                background-color: transparent;
            }
            QLineEdit {
                background-color: #f9f9fb;
                border: 1px solid #d1d1d6;
                border-radius: 8px;
                padding: 12px 14px;
                font-size: 14px;
                color: #222222;
            }
            QLineEdit:focus {
                border: 1.5px solid #007aff;
                background-color: #ffffff;
            }
            /* 주 동작 버튼 (로그인 / 회원가입) */
            QPushButton#btn_primary {
                background-color: #333333;
                color: #ffffff;
                border: none;
                border-radius: 8px;
                font-size: 15px;
                font-weight: bold;
                padding: 13px;
                min-height: 22px;
            }
            QPushButton#btn_primary:hover {
                background-color: #555555;
            }
            QPushButton#btn_primary:pressed {
                background-color: #222222;
            }
            /* 소셜 로그인 버튼 */
            QPushButton#btn_social {
                background-color: #ffffff;
                border: 1px solid #d1d1d6;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 500;
                color: #333333;
                padding: 10px;
            }
            QPushButton#btn_social:hover {
                background-color: #f5f5f7;
            }
            /* 텍스트 링크 버튼 */
            QPushButton#btn_link {
                background-color: transparent;
                border: none;
                color: #007aff;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton#btn_link:hover {
                text-decoration: underline;
            }
        """)

        # 화면 중앙 정렬을 위한 외부 레이아웃
        outer_layout = QVBoxLayout(self)
        outer_layout.setAlignment(Qt.AlignCenter)

        # 중앙 카드 프레임
        card = QFrame()
        card.setObjectName("card")
        card.setFixedWidth(420)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(36, 40, 36, 36)
        card_layout.setSpacing(14)

        # 헤더 타이틀
        self.lbl_logo = QLabel("🎸")
        self.lbl_logo.setAlignment(Qt.AlignCenter)
        self.lbl_logo.setStyleSheet("font-size: 40px;")
        card_layout.addWidget(self.lbl_logo)

        self.lbl_title = QLabel("AI 기타 연습 가이드")
        self.lbl_title.setAlignment(Qt.AlignCenter)
        self.lbl_title.setStyleSheet("font-size: 20px; font-weight: bold; color: #111111;")
        card_layout.addWidget(self.lbl_title)

        self.lbl_subtitle = QLabel("로그인하여 나의 연습 악보를 불러오세요.")
        self.lbl_subtitle.setAlignment(Qt.AlignCenter)
        self.lbl_subtitle.setStyleSheet("font-size: 13px; color: #86868b; margin-bottom: 10px;")
        card_layout.addWidget(self.lbl_subtitle)

        # 입력 폼
        self.input_email = QLineEdit()
        self.input_email.setPlaceholderText("이메일 주소 (예: user@example.com)")
        card_layout.addWidget(self.input_email)

        # 회원가입 시에만 나타나는 사용자 이름 입력창
        self.input_name = QLineEdit()
        self.input_name.setPlaceholderText("사용자 이름 / 닉네임")
        self.input_name.setVisible(False)
        card_layout.addWidget(self.input_name)

        self.input_pw = QLineEdit()
        self.input_pw.setPlaceholderText("비밀번호")
        self.input_pw.setEchoMode(QLineEdit.Password)
        card_layout.addWidget(self.input_pw)

        # 회원가입 시에만 나타나는 비밀번호 확인창
        self.input_pw_confirm = QLineEdit()
        self.input_pw_confirm.setPlaceholderText("비밀번호 확인")
        self.input_pw_confirm.setEchoMode(QLineEdit.Password)
        self.input_pw_confirm.setVisible(False)
        card_layout.addWidget(self.input_pw_confirm)

        # 엔터 키 치면 바로 로그인/가입 실행
        self.input_pw.returnPressed.connect(self.handle_submit)
        self.input_pw_confirm.returnPressed.connect(self.handle_submit)

        # 메인 제출 버튼
        self.btn_submit = QPushButton("로그인")
        self.btn_submit.setObjectName("btn_primary")
        self.btn_submit.setCursor(Qt.PointingHandCursor)
        self.btn_submit.clicked.connect(self.handle_submit)
        card_layout.addWidget(self.btn_submit)

        # 전환 토글 영역 (로그인 <-> 회원가입)
        switch_layout = QHBoxLayout()
        switch_layout.setAlignment(Qt.AlignCenter)
        self.lbl_switch_desc = QLabel("계정이 없으신가요?")
        self.lbl_switch_desc.setStyleSheet("color: #666666; font-size: 13px;")
        self.btn_switch_mode = QPushButton("회원가입")
        self.btn_switch_mode.setObjectName("btn_link")
        self.btn_switch_mode.setCursor(Qt.PointingHandCursor)
        self.btn_switch_mode.clicked.connect(self.toggle_mode)
        switch_layout.addWidget(self.lbl_switch_desc)
        switch_layout.addWidget(self.btn_switch_mode)
        card_layout.addLayout(switch_layout)

        # 구분선
        divider_layout = QHBoxLayout()
        line_left = QFrame()
        line_left.setFrameShape(QFrame.HLine)
        line_left.setStyleSheet("color: #e5e5ea;")
        line_right = QFrame()
        line_right.setFrameShape(QFrame.HLine)
        line_right.setStyleSheet("color: #e5e5ea;")
        lbl_or = QLabel("또는")
        lbl_or.setStyleSheet("color: #8e8e93; font-size: 11px; padding: 0 8px;")
        divider_layout.addWidget(line_left)
        divider_layout.addWidget(lbl_or)
        divider_layout.addWidget(line_right)
        card_layout.addLayout(divider_layout)

        # 소셜 로그인 버튼 레이아웃
        self.btn_google = QPushButton("🌐 Google 계정으로 로그인")
        self.btn_google.setObjectName("btn_social")
        self.btn_google.setCursor(Qt.PointingHandCursor)
        self.btn_google.clicked.connect(lambda: self.handle_social_login("Google"))
        card_layout.addWidget(self.btn_google)

        self.btn_guest = QPushButton("👤 게스트 모드로 바로 시작")
        self.btn_guest.setObjectName("btn_social")
        self.btn_guest.setCursor(Qt.PointingHandCursor)
        self.btn_guest.clicked.connect(self.handle_guest_login)
        card_layout.addWidget(self.btn_guest)

        outer_layout.addWidget(card)

    def toggle_mode(self):
        """로그인 ↔ 회원가입 폼 전환"""
        self.is_signup_mode = not self.is_signup_mode

        if self.is_signup_mode:
            self.lbl_title.setText("새 계정 만들기")
            self.lbl_subtitle.setText("연습 기록과 맞춤 악보를 안전하게 저장합니다.")
            self.btn_submit.setText("회원가입 완료")
            self.lbl_switch_desc.setText("이미 계정이 있으신가요?")
            self.btn_switch_mode.setText("로그인")
            self.input_name.setVisible(True)
            self.input_pw_confirm.setVisible(True)
        else:
            self.lbl_title.setText("AI 기타 연습 가이드")
            self.lbl_subtitle.setText("로그인하여 나의 연습 악보를 불러오세요.")
            self.btn_submit.setText("로그인")
            self.lbl_switch_desc.setText("계정이 없으신가요?")
            self.btn_switch_mode.setText("회원가입")
            self.input_name.setVisible(False)
            self.input_pw_confirm.setVisible(False)

        self.input_pw.clear()
        self.input_pw_confirm.clear()

    def handle_submit(self):
        """제출 처리 (로그인 or 회원가입)"""
        email = self.input_email.text().strip()
        pw = self.input_pw.text().strip()

        if not email or not pw:
            QMessageBox.warning(self, "입력 오류", "이메일과 비밀번호를 모두 입력해주세요.")
            return

        if self.is_signup_mode:
            # 회원가입 검증
            name = self.input_name.text().strip()
            pw_confirm = self.input_pw_confirm.text().strip()

            if not name:
                QMessageBox.warning(self, "입력 오류", "사용자 이름을 입력해주세요.")
                return
            if pw != pw_confirm:
                QMessageBox.warning(self, "입력 오류", "비밀번호가 일치하지 않습니다.")
                return

            self._process_signup(email, name, pw)
        else:
            # 로그인 검증
            self._process_login(email, pw)

    def _process_signup(self, email: str, name: str, pw: str):
        """회원가입 로직 (로컬 DB 저장)"""
        try:
            with open(self.users_file, "r", encoding="utf-8") as f:
                users = json.load(f)

            if email in users:
                QMessageBox.warning(self, "가입 불가", "이미 존재하는 이메일입니다.")
                return

            users[email] = {
                "username": name,
                "password_hash": hash_password(pw),
                "created_at": "2026-03",
            }

            with open(self.users_file, "w", encoding="utf-8") as f:
                json.dump(users, f, indent=2, ensure_ascii=False)

            QMessageBox.information(self, "가입 성공", f"환영합니다, {name}님!\n로그인을 진행해주세요.")
            self.toggle_mode()
            self.input_email.setText(email)
        except Exception as e:
            QMessageBox.critical(self, "오류", f"회원가입 중 오류가 발생했습니다: {e}")

    def _process_login(self, email: str, pw: str):
        """로그인 로직 (인증 검증)"""
        try:
            with open(self.users_file, "r", encoding="utf-8") as f:
                users = json.load(f)

            user_data = users.get(email)
            if not user_data or user_data.get("password_hash") != hash_password(pw):
                QMessageBox.warning(self, "로그인 실패", "이메일 또는 비밀번호가 일치하지 않습니다.")
                return

            user_payload = {
                "email": email,
                "username": user_data.get("username", "사용자"),
                "is_guest": False,
            }
            self.on_login_complete(user_payload)
        except Exception as e:
            QMessageBox.critical(self, "오류", f"로그인 중 오류가 발생했습니다: {e}")

    def handle_guest_login(self):
        """게스트 바로 시작"""
        user_payload = {
            "email": "guest@local",
            "username": "게스트",
            "is_guest": True,
        }
        self.on_login_complete(user_payload)

    def handle_social_login(self, provider: str):
        """소셜 로그인 핸들러 (향후 브라우저 OAuth 연동)"""
        QMessageBox.information(
            self,
            f"{provider} 로그인",
            f"{provider} 인증 창을 호출합니다.\n(현재는 게스트 세션으로 바로 연결됩니다.)"
        )
        user_payload = {
            "email": f"{provider.lower()}_user@guitar.app",
            "username": f"{provider} 연동 유저",
            "is_guest": False,
        }
        self.on_login_complete(user_payload)

    def on_login_complete(self, user_info: dict):
        """로그인 성공 후 다음 화면으로 라우팅"""
        self.login_success.emit(user_info)
        # 메인 윈도우의 스위칭 함수 호출 (SelectPage로 이동)
        if hasattr(self.main_window, "switch_to_select"):
            self.main_window.current_user = user_info
            self.main_window.switch_to_select()