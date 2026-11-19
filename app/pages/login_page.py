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
from app.api_client import api_client
from app.config import USER_DATA_DIR
from app.google_auth import GoogleAuthConfigurationError, fetch_google_id_token




def hash_password(password: str) -> str:
    """비밀번호 SHA-256 단방향 암호화 해시 함수"""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


class LoginPage(QWidget):
    # 로그인 성공 시 유저 정보(dict)를 메인 윈도우로 쏘아 올리는 시그널
    login_success = Signal(dict)

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

        # 로컬 유저 DB 파일 경로
        self.db_dir = str(USER_DATA_DIR)
        os.makedirs(self.db_dir, exist_ok=True)
        self.users_file = os.path.join(self.db_dir, "users.json")
        self._ensure_user_db()

        self.is_signup_mode = False  # False: 로그인 모드, True: 회원가입 모드
        self.init_ui()

    def _ensure_user_db(self):
        """유저 파일이 없으면 기본 생성"""
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
        # ── [프로덕션급 미니멀 라이트 테마 QSS] ──
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("""
            QWidget {
                background-color: #f8f9fa;
                color: #212529;
            }

            /* 메인 카드 컨테이너 */
            QFrame#card {
                background-color: #ffffff;
                border: 1px solid #e9ecef;
                border-radius: 12px;
            }

            QLabel {
                background-color: transparent;
            }

            /* 텍스트 입력 필드 */
            QLineEdit {
                background-color: #ffffff;
                border: 1px solid #ced4da;
                border-radius: 6px;
                padding: 10px 14px;
                font-size: 13.5px;
                color: #212529;
                selection-background-color: #2563eb;
            }
            QLineEdit:hover {
                border: 1px solid #adb5bd;
            }
            QLineEdit:focus {
                border: 1px solid #2563eb;
                background-color: #ffffff;
            }
            QLineEdit::placeholder {
                color: #adb5bd;
            }

            /* 기본 실행 버튼 (로그인 / 회원가입 완료) */
            QPushButton#btn_primary {
                background-color: #111827;
                color: #ffffff;
                border: 1px solid #111827;
                border-radius: 6px;
                font-size: 14px;
                font-weight: 600;
                padding: 11px;
                min-height: 18px;
            }
            QPushButton#btn_primary:hover {
                background-color: #27272a;
                border-color: #27272a;
            }
            QPushButton#btn_primary:pressed {
                background-color: #09090b;
                border-color: #09090b;
            }

            /* 보조 소셜 버튼 */
            QPushButton#btn_social {
                background-color: #ffffff;
                border: 1px solid #ced4da;
                border-radius: 6px;
                font-size: 13px;
                font-weight: 500;
                color: #374151;
                padding: 10px;
            }
            QPushButton#btn_social:hover {
                background-color: #f8f9fa;
                border-color: #adb5bd;
            }
            QPushButton#btn_social:pressed {
                background-color: #e9ecef;
            }

            /* 텍스트 링크 버튼 */
            QPushButton#btn_link {
                background-color: transparent;
                border: none;
                color: #2563eb;
                font-size: 12.5px;
                font-weight: 600;
                padding: 2px 4px;
            }
            QPushButton#btn_link:hover {
                color: #1d4ed8;
                text-decoration: underline;
            }
        """)

        # 화면 중앙 정렬 래퍼 레이아웃
        outer_layout = QVBoxLayout(self)
        outer_layout.setAlignment(Qt.AlignCenter)
        outer_layout.setContentsMargins(24, 24, 24, 24)

        # 중앙 카드
        card = QFrame()
        card.setObjectName("card")
        card.setFixedWidth(400)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(36, 40, 36, 36)
        card_layout.setSpacing(12)

        # 상단 브랜드 로고마크 (이모지 대체: 미니멀 텍스트 엠블럼)
        self.lbl_brand_badge = QLabel("DEVs")
        self.lbl_brand_badge.setAlignment(Qt.AlignCenter)
        self.lbl_brand_badge.setStyleSheet("""
            font-size: 11px;
            font-weight: 700;
            color: #4b5563;
            letter-spacing: 2px;
            padding-bottom: 2px;
        """)
        card_layout.addWidget(self.lbl_brand_badge)

        self.lbl_title = QLabel("기타 연습 지원 프로그램")
        self.lbl_title.setAlignment(Qt.AlignCenter)
        self.lbl_title.setStyleSheet("font-size: 18px; font-weight: 700; color: #111827; letter-spacing: -0.3px;")
        card_layout.addWidget(self.lbl_title)

        self.lbl_subtitle = QLabel("서비스 이용을 위해 계정으로 로그인해 주세요.")
        self.lbl_subtitle.setAlignment(Qt.AlignCenter)
        self.lbl_subtitle.setStyleSheet("font-size: 12.5px; color: #6b7280; margin-bottom: 12px;")
        card_layout.addWidget(self.lbl_subtitle)

        # 입력 필드군
        self.input_email = QLineEdit()
        self.input_email.setPlaceholderText("이메일 주소")
        card_layout.addWidget(self.input_email)

        # 회원가입 시 표시되는 닉네임 입력창
        self.input_name = QLineEdit()
        self.input_name.setPlaceholderText("사용자 이름")
        self.input_name.setVisible(False)
        card_layout.addWidget(self.input_name)

        self.input_pw = QLineEdit()
        self.input_pw.setPlaceholderText("비밀번호")
        self.input_pw.setEchoMode(QLineEdit.Password)
        card_layout.addWidget(self.input_pw)

        # 회원가입 시 표시되는 비밀번호 확인창
        self.input_pw_confirm = QLineEdit()
        self.input_pw_confirm.setPlaceholderText("비밀번호 확인")
        self.input_pw_confirm.setEchoMode(QLineEdit.Password)
        self.input_pw_confirm.setVisible(False)
        card_layout.addWidget(self.input_pw_confirm)

        # 엔터 키 연결
        self.input_pw.returnPressed.connect(self.handle_submit)
        self.input_pw_confirm.returnPressed.connect(self.handle_submit)

        card_layout.addSpacing(4)

        # 메인 제출 버튼
        self.btn_submit = QPushButton("로그인")
        self.btn_submit.setObjectName("btn_primary")
        self.btn_submit.setCursor(Qt.PointingHandCursor)
        self.btn_submit.clicked.connect(self.handle_submit)
        card_layout.addWidget(self.btn_submit)

        # 모드 전환 영역 (로그인 <-> 회원가입)
        switch_layout = QHBoxLayout()
        switch_layout.setAlignment(Qt.AlignCenter)
        switch_layout.setContentsMargins(0, 4, 0, 4)
        self.lbl_switch_desc = QLabel("계정이 없으신가요?")
        self.lbl_switch_desc.setStyleSheet("color: #6b7280; font-size: 12.5px;")
        self.btn_switch_mode = QPushButton("회원가입")
        self.btn_switch_mode.setObjectName("btn_link")
        self.btn_switch_mode.setCursor(Qt.PointingHandCursor)
        self.btn_switch_mode.clicked.connect(self.toggle_mode)
        switch_layout.addWidget(self.lbl_switch_desc)
        switch_layout.addWidget(self.btn_switch_mode)
        card_layout.addLayout(switch_layout)

        # 정돈된 디바이더
        divider_layout = QHBoxLayout()
        divider_layout.setContentsMargins(0, 6, 0, 6)
        line_left = QFrame()
        line_left.setFrameShape(QFrame.HLine)
        line_left.setStyleSheet("border: none; border-top: 1px solid #e5e7eb;")
        line_right = QFrame()
        line_right.setFrameShape(QFrame.HLine)
        line_right.setStyleSheet("border: none; border-top: 1px solid #e5e7eb;")
        # lbl_or = QLabel("또는")
        # lbl_or.setStyleSheet("color: #9ca3af; font-size: 11px; padding: 0 10px;")
        divider_layout.addWidget(line_left)
        # divider_layout.addWidget(lbl_or)
        divider_layout.addWidget(line_right)
        card_layout.addLayout(divider_layout)

        # Google 간편 인증 버튼
        self.btn_google = QPushButton("Google 계정으로 계속하기")
        self.btn_google.setObjectName("btn_social")
        self.btn_google.setCursor(Qt.PointingHandCursor)
        self.btn_google.clicked.connect(self.handle_google_login)
        card_layout.addWidget(self.btn_google)

        # 게스트 진입 버튼
        self.btn_guest = QPushButton("로그인 없이 계속하기")
        self.btn_guest.setObjectName("btn_link")
        self.btn_guest.setCursor(Qt.PointingHandCursor)
        self.btn_guest.setStyleSheet("color: #6b7280; font-size: 12px; font-weight: normal; margin-top: 4px;")
        self.btn_guest.clicked.connect(self.handle_guest_login)
        card_layout.addWidget(self.btn_guest)

        outer_layout.addWidget(card)

    def toggle_mode(self):
        """로그인 <-> 회원가입 모드 스위치"""
        self.is_signup_mode = not self.is_signup_mode

        if self.is_signup_mode:
            self.lbl_title.setText("회원가입")
            self.lbl_subtitle.setText("새로운 계정을 생성하여 연습을 시작하세요.")
            self.input_name.setVisible(True)
            self.input_pw_confirm.setVisible(True)
            self.btn_submit.setText("가입 완료")
            self.lbl_switch_desc.setText("이미 계정이 있으신가요?")
            self.btn_switch_mode.setText("로그인")
        else:
            self.lbl_title.setText("기타 연습 지원 시스템")
            self.lbl_subtitle.setText("서비스 이용을 위해 계정으로 로그인해 주세요.")
            self.input_name.setVisible(False)
            self.input_pw_confirm.setVisible(False)
            self.btn_submit.setText("로그인")
            self.lbl_switch_desc.setText("계정이 없으신가요?")
            self.btn_switch_mode.setText("회원가입")

    def handle_submit(self):
        email = self.input_email.text().strip()
        pw = self.input_pw.text()

        if not email or not pw:
            QMessageBox.warning(self, "입력 오류", "이메일과 비밀번호를 모두 입력해 주세요.")
            return

        with open(self.users_file, "r", encoding="utf-8") as f:
            users = json.load(f)

        if self.is_signup_mode:
            name = self.input_name.text().strip()
            pw_confirm = self.input_pw_confirm.text()

            if not name:
                QMessageBox.warning(self, "입력 오류", "사용자 이름을 입력해 주세요.")
                return
            if pw != pw_confirm:
                QMessageBox.warning(self, "비밀번호 불일치", "비밀번호 확인이 일치하지 않습니다.")
                return
            if email in users:
                QMessageBox.warning(self, "계정 중복", "이미 등록된 이메일 주소입니다.")
                return

            users[email] = {
                "username": name,
                "password_hash": hash_password(pw),
                "created_at": "2026-09-28",
            }
            with open(self.users_file, "w", encoding="utf-8") as f:
                json.dump(users, f, indent=2, ensure_ascii=False)

            QMessageBox.information(self, "가입 성공", f"{name}님, 가입이 완료되었습니다.")
            self.toggle_mode()
        else:
            user = users.get(email)
            if not user or user.get("password_hash") != hash_password(pw):
                QMessageBox.critical(self, "로그인 실패", "이메일 또는 비밀번호가 올바르지 않습니다.")
                return

            # 로그인 성공 -> SelectPage로 전환
            self.login_success.emit(user)
            if hasattr(self.main_window, "switch_to_select"):
                self.main_window.switch_to_select()
            else:
                self.main_window.stacked_widget.setCurrentIndex(1)

    def handle_google_login(self):
        """실제 구글 OAuth 브라우저 로그인 수행"""
        print("[로그인 시작] 브라우저를 열어 구글 계정 로그인을 진행합니다...")

        # 1. 브라우저가 자동으로 열리고 사용자가 계정을 선택하여 신규 id_token 발급
        try:
            google_id_token = fetch_google_id_token()
        except GoogleAuthConfigurationError as error:
            QMessageBox.warning(self, "Google 로그인 설정 필요", str(error))
            return

        if not google_id_token:
            QMessageBox.warning(
                self, 
                "로그인 취소", 
                "구글 로그인이 완료되지 않았거나 취소되었습니다."
            )
            return

        # 2. 발급받은 실시간 id_token을 AWS 백엔드(/api/auth/google)로 전송하여 JWT 세션 획득
        success, res = api_client.login_with_google_id_token(google_id_token)

        if success:
            user_name = api_client.user_info.get("username", "사용자") if api_client.user_info else "사용자"
            print(f"[로그인 완료] {user_name}님 인증 완료. 메인 보관함으로 이동합니다.")

            # 3. 메인 보관함 화면(인덱스 1)으로 페이지 전환
            if hasattr(self.main_window, "stacked_widget"):
                self.main_window.stacked_widget.setCurrentIndex(1)
                
                # 4. 서버에 등록된 최신 악보 목록 자동 동기화
                select_page = self.main_window.stacked_widget.widget(1)
                if hasattr(select_page, "sync_with_server"):
                    select_page.sync_with_server()
        else:
            QMessageBox.critical(
                self, 
                "인증 실패", 
                f"서버 세션 발급에 실패했습니다:\n{res}"
            )


    def handle_guest_login(self):
        """비로그인 게스트 바로 시작"""
        guest_user = {"username": "게스트", "email": "guest@local"}
        self.login_success.emit(guest_user)
        if hasattr(self.main_window, "switch_to_select"):
            self.main_window.switch_to_select()
        else:
            self.main_window.stacked_widget.setCurrentIndex(1)