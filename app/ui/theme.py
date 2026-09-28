"""
Guitar Practice Support SW - Unified Design System Theme
전체 4개 화면(Login, Select, Practice, Admin)에 일괄 적용되는 공통 테마 토큰 및 스타일시트
"""

class Theme:
    # ── 기본 배경 및 서피스 색상 ──
    BG_BASE = "#0f1117"        # 최상위 윈도우 배경 (다크 미드나잇)
    BG_CARD = "#1a1d26"        # 카드, 패널, 폼 컨테이너 배경
    BG_INPUT = "#222634"       # 입력창, 리스트 아이템 배경
    BG_HOVER = "#2d3345"       # 마우스 호버 시 배경

    # ── 브랜드 및 액센트 포인트 색상 ──
    PRIMARY = "#3b82f6"        # 메인 액센트 블루 (선택, 포커스)
    PRIMARY_HOVER = "#60a5fa"
    ACCENT_AMBER = "#f59e0b"   # 연습/진행 상태 포인트
    ACCENT_GREEN = "#10b981"   # 정타/성공 판정

    # ── 텍스트 색상 ──
    TEXT_MAIN = "#f3f4f6"      # 메인 텍스트 (높은 가독성)
    TEXT_MUTED = "#9ca3af"     # 부가 설명, 라벨
    TEXT_DIM = "#6b7280"       # 비활성 텍스트

    # ── 테두리 및 구분선 ──
    BORDER = "#2e3446"
    BORDER_FOCUS = "#3b82f6"

    # ── 폰트 패밀리 ──
    FONT_FAMILY = "'Pretendard', 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif"

    @classmethod
    def get_global_stylesheet(cls) -> str:
        """전체 위젯에 상속되는 기본 QSS 규격"""
        return f"""
            QWidget {{
                font-family: {cls.FONT_FAMILY};
                color: {cls.TEXT_MAIN};
            }}
            QToolTip {{
                background-color: {cls.BG_CARD};
                color: {cls.TEXT_MAIN};
                border: 1px solid {cls.BORDER};
                padding: 6px 10px;
                border-radius: 6px;
            }}
            QScrollBar:vertical {{
                background: {cls.BG_BASE};
                width: 8px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: {cls.BORDER};
                border-radius: 4px;
                min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {cls.TEXT_DIM};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """